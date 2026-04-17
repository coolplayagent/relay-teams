from __future__ import annotations

from pathlib import Path

from relay_teams.hooks import HookLoader, HookEventName


def test_hook_loader_merges_user_project_and_local_precedence(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    project_root = tmp_path / "project"
    app_config_dir.mkdir()
    (project_root / ".relay-teams").mkdir(parents=True)
    (app_config_dir / "hooks.json").write_text(
        '{"hooks":{"PreToolUse":[{"matcher":"read","hooks":[{"type":"command","command":"user"}]}]}}',
        encoding="utf-8",
    )
    (project_root / ".relay-teams" / "hooks.json").write_text(
        '{"hooks":{"PreToolUse":[{"matcher":"write","hooks":[{"type":"command","command":"project"}]}]}}',
        encoding="utf-8",
    )
    (project_root / ".relay-teams" / "hooks.local.json").write_text(
        '{"hooks":{"PreToolUse":[{"matcher":"shell","hooks":[{"type":"command","command":"local"}]}]}}',
        encoding="utf-8",
    )

    loader = HookLoader(app_config_dir=app_config_dir, project_root=project_root)

    snapshot = loader.load_snapshot()

    groups = snapshot.hooks[HookEventName.PRE_TOOL_USE]
    assert [group.source.scope.value for group in groups] == [
        "project_local",
        "project",
        "user",
    ]
    assert [group.group.matcher for group in groups] == ["shell", "write", "read"]


def test_hook_loader_tolerates_invalid_runtime_file(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    (app_config_dir / "hooks.json").write_text('{"hooks":[]}', encoding="utf-8")

    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    snapshot = loader.load_snapshot()

    assert snapshot.hooks == {}
    assert len(snapshot.sources) == 1
