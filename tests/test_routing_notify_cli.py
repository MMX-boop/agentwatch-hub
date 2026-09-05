import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from agentwatch_notify import cli, notify
from agentwatch_notify.channels.router import CHANNELS, ChannelRouter
from agentwatch_notify.config import Settings, config_home, config_path

EVENT = json.dumps(
    {
        "type": "agent-turn-complete",
        "thread-id": "fixture-thread",
        "turn-id": "fixture-turn",
        "cwd": "C:/work/demo",
        "last-assistant-message": "测试通过。",
    }
)
CONFIG = """BARK_DEVICE_KEY=fixture-bark
QQ_ENABLED=true
QQ_TARGETS=private:10001
ONEBOT_ACCESS_TOKEN=fixture-onebot
FEISHU_ENABLED=true
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/fixture-hook
FEISHU_WEBHOOK_SECRET=fixture-sign
"""
SUCCESS = {"bark": {"code": 200}, "qq": {"status": "ok", "retcode": 0}, "feishu": {"code": 0}}


def write_config(text=CONFIG):
    config_home().mkdir(parents=True, exist_ok=True)
    config_path().write_text(text, encoding="utf-8")
    return config_path()


def transport_set(calls, failed=()):
    def make(name):
        def handler(request):
            calls.append((name, request))
            return httpx.Response(503 if name in failed else 200, json=SUCCESS[name])

        return httpx.MockTransport(handler)

    return {name: make(name) for name in SUCCESS}


@pytest.mark.parametrize("channel", ["qq", "feishu"])
def test_deliver_without_bark(channel):
    text = (
        "QQ_ENABLED=true\nQQ_TARGETS=private:10001\n"
        if channel == "qq"
        else "FEISHU_ENABLED=true\nFEISHU_WEBHOOK_URL=https://example.com/hook/fixture\n"
    )
    write_config(text)
    calls = []
    transports = transport_set(calls)
    assert notify.deliver("codex", EVENT, channel_transports=transports)
    assert [name for name, _ in calls] == [channel]
    assert not notify.deliver("codex", EVENT, channel_transports=transports)
    assert len(calls) == 1


def test_fanout_renders_once_and_marks_dedup_after_all_attempts(monkeypatch):
    write_config()
    calls = []
    rendered = []

    def render(*args, **kwargs):
        rendered.append(args)
        return {"title": "人格测试", "body": "Agent：Codex\n项目：demo\n结果：通过", "generated": True}

    monkeypatch.setattr(notify, "render_notification", render)
    transports = transport_set(calls, failed={"qq"})
    assert notify.deliver("codex", EVENT, channel_transports=transports)
    assert not notify.deliver("codex", EVENT, channel_transports=transports)
    assert len(rendered) == 1
    assert sorted(name for name, _ in calls) == ["bark", "feishu", "qq"]
    assert (config_home() / "sent.json").exists()


def test_llm_receives_no_channel_credentials_and_is_called_once():
    write_config(CONFIG + "LLM_BASE_URL=https://llm.example/v1\nLLM_MODEL=fixture\nLLM_API_KEY=fixture-llm\n")
    config = Settings(_env_file=config_path())
    llm_requests = []

    def llm(request):
        llm_requests.append(request)
        body = request.content.decode()
        assert all(secret not in body for secret in config.secrets)
        content = json.dumps({"title": "总裁，战报送达", "flavor": "茶还热着，结果已上桌。"})
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        )

    event = json.loads(EVENT)
    event["last-assistant-message"] = " ".join(config.secrets)
    outbound = []
    assert notify.deliver(
        "codex",
        json.dumps(event),
        channel_transports=transport_set(outbound),
        llm_transport=httpx.MockTransport(llm),
    )
    assert len(llm_requests) == 1
    assert len(outbound) == 3
    for name, request in outbound:
        payload = json.loads(request.content)
        text = (
            payload["body"]
            if name == "bark"
            else (payload["message"] if name == "qq" else payload["content"]["text"])
        )
        assert all(secret not in text for secret in config.secrets)


def test_all_failed_can_retry_and_empty_router_does_not_render(monkeypatch):
    write_config()
    calls = []
    assert not notify.deliver("codex", EVENT, channel_transports=transport_set(calls, failed=SUCCESS))
    assert not (config_home() / "sent.json").exists()
    assert notify.deliver("codex", EVENT, channel_transports=transport_set(calls))
    assert len(calls) == 6
    write_config("")

    def unexpected(*args, **kwargs):
        pytest.fail("No configured channel must not invoke persona / LLM")

    monkeypatch.setattr(notify, "render_notification", unexpected)
    assert not notify.deliver("codex", EVENT)


def test_concurrent_callbacks_deduplicate():
    write_config("BARK_DEVICE_KEY=fixture-bark\n")
    calls = []
    transports = transport_set(calls)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: notify.deliver("codex", EVENT, channel_transports=transports), range(2))
        )
    assert sorted(results) == [False, True]
    assert len(calls) == 1


def test_previous_codex_forwarding_survives_delivery_error(monkeypatch):
    write_config()
    (config_home() / "install-state.json").write_text(
        json.dumps({"previous_codex_notify": ["fixture-command", "--flag"]}), encoding="utf-8"
    )
    calls = []

    def fail(*args, **kwargs):
        raise RuntimeError("fixture-secret")

    monkeypatch.setattr(notify, "deliver", fail)
    monkeypatch.setattr(notify.subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    assert notify.dispatch("codex", EVENT) is False
    assert calls == [["fixture-command", "--flag", EVENT]]


@pytest.mark.parametrize("selection", ["qq", "feishu", "all", None])
def test_cli_channel_selection(selection, monkeypatch):
    write_config()
    calls = []
    transports = transport_set(calls)
    monkeypatch.setattr(cli, "ChannelRouter", lambda config: ChannelRouter(config, transports=transports))
    args = ["test"] + (["--channel", selection] if selection else [])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert "OK" in result.output
    expected = set(SUCCESS) if selection in {None, "all"} else {selection}
    assert {name for name, _ in calls} == expected
    assert "fixture" not in result.output


def test_cli_partial_success_exit_zero_and_all_failure_exit_one(monkeypatch):
    write_config()
    transports = transport_set([], failed={"qq"})
    monkeypatch.setattr(cli, "ChannelRouter", lambda config: ChannelRouter(config, transports=transports))
    result = CliRunner().invoke(cli.app, ["test"])
    assert result.exit_code == 0 and "FAILED" in result.output
    transports = transport_set([], failed=SUCCESS)
    assert CliRunner().invoke(cli.app, ["test"]).exit_code == 1
    write_config("")
    assert CliRunner().invoke(cli.app, ["test"]).exit_code == 1
    assert CliRunner().invoke(cli.app, ["test", "--channel", "qq"]).exit_code == 1
    assert CliRunner().invoke(cli.app, ["test", "--channel", "unknown"]).exit_code != 0


def test_doctor_reports_channels_without_secrets():
    write_config(CONFIG + "LLM_API_KEY=fixture-llm\n")
    result = CliRunner().invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    for channel in CHANNELS:
        assert channel.label in result.output
    assert result.output.count("CONFIGURED") == 3
    for secret in Settings(_env_file=config_path()).secrets:
        assert secret not in result.output
    assert "Codex notify" in result.output


@pytest.mark.parametrize("command", [["doctor"], ["test"]])
def test_invalid_config_never_prints_webhook(command):
    write_config("FEISHU_WEBHOOK_URL=http://example.com/fixture-private-hook\n")
    result = CliRunner().invoke(cli.app, command)
    assert "fixture-private-hook" not in result.output
    assert "http://example.com" not in result.output
    assert "ValidationError" in result.output


def test_init_defaults_match_example_and_preserve_existing():
    result = CliRunner().invoke(cli.app, ["init"])
    assert result.exit_code == 0
    example = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env.example")
    initial = Settings(_env_file=config_path())
    assert initial.model_dump() == example.model_dump()
    write_config("BARK_DEVICE_KEY=fixture-keep\n")
    assert CliRunner().invoke(cli.app, ["init"]).exit_code == 0
    assert "fixture-keep" in config_path().read_text("utf-8")
