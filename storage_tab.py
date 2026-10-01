"""Storage tab: open / refresh / clean generated images, app.log and the Chromium cache."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

import storage_maintenance as sm
from core_new import get_app_data_dir, get_generated_images_dir, get_logs_dir


class StorageTab(QWidget):
    """is_bridge_alive: () -> bool.  stop_bridge: () -> bool (False = busy, try later)."""

    _result = pyqtSignal(str, str, object)   # kind, key, payload

    def __init__(self, chrome_profile_dir: Path,
                 is_bridge_alive: Callable[[], bool],
                 stop_bridge: Optional[Callable[[], bool]] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.profile_dir = Path(chrome_profile_dir)
        self.is_bridge_alive = is_bridge_alive
        self.stop_bridge = stop_bridge
        self._busy: set[str] = set()
        self._shown_once = False
        self._result.connect(self._on_result)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        title = QLabel('存储与缓存')
        title.setObjectName('pageTitle')
        root.addWidget(title)

        self.sections = {}
        specs = (
            ('images', '已生成图片（generated_images）',
             '每条警告生成的 PNG。清除后不影响以后发送；最近 10 分钟内生成的会保留。', '清除全部图片'),
            ('logs', '运行日志（app.log）',
             '清除后只保留最近三天（今天及前两天），更早的日志会被删除。', '只保留最近三天'),
            ('chrome', 'WhatsApp 浏览器缓存（chrome_profile）',
             '只清除缓存文件夹；登录信息（Local Storage、IndexedDB、Cookies 等）会保留，无需重新扫码。',
             '清除缓存（保留登录）'),
        )
        for key, name, hint, clean_caption in specs:
            box = QGroupBox(name)
            layout = QVBoxLayout(box)
            hint_label = QLabel(hint)
            hint_label.setWordWrap(True)
            stat_label = QLabel('—')
            stat_label.setWordWrap(True)
            row = QHBoxLayout()
            open_btn = QPushButton('打开文件夹')
            refresh = QPushButton('刷新')
            clean = QPushButton(clean_caption)
            clean.setObjectName('primary')
            row.addWidget(open_btn)
            row.addWidget(refresh)
            row.addWidget(clean)
            row.addStretch()
            layout.addWidget(hint_label)
            layout.addWidget(stat_label)
            layout.addLayout(row)
            root.addWidget(box)
            open_btn.clicked.connect(lambda _=False, k=key: self.open_folder(k))
            refresh.clicked.connect(lambda _=False, k=key: self.refresh(k))
            clean.clicked.connect(lambda _=False, k=key: self.clean(k))
            self.sections[key] = {'stat': stat_label, 'refresh': refresh, 'clean': clean}

        bottom = QHBoxLayout()
        all_btn = QPushButton('全部刷新')
        all_btn.clicked.connect(self.refresh_all)
        root_btn = QPushButton('打开数据根目录')
        root_btn.clicked.connect(lambda: self._open_path(get_app_data_dir()))
        self.message = QLabel('')
        self.message.setWordWrap(True)
        bottom.addWidget(root_btn)
        bottom.addWidget(all_btn)
        bottom.addWidget(self.message, 1)
        root.addLayout(bottom)
        root.addStretch()

    # ------------------------------------------------------------ helpers
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            self.refresh_all()

    def _set_busy(self, key: str, busy: bool) -> None:
        (self._busy.add if busy else self._busy.discard)(key)
        for name in ('refresh', 'clean'):
            self.sections[key][name].setEnabled(not busy)

    def _spawn(self, kind: str, key: str, func: Callable[[], object]) -> None:
        self._set_busy(key, True)

        def run() -> None:
            try:
                payload = func()
            except Exception as exc:       # reported in the UI
                payload = exc
            self._result.emit(kind, key, payload)

        threading.Thread(target=run, name=f'storage-{kind}-{key}', daemon=True).start()

    def _stats_func(self, key: str) -> Callable[[], object]:
        return {
            'images': lambda: sm.image_stats(get_generated_images_dir()),
            'logs': lambda: sm.log_stats(get_logs_dir()),
            'chrome': lambda: sm.chrome_stats(self.profile_dir),
        }[key]

    # ------------------------------------------------------------ open folder
    def _folder_for(self, key: str) -> Path:
        return {
            'images': get_generated_images_dir,
            'logs': get_logs_dir,
            'chrome': lambda: self.profile_dir,
        }[key]()

    def open_folder(self, key: str) -> None:
        self._open_path(self._folder_for(key))

    def _open_path(self, path: Path) -> None:
        path = Path(path)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.message.setText(f'无法创建文件夹：{exc}')
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        if not opened and hasattr(os, 'startfile'):
            try:
                os.startfile(str(path))          # Windows fallback
                opened = True
            except OSError:
                pass
        self.message.setText(f'已打开：{path}' if opened else f'无法打开文件夹：{path}')

    # ------------------------------------------------------------ actions
    def refresh_all(self) -> None:
        for key in self.sections:
            self.refresh(key)

    def refresh(self, key: str) -> None:
        if key in self._busy:
            return
        self.sections[key]['stat'].setText('正在统计…')
        self._spawn('stats', key, self._stats_func(key))

    def clean(self, key: str) -> None:
        if key in self._busy:
            return
        if key == 'images':
            if not self._confirm('清除已生成图片',
                                 '删除 generated_images 里的 PNG 图片（最近 10 分钟内生成的保留）？'):
                return
            self._spawn('clean', key, lambda: sm.clear_generated_images(get_generated_images_dir()))
        elif key == 'logs':
            if not self._confirm('清理日志',
                                 'app.log 将只保留最近三天（今天及前两天），更早的日志会被永久删除。继续？'):
                return
            self._spawn('clean', key, lambda: sm.purge_logs(get_logs_dir(), sm.LOG_KEEP_DAYS))
        elif key == 'chrome':
            self._clean_chrome()

    def _clean_chrome(self) -> None:
        alive = False
        try:
            alive = bool(self.is_bridge_alive())
        except Exception:
            pass
        text = ('只会删除浏览器缓存文件夹，登录信息会保留，不需要重新扫码。\n\n'
                '首次重新打开 WhatsApp 时加载会稍慢一些。')
        if alive:
            text = ('WhatsApp 桥接正在运行，清理缓存前必须先停止桥接并暂停监控。\n'
                    '清理完成后请回到“监控”页，点击“启动 / 扫码登录”恢复。\n\n') + text
        if not self._confirm('清除浏览器缓存', text + '\n\n继续？'):
            return
        if alive:
            if self.stop_bridge is None or not self.stop_bridge():
                QMessageBox.warning(self, '暂时无法清理', '当前正在执行一轮查询或发送，请稍后再试。')
                return
        self._spawn('clean', 'chrome', lambda: sm.clear_chrome_cache(self.profile_dir))

    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    # ------------------------------------------------------------ results
    def _on_result(self, kind: str, key: str, payload: object) -> None:
        self._set_busy(key, False)
        label = self.sections[key]['stat']
        if isinstance(payload, Exception):
            label.setText(f'出错：{type(payload).__name__} · {payload}')
            self.message.setText(f'{key} 操作失败：{payload}')
            return
        if kind == 'clean':
            freed = sm.fmt_size(payload.freed_bytes)
            tail = f'；{len(payload.errors)} 项失败（可能被占用）' if payload.errors else ''
            self.message.setText(f'已释放 {freed}，处理 {payload.removed_items} 项{tail}。')
            self.refresh(key)
            return
        label.setText(self._describe(key, payload))

    @staticmethod
    def _describe(key: str, st: 'sm.Stats') -> str:
        if key == 'images':
            return f'{st.count} 个文件 · {sm.fmt_size(st.size)}'
        if key == 'logs':
            extra = f' · 最早日期 {st.extra.replace("earliest ", "")}' if st.extra else ''
            return f'{st.count} 个文件 · {sm.fmt_size(st.size)}{extra}'
        folders, _, total = st.extra.partition('|')
        total_text = sm.fmt_size(int(total)) if total.isdigit() else '—'
        return (f'可清除缓存 {sm.fmt_size(st.size)}（{folders} 个缓存文件夹） · '
                f'chrome_profile 总大小 {total_text} · 登录数据：保留')
