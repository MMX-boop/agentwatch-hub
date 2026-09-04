import re

ASSIGNMENT = re.compile(
    r"""(?ix)([\w-]*(?:password|passwd|secret|token|api[_-]?key|device[_-]?key)[\w-]*["']?\s*(?:=|:|\s)\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}]+)"""
)
HEADER = re.compile(r"(?im)((?:authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+")
BEARER = re.compile(r"(?i)\b(Bearer\s+)[a-z0-9._~+/=-]{8,}")
KEY = re.compile(r"\b(?:sk-[a-zA-Z0-9_-]{12,}|gh[pousr]_[a-zA-Z0-9]{16,}|AKIA[A-Z0-9]{16})")


class Sanitizer:
    def __init__(self, secrets: list[str] | None = None):
        self.secrets = secrets or []

    def text(self, value: str) -> str:
        value = re.sub(r"[\ud800-\udfff]", "\ufffd", str(value))
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = HEADER.sub(r"\1[REDACTED]", value)
        value = BEARER.sub(r"\1[REDACTED]", value)
        value = ASSIGNMENT.sub(r"\1[REDACTED]", value)
        value = KEY.sub("[REDACTED]", value)
        value = re.sub(
            r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)",
            "[REDACTED PRIVATE KEY]",
            value,
        )
        return value
