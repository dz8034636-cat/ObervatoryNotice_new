"""统一轮询与逐群投递；保持 hko_data.py 和 WhatsApp 桥接文件不变。"""
from __future__ import annotations
import hashlib
import json
import queue
import threading
from dataclasses import replace
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from core_new import (CAT_HEAT_STRESS_WORK,CAT_HOT_WEATHER,CAT_RAINSTORM,CAT_TROPICAL_CYCLONE,
                  CATEGORIES,Database,EVENT_ISSUE,EVENT_UPDATE,EVENT_UPGRADE,EVENT_DOWNGRADE,
                  NormalizedWarning,get_logger)
from hko_data import HKOClient,fetch_hsww_warning,normalize_warnsum,normalize_hsww
from pathlib import Path
from whatsapp_service import build_warning_message, generate_alert_card

log=get_logger('engine')
HKT=ZoneInfo('Asia/Hong_Kong')

def now(): return datetime.now(HKT).isoformat(timespec='seconds')
def worktime(settings):
    if not settings.work_hours_enabled: return True
    dt=datetime.now(HKT)
    try:
        start=dt_time.fromisoformat(settings.work_start_time)
        end=dt_time.fromisoformat(settings.work_end_time)
        return dt.weekday() in settings.workdays and start<=dt.time().replace(tzinfo=None)<=end
    except (TypeError,ValueError) as exc:
        raise ValueError('工作时间配置无效') from exc

def issue_key(w):
    # 以来源提供的发出时间区分同级新一轮警告；无发出时间时使用更新时间。
    raw=json.dumps([w.warning_category,w.warning_level,w.issue_time or w.update_time or 'TIME_UNKNOWN'],ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def text_for(w):
    provider='劳工处' if w.warning_category==CAT_HEAT_STRESS_WORK else '香港天文台'
    label={'ISSUE':'发出','UPGRADE':'升级','DOWNGRADE':'降级','UPDATE':'有效'}
    lines=[f'⚠️ {provider}警告',f'警告：{w.display_name()}',f'状态：{label.get(w.event_type,"有效")}']
    if w.issue_time: lines.append(f'{provider}发布时间：{w.issue_time}')
    if w.update_time and w.update_time!=w.issue_time: lines.append(f'{provider}更新时间：{w.update_time}')
    if not w.issue_time and not w.update_time: lines.append(f'{provider}时间：未提供')
    return '\n'.join(lines)

def interpret_result(result, photo_requested):
    """只根据明确回传认定图片成功；未知结果绝不自动重发。"""
    if not isinstance(result,dict): return 'UNKNOWN','UNKNOWN','UNEXPECTED_RESULT',repr(result)
    status=str(result.get('status','UNKNOWN'))
    code=str(result.get('code') or result.get('error_code') or status)
    detail=str(result.get('detail') or result.get('message') or '')
    explicit=result.get('image_sent',result.get('photo_sent',None))
    if status in ('SENT_WITH_IMAGE','SENT_IMAGE') or explicit is True:
        return 'SENT','SENT',code,detail
    if status.startswith('SENT'):
        return ('PHOTO_FAILED' if explicit is False else 'PHOTO_UNKNOWN'),('FAILED' if explicit is False else 'UNKNOWN'),code,detail
    if status in ('NOT_LOGGED_IN','LOGIN_REQUIRED','GROUP_NOT_FOUND'):
        return 'FAILED','NOT_SENT',code,detail
    if status in ('FAILED','ERROR','NETWORK_ERROR','BRIDGE_ERROR','SEND_FAILED'):
        return 'FAILED','NOT_SENT',code,detail
    return 'UNKNOWN','UNKNOWN',code,detail

class AlertEngine:
    def __init__(self,db:Database,settings_manager,sender):
        self.db,self.settings_manager,self.sender=db,settings_manager,sender
        self._busy=threading.Lock()
    def run_one_poll_cycle(self,progress_cb=None):
        if not self._busy.acquire(blocking=False): return [{'status':'BUSY','detail':'上一轮仍在运行'}]
        try: return self._cycle(progress_cb or (lambda message:None))
        finally: self._busy.release()
    def _cycle(self,progress):
        settings=self.settings_manager.settings
        groups=[g for g in self.db.list_groups() if g.enabled and g.whatsapp_exact_name.strip() and any(g.subscriptions.values())]
        in_hours=worktime(settings)
        eligible=[g for g in groups if in_hours or g.is_24x7]
        if not eligible:
            progress('非工作时间：没有 24/7 订阅群组，停止 API 查询。' if not in_hours else '没有启用的订阅群组。')
            return [{'status':'SKIPPED','detail':'无需查询'}]
        want_hko=any(any(g.subscriptions.get(level,False) for category in (CAT_TROPICAL_CYCLONE,CAT_RAINSTORM,CAT_HOT_WEATHER) for level in CATEGORIES[category]) for g in eligible)
        want_hsww=any(any(g.subscriptions.get(level,False) for level in CATEGORIES[CAT_HEAT_STRESS_WORK]) for g in eligible)
        checked=now()
        warnings=[]
        results=[]
        if want_hko:
            try:
                snap=HKOClient(data_source_mode=settings.data_source_mode).fetch_warnsum_snapshot(lang='sc')
                warnings+=list(normalize_warnsum(snap.raw,detected_time=checked,payload_hash=snap.payload_hash).values())
                progress('HKO 查询完成')
            except Exception as exc:
                log.exception('HKO 查询失败')
                results.append({'status':'SOURCE_FAILED','source':'HKO','code':type(exc).__name__,'detail':str(exc)})
        if want_hsww:
            try:
                warnings.append(normalize_hsww(fetch_hsww_warning(lang='tc'),detected_time=checked))
                progress('劳工处查询完成')
            except Exception as exc:
                log.exception('劳工处查询失败')
                results.append({'status':'SOURCE_FAILED','source':'HSWW','code':type(exc).__name__,'detail':str(exc)})
        for w in warnings:
            old=self.db.get_observed(w.warning_category)
            old_level=old['level'] if old else None
            if not w.is_active():
                self.db.save_observed(w,checked)
                continue
            previous_rank=0
            if old_level:
                from core_new import RANKS
                previous_rank=RANKS.get(old_level,0)
            kind=(EVENT_ISSUE if not previous_rank else EVENT_UPGRADE if w.rank()>previous_rank else
                  EVENT_DOWNGRADE if w.rank()<previous_rank else EVENT_UPDATE)
            w=replace(w,event_type=kind,previous_level=old_level)
            key=issue_key(w)
            targets=[g for g in eligible if g.subscriptions.get(w.warning_level,False)]
            if not targets:
                self.db.save_observed(w,checked)
                continue
            due=[g for g in targets if not (self.db.get_delivery(w.warning_category,key,g.group_id) and
                                            self.db.get_delivery(w.warning_category,key,g.group_id)['status'] in ('SENT','UNKNOWN','PHOTO_UNKNOWN','PHOTO_FAILED'))]
            if not due:
                self.db.save_observed(w,checked)
                continue
            image=None
            if settings.include_image:
                try:
                    message = build_warning_message(w)
                    image_path = generate_alert_card(
                        w,
                        org_name=settings.org_name,
                        app_name=settings.app_name,
                    )

                    image_file = Path(image_path)
                    if not image_file.is_file() or image_file.stat().st_size == 0:
                        raise RuntimeError(f"警告图片未成功生成：{image_path}")

                    image_path = str(image_file.resolve())

                except Exception as exc:
                    log.exception("警告文字或图片生成失败：%s", w.warning_level)

                    for g in due:
                        self.db.save_delivery(
                            w, key, g.group_id,
                            "FAILED", "NOT_SENT",
                            "CONTENT_GENERATION_FAILED", str(exc),
                            checked, now(), 0,
                        )
                        results.append({
                            "status": "FAILED",
                            "code": "CONTENT_GENERATION_FAILED",
                            "group": g.display_name,
                            "level": w.warning_level,
                        })

                    self.db.save_observed(w, checked)
                    continue
                except Exception as exc:
                    log.exception('图片生成失败')
                    for g in due:
                        self.db.save_delivery(w,key,g.group_id,'FAILED','NOT_SENT','IMAGE_GENERATION_FAILED',str(exc),checked,now(),0)
                        results.append({'status':'FAILED','code':'IMAGE_GENERATION_FAILED','group':g.display_name,'level':w.warning_level})
                    self.db.save_observed(w,checked)
                    continue
            for g in due:
                if settings.dry_run_mode:
                    results.append({'status':'DRY_RUN','group':g.display_name,'level':w.warning_level})
                    continue
                attempts=max(1,min(int(settings.retry_failed_delivery_max_attempts),10))
                for attempt in range(1,attempts+1):
                    try:
                        result=self.sender.send_text_and_image(g.whatsapp_exact_name,text_for(w),image)
                        status,photo,code,detail=interpret_result(result,settings.include_image)
                    except Exception as exc:
                        log.exception('桥接调用异常 group=%s',g.group_id)
                        status,photo,code,detail='UNKNOWN','UNKNOWN',type(exc).__name__,str(exc)
                    self.db.save_delivery(w,key,g.group_id,status,photo,code,detail,checked,now(),attempt)
                    results.append({'status':status,'photo':photo,'code':code,'detail':detail,'attempt':attempt,'group':g.display_name,'level':w.warning_level})
                    progress(f'{g.display_name} · {w.display_name()} · {status} · {code}')
                    if status!='FAILED': break
            self.db.save_observed(w,checked)
        return results

class AlertScheduler:
    """只有一个轮询线程；手动检查也由该线程执行，避免浏览器并发使用。"""
    def __init__(self,engine,settings_manager,event_queue:queue.Queue):
        self.engine,self.settings_manager,self.events=engine,settings_manager,event_queue
        self._stop=threading.Event()
        self._wake=threading.Event()
        self._paused=threading.Event()
        self._thread=None
        self._manual=False
        self._lock=threading.Lock()
    def _emit(self,kind,payload): self.events.put({'type':kind,'payload':payload,'time':now()})
    def start(self):
        if self._thread and self._thread.is_alive():
            self._paused.clear()
            self._wake.set()
            return
        self._stop.clear()
        self._paused.clear()
        self._thread=threading.Thread(target=self._run,name='alert-poll',daemon=True)
        self._thread.start()
        self._emit('scheduler_status','RUNNING')
    def pause(self):
        self._paused.set()
        self._emit('scheduler_status','PAUSED')
    def resume(self):
        self._paused.clear()
        self._wake.set()
        self._emit('scheduler_status','RUNNING')
    def trigger_immediate_check(self):
        self.start()
        with self._lock: self._manual=True
        self._wake.set()
    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive() and threading.current_thread()!=self._thread:
            self._thread.join(timeout=2)
        self._emit('scheduler_status','IDLE')
    def _run(self):
        first=True
        while not self._stop.is_set():
            with self._lock:
                manual=self._manual
                self._manual=False
            if not self._paused.is_set() or manual:
                self._emit('check_started',None)
                try: self._emit('check_completed',self.engine.run_one_poll_cycle(lambda text:self._emit('progress',text)))
                except Exception as exc:
                    log.exception('轮询失败')
                    self._emit('check_error',str(exc))
            delay=max(60,int(self.settings_manager.settings.poll_interval_minutes)*60)
            self._wake.wait(delay)
            self._wake.clear()
