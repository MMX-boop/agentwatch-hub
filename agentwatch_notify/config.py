import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PERSONAS = {
    "off",
    "boss",
    "heir_male",
    "heir_female",
    "emperor",
    "palace",
    "butler",
    "strategist",
    "bestie",
    "cat",
    "cyber",
    "detective",
}


def config_home() -> Path:
    custom = os.environ.get("AGENTWATCH_NOTIFY_HOME")
    return Path(custom).expanduser() if custom else Path.home() / ".agentwatch-notify"


def config_path() -> Path:
    return config_home() / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True, hide_input_in_errors=True)

    notify_enabled: bool = Field(True, validation_alias="NOTIFY_ENABLED")
    bark_enabled: bool = Field(True, validation_alias="BARK_ENABLED")
    bark_server: str = Field("https://api.day.app", validation_alias="BARK_SERVER")
    bark_device_key: SecretStr = Field(SecretStr(""), validation_alias="BARK_DEVICE_KEY")
    bark_group: str = Field("AgentWatch", validation_alias="BARK_GROUP")
    bark_sound: str = Field("minuet", validation_alias="BARK_SOUND")
    bark_level: str = Field("active", validation_alias="BARK_LEVEL")
    bark_icon_url: str = Field("", validation_alias="BARK_ICON_URL")

    qq_enabled: bool = Field(False, validation_alias="QQ_ENABLED")
    onebot_base_url: str = Field("http://127.0.0.1:3000", validation_alias="ONEBOT_BASE_URL")
    onebot_access_token: SecretStr = Field(SecretStr(""), validation_alias="ONEBOT_ACCESS_TOKEN")
    qq_targets: str = Field("", validation_alias="QQ_TARGETS", repr=False)

    feishu_enabled: bool = Field(False, validation_alias="FEISHU_ENABLED")
    feishu_webhook_url: SecretStr = Field(SecretStr(""), validation_alias="FEISHU_WEBHOOK_URL")
    feishu_webhook_secret: SecretStr = Field(SecretStr(""), validation_alias="FEISHU_WEBHOOK_SECRET")

    persona: str = Field("boss", validation_alias="PERSONA")
    claude_persona: str = Field("", validation_alias="CLAUDE_PERSONA")
    codex_persona: str = Field("", validation_alias="CODEX_PERSONA")

    llm_base_url: str = Field("", validation_alias="LLM_BASE_URL")
    llm_api_key: SecretStr = Field(SecretStr(""), validation_alias="LLM_API_KEY")
    llm_model: str = Field("", validation_alias="LLM_MODEL")
    llm_max_tokens: int = Field(180, ge=64, le=512, validation_alias="LLM_MAX_TOKENS")
    outbound_proxy: str = Field("", validation_alias="OUTBOUND_PROXY")

    @field_validator("persona", "claude_persona", "codex_persona")
    @classmethod
    def valid_persona(cls, value: str) -> str:
        value = value.strip().lower()
        if value and value not in PERSONAS:
            raise ValueError(f"Unknown persona: {value}")
        return value

    @field_validator("bark_server")
    @classmethod
    def safe_bark_server(cls, value: str) -> str:
        return safe_endpoint(value, "BARK_SERVER")

    @field_validator("onebot_base_url")
    @classmethod
    def safe_onebot_url(cls, value: str) -> str:
        return safe_endpoint(value, "ONEBOT_BASE_URL", local_http=True)

    @field_validator("feishu_webhook_url")
    @classmethod
    def safe_feishu_url(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value():
            safe_endpoint(value.get_secret_value(), "FEISHU_WEBHOOK_URL")
        return value

    @field_validator("qq_targets")
    @classmethod
    def valid_targets(cls, value: str) -> str:
        if not value.strip():
            return ""
        targets = list(dict.fromkeys(part.strip() for part in value.split(",")))
        if len(targets) > 8 or any(
            not re.fullmatch(r"(?:private|group):[1-9][0-9]{0,18}", target) for target in targets
        ):
            raise ValueError(
                "QQ_TARGETS requires up to 8 private:<positive ID> or group:<positive ID> entries"
            )
        if any(int(target.split(":")[1]) > 2**63 - 1 for target in targets):
            raise ValueError("QQ target ID exceeds signed 64-bit range")
        return ",".join(targets)

    @model_validator(mode="after")
    def remote_onebot_auth(self) -> "Settings":
        if (
            self.qq_enabled
            and urlsplit(self.onebot_base_url).hostname not in {"localhost", "127.0.0.1", "::1"}
            and not self.onebot_access_token.get_secret_value()
        ):
            raise ValueError("Remote OneBot requires ONEBOT_ACCESS_TOKEN")
        return self

    @field_validator("llm_base_url")
    @classmethod
    def safe_llm_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or (parsed.scheme == "http" and not local)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("LLM_BASE_URL requires HTTPS, except for localhost")
        return value.rstrip("/")

    @field_validator("outbound_proxy")
    @classmethod
    def safe_proxy(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("OUTBOUND_PROXY must be an HTTP(S) origin without credentials")
        return value.rstrip("/")

    def persona_for(self, provider: str) -> str:
        override = self.claude_persona if provider == "claude" else self.codex_persona
        return override or self.persona

    @property
    def secrets(self) -> list[str]:
        values = (
            self.bark_device_key,
            self.llm_api_key,
            self.onebot_access_token,
            self.feishu_webhook_url,
            self.feishu_webhook_secret,
        )
        return [value.get_secret_value() for value in values if value.get_secret_value()]


def safe_endpoint(value: str, label: str, local_http: bool = False) -> str:
    """Reject credentials/query/fragment and keep errors independent of secret input."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        valid = (
            bool(parsed.hostname)
            and (parsed.scheme == "https" or (local_http and local and parsed.scheme == "http"))
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and "?" not in value
            and "#" not in value
            and not any(char.isspace() or ord(char) < 32 for char in value)
            and "\\" not in value
            and (port is None or port > 0)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            f"{label} requires HTTPS (HTTP allowed only for local OneBot), without credentials/query/fragment"
        )
    return value.rstrip("/")


def load_settings(path: Path | None = None) -> Settings:
    path = path or config_path()
    return Settings(_env_file=path, _env_file_encoding="utf-8")
