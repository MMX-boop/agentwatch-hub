import json

import httpx
import pytest
from pydantic import ValidationError

from agentwatch_notify.config import Settings
from agentwatch_notify.persona import concise_summary, render_notification
from agentwatch_notify.sanitize import Sanitizer


def test_settings_reject_unknown_persona_and_insecure_remote_llm():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, persona="pirate")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_base_url="http://example.com/v1")


def test_summary_is_short_plain_and_redacted():
    sanitizer = Sanitizer(["fixture-secret"])
    raw = "测试正常，有什么需要帮忙的吗？\n\n**详情** token=fixture-secret\n" + "x" * 300
    assert concise_summary(raw, sanitizer) == "测试正常，有什么需要帮忙的吗？"


def test_static_persona_keeps_exact_technical_footer():
    settings = Settings(_env_file=None, persona="emperor")
    message = render_notification(settings, "claude", "sample", "构建通过。")
    assert message["generated"] is False
    assert "皇上" in message["title"]
    assert "Agent：Claude Code" in message["body"]
    assert "项目：sample" in message["body"]
    assert "结果：构建通过。" in message["body"]


def test_llm_persona_sends_only_bounded_context_and_appends_facts():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        content = json.dumps(
            {"title": "总裁，战场已净 📋", "flavor": "这局收得漂亮，战报已经放上桌。"}, ensure_ascii=False
        )
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        )

    settings = Settings(
        _env_file=None,
        persona="boss",
        llm_base_url="https://llm.example/v1",
        llm_model="fixture",
        llm_api_key="fixture-secret",
    )
    message = render_notification(
        settings, "codex", "sample", "构建通过，token=fixture-secret", transport=httpx.MockTransport(handler)
    )
    assert message["generated"] is True
    assert message["title"] == "总裁，战场已净 📋"
    assert "Agent：OpenAI Codex" in message["body"]
    outbound = json.loads(requests[0]["messages"][-1]["content"])
    assert set(outbound) == {"persona", "style", "event", "agent", "project", "summary"}
    assert "fixture-secret" not in json.dumps(outbound)
