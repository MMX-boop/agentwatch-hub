import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from filelock import FileLock

from agentwatch_notify.config import config_home, config_path, load_settings
from agentwatch_notify.persona import render_notification
from agentwatch_notify.providers import send_bark

MODULE = "agentwatch_notify"


def decode_payload(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8-sig")
    return raw


def project_name(cwd: Any, fallback: str) -> str:
    value = str(cwd or "").replace("\\", "/").rstrip("/")
    return value.rsplit("/", 1)[-1] or fallback


def normalize(provider: str, payload: dict) -> dict | None:
    if provider == "codex":
        if payload.get("type") != "agent-turn-complete":
            return None
        return {
            "category": "complete",
            "project": project_name(payload.get("cwd"), "Codex"),
            "summary": payload.get("last-assistant-message") or "本轮任务已经结束。",
            "identity": [payload.get("thread-id"), payload.get("turn-id")],
            "window": 86_400,
        }
    if provider == "claude":
        kind = payload.get("hook_event_name")
        if kind not in {"Stop", "StopFailure"}:
            return None
        failed = kind == "StopFailure"
        return {
            "category": "error" if failed else "complete",
            "project": project_name(payload.get("cwd"), "Claude"),
            "summary": (
                payload.get("last_assistant_message")
                or payload.get("error_details")
                or payload.get("error")
                or ("执行遇到问题。" if failed else "本轮任务已经结束。")
            ),
            "identity": [payload.get("session_id"), kind, payload.get("last_assistant_message")],
            "window": 30,
        }
    return None


def _cache() -> tuple[Path, Path]:
    path = config_home() / "sent.json"
    return path, Path(str(path) + ".lock")


def deliver(
    provider: str,
    raw: str | bytes,
    settings_file: Path | None = None,
    bark_transport=None,
    llm_transport=None,
) -> bool:
    if len(raw) > 524_288:
        return False
    payload = json.loads(decode_payload(raw))
    if not isinstance(payload, dict):
        return False
    event = normalize(provider, payload)
    if not event:
        return False
    settings = load_settings(settings_file)
    if not settings.notify_enabled or not settings.bark_device_key.get_secret_value():
        return False

    identity = event["identity"]
    identity_text = json.dumps(identity, ensure_ascii=True) if any(identity) else decode_payload(raw)
    digest = hashlib.sha256((provider + ":" + identity_text).encode("utf-8")).hexdigest()
    cache, lock = _cache()
    cache.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(lock, timeout=15):
        try:
            sent = json.loads(cache.read_text("utf-8")) if cache.exists() else {}
        except (OSError, ValueError, TypeError):
            sent = {}
        current = time.time()
        sent = {
            key: stamp
            for key, stamp in sent.items()
            if isinstance(stamp, (int, float)) and current - stamp < 86_400
        }
        previous = sent.get(digest)
        if isinstance(previous, (int, float)) and current - previous < event["window"]:
            return False

        message = render_notification(
            settings,
            provider,
            event["project"],
            event["summary"],
            event["category"],
            transport=llm_transport,
        )
        accepted = send_bark(settings, message, transport=bark_transport)
        if accepted:
            sent[digest] = current
            sent = dict(sorted(sent.items(), key=lambda item: item[1])[-1000:])
            temporary = cache.with_suffix(".tmp")
            temporary.write_text(json.dumps(sent), encoding="utf-8")
            os.replace(temporary, cache)
        return accepted


def forward_previous(raw: str) -> None:
    try:
        state = json.loads((config_home() / "install-state.json").read_text("utf-8"))
        previous = state.get("previous_codex_notify")
        if (
            not previous
            or not isinstance(previous, list)
            or not all(isinstance(item, str) for item in previous)
            or MODULE in previous
        ):
            return
        subprocess.run(
            [*previous, raw],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        pass


def dispatch(provider: str, raw: str | bytes) -> bool:
    text = decode_payload(raw)
    try:
        return deliver(provider, text, config_path())
    except Exception:
        return False
    finally:
        if provider == "codex":
            forward_previous(text)
