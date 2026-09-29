"""适配现有 WhatsAppBridgeClient，不修改 whatsapp_bridge_client.py / server.js。

GUI 改为：from whatsapp_integration import WhatsAppBridgeClient
引擎仍调用 sender.send_text_and_image(group_name, text, image_path)。
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
from whatsapp_bridge_client import WhatsAppBridgeClient as _ExistingBridge


class WhatsAppBridgeClient(_ExistingBridge):
    """保持旧公开方法和构造参数；只补充投递诊断元数据。"""

    def send_text_and_image(
        self, group_exact_name: str, text: str, image_path: Optional[str] = None
    ) -> dict:
        # 唯一的实际发送调用；不要在这里额外单发文字或图片。
        raw = super().send_text_and_image(group_exact_name, text, image_path)
        if not isinstance(raw, dict):
            return {
                'status': 'UNKNOWN', 'code': 'BRIDGE_BAD_RESPONSE',
                'detail': repr(raw), 'image_sent': None,
            }
        result = dict(raw)
        status = str(result.get('status', 'UNKNOWN')).upper()
        detail = str(result.get('detail') or '')
        code = result.get('code') or result.get('error_code')

        if self.dry_run:
            result['status'] = 'DRY_RUN'
            result['image_sent'] = None
            result['code'] = code or 'DRY_RUN'
            return result

        if status == 'SENT_TEXT_ONLY':
            result['image_sent'] = False if image_path else None
            result['code'] = code or 'PHOTO_SEND_FAILED'
            return result

        if status in ('SENT_WITH_IMAGE', 'SENT_IMAGE'):
            result['image_sent'] = True
            result['code'] = code or status
            return result

        if status == 'SENT':
            # 当前桥接服务端 /send 的 JSON 未随附件提供：SENT 不能证明照片成功。
            result.setdefault('image_sent', None if image_path else False)
            result['code'] = code or ('PHOTO_RESULT_UNKNOWN' if image_path else 'TEXT_SENT')
            return result

        if status == 'FAILED':
            if '未登录' in detail or '扫码' in detail:
                code = code or 'WA_NOT_LOGGED_IN'
            elif '未启动' in detail:
                code = code or 'WA_BRIDGE_NOT_STARTED'
            elif '找不到' in detail or '完全匹配' in detail:
                code = code or 'WA_GROUP_NOT_FOUND'
            elif '调用桥接服务失败' in detail:
                # POST 超时可能发生在服务端已发送以后，不可盲目重试。
                result['status'] = 'UNKNOWN'
                code = code or 'WA_TRANSPORT_RESULT_UNKNOWN'
            elif 'HTTP ' in detail:
                code = code or 'WA_BRIDGE_HTTP_ERROR'
            else:
                code = code or 'WA_SEND_FAILED'
            result['code'] = code
            result['image_sent'] = None
            return result

        result['code'] = code or status
        result.setdefault('image_sent', None)
        return result
