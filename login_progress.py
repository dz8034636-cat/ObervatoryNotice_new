"""Human-readable WhatsApp login progress (no Qt, easy to test).

Input is the JSON returned by the bridge's GET /status:
  status, detail, last_client_state, has_qr, updated_at (UTC ISO), ...
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

SPINNER = '◐◓◑◒'

WARN_STARTING = 60        # still loading WhatsApp Web
STUCK_STARTING = 180
WARN_NO_QR = 30           # QR_PENDING but no QR image yet
WARN_SYNC = 120           # AUTHENTICATED / CONNECTED, waiting for READY
STUCK_SYNC = 300

TIPS_STUCK = ('可能原因：网络或 DNS 慢、登录会话损坏、WhatsApp Web 页面加载异常。\n'
              '建议：先点“检查登录”；仍无变化可退出应用，运行 Clean-OldBridge.ps1，'
              '必要时在“存储”页打开 chrome_profile，把 session 文件夹改名备份后重新扫码。')


@dataclass
class LoginView:
    headline: str
    detail: str
    level: str          # ok | info | warn | error


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return '—'
    seconds = max(0, int(seconds))
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


def seconds_since(iso: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - stamp).total_seconds()


def describe(status: Optional[dict], *, elapsed: float, misses: int = 0, tick: int = 0,
             checked_at: str = '', bridge_starting: bool = False,
             now: Optional[datetime] = None) -> LoginView:
    spin = SPINNER[tick % len(SPINNER)]
    head_time = f'{spin} 已用时 {fmt_duration(elapsed)}'
    checked = f' · 最近检查 {checked_at}' if checked_at else ''

    if bridge_starting and not status:
        return LoginView('① 正在启动桥接进程…',
                         f'{head_time}{checked}\n正在启动 node.exe 并等待桥接服务响应（最多约 30 秒）。',
                         'info')
    if not status:
        if misses >= 3:
            return LoginView(f'⚠ 桥接无响应（连续 {misses} 次）',
                             f'{head_time}{checked}\n桥接进程可能已退出或卡死。可点“启动 / 扫码登录”重启，'
                             '或查看“存储”页里的 app.log。', 'error')
        return LoginView('正在读取登录状态…', f'{head_time}{checked}', 'info')

    state = str(status.get('status') or '').upper()
    note = str(status.get('detail') or '').strip()
    client_state = str(status.get('last_client_state') or '').strip()
    has_qr = bool(status.get('has_qr'))
    stage_for = seconds_since(status.get('updated_at'), now)
    timing = f'{head_time} · 本阶段已持续 {fmt_duration(stage_for)}{checked}'
    report = f'桥接回报：{note or "—"}'
    if client_state and client_state != 'UNKNOWN':
        report += f'（WhatsApp 客户端状态：{client_state}）'

    if state == 'READY':
        return LoginView('✅ WhatsApp 已登录 · 桥接在后台运行', '', 'ok')

    if state == 'STARTING':
        wait = stage_for if stage_for is not None else elapsed
        if wait >= STUCK_STARTING:
            return LoginView('❌ ② 加载 WhatsApp Web 超过 3 分钟，很可能卡住了',
                             f'{timing}\n{report}\n{TIPS_STUCK}', 'error')
        if wait >= WARN_STARTING:
            return LoginView('⚠ ② 仍在加载 WhatsApp Web（已超过 1 分钟）',
                             f'{timing}\n{report}\n首次启动或网络较慢时可能需要 1–3 分钟，请再等一会儿。', 'warn')
        return LoginView('② 正在启动浏览器并加载 WhatsApp Web…',
                         f'{timing}\n{report}\n正常情况下 10–60 秒内会出现二维码或直接登录。', 'info')

    if state == 'QR_PENDING':
        if not has_qr:
            level = 'warn' if (stage_for or 0) >= WARN_NO_QR else 'info'
            return LoginView('③ 正在生成二维码…', f'{timing}\n{report}', level)
        return LoginView('③ 请用手机扫码登录',
                         f'{timing}\n手机 WhatsApp → 设置 → 已关联设备 → 关联设备。二维码会定期刷新，请扫描最新的一张。',
                         'info')

    if state in ('AUTHENTICATED', 'CONNECTED'):
        wait = stage_for or 0
        if wait >= STUCK_SYNC:
            return LoginView('❌ ④ 同步聊天超过 5 分钟，很可能卡住了',
                             f'{timing}\n{report}\n请确认手机联网且 WhatsApp 在前台。\n{TIPS_STUCK}', 'error')
        if wait >= WARN_SYNC:
            return LoginView('⚠ ④ 同步聊天较慢（已超过 2 分钟）',
                             f'{timing}\n{report}\n请保持手机联网，并打开手机上的 WhatsApp。', 'warn')
        return LoginView('④ 已扫码，正在同步聊天…',
                         f'{timing}\n{report}\n通常需要 10–60 秒，请保持手机联网。', 'info')

    if state == 'AUTH_FAILURE':
        return LoginView('❌ 登录验证失败', f'{timing}\n{report}\n请重新扫码；仍失败请重置 session（见“存储”页）。', 'error')
    if state == 'DISCONNECTED':
        return LoginView('⚠ WhatsApp 连接已断开', f'{timing}\n{report}\n请点“启动 / 扫码登录”重新连接。', 'warn')
    if state == 'ERROR':
        return LoginView('❌ 桥接初始化失败', f'{timing}\n{report}', 'error')
    return LoginView(f'状态：{state or "未知"}', f'{timing}\n{report}', 'info')
