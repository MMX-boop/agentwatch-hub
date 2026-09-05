import httpx
import pytest

from agentwatch_notify.config import Settings


@pytest.fixture(autouse=True)
def isolated_notification_environment(monkeypatch, tmp_path):
    """Tests must never use a developer's real notification settings or network."""
    for field in Settings.model_fields.values():
        if isinstance(field.validation_alias, str):
            monkeypatch.delenv(field.validation_alias, raising=False)
    monkeypatch.setenv("AGENTWATCH_NOTIFY_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))

    def forbidden(*args, **kwargs):
        raise AssertionError("Real HTTP is forbidden in tests; provide MockTransport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
