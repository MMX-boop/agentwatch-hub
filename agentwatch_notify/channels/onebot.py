from concurrent.futures import ThreadPoolExecutor

import httpx

from agentwatch_notify.channels.base import NotificationMessage
from agentwatch_notify.channels.http import post_json
from agentwatch_notify.config import Settings


class OneBotChannel:
    """OneBot v11 HTTP; NapCat is one compatible implementation."""

    name = "qq"
    label = "QQ / OneBot"

    def enabled(self, settings: Settings) -> bool:
        return settings.qq_enabled

    def configured(self, settings: Settings) -> bool:
        return bool(settings.onebot_base_url and settings.qq_targets)

    def send(
        self, settings: Settings, message: NotificationMessage, transport: httpx.BaseTransport | None = None
    ) -> bool:
        if not settings.notify_enabled or not self.enabled(settings) or not self.configured(settings):
            return False
        token = settings.onebot_access_token.get_secret_value()
        headers = {"Authorization": "Bearer " + token} if token else {}
        text = message.plain_text()

        def send_target(target: str) -> bool:
            kind, identifier = target.split(":")
            id_key = "user_id" if kind == "private" else "group_id"
            body = {id_key: int(identifier), "message": text, "auto_escape": True}
            try:
                result = post_json(
                    settings, settings.onebot_base_url + f"/send_{kind}_msg", body, transport, headers
                )
                return (
                    result is not None
                    and result.get("status") == "ok"
                    and type(result.get("retcode")) is int
                    and result["retcode"] == 0
                )
            except Exception:
                # Target isolation boundary: never log an exception containing a token or recipient.
                return False

        targets = settings.qq_targets.split(",")
        # Settings caps fan-out at 8. A slow recipient must not delay starting other sends.
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            results = list(pool.map(send_target, targets))
        return any(results)
