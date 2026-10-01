"""QR widgets for the WhatsApp login pop-up.

QrLabel      : always draws the WHOLE code (quiet zone included), sharp, and
               re-renders when the window is resized.  If there is not enough
               room it says so instead of showing a cropped, unscannable code.
QrZoomDialog : the login pop-up - a resizable window with the code.
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QDialog, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from qr_sizing import plan_qr_size

TOO_SMALL_TEXT = '窗口太小，无法完整显示二维码。\n请把此窗口拖大。'


class QrLabel(QLabel):
    def __init__(self, text: str = '', parent: Optional[QWidget] = None, min_side: int = 260) -> None:
        super().__init__(text, parent)
        self._source: Optional[QPixmap] = None
        self._min_side = min_side
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumSize(min_side, min_side)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet('background: white; border: 1px solid #d6dce7; border-radius: 6px;')

    # Fixed hints: the label must not follow the pixmap size, or resizing loops.
    def sizeHint(self) -> QSize:
        return QSize(300, 300)

    def minimumSizeHint(self) -> QSize:
        return QSize(self._min_side, self._min_side)

    def set_source(self, pix: Optional[QPixmap]) -> None:
        self._source = pix if pix is not None and not pix.isNull() else None
        if self._source is None:
            super().setPixmap(QPixmap())
        self._render()

    def setText(self, text: str) -> None:          # status messages replace the code
        self._source = None
        super().setPixmap(QPixmap())
        super().setText(text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        if self._source is None:
            return
        dpr = self.devicePixelRatioF()
        avail = int((min(self.width(), self.height()) - 4) * dpr)
        plan = plan_qr_size(self._source.width(), avail)
        if plan.too_small:
            super().setPixmap(QPixmap())
            super().setText(TOO_SMALL_TEXT)
            return
        mode = Qt.FastTransformation if plan.crisp else Qt.SmoothTransformation
        scaled = self._source.scaled(plan.target, plan.target, Qt.KeepAspectRatio, mode)
        scaled.setDevicePixelRatio(dpr)
        super().setPixmap(scaled)


class QrZoomDialog(QDialog):
    """The WhatsApp login pop-up. Non-modal; updates itself when the QR refreshes."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('WhatsApp 扫码登录')
        self.resize(580, 700)
        self.setMinimumSize(380, 480)
        layout = QVBoxLayout(self)
        self.label = QrLabel('二维码准备中…', min_side=300)
        layout.addWidget(self.label, 1)
        tip = QLabel('手机 WhatsApp → 设置 → 已关联设备 → 关联设备，然后扫描上面的二维码。\n'
                     '二维码会定期刷新，此窗口会自动更新；登录成功后会自动关闭。\n'
                     '扫不上时：拖大窗口、调高屏幕亮度，手机离屏幕 20–30 厘米并保持稳定。')
        tip.setWordWrap(True)
        layout.addWidget(tip)
        close = QPushButton('关闭')
        close.clicked.connect(self.close)
        layout.addWidget(close)

    def set_source(self, pix: Optional[QPixmap]) -> None:
        self.label.set_source(pix)
