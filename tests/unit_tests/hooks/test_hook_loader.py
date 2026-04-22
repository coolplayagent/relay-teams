from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.hooks import HookLoader, HookEventName
from relay_teams.hooks.hook_models import HooksConfig
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.skills.skill_models import Skill, SkillMetadata, SkillSource


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
    assert snapshot.sources == ()


def test_hook_loader_includes_role_and_skill_hooks(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    role_path = tmp_path / "roles" / "reviewer.md"
    role_path.parent.mkdir()
    role_registry.register(
        RoleDefinition(
            role_id="Reviewer",
            name="Reviewer",
            description="review role",
            version="1",
            tools=(),
            skills=("app:guardrail",),
            system_prompt="review",
            hooks=HooksConfig.model_validate(
                {
                    "hooks": {
                        "TaskCompleted": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "role-hook",
                                    }
                                ]
                            }
                        ]
                    }
                }
            ),
            source_path=role_path,
        )
    )

    class _SkillRegistry:
        def list_skill_definitions(self) -> tuple[Skill, ...]:
            return (
                Skill(
                    ref="app:guardrail",
                    metadata=SkillMetadata(
                        name="guardrail",
                        description="skill",
                        instructions="do guardrail work",
                        hooks=HooksConfig.model_validate(
                            {
                                "hooks": {
                                    "PreToolUse": [
                                        {
                                            "matcher": "shell",
                                            "hooks": [
                                                {
                                                    "type": "command",
                                                    "command": "skill-hook",
                                                }
                                            ],
                                        }
                                    ]
                                }
                            }
                        ),
                    ),
                    directory=tmp_path / "skills" / "guardrail",
                    source=SkillSource.USER_RELAY_TEAMS,
                ),
            )

    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=None,
        get_role_registry=lambda: role_registry,
        get_skill_registry=lambda: _SkillRegistry(),
    )

    snapshot = loader.load_snapshot()

    task_groups = snapshot.hooks[HookEventName.TASK_COMPLETED]
    assert len(task_groups) == 1
    assert task_groups[0].source.scope.value == "role"
    assert task_groups[0].group.role_ids == ("Reviewer",)

    tool_groups = snapshot.hooks[HookEventName.PRE_TOOL_USE]
    assert len(tool_groups) == 1
    assert tool_groups[0].source.scope.value == "skill"
    assert tool_groups[0].group.role_ids == ("Reviewer",)


def test_hook_loader_validate_payload_rejects_unknown_agent_role(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="Verifier",
            name="Verifier",
            description="verifies output",
            version="1",
            tools=(),
            system_prompt="verify",
        )
    )
    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=None,
        get_role_registry=lambda: role_registry,
    )

    with pytest.raises(ValueError, match="Unknown agent hook role_id: MissingRole"):
        loader.validate_payload(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "agent",
                                    "role_id": "MissingRole",
                                    "prompt": "review $ARGUMENTS",
                                }
                            ]
                        }
                    ]
                }
            }
        )


def test_hook_loader_tolerates_invalid_agent_role_reference_at_runtime(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    (app_config_dir / "hooks.json").write_text(
        '{"hooks":{"Stop":[{"hooks":[{"type":"agent","role_id":"MissingRole","prompt":"review"}]}]}}',
        encoding="utf-8",
    )
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="Verifier",
            name="Verifier",
            description="verifies output",
            version="1",
            tools=(),
            system_prompt="verify",
        )
    )
    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=None,
        get_role_registry=lambda: role_registry,
    )

    snapshot = loader.load_snapshot()

    assert snapshot.hooks == {}
