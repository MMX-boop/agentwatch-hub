import sys
from typing import Annotated, Optional

import typer

from agentwatch_notify.config import PERSONAS, config_path, load_settings
from agentwatch_notify.install import (
    claude_installed,
    codex_installed,
    install_claude,
    install_codex,
    uninstall_claude,
    uninstall_codex,
)
from agentwatch_notify.notify import dispatch
from agentwatch_notify.persona import render_notification
from agentwatch_notify.providers import send_bark

app = typer.Typer(
    name="agentwatch-notify",
    help="Send Codex and Claude Code completion notifications to Bark.",
    no_args_is_help=True,
)

DEFAULT_ENV = """NOTIFY_ENABLED=true
BARK_SERVER=https://api.day.app
BARK_DEVICE_KEY=
BARK_GROUP=AgentWatch
BARK_SOUND=minuet
BARK_LEVEL=active
BARK_ICON_URL=
PERSONA=boss
CLAUDE_PERSONA=
CODEX_PERSONA=
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_MAX_TOKENS=180
OUTBOUND_PROXY=
"""


def selected_agents(agent: str) -> tuple[str, ...]:
    value = agent.lower()
    if value == "all":
        return ("claude", "codex")
    if value not in {"claude", "codex"}:
        raise typer.BadParameter("agent must be claude, codex, or all")
    return (value,)


@app.command("init")
def init_config(force: bool = typer.Option(False, help="Replace the existing config after saving a backup.")):
    """Create ~/.agentwatch-notify/.env."""
    target = config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        typer.echo(f"Config already exists: {target}")
        raise typer.Exit(0)
    if target.exists():
        backup = target.with_suffix(".env.bak")
        backup.write_bytes(target.read_bytes())
    target.write_text(DEFAULT_ENV, encoding="utf-8")
    typer.echo(f"Created: {target}")
    typer.echo("Add your BARK_DEVICE_KEY, then run: agentwatch-notify install all")


@app.command()
def install(agent: Annotated[str, typer.Argument(help="claude, codex, or all")] = "all"):
    """Install reversible completion callbacks without replacing other Claude hooks."""
    if not config_path().exists():
        raise typer.BadParameter("Run `agentwatch-notify init` first")
    for item in selected_agents(agent):
        target = install_claude() if item == "claude" else install_codex()
        typer.echo(f"Installed {item}: {target}")
    typer.echo("Restart the selected CLI before testing.")


@app.command()
def uninstall(agent: Annotated[str, typer.Argument(help="claude, codex, or all")] = "all"):
    """Remove only AgentWatch callbacks and restore the previous Codex notify command."""
    for item in selected_agents(agent):
        target = uninstall_claude() if item == "claude" else uninstall_codex()
        typer.echo(f"Removed {item}: {target}")


@app.command()
def doctor():
    """Check local configuration without sending a notification."""
    target = config_path()
    configured = False
    error = ""
    try:
        settings = load_settings(target)
        configured = bool(settings.bark_device_key.get_secret_value())
    except Exception as exc:
        error = type(exc).__name__
    rows = (
        ("Config", "OK" if target.exists() and not error else error or "MISSING"),
        ("Bark key", "CONFIGURED" if configured else "MISSING"),
        ("Claude Code", "INSTALLED" if claude_installed() else "MISSING"),
        ("Codex", "INSTALLED" if codex_installed() else "MISSING"),
    )
    for label, status in rows:
        typer.echo(f"{label:12} {status}")


@app.command("test")
def test_notification():
    """Send one explicit Bark connection test."""
    settings = load_settings()
    accepted = send_bark(
        settings,
        {
            "title": "AgentWatch 已接通 ✅",
            "body": "Codex / Claude Code 的完成通知通道配置成功。",
        },
    )
    typer.echo(
        "Bark accepted the test notification." if accepted else "Bark did not accept the test notification."
    )
    if not accepted:
        raise typer.Exit(1)


@app.command()
def preview(
    provider: Annotated[str, typer.Option(help="claude or codex")] = "claude",
    result: Annotated[str, typer.Option(help="Example result summary")] = "测试和构建均已通过。",
):
    """Preview persona copy locally; this does not send Bark."""
    if provider not in {"claude", "codex"}:
        raise typer.BadParameter("provider must be claude or codex")
    settings = load_settings()
    message = render_notification(settings, provider, "demo-project", result)
    typer.echo(message["title"])
    typer.echo(message["body"])
    typer.echo("source: llm" if message["generated"] else "source: local template")


@app.command(hidden=True)
def notify(
    provider: Annotated[str, typer.Option(help="claude or codex")],
    payload: Optional[str] = typer.Argument(None),
):
    """Internal callback used by Codex and Claude Code."""
    try:
        raw = payload if payload is not None else getattr(sys.stdin, "buffer", sys.stdin).read(524_289)
        dispatch(provider, raw)
    except Exception:
        pass


@app.command()
def personas():
    """List available notification personalities."""
    for name in sorted(PERSONAS):
        typer.echo(name)


def main():
    app()
