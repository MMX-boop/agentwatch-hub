from dataclasses import dataclass
from typing import Protocol

import httpx

from agentwatch_notify.config import Settings


@dataclass(frozen=True)
class NotificationMessage:
    """Already sanitized and rendered once, before any channel is selected."""

    title: str
    body: str
    generated: bool = False

    def plain_text(self, limit: int = 2500) -> str:
        text = f"🤖 AgentWatch\n\n{self.title}\n{self.body}"
        return text if len(text) <= limit else text[: limit - 1] + "…"


class NotificationChannel(Protocol):
    name: str
    label: str

    def enabled(self, settings: Settings) -> bool: ...

    def configured(self, settings: Settings) -> bool: ...

    def send(
        self, settings: Settings, message: NotificationMessage, transport: httpx.BaseTransport | None = None
    ) -> bool: ...
