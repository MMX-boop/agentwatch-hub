import json
from threading import Barrier

import httpx
import pytest
from pydantic import ValidationError

from agentwatch_notify.channels.bark import BarkChannel
from agentwatch_notify.channels.base import NotificationMessage
from agentwatch_notify.channels.feishu import FeishuWebhookChannel, webhook_sign
from agentwatch_notify.channels.http import client_options
from agentwatch_notify.channels.onebot import OneBotChannel
from agentwatch_notify.channels.router import ChannelRouter
from agentwatch_notify.config import Settings
from agentwatch_notify.providers import send_bark

MESSAGE = NotificationMessage("总裁，战报出炉了 📋", "Agent：Codex\n项目：demo\n结果：测试通过。")
SUCCESS = {"bark": {"code": 200}, "qq": {"status": "ok", "retcode": 0}, "feishu": {"code": 0}}


def settings(**kwargs):
    values = dict(
        bark_device_key="fixture-bark",
        qq_enabled=True,
        qq_targets="private:10001,group:10002",
        onebot_access_token="fixture-onebot",
        feishu_enabled=True,
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/fixture-hook",
        feishu_webhook_secret="fixture-signing-secret",
    )
    values.update(kwargs)
    return Settings(_env_file=None, **values)


def mock_result(value, status=200):
    return httpx.MockTransport(lambda _: httpx.Response(status, json=value))


def test_legacy_bark_payload_and_empty_key():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 200})

    transport = httpx.MockTransport(handler)
    config = Settings(
        _env_file=None, bark_device_key="fixture-bark", bark_icon_url="https://example.com/dog.png"
    )
    assert send_bark(config, {"title": "t" * 200, "body": "好" * 1000}, transport)
    body = json.loads(requests[0].content)
    assert body == {
        "title": "t" * 120,
        "body": "好" * 900,
        "device_key": "fixture-bark",
        "group": "AgentWatch",
        "sound": "minuet",
        "level": "active",
        "isArchive": 1,
        "icon": "https://example.com/dog.png",
    }
    assert "fixture-bark" not in str(requests[0].url)
    assert not BarkChannel().send(Settings(_env_file=None), MESSAGE, transport)
    assert not BarkChannel().send(settings(bark_enabled=False), MESSAGE, transport)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "url", ["http://example.com", "https://u:p@example.com", "https://example.com/?token=x"]
)
def test_bark_validation(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, bark_server=url)


def test_qq_private_group_auth_and_literal_text():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=SUCCESS["qq"])

    message = NotificationMessage("测试😀", "[CQ:at,qq=all]" + "汉字😀" * 2000)
    assert OneBotChannel().send(settings(), message, httpx.MockTransport(handler))
    by_path = {req.url.path: req for req in requests}
    for action, id_field, identifier in [("private", "user_id", 10001), ("group", "group_id", 10002)]:
        req = by_path[f"/send_{action}_msg"]
        assert req.method == "POST"
        assert req.headers["Authorization"] == "Bearer fixture-onebot"
        payload = json.loads(req.content)
        assert payload[id_field] == identifier
        assert payload["auto_escape"] is True
        assert len(payload["message"]) == 2500
        assert payload["message"].endswith("…")
        assert "[CQ:at,qq=all]" in payload["message"]
        assert "\ufffd" not in payload["message"]


@pytest.mark.parametrize(
    "target",
    [
        "10001",
        "private:x",
        "user:10001",
        "group:0",
        "private:-1",
        "private:1,",
        "private:9223372036854775808",
        ",".join(f"private:{i}" for i in range(1, 10)),
    ],
)
def test_invalid_qq_targets(target):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, qq_targets=target)


def test_duplicate_targets_send_once():
    config = settings(qq_targets=" private:10001,private:10001 ")
    assert config.qq_targets == "private:10001"


@pytest.mark.parametrize("response", [{"status": "async", "retcode": 1}, {"status": "ok", "retcode": False}])
def test_onebot_async_or_invalid_status_not_reported_as_delivered(response):
    assert not OneBotChannel().send(settings(), MESSAGE, mock_result(response))


@pytest.mark.parametrize("url", ["http://localhost:3000", "http://127.0.0.1:3000", "http://[::1]:3000"])
def test_local_onebot_http_and_proxy_bypass(url, monkeypatch):
    config = settings(onebot_base_url=url, outbound_proxy="http://proxy.example:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://unwanted.example:7890")
    opts = client_options(config, url)
    assert "proxy" not in opts
    assert opts["trust_env"] is False
    assert opts["follow_redirects"] is False
    assert opts["timeout"] > 0
    assert opts.get("verify", True) is True
    assert client_options(config, "https://example.com")["proxy"] == config.outbound_proxy


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8:3000",
        "http://192.168.1.5",
        "http://localhost.evil",
        "http://0.0.0.0:3000",
        "https://u:p@example.com",
        "https://example.com?token=x",
        "https://example.com#secret",
        "http://127.0.0.1\\@example.com",
        "https://example.com:bad",
        "https://[broken",
        "https://example.com:0",
    ],
)
def test_unsafe_onebot_endpoint_rejected(url):
    with pytest.raises(ValidationError):
        settings(onebot_base_url=url)


def test_remote_onebot_needs_auth():
    with pytest.raises(ValidationError):
        settings(onebot_base_url="https://qq.example", onebot_access_token="")
    assert settings(onebot_base_url="https://qq.example").qq_enabled


def test_feishu_payload_and_signature(monkeypatch):
    monkeypatch.setattr("agentwatch_notify.channels.feishu.time.time", lambda: 1700000000)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "msg": "success"})

    config = settings()
    assert FeishuWebhookChannel().send(config, MESSAGE, httpx.MockTransport(handler))
    body = json.loads(requests[0].content)
    assert body == {
        "msg_type": "text",
        "content": {"text": MESSAGE.plain_text()},
        "timestamp": "1700000000",
        "sign": webhook_sign(1700000000, "fixture-signing-secret"),
    }
    assert "fixture-signing-secret" not in requests[0].content.decode()


def test_feishu_signature_fixed_vector():
    # Independent digest vector, not another call to the implementation under test.
    assert (
        webhook_sign(1700000000, "fixture-signing-secret") == "p7jQQAwRH+waZB1I+OqmysAwmjZVKfbEC1Fpr/9O/Ns="
    )


def test_feishu_unsigned_and_legacy_response():
    def handler(request):
        body = json.loads(request.content)
        assert "sign" not in body and "timestamp" not in body
        return httpx.Response(200, json={"StatusCode": 0, "StatusMessage": "success"})

    assert FeishuWebhookChannel().send(
        settings(feishu_webhook_secret=""), MESSAGE, httpx.MockTransport(handler)
    )


@pytest.mark.parametrize(
    "url", ["http://localhost/hook", "http://open.feishu.cn/hook", "https://u:p@example.com/hook"]
)
def test_feishu_requires_https_and_hides_invalid_value(url):
    with pytest.raises(ValidationError) as error:
        settings(feishu_webhook_url=url)
    assert url not in str(error.value)


@pytest.mark.parametrize("channel", [BarkChannel(), OneBotChannel(), FeishuWebhookChannel()])
@pytest.mark.parametrize(
    "status,value", [(503, {}), (200, []), (200, {}), (200, {"code": 99, "retcode": 99})]
)
def test_http_and_application_errors_return_false(channel, status, value):
    assert not channel.send(settings(), MESSAGE, mock_result(value, status))


@pytest.mark.parametrize("channel", [BarkChannel(), OneBotChannel(), FeishuWebhookChannel()])
def test_redirect_timeout_invalid_json_and_large_response(channel):
    for response in [
        httpx.Response(302, headers={"Location": "https://evil.example"}),
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, content=b"x" * 70000),
    ]:
        requests = []

        def handler(request):
            requests.append(request)
            return response

        assert not channel.send(settings(qq_targets="private:10001"), MESSAGE, httpx.MockTransport(handler))
        assert len(requests) == 1  # A redirect never forwards credentials.

    def timeout(request):
        raise httpx.ReadTimeout("fixture secret must stay inside transport")

    assert not channel.send(settings(), MESSAGE, httpx.MockTransport(timeout))


@pytest.mark.parametrize("failed", [None, "bark", "qq", "feishu"])
def test_router_independent_outcomes(failed):
    transports = {name: mock_result(value, 503 if name == failed else 200) for name, value in SUCCESS.items()}
    assert ChannelRouter(settings(), transports=transports).send(MESSAGE) == {
        name: name != failed for name in SUCCESS
    }


def test_router_exception_isolation_and_parallel_start():
    rendezvous = Barrier(3)

    class Stub:
        def __init__(self, name):
            self.name = self.label = name

        def enabled(self, config):
            return True

        def configured(self, config):
            return True

        def send(self, config, message, transport=None):
            rendezvous.wait(timeout=3)
            if self.name == "broken":
                raise RuntimeError("fixture private token")
            return True

    channels = [Stub("bark"), Stub("broken"), Stub("feishu")]
    assert ChannelRouter(settings(), channels).send(MESSAGE) == {
        "bark": True,
        "broken": False,
        "feishu": True,
    }


def test_router_no_channels_or_global_disabled():
    assert ChannelRouter(Settings(_env_file=None)).send(MESSAGE) == {}
    assert ChannelRouter(settings(notify_enabled=False)).send(MESSAGE) == {}


def test_qq_partial_recipient_failure_still_attempts_others():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("send_private_msg"):
            raise RuntimeError("fixture target failed")
        return httpx.Response(200, json=SUCCESS["qq"])

    assert OneBotChannel().send(settings(), MESSAGE, httpx.MockTransport(handler))
    assert len(requests) == 2


def test_secrets_are_redacted_from_repr_and_persona():
    from agentwatch_notify.persona import render_notification

    config = settings(llm_api_key="fixture-llm")
    secrets = config.secrets
    for secret in secrets:
        assert secret not in repr(config)
    message = render_notification(config, "codex", "demo", " ".join(secrets))
    assert all(secret not in str(message) for secret in secrets)
