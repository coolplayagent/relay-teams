from __future__ import annotations

from pathlib import Path

from relay_teams.hooks import HookConfigLoader, HookEventName, HookHandlerType


def test_hook_config_loader_reads_hooks_json(tmp_path: Path) -> None:
    config_path = tmp_path / "hooks.json"
    config_path.write_text(
        '{"hooks":{"PreToolUse":[{"matcher":"read","hooks":[{"type":"command","command":"echo ok"}]}]}}',
        encoding="utf-8",
    )

    config = HookConfigLoader(config_dir=tmp_path).load()

    groups = config.groups_for(HookEventName.PRE_TOOL_USE)
    assert len(groups) == 1
    assert groups[0].matcher == "read"
    assert len(groups[0].hooks) == 1
    assert groups[0].hooks[0].type == HookHandlerType.COMMAND
    assert groups[0].hooks[0].command == "echo ok"
