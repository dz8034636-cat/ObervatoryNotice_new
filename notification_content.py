"""统一通知内容：文字和 PNG 均来自原有 whatsapp_service.py。

不使用其中的 Selenium WhatsAppSender；实际投递仍由 WhatsAppBridgeClient 完成。
"""
from __future__ import annotations
from pathlib import Path
from core import NormalizedWarning
from whatsapp_service import build_warning_message, generate_alert_card


def build_notification(event: NormalizedWarning, settings) -> tuple[str, str]:
    """为一条警告生成既定文字及相应图片；失败时抛错，不发送纯文字。"""
    if not event.is_active():
        raise ValueError('不能为无效或已取消的警告生成有效警告通知')
    message = build_warning_message(event)
    if not message.strip():
        raise ValueError('WhatsApp Service 返回空白通知文字')
    image_path = generate_alert_card(
        event,
        org_name=getattr(settings, 'org_name', ''),
        app_name=getattr(settings, 'app_name', '天气警告通知'),
    )
    image = Path(image_path)
    if not image.is_file() or image.stat().st_size == 0:
        raise RuntimeError(f'WhatsApp Service 未生成有效警告图片：{image}')
    return message, str(image.resolve())
