import base64
import hashlib
import hmac
import time

import httpx

from agentwatch_notify.channels.base import NotificationMessage
from agentwatch_notify.channels.http import post_json
from agentwatch_notify.config import Settings


def webhook_sign(timestamp: int, secret: str) -> str:
    # Feishu uses timestamp + newline + secret as the KEY, and an empty message.
    key = f"{timestamp}\n{secret}".encode("utf-8")
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("ascii")


class FeishuWebhookChannel:
    """Outbound custom-group bot; a future App Bot is a separate channel/adapter."""

    name = "feishu"
    label = "Feishu"

    def enabled(self, settings: Settings) -> bool:
        return settings.feishu_enabled

    def configured(self, settings: Settings) -> bool:
        return bool(settings.feishu_webhook_url.get_secret_value())

    def send(
        self, settings: Settings, message: NotificationMessage, transport: httpx.BaseTransport | None = None
    ) -> bool:
        if not settings.notify_enabled or not self.enabled(settings) or not self.configured(settings):
            return False
        body = {"msg_type": "text", "content": {"text": message.plain_text()}}
        secret = settings.feishu_webhook_secret.get_secret_value()
        if secret:
            timestamp = int(time.time())
            body.update(timestamp=str(timestamp), sign=webhook_sign(timestamp, secret))
        result = post_json(settings, settings.feishu_webhook_url.get_secret_value(), body, transport)
        if result is None:
            return False
        # Accept current and legacy success formats, never a missing status.
        code = result.get("code", result.get("StatusCode"))
        return type(code) is int and code == 0
