import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import tomlkit
from filelock import FileLock

from agentwatch_notify.config import config_home
from agentwatch_notify.notify import MODULE

EVENTS = ("Stop", "StopFailure")


def atomic_write(path: Path, content: str, backup: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text("utf-8-sig") == content:
        return
    if backup and path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, path.with_name(path.name + f".agentwatch-notify-{stamp}.bak"))
    temporary = path.with_name(path.name + ".agentwatch-notify.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def python_command(gui: bool = False, executable: Path | None = None) -> Path:
    value = executable or Path(sys.executable)
    if gui and os.name == "nt" and value.with_name("pythonw.exe").is_file():
        return value.with_name("pythonw.exe")
    return value.resolve()


def codex_config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"


def claude_config_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "settings.json"


def state_path() -> Path:
    return config_home() / "install-state.json"


def command(provider: str, gui: bool = False, executable: Path | None = None) -> list[str]:
    return [
        str(python_command(gui, executable)),
        "-m",
        MODULE,
        "notify",
        "--provider",
        provider,
    ]


def install_codex(
    target: Path | None = None,
    state: Path | None = None,
    executable: Path | None = None,
) -> Path:
    target = target or codex_config_path()
    state = state or state_path()
    wanted = command("codex", gui=True, executable=executable)
    with FileLock(str(target) + ".agentwatch-notify.lock"):
        data = tomlkit.parse(target.read_text("utf-8") if target.exists() else "")
        previous = data.get("notify")
        if previous is not None and (
            not isinstance(previous, list) or not all(isinstance(item, str) for item in previous)
        ):
            raise ValueError("Existing Codex notify setting is not a command array")
        if previous and MODULE in previous:
            if list(previous) != wanted:
                raise ValueError("Another AgentWatch installation already owns Codex notify")
            return target
        state.parent.mkdir(parents=True, exist_ok=True)
        saved = {}
        if state.exists():
            try:
                saved = json.loads(state.read_text("utf-8"))
            except (OSError, ValueError, TypeError):
                saved = {}
        saved["previous_codex_notify"] = list(previous) if previous is not None else None
        atomic_write(state, json.dumps(saved, ensure_ascii=False, indent=2) + "\n", backup=False)
        data["notify"] = wanted
        atomic_write(target, tomlkit.dumps(data))
    return target


def uninstall_codex(target: Path | None = None, state: Path | None = None) -> Path:
    target = target or codex_config_path()
    state = state or state_path()
    with FileLock(str(target) + ".agentwatch-notify.lock"):
        data = tomlkit.parse(target.read_text("utf-8"))
        if MODULE not in list(data.get("notify", [])):
            raise ValueError("AgentWatch does not own the current Codex notify setting")
        saved = json.loads(state.read_text("utf-8"))
        previous = saved.get("previous_codex_notify")
        if previous is None:
            del data["notify"]
        else:
            data["notify"] = previous
        atomic_write(target, tomlkit.dumps(data))
    return target


def owns_claude(handler: dict) -> bool:
    if not isinstance(handler, dict):
        return False
    value = str(handler.get("command", "")) + " " + " ".join(handler.get("args", []))
    return MODULE in value and "notify" in value


def install_claude(target: Path | None = None, executable: Path | None = None) -> Path:
    target = target or claude_config_path()
    with FileLock(str(target) + ".agentwatch-notify.lock"):
        data = json.loads(target.read_text("utf-8-sig")) if target.exists() else {}
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise ValueError("Claude settings has an invalid hooks object")
        for event in EVENTS:
            groups = hooks.setdefault(event, [])
            if not any(owns_claude(handler) for group in groups for handler in group.get("hooks", [])):
                argv = command("claude", executable=executable)
                groups.append(
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": argv[0],
                                "args": argv[1:],
                                "timeout": 20,
                            }
                        ]
                    }
                )
        atomic_write(target, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return target


def uninstall_claude(target: Path | None = None) -> Path:
    target = target or claude_config_path()
    with FileLock(str(target) + ".agentwatch-notify.lock"):
        data = json.loads(target.read_text("utf-8-sig"))
        hooks = data.get("hooks", {})
        for event, groups in list(hooks.items()):
            kept = []
            for group in groups:
                handlers = [handler for handler in group.get("hooks", []) if not owns_claude(handler)]
                if handlers:
                    kept.append({**group, "hooks": handlers})
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
        atomic_write(target, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return target


def codex_installed(target: Path | None = None) -> bool:
    target = target or codex_config_path()
    try:
        return MODULE in list(tomlkit.parse(target.read_text("utf-8")).get("notify", []))
    except (OSError, ValueError, TypeError):
        return False


def claude_installed(target: Path | None = None) -> bool:
    target = target or claude_config_path()
    try:
        hooks = json.loads(target.read_text("utf-8-sig")).get("hooks", {})
        return all(
            any(owns_claude(handler) for group in hooks.get(event, []) for handler in group.get("hooks", []))
            for event in EVENTS
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False
