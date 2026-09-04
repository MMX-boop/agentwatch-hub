import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
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
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    notify_enabled: bool = Field(True, validation_alias="NOTIFY_ENABLED")
    bark_server: str = Field("https://api.day.app", validation_alias="BARK_SERVER")
    bark_device_key: SecretStr = Field(SecretStr(""), validation_alias="BARK_DEVICE_KEY")
    bark_group: str = Field("AgentWatch", validation_alias="BARK_GROUP")
    bark_sound: str = Field("minuet", validation_alias="BARK_SOUND")
    bark_level: str = Field("active", validation_alias="BARK_LEVEL")
    bark_icon_url: str = Field("", validation_alias="BARK_ICON_URL")

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
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("BARK_SERVER must be an HTTPS origin")
        return value.rstrip("/")

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
        values = (self.bark_device_key, self.llm_api_key)
        return [value.get_secret_value() for value in values if value.get_secret_value()]


def load_settings(path: Path | None = None) -> Settings:
    path = path or config_path()
    return Settings(_env_file=path, _env_file_encoding="utf-8")
