import json

import httpx

from agentwatch_notify.config import Settings
from agentwatch_notify.notify import decode_payload, deliver, normalize
from agentwatch_notify.providers import send_bark


def env_file(tmp_path):
    target = tmp_path / ".env"
    target.write_text(
        "BARK_DEVICE_KEY=fixture-device\nBARK_SERVER=https://api.day.app\nPERSONA=boss\n",
        encoding="utf-8",
    )
    return target


def test_claude_stdin_is_decoded_as_utf8():
    message = "测试正常，有什么需要帮忙的吗？"
    raw = json.dumps({"last_assistant_message": message}, ensure_ascii=False).encode("utf-8")
    assert json.loads(decode_payload(raw))["last_assistant_message"] == message


def test_only_completion_events_are_normalized():
    assert normalize("claude", {"hook_event_name": "Stop", "cwd": "C:/work/demo"})["project"] == "demo"
    assert normalize("claude", {"hook_event_name": "Notification"}) is None
    assert normalize("codex", {"type": "other"}) is None


def test_delivery_deduplicates_and_never_puts_device_key_in_url(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_NOTIFY_HOME", str(tmp_path))
    calls = []

    def bark(request):
        calls.append(request)
        return httpx.Response(200, json={"code": 200})

    raw = json.dumps(
        {
            "type": "agent-turn-complete",
            "thread-id": "thread-fixture",
            "turn-id": "turn-fixture",
            "cwd": "C:/work/sample",
            "last-assistant-message": "测试通过。",
        },
        ensure_ascii=False,
    )
    transport = httpx.MockTransport(bark)
    assert deliver("codex", raw, env_file(tmp_path), bark_transport=transport)
    assert not deliver("codex", raw, env_file(tmp_path), bark_transport=transport)
    assert len(calls) == 1
    assert "fixture-device" not in str(calls[0].url)
    assert json.loads(calls[0].content)["device_key"] == "fixture-device"


def test_bark_failure_does_not_raise():
    settings = Settings(_env_file=None, bark_device_key="fixture")
    transport = httpx.MockTransport(lambda _: httpx.Response(503))
    assert not send_bark(settings, {"title": "test", "body": "test"}, transport)
