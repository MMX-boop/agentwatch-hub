import json
import sys
from pathlib import Path

import tomlkit

from agentwatch_notify.install import (
    claude_installed,
    codex_installed,
    install_claude,
    install_codex,
    uninstall_claude,
    uninstall_codex,
)


def test_codex_install_preserves_and_restores_previous_notifier(tmp_path):
    target = tmp_path / "config.toml"
    state = tmp_path / "state.json"
    target.write_text('model = "fixture"\nnotify = ["old-notifier", "--flag"]\n', encoding="utf-8")
    install_codex(target, state, Path(sys.executable))
    assert codex_installed(target)
    assert json.loads(state.read_text("utf-8"))["previous_codex_notify"] == ["old-notifier", "--flag"]
    install_codex(target, state, Path(sys.executable))
    uninstall_codex(target, state)
    restored = tomlkit.parse(target.read_text("utf-8"))
    assert list(restored["notify"]) == ["old-notifier", "--flag"]
    assert restored["model"] == "fixture"


def test_claude_install_merges_and_removes_only_our_hooks(tmp_path):
    target = tmp_path / "settings.json"
    original = {
        "model": "fixture",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "my-hook"}]}]},
    }
    target.write_text(json.dumps(original), encoding="utf-8")
    install_claude(target, Path(sys.executable))
    assert claude_installed(target)
    install_claude(target, Path(sys.executable))
    uninstall_claude(target)
    assert json.loads(target.read_text("utf-8")) == original
