"""Pure-Python sizing rules for the WhatsApp login QR code (no Qt).

The bridge makes the QR with node-qrcode's defaults: 4 pixels per module and a
4-module quiet zone, so  width = (4 * version + 17 + 2 * 4) * 4.
Scaling by a whole number of pixels per module keeps every module a perfect
square, which scanners need.  Scaling by an arbitrary factor (the old
250x250 smooth scaling) blurs the edges and cuts the quiet zone when squeezed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

QUIET_MODULES = 4
MIN_PX_PER_MODULE = 2
FALLBACK_MIN_SIDE = 150


@dataclass
class QrPlan:
    target: int         # side length to draw, in device pixels
    crisp: bool         # True = whole pixels per module (nearest neighbour)
    too_small: bool     # True = not enough room to draw a scannable code
    cells: int = 0      # modules incl. quiet zone (0 if unknown)


def detect_cell_px(width: int) -> Optional[int]:
    """Pixels per module in the source image, or None if it cannot be inferred."""
    for px in (4, 8, 2, 3, 5, 6, 10, 1):
        if width % px:
            continue
        cells = width // px
        rest = cells - 2 * QUIET_MODULES - 17
        if rest > 0 and rest % 4 == 0 and 1 <= rest // 4 <= 40:
            return px
    return None


def plan_qr_size(src_width: int, avail: int) -> QrPlan:
    avail = max(0, int(avail))
    px = detect_cell_px(src_width)
    if px is None:                                   # unknown layout: smooth fit
        if avail < FALLBACK_MIN_SIDE:
            return QrPlan(0, False, True)
        return QrPlan(avail, False, False)
    cells = src_width // px
    k = avail // cells                               # whole pixels per module
    if k < MIN_PX_PER_MODULE:
        return QrPlan(0, True, True, cells)
    return QrPlan(cells * k, True, False, cells)
