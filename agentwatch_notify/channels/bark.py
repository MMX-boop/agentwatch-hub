import httpx

from agentwatch_notify.channels.base import NotificationMessage
from agentwatch_notify.channels.http import post_json
from agentwatch_notify.config import Settings


class BarkChannel:
    name = "bark"
    label = "Bark"

    def enabled(self, settings: Settings) -> bool:
        return settings.bark_enabled

    def configured(self, settings: Settings) -> bool:
        return bool(settings.bark_device_key.get_secret_value())

    def send(
        self, settings: Settings, message: NotificationMessage, transport: httpx.BaseTransport | None = None
    ) -> bool:
        if not settings.notify_enabled or not self.enabled(settings) or not self.configured(settings):
            return False
        body = {
            "title": message.title[:120],
            "body": message.body[:900],
            "device_key": settings.bark_device_key.get_secret_value(),
            "group": settings.bark_group,
            "sound": settings.bark_sound,
            "level": settings.bark_level,
            "isArchive": 1,
        }
        if settings.bark_icon_url:
            body["icon"] = settings.bark_icon_url
        result = post_json(settings, settings.bark_server + "/push", body, transport)
        return result is not None and result.get("code") == 200
