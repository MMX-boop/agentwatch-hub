"""Shared HTTP policy: verified TLS, explicit proxy, no redirects or response logging."""

import json
from typing import Any
from urllib.parse import urlsplit

import httpx

from agentwatch_notify.config import Settings


def client_options(
    settings: Settings, url: str, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "timeout": 10.0,
        "transport": transport,
        "follow_redirects": False,
        "trust_env": False,
    }
    local = urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}
    if not local and transport is None and settings.outbound_proxy:
        options["proxy"] = settings.outbound_proxy
    return options


def post_json(
    settings: Settings,
    url: str,
    payload: dict[str, Any],
    transport: httpx.BaseTransport | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    try:
        with httpx.Client(**client_options(settings, url, transport)) as client:
            # Bound response memory as well as per-operation network waits.
            with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    content.extend(chunk)
                    if len(content) > 65_536:
                        return None
                result = json.loads(content)
        return result if isinstance(result, dict) else None
    except (httpx.HTTPError, ValueError, TypeError, OSError):
        return None
