import sys
from typing import Annotated, Optional

import typer

from agentwatch_notify.channels.base import NotificationMessage
from agentwatch_notify.channels.router import CHANNELS, ChannelRouter
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

app = typer.Typer(
    name="agentwatch-notify",
    help="Send Codex desktop and Claude Code completion notifications to Bark, QQ / OneBot and Feishu.",
    no_args_is_help=True,
)

DEFAULT_ENV = """NOTIFY_ENABLED=true
BARK_ENABLED=true
BARK_SERVER=https://api.day.app
BARK_DEVICE_KEY=
BARK_GROUP=AgentWatch
BARK_SOUND=minuet
BARK_LEVEL=active
BARK_ICON_URL=
QQ_ENABLED=false
ONEBOT_BASE_URL=http://127.0.0.1:3000
ONEBOT_ACCESS_TOKEN=
QQ_TARGETS=
FEISHU_ENABLED=false
FEISHU_WEBHOOK_URL=
FEISHU_WEBHOOK_SECRET=
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
    typer.echo("Configure Bark, QQ / OneBot or Feishu, then run: agentwatch-notify install all")


@app.command()
def install(agent: Annotated[str, typer.Argument(help="claude, codex, or all")] = "all"):
    """Install reversible completion callbacks without replacing other Claude hooks."""
    if not config_path().exists():
        raise typer.BadParameter("Run `agentwatch-notify init` first")
    for item in selected_agents(agent):
        target = install_claude() if item == "claude" else install_codex()
        typer.echo(f"Installed {item}: {target}")
    typer.echo("Fully quit and reopen the Codex desktop app, or restart your Claude Code CLI session.")


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
    settings = None
    error = ""
    try:
        settings = load_settings(target)
    except Exception as exc:
        error = type(exc).__name__
    router = ChannelRouter(settings) if settings is not None else None
    rows = (
        ("Config", "OK" if target.exists() and not error else error or "MISSING"),
        *((channel.label, router.status(channel) if router else "INVALID") for channel in CHANNELS),
        ("Claude Code", "INSTALLED" if claude_installed() else "MISSING"),
        ("Codex notify", "INSTALLED" if codex_installed() else "MISSING"),
    )
    for label, status in rows:
        typer.echo(f"{label:12} {status}")
    typer.echo(
        "Configuration check only. Verify delivery by completing a local task in the Codex desktop app."
    )


@app.command("test")
def test_notification(
    channel: Annotated[str, typer.Option(help="all, bark, qq, or feishu")] = "all",
):
    """Test enabled channels. Exit 0 if any accepts; 1 if none accepts."""
    if channel not in {"all", *(item.name for item in CHANNELS)}:
        raise typer.BadParameter("channel must be all, bark, qq, or feishu")
    try:
        settings = load_settings()
    except Exception as exc:
        # Configuration may contain a webhook URL; never print raw validation input.
        typer.echo(f"Config INVALID ({type(exc).__name__}); run agentwatch-notify doctor.")
        raise typer.Exit(1) from None
    router = ChannelRouter(settings)
    results = router.send(
        NotificationMessage("AgentWatch 已接通 ✅", "Codex / Claude Code 的完成通知通道测试。"), channel
    )
    for item in CHANNELS:
        if channel == "all" or channel == item.name:
            status = (
                ("OK" if results[item.name] else "FAILED") if item.name in results else router.status(item)
            )
            typer.echo(f"{item.label:12} {status}")
    if not any(results.values()):
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
