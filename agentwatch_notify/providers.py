"""Compatibility entry point for existing Bark callers."""

import httpx

from agentwatch_notify.channels.bark import BarkChannel
from agentwatch_notify.channels.base import NotificationMessage
from agentwatch_notify.config import Settings


def send_bark(settings: Settings, payload: dict, transport: httpx.BaseTransport | None = None) -> bool:
    message = NotificationMessage(str(payload["title"]), str(payload["body"]), bool(payload.get("generated")))
    return BarkChannel().send(settings, message, transport)
