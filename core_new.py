"""状态、配置与 SQLite；供现有 hko_data.py 原样导入。"""
from __future__ import annotations
import json
import logging
import logging.handlers
import os
import sqlite3
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from contextlib import contextmanager

APP_DIR_NAME = 'HKO_WhatsApp_Alert'
def get_app_data_dir():
    base = Path(os.environ['LOCALAPPDATA']) if os.environ.get('LOCALAPPDATA') else Path.home() / '.local' / 'share'
    p = base / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p
def _subdir(name):
    p = get_app_data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p
def get_config_dir(): return _subdir('config')
def get_data_dir(): return _subdir('data')
def get_logs_dir(): return _subdir('logs')
def get_snapshots_dir(): return _subdir('data/snapshots')
def get_generated_images_dir(): return _subdir('data/generated_images')
def get_chrome_profile_dir(): return _subdir('chrome_profile')
def get_db_path(): return get_data_dir() / 'app_state.sqlite3'
def get_config_file_path(): return get_config_dir() / 'settings.json'
def get_logger(name): return logging.getLogger('hko_alert.' + name)
def setup_logging(log_retention_days=30):
    log = logging.getLogger('hko_alert')
    log.setLevel(logging.INFO)
    if not log.handlers:
        handler = logging.handlers.TimedRotatingFileHandler(str(get_logs_dir() / 'app.log'), when='midnight', backupCount=log_retention_days, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
        log.addHandler(handler)
    return log

CAT_TROPICAL_CYCLONE = 'tropical_cyclone'
CAT_RAINSTORM = 'rainstorm'
CAT_HOT_WEATHER = 'hot_weather'
CAT_HEAT_STRESS_WORK = 'heat_stress_work'
TC_NONE, TC3, TC8NE, TC8SE, TC8SW, TC8NW, TC9, TC10 = 'TC_NONE','TC3','TC8NE','TC8SE','TC8SW','TC8NW','TC9','TC10'
RAIN_NONE, RAIN_YELLOW, RAIN_RED, RAIN_BLACK = 'RAIN_NONE','RAIN_YELLOW','RAIN_RED','RAIN_BLACK'
HOT_NONE, HOT_WEATHER = 'HOT_NONE','HOT_WEATHER'
HSWW_NONE, HSWW_AMBER, HSWW_RED, HSWW_BLACK = 'HSWW_NONE','HSWW_AMBER','HSWW_RED','HSWW_BLACK'
EVENT_ISSUE, EVENT_UPGRADE, EVENT_DOWNGRADE, EVENT_CANCEL, EVENT_UPDATE = 'ISSUE','UPGRADE','DOWNGRADE','CANCEL','UPDATE'
HIGH_PRIORITY_LEVELS = {TC8NE,TC8SE,TC8SW,TC8NW,TC9,TC10,RAIN_BLACK,HSWW_BLACK}
LEVEL_NAMES = {
    TC3:'三号强风信号',TC8NE:'八号东北烈风或暴风信号',TC8SE:'八号东南烈风或暴风信号',
    TC8SW:'八号西南烈风或暴风信号',TC8NW:'八号西北烈风或暴风信号',TC9:'九号烈风或暴风风力增强信号',TC10:'十号飓风信号',
    RAIN_YELLOW:'黄色暴雨警告',RAIN_RED:'红色暴雨警告',RAIN_BLACK:'黑色暴雨警告',HOT_WEATHER:'酷热天气警告',
    HSWW_AMBER:'黄色工作暑热警告',HSWW_RED:'红色工作暑热警告',HSWW_BLACK:'黑色工作暑热警告'}
LEVELS = tuple(LEVEL_NAMES)
CATEGORIES = {CAT_TROPICAL_CYCLONE: tuple(k for k in LEVELS if k.startswith('TC')),
              CAT_RAINSTORM: (RAIN_YELLOW, RAIN_RED, RAIN_BLACK),
              CAT_HOT_WEATHER: (HOT_WEATHER,),
              CAT_HEAT_STRESS_WORK: (HSWW_AMBER, HSWW_RED, HSWW_BLACK)}
RANKS = {TC_NONE:0,TC3:2,TC8NE:3,TC8SE:3,TC8SW:3,TC8NW:3,TC9:4,TC10:5,
         RAIN_NONE:0,RAIN_YELLOW:1,RAIN_RED:2,RAIN_BLACK:3,
         HOT_NONE:0,HOT_WEATHER:1,HSWW_NONE:0,HSWW_AMBER:1,HSWW_RED:2,HSWW_BLACK:3}
def level_display_name(category, level): return LEVEL_NAMES.get(level,level)

@dataclass
class NormalizedWarning:
    warning_category: str
    warning_level: str
    event_type: str = ''
    issue_time: str | None = None
    update_time: str | None = None
    expire_time: str | None = None
    detected_time: str | None = None
    source_message: str | None = None
    source_payload_hash: str = ''
    message_id: str = ''
    delivery_status: str = 'PENDING'
    previous_level: str | None = None
    raw_code: str | None = None
    def rank(self): return RANKS.get(self.warning_level,0)
    def is_active(self): return self.rank() > 0
    def display_name(self): return level_display_name(self.warning_category,self.warning_level)

@dataclass
class GroupConfig:
    group_id: int | None = None
    display_name: str = ''
    whatsapp_exact_name: str = ''
    enabled: bool = True
    is_24x7: bool = False
    subscriptions: dict[str,bool] = field(default_factory=lambda: {k:False for k in LEVELS})
    custom_work_hours_enabled: bool = False
    workdays: list[int] = field(default_factory=list)
    work_start_time: str = '09:00'
    work_end_time: str = '18:00'

@dataclass
class AppSettings:
    app_name: str = '天气警告通知'
    org_name: str = ''
    data_source_mode: str = 'hko_open_data_api'
    poll_interval_minutes: int = 5
    chrome_profile_dir: str = ''
    work_hours_enabled: bool = True
    workdays: list[int] = field(default_factory=lambda: [0,1,2,3,4])
    work_start_time: str = '09:00'
    work_end_time: str = '18:00'
    retry_failed_delivery_max_attempts: int = 3
    dry_run_mode: bool = True
    include_image: bool = True
    auto_start_monitor: bool = True
    start_minimized: bool = False

class SettingsManager:
    def __init__(self,path=None):
        self._path = Path(path or get_config_file_path())
        self._lock = threading.RLock()
        self.settings = AppSettings(chrome_profile_dir=str(get_chrome_profile_dir()))
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding='utf-8'))
                for key in self.settings.__dataclass_fields__:
                    if key in data: setattr(self.settings,key,data[key])
            except (OSError,ValueError,TypeError): get_logger('core').exception('设置读取失败，使用默认设置')
    def save(self):
        with self._lock:
            tmp = self._path.with_suffix('.tmp')
            tmp.write_text(json.dumps(asdict(self.settings),ensure_ascii=False,indent=2),encoding='utf-8')
            tmp.replace(self._path)
    def update(self,**kwargs):
        with self._lock:
            for k,v in kwargs.items():
                if k not in self.settings.__dataclass_fields__: raise ValueError(k)
                setattr(self.settings,k,v)
            self.save()

class Database:
    def __init__(self,path=None):
        self.path = Path(path or get_db_path())
        self._lock = threading.RLock()
        with self.conn() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS alert_groups (
                  group_id INTEGER PRIMARY KEY, display_name TEXT NOT NULL,
                  whatsapp_exact_name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                  is_24x7 INTEGER NOT NULL DEFAULT 0, subscriptions TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observed (
                  category TEXT PRIMARY KEY, level TEXT NOT NULL, issue_time TEXT,
                  update_time TEXT, detected_time TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries (
                  id INTEGER PRIMARY KEY, category TEXT NOT NULL, level TEXT NOT NULL,
                  issue_key TEXT NOT NULL, group_id INTEGER NOT NULL, status TEXT NOT NULL,
                  photo_status TEXT NOT NULL, fault_code TEXT NOT NULL DEFAULT '',
                  detail TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL,
                  attempted_at TEXT NOT NULL, attempts INTEGER NOT NULL,
                  UNIQUE(category,issue_key,group_id));
                CREATE INDEX IF NOT EXISTS ix_deliveries_recent ON deliveries(id DESC);
                CREATE TABLE IF NOT EXISTS ui_state (
                  key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS daily_text_sends (
                  category TEXT NOT NULL, issue_key TEXT NOT NULL, group_id INTEGER NOT NULL,
                  send_day TEXT NOT NULL, sent_at TEXT NOT NULL, status TEXT NOT NULL,
                  PRIMARY KEY(category,issue_key,group_id,send_day));
            ''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(alert_groups)')}
            for column, definition in (
                ('custom_work_hours_enabled', 'INTEGER NOT NULL DEFAULT 0'),
                ('workdays', "TEXT NOT NULL DEFAULT '[]'"),
                ('work_start_time', "TEXT NOT NULL DEFAULT '09:00'"),
                ('work_end_time', "TEXT NOT NULL DEFAULT '18:00'"),
            ):
                if column not in columns:
                    db.execute(f'ALTER TABLE alert_groups ADD COLUMN {column} {definition}')
            count = db.execute('SELECT COUNT(*) FROM alert_groups').fetchone()[0]
            old = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='groups'").fetchone()
            if count == 0 and old:
                for row in db.execute('SELECT * FROM groups').fetchall():
                    keys = set(row.keys())
                    sub = {k:False for k in LEVELS}
                    legacy = {'subscribe_tc':CATEGORIES[CAT_TROPICAL_CYCLONE],
                              'subscribe_hot_weather':CATEGORIES[CAT_HOT_WEATHER],
                              'subscribe_heat_stress_work':CATEGORIES[CAT_HEAT_STRESS_WORK],
                              'subscribe_rain_yellow':(RAIN_YELLOW,),
                              'subscribe_rain_red':(RAIN_RED,),
                              'subscribe_rain_black':(RAIN_BLACK,)}
                    for flag,levels in legacy.items():
                        if flag in keys and row[flag]:
                            for level in levels: sub[level] = True
                    db.execute('INSERT INTO alert_groups(group_id,display_name,whatsapp_exact_name,enabled,is_24x7,subscriptions) VALUES(?,?,?,?,?,?)',
                               (row['group_id'],row['display_name'],row['whatsapp_exact_name'],row['enabled'],row['is_24x7'],json.dumps(sub)))
            from datetime import datetime
            from zoneinfo import ZoneInfo
            hkt = ZoneInfo('Asia/Hong_Kong')
            for old_send in db.execute("SELECT category,issue_key,group_id,attempted_at,status FROM deliveries WHERE status IN ('SENT','PHOTO_FAILED','PHOTO_UNKNOWN')").fetchall():
                try:
                    sent_at = datetime.fromisoformat(old_send['attempted_at'])
                    if sent_at.tzinfo is None:
                        sent_at = sent_at.replace(tzinfo=hkt)
                    send_day = sent_at.astimezone(hkt).date().isoformat()
                    db.execute('''INSERT OR IGNORE INTO daily_text_sends(category,issue_key,group_id,send_day,sent_at,status)
                                  VALUES(?,?,?,?,?,?)''',
                               (old_send['category'],old_send['issue_key'],old_send['group_id'],send_day,
                                old_send['attempted_at'],old_send['status']))
                except (TypeError, ValueError):
                    get_logger('core').warning('旧投递时间无法迁移：group=%s issue=%s',old_send['group_id'],old_send['issue_key'])
    @contextmanager
    def conn(self):
        db = sqlite3.connect(str(self.path),timeout=20)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally: db.close()
    def list_groups(self):
        with self._lock,self.conn() as db:
            rows=db.execute('SELECT * FROM alert_groups ORDER BY group_id').fetchall()
        return [GroupConfig(r['group_id'],r['display_name'],r['whatsapp_exact_name'],bool(r['enabled']),bool(r['is_24x7']),json.loads(r['subscriptions']),bool(r['custom_work_hours_enabled']),json.loads(r['workdays']),r['work_start_time'],r['work_end_time']) for r in rows]
    def save_group(self,g):
        with self._lock,self.conn() as db:
            args=(g.display_name,g.whatsapp_exact_name,int(g.enabled),int(g.is_24x7),json.dumps(g.subscriptions),
                  int(g.custom_work_hours_enabled),json.dumps(g.workdays),g.work_start_time,g.work_end_time)
            if g.group_id is None:
                g.group_id=db.execute('''INSERT INTO alert_groups(display_name,whatsapp_exact_name,enabled,is_24x7,subscriptions,
                    custom_work_hours_enabled,workdays,work_start_time,work_end_time) VALUES(?,?,?,?,?,?,?,?,?)''',args).lastrowid
            else:
                db.execute('''UPDATE alert_groups SET display_name=?,whatsapp_exact_name=?,enabled=?,is_24x7=?,subscriptions=?,
                    custom_work_hours_enabled=?,workdays=?,work_start_time=?,work_end_time=? WHERE group_id=?''',args+(g.group_id,))
        return g.group_id
    def delete_group(self,group_id):
        with self._lock,self.conn() as db: db.execute('DELETE FROM alert_groups WHERE group_id=?',(group_id,))
    def get_observed(self,category):
        with self._lock,self.conn() as db: return db.execute('SELECT * FROM observed WHERE category=?',(category,)).fetchone()
    def save_observed(self,w,checked_at):
        with self._lock,self.conn() as db:
            db.execute('''INSERT INTO observed VALUES(?,?,?,?,?) ON CONFLICT(category) DO UPDATE SET
               level=excluded.level,issue_time=excluded.issue_time,update_time=excluded.update_time,detected_time=excluded.detected_time''',
               (w.warning_category,w.warning_level,w.issue_time,w.update_time,checked_at))
    def get_delivery(self,category,issue_key,group_id):
        with self._lock,self.conn() as db:
            return db.execute('SELECT * FROM deliveries WHERE category=? AND issue_key=? AND group_id=?',(category,issue_key,group_id)).fetchone()
    def save_delivery(self,w,issue_key,group_id,status,photo_status,code,detail,checked_at,attempted_at,attempts):
        with self._lock,self.conn() as db:
            db.execute('''INSERT INTO deliveries(category,level,issue_key,group_id,status,photo_status,fault_code,detail,checked_at,attempted_at,attempts)
               VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(category,issue_key,group_id) DO UPDATE SET
               level=excluded.level,status=excluded.status,photo_status=excluded.photo_status,fault_code=excluded.fault_code,
               detail=excluded.detail,checked_at=excluded.checked_at,attempted_at=excluded.attempted_at,attempts=excluded.attempts''',
               (w.warning_category,w.warning_level,issue_key,group_id,status,photo_status,str(code),str(detail),checked_at,attempted_at,attempts))
            if status in ('SENT','PHOTO_FAILED','PHOTO_UNKNOWN'):
                from datetime import datetime
                from zoneinfo import ZoneInfo
                sent = datetime.fromisoformat(attempted_at)
                hkt = ZoneInfo('Asia/Hong_Kong')
                if sent.tzinfo is None:
                    sent = sent.replace(tzinfo=hkt)
                day = sent.astimezone(hkt).date().isoformat()
                db.execute('''INSERT INTO daily_text_sends(category,issue_key,group_id,send_day,sent_at,status)
                              VALUES(?,?,?,?,?,?) ON CONFLICT(category,issue_key,group_id,send_day) DO UPDATE SET
                              sent_at=excluded.sent_at,status=excluded.status''',
                           (w.warning_category,issue_key,group_id,day,attempted_at,status))
    def last_text_send(self,category,issue_key,group_id):
        with self._lock,self.conn() as db:
            return db.execute('''SELECT * FROM daily_text_sends WHERE category=? AND issue_key=? AND group_id=?
                                 ORDER BY send_day DESC LIMIT 1''',(category,issue_key,group_id)).fetchone()
    def recent_deliveries(self,limit=100):
        with self._lock,self.conn() as db:
            row = db.execute("SELECT value FROM ui_state WHERE key='history_cleared_at'").fetchone()
            cleared_at = row['value'] if row else ''
            sql = ("SELECT d.*,g.display_name FROM deliveries d "
                   "LEFT JOIN alert_groups g ON d.group_id=g.group_id "
                   "WHERE d.attempted_at > ? ORDER BY d.id DESC LIMIT ?")
            return db.execute(sql,(cleared_at,limit)).fetchall()

    def clear_history_view(self):
        # 只清空"记录"页显示；不删除任何数据，因此不影响去重和跨日重发。
        from datetime import datetime
        from zoneinfo import ZoneInfo
        stamp = datetime.now(ZoneInfo('Asia/Hong_Kong')).isoformat(timespec='seconds')
        sql = ("INSERT INTO ui_state(key,value) VALUES('history_cleared_at',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        with self._lock,self.conn() as db:
            db.execute(sql,(stamp,))
        return stamp

    def purge_old_records(self,keep_days=3):
        # 真正删除旧记录。至少保留最近 2 天：跨日重发需要"昨天已发"的记录。
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        keep_days = max(2, int(keep_days))
        today = datetime.now(ZoneInfo('Asia/Hong_Kong')).date()
        cutoff = (today - timedelta(days=keep_days)).isoformat()
        with self._lock,self.conn() as db:
            deliveries = db.execute("DELETE FROM deliveries WHERE substr(attempted_at,1,10) < ?",(cutoff,)).rowcount
            daily = db.execute("DELETE FROM daily_text_sends WHERE send_day < ?",(cutoff,)).rowcount
        return deliveries, daily
