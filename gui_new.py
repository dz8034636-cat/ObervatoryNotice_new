"""Windows 优先的 PyQt5 主窗口；无系统托盘时仍可正常最小化到任务栏。"""
from __future__ import annotations
import os
import queue
import sys
import threading
from pathlib import Path
from datetime import datetime
from PyQt5.QtCore import Qt,QTimer,QUrl,pyqtSignal,QObject
from PyQt5.QtGui import QColor,QIcon,QPainter,QDesktopServices
from PyQt5.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,
    QLabel,QPushButton,QCheckBox,QLineEdit,QSpinBox,QTimeEdit,QTabWidget,QGroupBox,
    QDialog,QDialogButtonBox,QScrollArea,QTableWidget,QTableWidgetItem,QHeaderView,
    QTextEdit,QMessageBox,QSystemTrayIcon,QMenu,QComboBox)
from core_new import Database,GroupConfig,SettingsManager,LEVEL_NAMES,LEVELS,CATEGORIES,get_logs_dir,setup_logging
from engine_new import AlertEngine,AlertScheduler,worktime
from whatsapp_integration import WhatsAppBridgeClient

STYLE='''
QWidget {font-family: "Segoe UI", "Helvetica Neue", Arial; font-size: 13px; color: #1f2937; background:#f6f7f9;}
QMainWindow, QDialog {background:#f6f7f9;}
QTabWidget::pane {border:0;}
QTabBar::tab {background:transparent;padding:12px 20px;color:#6b7280;}
QTabBar::tab:selected {color:#2563eb;border-bottom:2px solid #2563eb;}
QGroupBox {border:1px solid #e5e7eb;border-radius:12px;margin-top:14px;padding:16px;background:white;font-weight:600;}
QGroupBox::title {subcontrol-origin:margin;left:14px;padding:0 4px;background:white;}
QPushButton {background:white;border:1px solid #d9dfe8;border-radius:8px;padding:8px 14px;}
QPushButton:hover {background:#eff6ff;}
QPushButton#primary {background:#2563eb;color:white;border:0;}
QLineEdit,QSpinBox,QTimeEdit,QComboBox {background:white;border:1px solid #d9dfe8;border-radius:7px;padding:6px;}
QTableWidget,QTextEdit {background:white;border:1px solid #e5e7eb;border-radius:9px;}
'''
import time

def icon():
    from PyQt5.QtGui import QPixmap
    pix=QPixmap(64,64); pix.fill(Qt.transparent)
    p=QPainter(pix);p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor('#2563eb'));p.setPen(Qt.NoPen);p.drawRoundedRect(4,4,56,56,15,15)
    p.setPen(QColor('white'));p.drawText(pix.rect(),Qt.AlignCenter,'⚠')
    p.end();return QIcon(pix)

def startup_path():
    if os.name!='nt': return None
    return Path(os.environ['APPDATA'])/'Microsoft'/'Windows'/'Start Menu'/'Programs'/'Startup'/'WeatherAlert.cmd'

def startup_enabled():
    p=startup_path();return bool(p and p.exists())

def set_startup(enabled):
    p=startup_path()
    if p is None: raise RuntimeError('开机登录自启动目前只支持 Windows')
    if not enabled:
        p.unlink(missing_ok=True);return
    exe=Path(sys.executable).resolve()
    if not getattr(sys,'frozen',False) and exe.name.lower()=='python.exe':
        pyw=exe.with_name('pythonw.exe')
        if pyw.exists(): exe=pyw
    if getattr(sys,'frozen',False): target=f'"{exe}" --minimized'
    else: target=f'"{exe}" "{Path(__file__).resolve()}" --minimized'
    p.write_text('@echo off\nstart "" '+target+'\n',encoding='utf-8')

class Signals(QObject):
    finished=pyqtSignal(str,object)

def row(title,widget):
    w=QWidget();line=QHBoxLayout(w);line.setContentsMargins(0,0,0,0)
    label=QLabel(title);label.setMinimumWidth(118);line.addWidget(label);line.addWidget(widget,1)
    return w

class GroupEditor(QDialog):
    def __init__(self,parent,group,known):
        super().__init__(parent)
        self.group=group or GroupConfig()
        self.setWindowTitle('群组订阅');self.resize(500,620)
        outer=QVBoxLayout(self)
        title=QLabel('选择群组与警告');title.setStyleSheet('font-size:20px;font-weight:600;')
        outer.addWidget(title)
        self.name=QComboBox();self.name.setEditable(True)
        self.name.addItems(sorted(set(known+[self.group.whatsapp_exact_name])-{''}))
        self.name.setCurrentText(self.group.whatsapp_exact_name)
        outer.addWidget(row('WhatsApp 精确名称',self.name))
        self.enabled=QCheckBox('启用此群组');self.enabled.setChecked(self.group.enabled);outer.addWidget(self.enabled)
        self.all_hours=QCheckBox('24/7（明确勾选才启用）');self.all_hours.setChecked(self.group.is_24x7);outer.addWidget(self.all_hours)
        scroll=QScrollArea();scroll.setWidgetResizable(True);body=QWidget();items=QVBoxLayout(body)
        labels={'tropical_cyclone':'热带气旋','rainstorm':'暴雨','heat_stress_work':'劳工处工作暑热','hot_weather':'HKO 酷热'}
        self.boxes={}
        for category in ('tropical_cyclone','rainstorm','heat_stress_work','hot_weather'):
            section=QGroupBox(labels[category]);layout=QGridLayout(section)
            for i,level in enumerate(CATEGORIES[category]):
                box=QCheckBox(LEVEL_NAMES[level]);box.setChecked(bool(self.group.subscriptions.get(level,False)))
                layout.addWidget(box,i//2,i%2);self.boxes[level]=box
            items.addWidget(section)
        items.addStretch();scroll.setWidget(body);outer.addWidget(scroll)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate);buttons.rejected.connect(self.reject);outer.addWidget(buttons)
    def validate(self):
        name=self.name.currentText().strip()
        if not name or not any(b.isChecked() for b in self.boxes.values()):
            QMessageBox.warning(self,'检查输入','请填写精确群组名称并至少勾选一种警告。');return
        self.group.display_name=self.group.whatsapp_exact_name=name
        self.group.enabled=self.enabled.isChecked()
        self.group.is_24x7=self.all_hours.isChecked()
        self.group.subscriptions={level:box.isChecked() for level,box in self.boxes.items()}
        self.accept()

class MainWindow(QMainWindow):
    def __init__(self,minimized=False):
        super().__init__();setup_logging()
        self.settings=SettingsManager();self.db=Database();self.events=queue.Queue()
        s=self.settings.settings
        self.sender=WhatsAppBridgeClient(chrome_profile_dir=Path(s.chrome_profile_dir),dry_run=s.dry_run_mode,headless=False)
        self.engine=AlertEngine(self.db,self.settings,self.sender)
        self.scheduler=AlertScheduler(self.engine,self.settings,self.events)
        self.signals=Signals();self.signals.finished.connect(self.worker_result)
        self.known_names=[];self.quitting=False
        self.setWindowTitle('天气警告通知');self.setWindowIcon(icon());self.resize(900,690)
        self.build();self.setup_tray()
        self.timer=QTimer(self);self.timer.timeout.connect(self.drain);self.timer.start(250)
        if s.auto_start_monitor: self.scheduler.start()
        if not minimized: self.show()
        elif not self.tray: self.showMinimized()
        self.started_monotonic = time.monotonic()
    def build(self):
        s = self.settings.settings
        tabs=QTabWidget();self.setCentralWidget(tabs)
        overview=QWidget();groups=QWidget();wa=QWidget();history=QWidget();settings=QWidget()
        for widget,name in ((overview,'概况'),(groups,'群组'),(wa,'WhatsApp'),(history,'记录'),(settings,'设置')):tabs.addTab(widget,name)
        o=QVBoxLayout(overview);o.setContentsMargins(24,24,24,24)
        self.status=QLabel('准备就绪');self.status.setStyleSheet('font-size:22px;font-weight:600;');o.addWidget(self.status)
        self.rule=QLabel();o.addWidget(self.rule)
        bar=QHBoxLayout();o.addLayout(bar)
        for title,callback in (('立即检查',self.check_now),('暂停 / 继续',self.toggle_pause),('隐藏到托盘',self.hide_to_tray)):
            b=QPushButton(title);b.clicked.connect(callback);bar.addWidget(b)
        bar.addStretch()
        self.latest=QTextEdit();self.latest.setReadOnly(True);o.addWidget(self.latest,1)
        g=QVBoxLayout(groups);g.setContentsMargins(24,24,24,24)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['群组','已勾选警告','接收时间','启用'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers);g.addWidget(self.table)
        gb=QHBoxLayout();g.addLayout(gb)
        for title,cb in (('从 WhatsApp 读取群组',self.load_groups),('新增',self.add_group),('编辑',self.edit_group),('删除',self.delete_group)):
            b=QPushButton(title);b.clicked.connect(cb);gb.addWidget(b)
        gb.addStretch();self.refresh_groups()
        v=QVBoxLayout(wa);v.setContentsMargins(24,24,24,24)
        v.addWidget(QLabel('使用现有 WhatsApp 桥接；先扫码登录，再开启正式发送。'))
        wb=QHBoxLayout();v.addLayout(wb)
        for title,action in (('打开登录窗口','open'),('检查登录','login'),('同步群组','groups')):
            b=QPushButton(title);b.clicked.connect(lambda checked=False,a=action:self.whatsapp_action(a));wb.addWidget(b)
        wb.addStretch();self.wa_info=QLabel('未检测');v.addWidget(self.wa_info);v.addStretch()
        h=QVBoxLayout(history);h.setContentsMargins(24,24,24,24)
        self.hist=QTableWidget(0,6);self.hist.setHorizontalHeaderLabels(['检查时间','群组','警告','结果','照片','故障代码'])
        self.hist.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);h.addWidget(self.hist)
        b=QPushButton('刷新记录');b.clicked.connect(self.refresh_history);h.addWidget(b)
        b=QPushButton('打开日志目录');b.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_logs_dir()))));h.addWidget(b)
        self.refresh_history()
        a=QVBoxLayout(settings);a.setContentsMargins(24,24,24,24)
        work=QGroupBox('工作时间');wl=QVBoxLayout(work);a.addWidget(work)
        self.days=[];dayrow=QHBoxLayout();wl.addLayout(dayrow)
        for i,label in enumerate('一二三四五六日'):
            cb=QCheckBox(label);cb.setChecked(i in s.workdays);self.days.append(cb);dayrow.addWidget(cb)
        ts=QHBoxLayout();wl.addLayout(ts)
        self.start=QTimeEdit();self.start.setDisplayFormat('HH:mm');self.start.setTime(datetime.strptime(s.work_start_time,'%H:%M').time())
        self.end=QTimeEdit();self.end.setDisplayFormat('HH:mm');self.end.setTime(datetime.strptime(s.work_end_time,'%H:%M').time())
        ts.addWidget(QLabel('开始'));ts.addWidget(self.start);ts.addWidget(QLabel('结束'));ts.addWidget(self.end)
        self.interval=QSpinBox();self.interval.setRange(1,60);self.interval.setValue(s.poll_interval_minutes)
        a.addWidget(row('查询间隔（分钟）',self.interval))
        self.retries=QSpinBox();self.retries.setRange(1,10);self.retries.setValue(s.retry_failed_delivery_max_attempts)
        a.addWidget(row('每次查询尝试上限',self.retries))
        self.dry=QCheckBox('演示模式（不真实发送）');self.dry.setChecked(s.dry_run_mode);a.addWidget(self.dry)
        self.auto=QCheckBox('启动程序时开始监控');self.auto.setChecked(s.auto_start_monitor);a.addWidget(self.auto)
        self.boot=QCheckBox('Windows 登录后自启动并隐藏到托盘');self.boot.setEnabled(os.name=='nt');self.boot.setChecked(startup_enabled());a.addWidget(self.boot)
        note=QLabel('照片默认必须生成；桥接若未明确回传照片结果，记录为“未知”。');note.setWordWrap(True);a.addWidget(note)
        save=QPushButton('保存设置');save.setObjectName('primary');save.clicked.connect(self.save_settings);a.addWidget(save);a.addStretch()
        self.refresh_rule()

    def refresh_rule(self):
        try: text='工作时间：正在查询' if worktime(self.settings.settings) else '非工作时间：暂停常规查询'
        except ValueError: text='工作时间配置无效'
        self.rule.setText(text+' · 仅明确勾选的 24/7 群组可在非工作时间查询')
    def setup_tray(self):
        self.tray=None
        if not QSystemTrayIcon.isSystemTrayAvailable():return
        self.tray=QSystemTrayIcon(icon(),self);menu=QMenu(self)
        menu.addAction('显示窗口',self.restore_window);menu.addAction('立即检查',self.check_now)
        menu.addSeparator();menu.addAction('退出',self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason:self.restore_window() if reason==QSystemTrayIcon.Trigger else None)
        self.tray.show()
    def hide_to_tray(self):
        if self.tray: self.hide();self.tray.showMessage('天气警告通知','监控在后台继续运行',QSystemTrayIcon.Information,2000)
        else: self.showMinimized()
    def closeEvent(self,event):
        if self.quitting: event.accept()
        else: event.ignore();self.hide_to_tray()
    def restore_window(self): self.showNormal();self.raise_();self.activateWindow()
    def quit_app(self):
        self.scheduler.stop()
        if self.scheduler._thread and self.scheduler._thread.is_alive():
            QMessageBox.warning(self,'稍后再试','本轮查询或发送仍在进行，请稍后退出，避免中断发送。');return
        try:self.sender.quit()
        except Exception: pass
        self.quitting=True
        QApplication.instance().quit()
    def check_now(self):self.scheduler.trigger_immediate_check()
    def toggle_pause(self):
        if self.scheduler._paused.is_set():self.scheduler.resume()
        else:self.scheduler.pause()
    def drain(self):
        self.refresh_rule()
        try:
            while True:
                item=self.events.get_nowait();kind=item['type'];payload=item['payload']
                if kind=='scheduler_status':self.status.setText({'RUNNING':'监控中','PAUSED':'已暂停','IDLE':'已停止'}.get(payload,payload))
                elif kind=='progress':self.latest.append(str(payload))
                elif kind=='check_error':self.latest.append('查询异常：'+str(payload))
                elif kind=='check_completed':
                    for result in payload:self.latest.append(str(result))
                    self.refresh_history()
        except queue.Empty:pass
    def refresh_groups(self):
        groups=self.db.list_groups();self.table.setRowCount(len(groups))
        for i,g in enumerate(groups):
            self.table.setVerticalHeaderItem(i,QTableWidgetItem(str(g.group_id)))
            for j,value in enumerate((g.display_name,str(sum(g.subscriptions.values())), '24/7' if g.is_24x7 else '工作时间','是' if g.enabled else '否')):
                self.table.setItem(i,j,QTableWidgetItem(value))
    def selected_group(self):
        r=self.table.currentRow()
        if r<0:return None
        gid=int(self.table.verticalHeaderItem(r).text())
        return next((g for g in self.db.list_groups() if g.group_id==gid),None)
    def edit_dialog(self,g):
        dialog=GroupEditor(self,g,self.known_names)
        if dialog.exec_()==QDialog.Accepted:self.db.save_group(dialog.group);self.refresh_groups()
    def add_group(self):self.edit_dialog(None)
    def edit_group(self):
        g=self.selected_group()
        if g:self.edit_dialog(g)
    def delete_group(self):
        g=self.selected_group()
        if g and QMessageBox.question(self,'删除群组',f'删除 {g.display_name} 的订阅？')==QMessageBox.Yes:
            self.db.delete_group(g.group_id);self.refresh_groups()
    def run_worker(self,kind,fn):
        def task():
            try:result=fn();self.signals.finished.emit(kind,result)
            except Exception as exc:self.signals.finished.emit(kind,exc)
        threading.Thread(target=task,daemon=True).start()
    def whatsapp_action(self,action):
        if not self.engine._busy.acquire(blocking=False):
            QMessageBox.information(self,'请稍后','正在发送或查询，请待本轮结束。');return
        def task():
            try:
                if action=='open':return self.sender.start_browser()
                if action=='login':return self.sender.is_logged_in(timeout=5)
                return self.sender.list_groups('')
            finally:self.engine._busy.release()
        self.run_worker(action,task)
    def load_groups(self):self.whatsapp_action('groups')
    def worker_result(self,kind,result):
        if isinstance(result,Exception):self.wa_info.setText(f'{kind} 失败：{type(result).__name__} · {result}');return
        if kind=='login':self.wa_info.setText('已登录' if result else '未登录／无法确认')
        elif kind=='groups':
            self.known_names=sorted({str(x.get('name','')).strip() for x in result if x.get('name')})
            self.wa_info.setText(f'已读取 {len(self.known_names)} 个群组；新增或编辑时可选择。')
        else:self.wa_info.setText('登录窗口已打开，请扫码。')
    def refresh_history(self):
        entries=self.db.recent_deliveries();self.hist.setRowCount(len(entries))
        for i,r in enumerate(entries):
            for j,value in enumerate((r['checked_at'],r['display_name'] or str(r['group_id']),r['level'],r['status'],r['photo_status'],r['fault_code'])):
                self.hist.setItem(i,j,QTableWidgetItem(str(value)))
    def save_settings(self):
        if self.start.time()>=self.end.time():
            QMessageBox.warning(self,'设置错误','结束时间应晚于开始时间');return
        if not any(x.isChecked() for x in self.days):
            QMessageBox.warning(self,'设置错误','至少选择一个工作日');return
        if not self.dry.isChecked() and self.settings.settings.dry_run_mode:
            if QMessageBox.question(self,'确认真实发送','关闭演示模式后，下次检查将向订阅群组真实发送 WhatsApp 消息。继续？')!=QMessageBox.Yes:
                self.dry.setChecked(True);return
        try:
            set_startup(self.boot.isChecked()) if os.name=='nt' else None
            self.settings.update(workdays=[i for i,c in enumerate(self.days) if c.isChecked()],
                work_start_time=self.start.time().toString('HH:mm'),work_end_time=self.end.time().toString('HH:mm'),
                poll_interval_minutes=self.interval.value(),retry_failed_delivery_max_attempts=self.retries.value(),
                dry_run_mode=self.dry.isChecked(),auto_start_monitor=self.auto.isChecked())
            self.sender.dry_run=self.dry.isChecked()
            QMessageBox.information(self,'已保存','设置已保存；自启动在下次 Windows 登录后生效。')
        except Exception as exc:QMessageBox.critical(self,'设置保存失败',str(exc))

def main():
    app=QApplication(sys.argv);app.setStyle('Fusion');app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    window=MainWindow(minimized='--minimized' in sys.argv)
    return app.exec_()
if __name__=='__main__':sys.exit(main())
