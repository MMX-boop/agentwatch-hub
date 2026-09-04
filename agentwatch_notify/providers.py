from urllib.parse import urlsplit

import httpx

from agentwatch_notify.config import Settings


def send_bark(settings: Settings, payload: dict, transport=None) -> bool:
    key = settings.bark_device_key.get_secret_value()
    if not settings.notify_enabled or not key:
        return False
    if urlsplit(settings.bark_server).scheme != "https":
        return False
    body = {
        "title": str(payload["title"])[:120],
        "body": str(payload["body"])[:900],
        "device_key": key,
        "group": settings.bark_group,
        "sound": settings.bark_sound,
        "level": settings.bark_level,
        "isArchive": 1,
    }
    if settings.bark_icon_url:
        body["icon"] = settings.bark_icon_url
    options = {"timeout": 10, "transport": transport, "follow_redirects": False}
    if transport is None and settings.outbound_proxy:
        options["proxy"] = settings.outbound_proxy
    try:
        with httpx.Client(**options) as client:
            result = client.post(settings.bark_server + "/push", json=body)
            result.raise_for_status()
        return result.json().get("code") == 200
    except (httpx.HTTPError, ValueError, TypeError):
        return False
