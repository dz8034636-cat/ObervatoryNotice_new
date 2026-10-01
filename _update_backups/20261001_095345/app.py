
from __future__ import annotations

import base64
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QTime, QTimer, QUrl, QObject, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QDesktopServices
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSystemTrayIcon,
    QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QTimeEdit,
    QVBoxLayout, QWidget, QHeaderView,
)

from core_new import (
    CATEGORIES, LEVEL_NAMES, Database, GroupConfig, SettingsManager,
    get_logs_dir, setup_logging,
)
from engine_new import AlertEngine, AlertScheduler, worktime, group_worktime
from whatsapp_bridge_client import WhatsAppBridgeClient
from qr_widgets import QrLabel, QrZoomDialog
from login_progress import describe as describe_login
from storage_tab import StorageTab


def theme(scale: float) -> str:
    base = 12.0 * scale
    button = 11.5 * scale
    return f'''
    QWidget {{font-family: "Segoe UI", "Microsoft YaHei UI", Arial;
              font-size: {base:.1f}pt; color: #202733; background: #f6f7fa;}}
    QMainWindow, QDialog {{background: #f6f7fa;}}
    QTabWidget::pane {{border: 0;}}
    QTabBar::tab {{background: transparent; border: 0; padding: 12px 19px;
                   color: #667085;}}
    QTabBar::tab:selected {{color: #2466ce; border-bottom: 2px solid #2466ce;}}
    QGroupBox {{background: white; border: 1px solid #e4e8ef; border-radius: 12px;
                margin-top: 17px; padding: 16px; font-weight: 600;}}
    QGroupBox::title {{subcontrol-origin: margin; left: 14px;
                       background: white; padding: 0 5px;}}
    QLabel#pageTitle {{font-size: {20 * scale:.1f}pt; font-weight: 700;}}
    QLabel#statusLabel {{color: #2563eb; font-weight: 600;}}
    QPushButton {{background: white; border: 1px solid #d6dce7; border-radius: 8px;
                  padding: 8px 13px; font-size: {button:.1f}pt;}}
    QPushButton:hover {{background: #eef4ff;}}
    QPushButton#primary {{background: #2466ce; border-color: #2466ce; color: white;}}
    QPushButton#primary:hover {{background: #1e57b0;}}
    QLineEdit, QComboBox, QTimeEdit, QSpinBox {{background: white;
                border: 1px solid #d6dce7; border-radius: 7px; padding: 6px;}}
    QTableWidget, QTextEdit {{background: white; border: 1px solid #e4e8ef;
                              border-radius: 9px; selection-background-color: #dceafe;}}
    QScrollArea {{border: 0;}}
    '''


def app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor('#2466ce'))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setPen(QColor('white'))
    painter.drawText(pix.rect(), Qt.AlignCenter, '!')
    painter.end()
    return QIcon(pix)


def startup_file() -> Path | None:
    root = os.environ.get('APPDATA')
    if os.name != 'nt' or not root:
        return None
    return Path(root) / 'Microsoft/Windows/Start Menu/Programs/Startup/WeatherAlert.cmd'


def configure_startup(enabled: bool) -> None:
    file = startup_file()
    if file is None:
        if enabled:
            raise RuntimeError('本版本仅在 Windows 支持登录后自启动。')
        return
    if not enabled:
        file.unlink(missing_ok=True)
        return
    executable = Path(sys.executable).resolve()
    if getattr(sys, 'frozen', False):
        command = f'"{executable}" --minimized'
    else:
        pythonw = executable.with_name('pythonw.exe')
        if pythonw.exists():
            executable = pythonw
        entry = Path(sys.argv[0]).resolve()
        command = f'"{executable}" "{entry}" --minimized'
    file.write_text('@echo off\nstart "" ' + command + '\n', encoding='utf-8')


class WorkerSignals(QObject):
    completed = pyqtSignal(str, object)


def form_row(title: str, control: QWidget) -> QWidget:
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    caption = QLabel(title)
    caption.setMinimumWidth(140)
    layout.addWidget(caption)
    layout.addWidget(control, 1)
    return box


class GroupEditor(QDialog):
    def __init__(self, parent: QWidget, group: GroupConfig | None, names: list[str]):
        super().__init__(parent)
        self.group = group or GroupConfig()
        self.setWindowTitle('群组与警告订阅')
        self.resize(610, 690)
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel('请选择 WhatsApp 精确群组名称及要接收的警告。'))
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.addItems(sorted(set(names + [self.group.whatsapp_exact_name]) - {''}))
        self.name.setCurrentText(self.group.whatsapp_exact_name)
        outer.addWidget(form_row('群组精确名称', self.name))
        self.enabled = QCheckBox('启用群组')
        self.enabled.setChecked(self.group.enabled)
        self.round_clock = QCheckBox('24/7 接收（默认关闭）')
        self.round_clock.setChecked(self.group.is_24x7)
        outer.addWidget(self.enabled)
        outer.addWidget(self.round_clock)
        self.custom_hours = QCheckBox('自定义本群工作时间（24/7 时用于跨日提醒）')
        self.custom_hours.setChecked(self.group.custom_work_hours_enabled)
        outer.addWidget(self.custom_hours)
        hours = QGroupBox('工作日与时间 · 香港时间')
        schedule = QVBoxLayout(hours)
        self.group_days = []
        day_row = QHBoxLayout()
        default_days = self.group.workdays if self.group.custom_work_hours_enabled else parent.settings.settings.workdays
        for index, label in enumerate('一二三四五六日'):
            box = QCheckBox(label)
            box.setChecked(index in default_days)
            day_row.addWidget(box)
            self.group_days.append(box)
        schedule.addLayout(day_row)
        time_row = QHBoxLayout()
        default_start = self.group.work_start_time if self.group.custom_work_hours_enabled else parent.settings.settings.work_start_time
        default_end = self.group.work_end_time if self.group.custom_work_hours_enabled else parent.settings.settings.work_end_time
        self.group_start = QTimeEdit()
        self.group_start.setDisplayFormat('HH:mm')
        self.group_start.setTime(QTime.fromString(default_start, 'HH:mm'))
        self.group_end = QTimeEdit()
        self.group_end.setDisplayFormat('HH:mm')
        self.group_end.setTime(QTime.fromString(default_end, 'HH:mm'))
        time_row.addWidget(QLabel('开始'))
        time_row.addWidget(self.group_start)
        time_row.addWidget(QLabel('结束'))
        time_row.addWidget(self.group_end)
        schedule.addLayout(time_row)
        hours.setEnabled(self.custom_hours.isChecked())
        self.custom_hours.toggled.connect(hours.setEnabled)
        outer.addWidget(hours)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        grid = QVBoxLayout(body)
        names_by_category = {
            'tropical_cyclone': '热带气旋',
            'rainstorm': '暴雨',
            'heat_stress_work': '劳工处工作暑热',
            'hot_weather': '天文台酷热天气',
        }
        self.level_checks: dict[str, QCheckBox] = {}
        for category in names_by_category:
            section = QGroupBox(names_by_category[category])
            section_grid = QGridLayout(section)
            for index, level in enumerate(CATEGORIES[category]):
                check = QCheckBox(LEVEL_NAMES[level])
                check.setChecked(bool(self.group.subscriptions.get(level, False)))
                section_grid.addWidget(check, index // 2, index % 2)
                self.level_checks[level] = check
            grid.addWidget(section)
        grid.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        controls.accepted.connect(self.save)
        controls.rejected.connect(self.reject)
        outer.addWidget(controls)

    def save(self) -> None:
        name = self.name.currentText().strip()
        if not name or not any(c.isChecked() for c in self.level_checks.values()):
            QMessageBox.warning(self, '填写不完整', '请填写群组精确名称，并至少勾选一种警告。')
            return
        self.group.whatsapp_exact_name = name
        self.group.display_name = name
        self.group.enabled = self.enabled.isChecked()
        if self.custom_hours.isChecked():
            if not any(box.isChecked() for box in self.group_days):
                QMessageBox.warning(self, '工作时间', '请至少选择一个工作日。')
                return
            if self.group_start.time() >= self.group_end.time():
                QMessageBox.warning(self, '工作时间', '结束时间必须晚于开始时间。')
                return
        self.group.is_24x7 = self.round_clock.isChecked()
        self.group.custom_work_hours_enabled = self.custom_hours.isChecked()
        self.group.workdays = [i for i, box in enumerate(self.group_days) if box.isChecked()]
        self.group.work_start_time = self.group_start.time().toString('HH:mm')
        self.group.work_end_time = self.group_end.time().toString('HH:mm')
        self.group.subscriptions = {
            level: check.isChecked() for level, check in self.level_checks.items()
        }
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, minimized: bool = False):
        super().__init__()
        setup_logging()
        self.settings = SettingsManager()
        self.db = Database()
        self.events = queue.Queue()
        self.start_clock = time.monotonic()
        self.signals = WorkerSignals()
        self.signals.completed.connect(self.on_worker_complete)
        self.names: list[str] = []
        self._auth_busy = False
        self._loading_bridge = False
        self._quitting = False
        self._restart_required = False
        self._theme_scale = 0.0
        self._single_check_running = False
        self._qr_dismissed = False
        self._qr_pixmap = None
        self._qr_dialog = None
        self._login_started = None
        self._last_status = None
        self._status_misses = 0
        self._login_tick = 0
        self._last_check_text = ''
        s = self.settings.settings
        self.sender = WhatsAppBridgeClient(
            chrome_profile_dir=Path(s.chrome_profile_dir),
            dry_run=s.dry_run_mode,
            headless=True,
        )
        self.engine = AlertEngine(self.db, self.settings, self.sender)
        self.scheduler = AlertScheduler(self.engine, self.settings, self.events)
        self.setWindowTitle('天气警告通知')
        self.setWindowIcon(app_icon())
        self.resize(1060, 760)
        self.setMinimumSize(850, 700)
        self.build()
        self.setup_tray()
        self.queue_timer = QTimer(self)
        self.queue_timer.timeout.connect(self.drain_events)
        self.queue_timer.start(300)
        self.runtime_timer = QTimer(self)
        self.runtime_timer.timeout.connect(self.update_runtime)
        self.runtime_timer.start(1000)
        self.qr_timer = QTimer(self)
        self.qr_timer.timeout.connect(self.poll_auth)
        self.login_clock = QTimer(self)
        self.login_clock.timeout.connect(self.refresh_login_detail)
        self.update_runtime()
        if not minimized:
            self.show()
        elif self.tray is None:
            self.showMinimized()
        if s.auto_start_monitor:
            if s.dry_run_mode:
                self.scheduler.start()
            else:
                QTimer.singleShot(0, self.start_whatsapp)

    def build(self) -> None:
        s = self.settings.settings
        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        dashboard, groups, history, settings = QWidget(), QWidget(), QWidget(), QWidget()
        for page, name in ((dashboard, '监控'), (groups, '群组'),
                           (history, '记录'), (settings, '设置')):
            tabs.addTab(page, name)
        self.storage_tab = StorageTab(Path(s.chrome_profile_dir),
                                      self.sender.is_browser_alive,
                                      self.stop_bridge_for_maintenance)
        tabs.addTab(self.storage_tab, '存储')
        main = QVBoxLayout(dashboard)
        main.setContentsMargins(24, 20, 24, 20)
        header = QHBoxLayout()
        self.title = QLabel('天气警告监控')
        self.title.setObjectName('pageTitle')
        header.addWidget(self.title)
        header.addStretch()
        self.run_status = QLabel('未启动')
        self.run_status.setObjectName('statusLabel')
        header.addWidget(self.run_status)
        main.addLayout(header)
        self.work_status = QLabel('正在检查工作时间规则…')
        self.uptime = QLabel('本次运行：00:00:00')
        self.last_check = QLabel('上次查询：—')
        main.addWidget(self.work_status)
        line = QHBoxLayout()
        for widget in (self.uptime, self.last_check):
            line.addWidget(widget)
        line.addStretch()
        main.addLayout(line)
        controls = QHBoxLayout()
        for caption, handler in (
            ('开始监控', self.start_monitor),
            ('立即检查', self.check_now),
            ('暂停 / 继续', self.toggle_monitor),
            ('最小化运行', self.minimize_to_tray),
            ('退出程序', self.exit_app),
        ):
            button = QPushButton(caption)
            button.clicked.connect(handler)
            controls.addWidget(button)
        controls.addStretch()
        main.addLayout(controls)
        wa_card = QGroupBox('WhatsApp 登录 · 主页面')
        wa_layout = QVBoxLayout(wa_card)
        top = QHBoxLayout()
        self.login_status = QLabel('未检测登录')
        top.addWidget(self.login_status, 1)
        self.login_button = QPushButton('启动 / 扫码登录')
        self.login_button.setObjectName('primary')
        self.login_button.clicked.connect(self.start_whatsapp)
        top.addWidget(self.login_button)
        check_button = QPushButton('检查登录')
        check_button.clicked.connect(self.poll_auth)
        top.addWidget(check_button)
        qr_button = QPushButton('显示二维码')
        qr_button.clicked.connect(self.reopen_qr_popup)
        top.addWidget(qr_button)
        wa_layout.addLayout(top)
        self.login_detail = QLabel('')
        self.login_detail.setWordWrap(True)
        self.login_detail.setStyleSheet('color: #667085;')
        self.login_detail.hide()
        wa_layout.addWidget(self.login_detail)
        self.qr_box = QWidget()
        qr_line = QHBoxLayout(self.qr_box)
        self.qr_picture = QrLabel('等待二维码…')
        qr_line.addWidget(self.qr_picture, 1)
        qr_description = QVBoxLayout()
        note = QLabel('请用 WhatsApp 手机端扫码。\n登录成功后二维码会自动隐藏；\n桥接继续在后台运行。')
        note.setWordWrap(True)
        qr_description.addWidget(note)
        zoom_button = QPushButton('放大二维码（扫不上时用）')
        zoom_button.clicked.connect(self.open_qr_zoom)
        qr_description.addWidget(zoom_button)
        hide_button = QPushButton('隐藏二维码（不退出登录）')
        hide_button.clicked.connect(self.hide_qr)
        qr_description.addWidget(hide_button)
        qr_description.addStretch()
        qr_line.addLayout(qr_description, 1)
        self.qr_box.hide()   # the QR code is shown in a pop-up window instead (show_qr_popup)
        main.addWidget(wa_card)
        recent_row = QHBoxLayout()
        recent_row.addWidget(QLabel('最近检查与发送结果'))
        recent_row.addStretch()
        clear_recent_button = QPushButton('清除运行信息')
        clear_recent_button.clicked.connect(lambda: self.recent.clear())
        recent_row.addWidget(clear_recent_button)
        main.addLayout(recent_row)
        self.recent = QTextEdit()
        self.recent.setReadOnly(True)
        main.addWidget(self.recent, 1)

        gl = QVBoxLayout(groups)
        gl.setContentsMargins(24, 20, 24, 20)
        self.group_table = QTableWidget(0, 4)
        self.group_table.setHorizontalHeaderLabels(['群组名称', '警告种类数', '接收时间', '启用'])
        self.group_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.group_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.group_table.setEditTriggers(QTableWidget.NoEditTriggers)
        gl.addWidget(self.group_table)
        group_actions = QHBoxLayout()
        for caption, handler in (
            ('读取 WhatsApp 群组', self.load_groups),
            ('新增', self.add_group),
            ('编辑', self.edit_group),
            ('删除', self.delete_group),
        ):
            button = QPushButton(caption)
            button.clicked.connect(handler)
            group_actions.addWidget(button)
        group_actions.addStretch()
        gl.addLayout(group_actions)
        self.group_hint = QLabel('群组发送使用 WhatsApp 精确名称；请先登录再读取群组。')
        gl.addWidget(self.group_hint)
        self.refresh_groups()

        hl = QVBoxLayout(history)
        hl.setContentsMargins(24, 20, 24, 20)
        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(
            ['查询时间', '群组', '警告', '结果', '照片', '故障代码']
        )
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        hl.addWidget(self.history_table)
        hb = QHBoxLayout()
        reload_button = QPushButton('刷新记录')
        reload_button.clicked.connect(self.refresh_history)
        hb.addWidget(reload_button)
        log_button = QPushButton('打开日志目录')
        log_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_logs_dir())))
        )
        hb.addWidget(log_button)
        clear_view_button = QPushButton('清除记录显示')
        clear_view_button.clicked.connect(self.clear_history_view)
        hb.addWidget(clear_view_button)
        purge_button = QPushButton('删除旧记录（保留最近3天）')
        purge_button.clicked.connect(self.purge_old_history)
        hb.addWidget(purge_button)
        hb.addStretch()
        hl.addLayout(hb)
        self.refresh_history()

        sl = QVBoxLayout(settings)
        sl.setContentsMargins(24, 20, 24, 20)
        hours = QGroupBox('工作时间（香港时间）')
        hours_layout = QVBoxLayout(hours)
        day_row = QHBoxLayout()
        self.days: list[QCheckBox] = []
        for index, label in enumerate('一二三四五六日'):
            check = QCheckBox(label)
            check.setChecked(index in s.workdays)
            day_row.addWidget(check)
            self.days.append(check)
        hours_layout.addLayout(day_row)
        times = QHBoxLayout()
        self.start_time = QTimeEdit()
        self.start_time.setDisplayFormat('HH:mm')
        self.start_time.setTime(QTime.fromString(s.work_start_time, 'HH:mm'))
        self.end_time = QTimeEdit()
        self.end_time.setDisplayFormat('HH:mm')
        self.end_time.setTime(QTime.fromString(s.work_end_time, 'HH:mm'))
        times.addWidget(QLabel('开始'))
        times.addWidget(self.start_time)
        times.addWidget(QLabel('结束'))
        times.addWidget(self.end_time)
        hours_layout.addLayout(times)
        sl.addWidget(hours)
        self.interval = QSpinBox()
        self.interval.setRange(1, 60)
        self.interval.setValue(s.poll_interval_minutes)
        sl.addWidget(form_row('查询间隔（分钟）', self.interval))
        self.retry_count = QSpinBox()
        self.retry_count.setRange(1, 10)
        self.retry_count.setValue(s.retry_failed_delivery_max_attempts)
        sl.addWidget(form_row('每轮尝试上限', self.retry_count))
        self.demo_mode = QCheckBox('演示模式（不会真实发送）')
        self.demo_mode.setChecked(s.dry_run_mode)
        sl.addWidget(self.demo_mode)
        self.auto_monitor = QCheckBox('启动程序时开启监控')
        self.auto_monitor.setChecked(s.auto_start_monitor)
        sl.addWidget(self.auto_monitor)
        self.auto_start = QCheckBox('Windows 登录后自启动并最小化')
        self.auto_start.setEnabled(startup_file() is not None)
        file = startup_file()
        self.auto_start.setChecked(bool(file and file.exists()))
        sl.addWidget(self.auto_start)
        sl.addWidget(QLabel('仅明确设为 24/7 的群组可在非工作时间查询与接收。'))
        save_button = QPushButton('保存设置')
        save_button.setObjectName('primary')
        save_button.clicked.connect(self.save_settings)
        sl.addWidget(save_button)
        sl.addStretch()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = max(850, self.width())
        scale = 0.98 if width < 1000 else 1.08 if width >= 1300 else 1.0
        if scale != self._theme_scale:
            self._theme_scale = scale
            self.setStyleSheet(theme(scale))

    def setup_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu(self)
        menu.addAction('显示主窗口', self.restore_window)
        menu.addAction('立即检查', self.check_now)
        menu.addSeparator()
        menu.addAction('退出程序', self.exit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.restore_window()
            if reason == QSystemTrayIcon.Trigger else None
        )
        self.tray.show()

    def restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def minimize_to_tray(self) -> None:
        if self.tray is None:
            self.showMinimized()
        else:
            self.hide()

    def closeEvent(self, event) -> None:
        if self._quitting:
            event.accept()
        else:
            event.ignore()
            self.minimize_to_tray()

    def stop_bridge_for_maintenance(self) -> bool:
        """Used by the Storage tab before clearing the Chromium cache."""
        if not self.engine._busy.acquire(blocking=False):
            return False
        try:
            self.scheduler.stop()
            self.qr_timer.stop()
            self.login_clock.stop()
            self.sender.quit()
        finally:
            self.engine._busy.release()
        self.login_detail.hide()
        self.login_status.setStyleSheet('')
        self.login_status.setText('桥接已停止（清理缓存）；需要时点击“启动 / 扫码登录”。')
        return True

    def exit_app(self) -> None:
        self.scheduler.stop()
        thread = self.scheduler._thread
        if thread is not None and thread.is_alive():
            QMessageBox.warning(self, '仍在发送', '当前检查尚未完成，请稍后再退出。')
            return
        if self.engine._busy.locked():
            QMessageBox.warning(self, '仍在检查', '当前检查或发送尚未完成，请稍后再退出。')
            return
        self.qr_timer.stop()
        try:
            self.sender.quit()
        except Exception as exc:
            self.recent.append(f'关闭桥接时发生异常：{exc}')
        self._quitting = True
        QApplication.instance().quit()

    def run_worker(self, kind: str, operation) -> None:
        def work() -> None:
            try:
                result = operation()
                self.signals.completed.emit(kind, result)
            except Exception as exc:
                self.signals.completed.emit(kind, exc)
        threading.Thread(target=work, name=f'gui-{kind}', daemon=True).start()

    def start_whatsapp(self) -> None:
        if self._restart_required:
            self.login_status.setText('设置已变更，请重启应用。')
            return
        if self.settings.settings.dry_run_mode:
            self.login_status.setText('演示模式不会启动真实桥接；请关闭演示模式并重启。')
            return
        if self._loading_bridge:
            return
        if not self.engine._busy.acquire(blocking=False):
            self.login_status.setText('正在执行一轮查询，请稍后登录。')
            return
        self._loading_bridge = True
        self._qr_dismissed = False
        self.login_button.setEnabled(False)
        self.login_status.setText('正在启动 WhatsApp 桥接…')
        self._login_started = time.monotonic()
        self._last_status = None
        self._status_misses = 0
        self.login_clock.start(1000)
        self.refresh_login_detail()

        def boot():
            try:
                self.sender.start_browser()
                return True
            finally:
                self.engine._busy.release()
        self.run_worker('bridge_start', boot)

    def poll_auth(self) -> None:
        if self._auth_busy or self._loading_bridge or self._restart_required:
            return
        if self.settings.settings.dry_run_mode:
            self.login_status.setText('演示模式；没有真实 WhatsApp 登录。')
            return
        if not self.sender.is_browser_alive():
            self.login_status.setText('桥接未运行；请点击“启动 / 扫码登录”。')
            self.login_status.setStyleSheet('')
            self.login_detail.hide()
            self.login_clock.stop()
            self.qr_timer.stop()
            return
        self._auth_busy = True

        def fetch():
            status = self.sender.get_status_snapshot(timeout=2)
            logged_in = bool(status) and str(status.get('status', '')).upper() == 'READY'
            qr = None
            if status and not logged_in and status.get('has_qr'):
                qr = self.sender.get_login_qr_data_url()
            return logged_in, qr, status
        self.run_worker('auth', fetch)

    def on_worker_complete(self, kind: str, result: object) -> None:
        if kind == 'bridge_start':
            self._loading_bridge = False
            self.login_button.setEnabled(True)
            if isinstance(result, Exception):
                self.login_clock.stop()
                self.login_detail.hide()
                self.login_status.setText(f'桥接启动失败：{type(result).__name__} · {result}')
                return
            self.login_status.setText('桥接已启动，正在读取登录状态…')
            # the QR code appears in a pop-up window (see show_qr_popup)
            self.qr_timer.start(3000)
            self.poll_auth()
            return
        if kind == 'auth':
            self._auth_busy = False
            if isinstance(result, Exception):
                self.login_status.setText(f'登录检查失败：{type(result).__name__} · {result}')
                return
            logged_in, data_url, status = result
            self._last_status = status
            self._status_misses = 0 if status else self._status_misses + 1
            self._last_check_text = datetime.now().strftime('%H:%M:%S')
            self.refresh_login_detail()
            if logged_in:
                self.login_clock.stop()
                self.login_detail.hide()
                self.login_status.setText('WhatsApp 已登录 · 桥接在后台运行')
                self.login_status.setStyleSheet('color: #15803d; font-weight: 600;')
                self.qr_timer.stop()
                self.qr_box.hide()
                if self._qr_dialog is not None:
                    self._qr_dialog.close()
                if self.settings.settings.auto_start_monitor and not self._restart_required:
                    self.scheduler.start()
                return
            if not data_url:
                self.qr_picture.setText('二维码准备中，请稍候…')
                return
            try:
                if not data_url.startswith('data:image/') or ',' not in data_url:
                    raise ValueError('桥接返回非图片 data URL')
                image_bytes = base64.b64decode(data_url.split(',', 1)[1], validate=True)
                pix = QPixmap()
                if not pix.loadFromData(image_bytes):
                    raise ValueError('无法解码二维码图片')
                self._qr_pixmap = pix
                self.qr_picture.set_source(pix)
                if self._qr_dialog is not None and self._qr_dialog.isVisible():
                    self._qr_dialog.set_source(pix)
                self.show_qr_popup(pix)
            except Exception as exc:
                self.qr_picture.setText(f'二维码显示失败：{exc}')
            return
        if kind == 'groups':
            if isinstance(result, Exception):
                self.group_hint.setText(f'读取群组失败：{type(result).__name__} · {result}')
                return
            self.names = sorted({
                str(group.get('name', '')).strip()
                for group in result if group.get('name')
            })
            self.group_hint.setText(f'已读取 {len(self.names)} 个群组，可在新增／编辑窗口选择。')

    def refresh_login_detail(self) -> None:
        if self._login_started is None:
            return
        self._login_tick += 1
        view = describe_login(
            self._last_status,
            elapsed=time.monotonic() - self._login_started,
            misses=self._status_misses,
            tick=self._login_tick,
            checked_at=self._last_check_text,
            bridge_starting=self._loading_bridge,
        )
        color = {'ok': '#15803d', 'info': '#2563eb', 'warn': '#b45309', 'error': '#b91c1c'}[view.level]
        self.login_status.setText(view.headline)
        self.login_status.setStyleSheet(f'color: {color}; font-weight: 600;')
        self.login_detail.setText(view.detail)
        self.login_detail.setVisible(bool(view.detail))

    def open_qr_zoom(self) -> None:
        if self._qr_pixmap is None:
            QMessageBox.information(self, '二维码', '二维码还没有生成，请稍候。')
            return
        if self._qr_dialog is None:
            self._qr_dialog = QrZoomDialog(self)
        self._qr_dialog.set_source(self._qr_pixmap)
        self._qr_dialog.show()
        self._qr_dialog.raise_()
        self._qr_dialog.activateWindow()

    def show_qr_popup(self, pix) -> None:
        """Show / refresh the login QR code in a pop-up window."""
        self._qr_pixmap = pix
        if self._qr_dialog is None:
            self._qr_dialog = QrZoomDialog(self)
            self._qr_dialog.finished.connect(self._on_qr_popup_closed)
        self._qr_dialog.set_source(pix)
        if self._qr_dialog.isVisible() or self._qr_dismissed:
            return
        self._qr_dialog.show()
        self._qr_dialog.raise_()
        self._qr_dialog.activateWindow()

    def _on_qr_popup_closed(self, _result: int) -> None:
        self._qr_dismissed = True

    def reopen_qr_popup(self) -> None:
        if self._qr_pixmap is None:
            QMessageBox.information(self, '二维码', '二维码还没有生成，请稍候（可看上方的登录进度）。')
            return
        self._qr_dismissed = False
        self.show_qr_popup(self._qr_pixmap)
        self._qr_dialog.raise_()
        self._qr_dialog.activateWindow()

    def hide_qr(self) -> None:
        self.login_clock.stop()
        self.qr_box.hide()
        self.qr_timer.stop()
        self.login_status.setText('二维码已隐藏；桥接仍在运行。点击检查登录可刷新状态。')

    def start_monitor(self) -> None:
        if self._restart_required:
            QMessageBox.warning(self, '需要重启', '发送模式已修改，请重启应用后监控。')
            return
        if not self.settings.settings.dry_run_mode and not self.sender.is_logged_in(timeout=2):
            self.login_status.setText('请先在主页面完成 WhatsApp 登录。')
            self.start_whatsapp()
            return
        self.scheduler.start()

    def check_now(self) -> None:
        if self._restart_required:
            QMessageBox.warning(self, '需要重启', '请重启应用后检查。')
            return
        if not self.settings.settings.dry_run_mode and not self.sender.is_logged_in(timeout=2):
            self.login_status.setText('请先完成 WhatsApp 登录。')
            return
        self.run_single_check()

    def run_single_check(self) -> None:
        """One poll cycle on its own thread. Does NOT start, resume or reschedule monitoring."""
        if self._single_check_running:
            self.recent.append('上一次手动检查仍在进行，请稍候。')
            return
        self._single_check_running = True
        emit = self.scheduler._emit
        emit('progress', '手动检查一次（不会启动或恢复监控）')

        def work() -> None:
            try:
                emit('check_started', None)
                summary = self.engine.run_one_poll_cycle(lambda text: emit('progress', text))
                emit('check_completed', summary)
            except Exception as exc:
                import logging
                logging.getLogger('hko_alert').exception('手动检查失败')
                emit('check_error', str(exc))
            finally:
                self._single_check_running = False

        threading.Thread(target=work, name='manual-check', daemon=True).start()

    def toggle_monitor(self) -> None:
        if self.scheduler._paused.is_set():
            self.scheduler.resume()
        else:
            self.scheduler.pause()

    def update_runtime(self) -> None:
        seconds = max(0, int(time.monotonic() - self.start_clock))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.uptime.setText(f'本次运行：{hours:02d}:{minutes:02d}:{seconds:02d}')
        try:
            configured = [g for g in self.db.list_groups() if g.enabled and any(g.subscriptions.values())]
            if any(group_worktime(g, self.settings.settings) for g in configured):
                state = '有群组处于工作时间 · 可查询'
            elif any(g.is_24x7 for g in configured):
                state = '工作时间外 · 仅 24/7 群组查询'
            else:
                state = '所有群组均在工作时间外 · 暂停 API 查询'
        except ValueError:
            state = '工作时间配置无效'
        self.work_status.setText(state)

    def drain_events(self) -> None:
        try:
            while True:
                item = self.events.get_nowait()
                kind, data = item['type'], item['payload']
                if kind == 'scheduler_status':
                    self.run_status.setText({
                        'RUNNING': '监控中', 'PAUSED': '已暂停', 'IDLE': '未启动'
                    }.get(data, str(data)))
                elif kind == 'progress':
                    self.recent.append(str(data))
                elif kind == 'check_error':
                    self.recent.append(f'查询异常：{data}')
                elif kind == 'check_completed':
                    completed_at = datetime.fromisoformat(item['time'])
                    display_time = completed_at.strftime('%Y-%m-%d %H:%M:%S')

                    for result in data:
                        self.recent.append(str(result))

                    self.recent.append(f'本轮查询完成：{display_time}')
                    self.last_check.setText(f'上次查询：{display_time}')
                    self.refresh_history()
        except queue.Empty:
            pass

    def refresh_groups(self) -> None:
        groups = self.db.list_groups()
        self.group_table.setRowCount(len(groups))
        for index, group in enumerate(groups):
            self.group_table.setVerticalHeaderItem(index, QTableWidgetItem(str(group.group_id)))
            cells = (
                group.display_name,
                str(sum(bool(value) for value in group.subscriptions.values())),
                (('24/7 · 跨日提醒 ' if group.is_24x7 else '自定义工作时间 ')
                 + group.work_start_time + '–' + group.work_end_time
                 if group.custom_work_hours_enabled else
                 ('24/7 · 默认时段跨日提醒' if group.is_24x7 else '跟随默认工作时间')),
                '是' if group.enabled else '否',
            )
            for column, value in enumerate(cells):
                self.group_table.setItem(index, column, QTableWidgetItem(value))

    def selected_group(self) -> GroupConfig | None:
        index = self.group_table.currentRow()
        if index < 0:
            return None
        group_id = int(self.group_table.verticalHeaderItem(index).text())
        return next((g for g in self.db.list_groups() if g.group_id == group_id), None)

    def edit_group_dialog(self, group: GroupConfig | None) -> None:
        dialog = GroupEditor(self, group, self.names)
        if dialog.exec_() == QDialog.Accepted:
            self.db.save_group(dialog.group)
            self.refresh_groups()

    def add_group(self) -> None:
        self.edit_group_dialog(None)

    def edit_group(self) -> None:
        group = self.selected_group()
        if group is not None:
            self.edit_group_dialog(group)

    def delete_group(self) -> None:
        group = self.selected_group()
        if group is None:
            return
        if QMessageBox.question(self, '确认删除', f'删除群组“{group.display_name}”的订阅？') == QMessageBox.Yes:
            self.db.delete_group(group.group_id)
            self.refresh_groups()

    def load_groups(self) -> None:
        if self.settings.settings.dry_run_mode:
            self.group_hint.setText('当前为演示模式；读取到的将是模拟群组。')
        if not self.engine._busy.acquire(blocking=False):
            self.group_hint.setText('查询正在进行，请稍后重试。')
            return

        def fetch():
            try:
                return self.sender.list_groups('')
            finally:
                self.engine._busy.release()
        self.group_hint.setText('正在从 WhatsApp 读取群组…')
        self.run_worker('groups', fetch)

    def clear_history_view(self) -> None:
        answer = QMessageBox.question(
            self, '清除记录显示',
            '只清空"记录"页的显示，不会删除数据库记录，'
            '也不影响去重和跨日重发。是否继续？')
        if answer == QMessageBox.Yes:
            self.db.clear_history_view()
            self.refresh_history()

    def purge_old_history(self) -> None:
        answer = QMessageBox.question(
            self, '删除旧记录',
            '将永久删除 3 天前的投递记录，保留最近 3 天'
            '（跨日重发需要昨天的记录）。此操作不可恢复，是否继续？')
        if answer != QMessageBox.Yes:
            return
        deliveries, daily = self.db.purge_old_records(keep_days=3)
        self.refresh_history()
        QMessageBox.information(
            self, '已删除', f'已删除投递记录 {deliveries} 条、每日发送记录 {daily} 条。')

    def refresh_history(self) -> None:
        records = self.db.recent_deliveries()
        self.history_table.setRowCount(len(records))
        for index, record in enumerate(records):
            cells = (
                record['checked_at'],
                record['display_name'] or str(record['group_id']),
                record['level'], record['status'],
                record['photo_status'], record['fault_code'],
            )
            for column, value in enumerate(cells):
                self.history_table.setItem(index, column, QTableWidgetItem(str(value)))

    def save_settings(self) -> None:
        if self.start_time.time() >= self.end_time.time():
            QMessageBox.warning(self, '工作时间', '结束时间应晚于开始时间。')
            return
        days = [i for i, check in enumerate(self.days) if check.isChecked()]
        if not days:
            QMessageBox.warning(self, '工作时间', '请至少选择一个工作日。')
            return
        old_demo = self.settings.settings.dry_run_mode
        new_demo = self.demo_mode.isChecked()
        if old_demo and not new_demo:
            answer = QMessageBox.question(
                self, '确认真实发送',
                '关闭演示模式后，重启应用并登录 WhatsApp，将开始真实发送。继续？'
            )
            if answer != QMessageBox.Yes:
                self.demo_mode.setChecked(True)
                return
        try:
            configure_startup(self.auto_start.isChecked())
            self.settings.update(
                work_hours_enabled=True,
                workdays=days,
                work_start_time=self.start_time.time().toString('HH:mm'),
                work_end_time=self.end_time.time().toString('HH:mm'),
                poll_interval_minutes=self.interval.value(),
                retry_failed_delivery_max_attempts=self.retry_count.value(),
                dry_run_mode=new_demo,
                auto_start_monitor=self.auto_monitor.isChecked(),
            )
        except Exception as exc:
            QMessageBox.critical(self, '设置保存失败', str(exc))
            return
        if old_demo != new_demo:
            self._restart_required = True
            self.scheduler.pause()
            QMessageBox.information(self, '请重启应用', '发送模式已保存。为避免新旧桥接状态混用，请退出并重新打开应用。')
        else:
            QMessageBox.information(self, '已保存', '设置已保存。')


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setQuitOnLastWindowClosed(False)
    font = QFont('Segoe UI')
    font.setPointSizeF(11.0)
    app.setFont(font)
    window = MainWindow(minimized='--minimized' in sys.argv)
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
