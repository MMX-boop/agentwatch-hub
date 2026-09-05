from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor

import httpx

from agentwatch_notify.channels.bark import BarkChannel
from agentwatch_notify.channels.base import NotificationChannel, NotificationMessage
from agentwatch_notify.channels.feishu import FeishuWebhookChannel
from agentwatch_notify.channels.onebot import OneBotChannel
from agentwatch_notify.config import Settings

CHANNELS: tuple[NotificationChannel, ...] = (BarkChannel(), OneBotChannel(), FeishuWebhookChannel())


class ChannelRouter:
    def __init__(
        self,
        settings: Settings,
        channels: Sequence[NotificationChannel] = CHANNELS,
        transports: Mapping[str, httpx.BaseTransport] | None = None,
    ) -> None:
        self.settings = settings
        self.channels = tuple(channels)
        self.transports = transports or {}

    def status(self, channel: NotificationChannel) -> str:
        try:
            if not self.settings.notify_enabled or not channel.enabled(self.settings):
                return "DISABLED"
            return "CONFIGURED" if channel.configured(self.settings) else "MISSING"
        except Exception:
            # Extension boundary: a faulty channel must not disable the others.
            return "INVALID"

    def active(self, selection: str = "all") -> tuple[NotificationChannel, ...]:
        if selection != "all" and selection not in {channel.name for channel in self.channels}:
            raise ValueError("Unknown notification channel")
        return tuple(
            channel
            for channel in self.channels
            if (selection == "all" or channel.name == selection) and self.status(channel) == "CONFIGURED"
        )

    def send(self, message: NotificationMessage, selection: str = "all") -> dict[str, bool]:
        selected = self.active(selection)
        if not selected:
            return {}

        def send_one(channel: NotificationChannel) -> bool:
            try:
                return bool(channel.send(self.settings, message, self.transports.get(channel.name)))
            except Exception:
                # Provider isolation boundary. Never expose payloads, credentials or raw exceptions.
                return False

        with ThreadPoolExecutor(max_workers=len(selected)) as pool:
            results = list(pool.map(send_one, selected))
        return {channel.name: accepted for channel, accepted in zip(selected, results)}
