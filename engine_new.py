"""告警轮询与逐群发送：发布／升级／降级发送；Update 不发送；Cancel 只发文字。"""
from __future__ import annotations

import hashlib
import json
import queue
import threading
from dataclasses import replace
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from core_new import (
    CAT_HEAT_STRESS_WORK, CAT_HOT_WEATHER, CAT_RAINSTORM, CAT_TROPICAL_CYCLONE,
    CATEGORIES, Database, EVENT_DOWNGRADE, EVENT_ISSUE, EVENT_UPDATE, EVENT_UPGRADE,
    NormalizedWarning, get_logger,
)
from hko_data import HKOClient, fetch_hsww_warning, normalize_hsww, normalize_warnsum
from whatsapp_service import build_warning_message, generate_alert_card

log = get_logger('engine')
HKT = ZoneInfo('Asia/Hong_Kong')


def now() -> str:
    return datetime.now(HKT).isoformat(timespec='seconds')

def display_source_time(value: str | None) -> str:
    if not value:
        return "未提供"

    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value).replace("T", " ").replace("+08:00", "")

def warning_source_name(warning: NormalizedWarning) -> str:
    if warning.warning_category == CAT_HEAT_STRESS_WORK:
        return "勞工處"

    return "天文台"

def worktime(settings, at=None) -> bool:
    if not settings.work_hours_enabled:
        return True
    dt = (at or datetime.now(HKT)).astimezone(HKT)
    try:
        start = dt_time.fromisoformat(settings.work_start_time)
        end = dt_time.fromisoformat(settings.work_end_time)
        return dt.weekday() in settings.workdays and start <= dt.time().replace(tzinfo=None) <= end
    except (TypeError, ValueError) as exc:
        raise ValueError('全局工作时间配置无效') from exc


def group_worktime(group, settings, at=None) -> bool:
    dt = (at or datetime.now(HKT)).astimezone(HKT)
    if not group.custom_work_hours_enabled:
        return worktime(settings, dt)
    try:
        start = dt_time.fromisoformat(group.work_start_time)
        end = dt_time.fromisoformat(group.work_end_time)
        return dt.weekday() in group.workdays and start <= dt.time().replace(tzinfo=None) <= end
    except (TypeError, ValueError) as exc:
        raise ValueError(f'群组 {group.display_name} 工作时间配置无效') from exc


def _hash(parts) -> str:
    raw = json.dumps(parts, ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def issue_key(w: NormalizedWarning) -> str:
    return _hash([w.warning_category, w.warning_level, w.issue_time or w.update_time or 'TIME_UNKNOWN'])


def transition_key(w: NormalizedWarning) -> str:
    return _hash([w.warning_category, w.warning_level, w.event_type, w.update_time or w.detected_time or 'TIME_UNKNOWN'])


def cancellation_key(w: NormalizedWarning) -> str:
    return _hash([w.warning_category, w.warning_level, w.issue_time or 'TIME_UNKNOWN', 'CANCEL', w.update_time or 'TIME_UNKNOWN'])


def interpret_result(result, photo_requested: bool):
    if not isinstance(result, dict):
        return 'UNKNOWN', 'UNKNOWN', 'UNEXPECTED_RESULT', repr(result)
    status = str(result.get('status', 'UNKNOWN'))
    code = str(result.get('code') or result.get('error_code') or status)
    detail = str(result.get('detail') or result.get('message') or '')
    explicit = result.get('image_sent', result.get('photo_sent', None))
    if not photo_requested and status.startswith('SENT'):
        return 'SENT', 'NOT_REQUESTED', code, detail
    if status in ('SENT_WITH_IMAGE', 'SENT_IMAGE') or explicit is True:
        return 'SENT', 'SENT', code, detail
    if status.startswith('SENT'):
        return ('PHOTO_FAILED' if explicit is False else 'PHOTO_UNKNOWN',
                'FAILED' if explicit is False else 'UNKNOWN', code, detail)
    if status in ('NOT_LOGGED_IN', 'LOGIN_REQUIRED', 'GROUP_NOT_FOUND', 'FAILED', 'ERROR', 'NETWORK_ERROR', 'BRIDGE_ERROR', 'SEND_FAILED'):
        return 'FAILED', 'NOT_SENT', code, detail
    return 'UNKNOWN', 'UNKNOWN', code, detail


class AlertEngine:
    def __init__(self, db: Database, settings_manager, sender):
        self.db = db
        self.settings_manager = settings_manager
        self.sender = sender
        self._busy = threading.Lock()

    def run_one_poll_cycle(self, progress_cb=None):
        if not self._busy.acquire(blocking=False):
            return [{'status': 'BUSY', 'detail': '上一轮仍在运行'}]
        try:
            return self._cycle(progress_cb or (lambda message: None))
        finally:
            self._busy.release()

    def _record_content_failure(self, warning, key, groups, checked, results, exc, event_type):
        for group in groups:
            self.db.save_delivery(warning, key, group.group_id, 'FAILED', 'NOT_SENT',
                                  'CONTENT_GENERATION_FAILED', str(exc), checked, now(), 0)
            results.append({'status': 'FAILED', 'photo': 'NOT_SENT', 'code': 'CONTENT_GENERATION_FAILED',
                            'detail': str(exc), 'group': group.display_name,
                            'level': warning.warning_level, 'event_type': event_type})

    def _send_to_groups(self, warning, key, groups, message, image_path, checked, results, progress):
        settings = self.settings_manager.settings
        photo_requested = image_path is not None
        for group in groups:
            if settings.dry_run_mode:
                results.append({'status': 'DRY_RUN', 'photo': 'NOT_REQUESTED' if not photo_requested else 'UNKNOWN',
                                'code': 'DRY_RUN', 'group': group.display_name,
                                'level': warning.warning_level, 'event_type': warning.event_type})
                continue
            attempts = max(1, min(int(settings.retry_failed_delivery_max_attempts), 10))
            for attempt in range(1, attempts + 1):
                try:
                    result = self.sender.send_text_and_image(group.whatsapp_exact_name, message, image_path)
                    status, photo, fault_code, detail = interpret_result(result, photo_requested)
                except Exception as exc:
                    log.exception('调用 WhatsApp Bridge 失败：group=%s', group.group_id)
                    status, photo, fault_code, detail = 'UNKNOWN', 'UNKNOWN', type(exc).__name__, str(exc)
                self.db.save_delivery(warning, key, group.group_id, status, photo, fault_code, detail,
                                      checked, now(), attempt)
                results.append({'status': status, 'photo': photo, 'code': fault_code, 'detail': detail,
                                'attempt': attempt, 'group': group.display_name,
                                'level': warning.warning_level, 'event_type': warning.event_type})
                progress(f'{group.display_name} · {warning.display_name()} · {warning.event_type} · {status} · {fault_code}')
                if status != 'FAILED':
                    break

    def _cancel(self, current, old, previous_rank, eligible, checked, results, progress):
        if previous_rank <= 0:
            self.db.save_observed(current, checked)
            return
        old_level = old['level']
        cancel = replace(current, warning_level=old_level, event_type='CANCEL', previous_level=old_level,
                         issue_time=old['issue_time'], update_time=current.update_time or old['update_time'])
        key = cancellation_key(cancel)
        targets = [g for g in eligible if g.subscriptions.get(old_level, False)]



        due = []
        for group in targets:
            prior = self.db.get_delivery(cancel.warning_category, key, group.group_id)
            if not (prior and prior['status'] in ('SENT', 'UNKNOWN')):
                due.append(group)
        if due:
            try:
                message = build_warning_message(cancel)
                if not message.strip():
                    raise RuntimeError('WhatsApp 取消文字模板返回空内容')
            except Exception as exc:
                log.exception('生成取消文字失败：%s', old_level)
                self._record_content_failure(cancel, key, due, checked, results, exc, 'CANCEL')
            else:
                # Cancel 永远只传 None 给 Bridge，不生成也不发送 PNG。
                self._send_to_groups(cancel, key, due, message, None, checked, results, progress)
        self.db.save_observed(current, checked)

    def _active_warning(self, current, old, previous_rank, check_at, eligible, checked, results, progress):
        if previous_rank == 0:
            event_type = EVENT_ISSUE
        elif current.rank() > previous_rank:
            event_type = EVENT_UPGRADE
        elif current.rank() < previous_rank:
            event_type = EVENT_DOWNGRADE
        else:
            event_type = EVENT_UPDATE
        warning = replace(current, event_type=event_type,
                          previous_level=old['level'] if old else None)
        transition = event_type in (EVENT_UPGRADE, EVENT_DOWNGRADE)
        key = transition_key(warning) if transition else issue_key(warning)
        targets = [g for g in eligible if g.subscriptions.get(warning.warning_level, False)]
        if not targets:
            self.db.save_observed(current, checked)
            return
        today = check_at.date().isoformat()

        due = []
        for group in targets:
            prior = self.db.get_delivery(warning.warning_category, key, group.group_id)
            if prior and prior['status'] == 'UNKNOWN':
                continue
            last_sent = self.db.last_text_send(warning.warning_category, key, group.group_id)
            if last_sent and last_sent['send_day'] == today:
                continue
            if event_type in (EVENT_ISSUE, EVENT_UPGRADE, EVENT_DOWNGRADE):
                due.append(group)
            elif last_sent and last_sent['send_day'] < today and group_worktime(group, self.settings_manager.settings, check_at):
                due.append(group)
            # EVENT_UPDATE：无发送。
        if not due:
            self.db.save_observed(current, checked)
            return
        try:
            # 仅使用你原有 whatsapp_service.py 的文字及图片内容。
            message = build_warning_message(warning)
            if not message.strip():
                raise RuntimeError('WhatsApp 文字模板返回空内容')
            image_path = None
            if self.settings_manager.settings.include_image:
                generated = generate_alert_card(warning, org_name=self.settings_manager.settings.org_name,
                                                 app_name=self.settings_manager.settings.app_name)
                image_file = Path(generated)
                if not image_file.is_file() or image_file.stat().st_size == 0:
                    raise RuntimeError(f'警告图片未成功生成：{generated}')
                image_path = str(image_file.resolve())
        except Exception as exc:
            log.exception('生成 WhatsApp 文字或图片失败：%s', warning.warning_level)
            self._record_content_failure(warning, key, due, checked, results, exc, event_type)
        else:
            self._send_to_groups(warning, key, due, message, image_path, checked, results, progress)
        self.db.save_observed(current, checked)

    def _cycle(self, progress):
        settings = self.settings_manager.settings
        groups = [g for g in self.db.list_groups()
                  if g.enabled and g.whatsapp_exact_name.strip() and any(g.subscriptions.values())]
        check_at = datetime.now(HKT)
        eligible = [g for g in groups if group_worktime(g, settings, check_at) or g.is_24x7]
        if not eligible:
            progress('当前所有群组均在工作时间外，停止 API 查询。' if groups else '没有启用的订阅群组。')
            return [{'status': 'SKIPPED', 'detail': '无需查询'}]
        want_hko = any(any(g.subscriptions.get(level, False) for category in
                       (CAT_TROPICAL_CYCLONE, CAT_RAINSTORM, CAT_HOT_WEATHER)
                       for level in CATEGORIES[category]) for g in eligible)
        want_hsww = any(any(g.subscriptions.get(level, False)
                        for level in CATEGORIES[CAT_HEAT_STRESS_WORK]) for g in eligible)
        checked = now()
        warnings, results = [], []
        if want_hko:
            try:
                snapshot = HKOClient(data_source_mode=settings.data_source_mode).fetch_warnsum_snapshot(lang='sc')
                warnings.extend(normalize_warnsum(snapshot.raw, detected_time=checked,
                                                  payload_hash=snapshot.payload_hash).values())
                progress('HKO 查询完成')
            except Exception as exc:
                log.exception('HKO 查询失败')
                results.append({'status': 'SOURCE_FAILED', 'source': 'HKO', 'code': type(exc).__name__, 'detail': str(exc)})
        if want_hsww:
            try:
                warnings.append(normalize_hsww(fetch_hsww_warning(lang='tc'), detected_time=checked))
                progress('劳工处查询完成')
            except Exception as exc:
                log.exception('劳工处查询失败')
                results.append({'status': 'SOURCE_FAILED', 'source': 'HSWW', 'code': type(exc).__name__, 'detail': str(exc)})
        for warning in warnings:
            old = self.db.get_observed(warning.warning_category)
            old_level = old['level'] if old else None
            from core_new import RANKS
            previous_rank = RANKS.get(old_level, 0) if old_level else 0
            if not warning.is_active():
                self._cancel(warning, old, previous_rank, eligible, checked, results, progress)
            else:
                self._active_warning(warning, old, previous_rank, check_at, eligible, checked, results, progress)
        return results


class AlertScheduler:
    def __init__(self, engine, settings_manager, event_queue: queue.Queue):
        self.engine, self.settings_manager, self.events = engine, settings_manager, event_queue
        self._stop, self._wake, self._paused = threading.Event(), threading.Event(), threading.Event()
        self._thread = None
        self._manual = False
        self._lock = threading.Lock()

    def _emit(self, kind, payload):
        self.events.put({'type': kind, 'payload': payload, 'time': now()})

    def start(self):
        if self._thread and self._thread.is_alive():
            self._paused.clear(); self._wake.set(); return
        self._stop.clear(); self._paused.clear()
        self._thread = threading.Thread(target=self._run, name='alert-poll', daemon=True)
        self._thread.start(); self._emit('scheduler_status', 'RUNNING')

    def pause(self):
        self._paused.set(); self._emit('scheduler_status', 'PAUSED')

    def resume(self):
        self._paused.clear(); self._wake.set(); self._emit('scheduler_status', 'RUNNING')

    def trigger_immediate_check(self):
        self.start()
        with self._lock: self._manual = True
        self._wake.set()

    def stop(self):
        self._stop.set(); self._wake.set()
        if self._thread and self._thread.is_alive() and threading.current_thread() != self._thread:
            self._thread.join(timeout=2)
        self._emit('scheduler_status', 'IDLE')

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                manual, self._manual = self._manual, False
            if not self._paused.is_set() or manual:
                self._emit('check_started', None)
                try:
                    summary = self.engine.run_one_poll_cycle(lambda text: self._emit('progress', text))
                    self._emit('check_completed', summary)
                except Exception as exc:
                    log.exception('轮询失败')
                    self._emit('check_error', str(exc))
            self._wake.wait(max(60, int(self.settings_manager.settings.poll_interval_minutes) * 60))
            self._wake.clear()
