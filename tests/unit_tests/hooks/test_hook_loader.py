from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.hooks import HookLoader, HookEventName
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from typing import cast

from relay_teams.skills.skill_models import Skill, SkillMetadata, SkillScope
from relay_teams.skills.skill_registry import SkillRegistry


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

    loader = HookLoader(
        app_config_dir=app_config_dir, project_root=tmp_path / "missing-project"
    )

    snapshot = loader.load_snapshot()

    assert snapshot.hooks == {}
    assert len(snapshot.sources) == 1


def test_hook_loader_reads_utf8_bom_runtime_file(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    (app_config_dir / "hooks.json").write_text(
        '\ufeff{"hooks":{"TaskCreated":[{"hooks":[{"type":"command","command":"from-bom"}]}]}}',
        encoding="utf-8",
    )

    loader = HookLoader(
        app_config_dir=app_config_dir, project_root=tmp_path / "missing-project"
    )

    snapshot = loader.load_snapshot()

    groups = snapshot.hooks[HookEventName.TASK_CREATED]
    assert len(groups) == 1
    assert groups[0].group.hooks[0].command == "from-bom"


class _FakeSkillRegistry:
    def __init__(self, skills: tuple[Skill, ...]) -> None:
        self._skills = skills

    def resolve_known(
        self,
        skill_names: tuple[str, ...],
        *,
        strict: bool = True,
        consumer: str | None = None,
    ) -> tuple[str, ...]:
        _ = (strict, consumer)
        return skill_names

    def list_skill_definitions(self) -> tuple[Skill, ...]:
        return self._skills


def _build_role_registry_with_embedded_hooks(tmp_path: Path) -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Primary role.",
            version="1.0.0",
            tools=(),
            skills=("app:review-skill",),
            hooks={
                "TaskCreated": [
                    {"hooks": [{"type": "command", "command": "role-created"}]}
                ]
            },
            system_prompt="Main agent prompt.",
            source_path=tmp_path / "MainAgent.md",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Reviewer",
            name="Reviewer",
            description="Reviews outputs.",
            version="1.0.0",
            tools=(),
            system_prompt="Review outputs.",
            source_path=tmp_path / "Reviewer.md",
        )
    )
    return registry


def test_hook_loader_merges_role_and_skill_embedded_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    monkeypatch.setattr(
        "relay_teams.hooks.hook_loader.LOGGER.warning", lambda *args, **kwargs: None
    )
    role_registry = _build_role_registry_with_embedded_hooks(tmp_path)
    skill_registry = _FakeSkillRegistry(
        (
            Skill(
                ref="app:review-skill",
                metadata=SkillMetadata(
                    name="review-skill",
                    description="Review skill",
                    instructions="Use when reviewing.",
                    hooks={
                        "SubagentStart": [
                            {"hooks": [{"type": "command", "command": "skill-start"}]}
                        ]
                    },
                ),
                directory=tmp_path / "skills" / "review-skill",
                scope=SkillScope.APP,
            ),
        )
    )
    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=tmp_path / "missing-project",
        get_role_registry=lambda: role_registry,
        get_skill_registry=lambda: cast(SkillRegistry, skill_registry),
    )

    snapshot = loader.load_snapshot()

    task_groups = snapshot.hooks[HookEventName.TASK_CREATED]
    assert len(task_groups) == 1
    assert task_groups[0].source.scope.value == "role"
    assert task_groups[0].group.role_ids == ("MainAgent",)

    subagent_groups = snapshot.hooks[HookEventName.SUBAGENT_START]
    assert len(subagent_groups) == 1
    assert subagent_groups[0].source.scope.value == "skill"
    assert subagent_groups[0].group.role_ids == ("MainAgent",)


def test_hook_loader_skips_unattached_skill_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    monkeypatch.setattr(
        "relay_teams.hooks.hook_loader.LOGGER.warning", lambda *args, **kwargs: None
    )
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Primary role.",
            version="1.0.0",
            tools=(),
            system_prompt="Main agent prompt.",
            source_path=tmp_path / "MainAgent.md",
        )
    )
    skill_registry = _FakeSkillRegistry(
        (
            Skill(
                ref="app:unused-skill",
                metadata=SkillMetadata(
                    name="unused-skill",
                    description="Unused skill",
                    instructions="Unused.",
                    hooks={
                        "TaskCreated": [
                            {"hooks": [{"type": "command", "command": "skill-created"}]}
                        ]
                    },
                ),
                directory=tmp_path / "skills" / "unused-skill",
                scope=SkillScope.APP,
            ),
        )
    )
    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=tmp_path / "missing-project",
        get_role_registry=lambda: role_registry,
        get_skill_registry=lambda: cast(SkillRegistry, skill_registry),
    )

    snapshot = loader.load_snapshot()

    assert HookEventName.TASK_CREATED not in snapshot.hooks


def test_hook_loader_validate_payload_accepts_supported_agent_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    monkeypatch.setattr(
        "relay_teams.hooks.hook_loader.LOGGER.warning", lambda *args, **kwargs: None
    )
    role_registry = _build_role_registry_with_embedded_hooks(tmp_path)
    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=tmp_path / "missing-project",
        get_role_registry=lambda: role_registry,
    )

    config = loader.validate_payload(
        {
            "hooks": {
                "TaskCreated": [
                    {
                        "hooks": [
                            {
                                "type": "agent",
                                "role_id": "MainAgent",
                                "prompt": "Review the new task.",
                            }
                        ]
                    }
                ]
            }
        }
    )

    assert config.hooks[HookEventName.TASK_CREATED][0].hooks[0].type.value == "agent"


def test_hook_loader_validate_payload_rejects_unsupported_prompt_event(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=tmp_path / "missing-project",
    )

    with pytest.raises(ValueError, match="PreCompact does not support prompt hooks"):
        loader.validate_payload(
            {
                "hooks": {
                    "PreCompact": [
                        {
                            "hooks": [
                                {
                                    "type": "prompt",
                                    "prompt": "Review the compaction request.",
                                }
                            ]
                        }
                    ]
                }
            }
        )


def test_hook_loader_validate_payload_rejects_session_start_http_hook(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=tmp_path / "missing-project",
    )

    with pytest.raises(ValueError, match="SessionStart does not support http hooks"):
        loader.validate_payload(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [
                                {
                                    "type": "http",
                                    "url": "https://example.test/hooks/start",
                                }
                            ],
                        }
                    ]
                }
            }
        )


def test_hook_loader_validate_payload_rejects_unimplemented_async_runtime_option(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=tmp_path / "missing-project",
    )

    with pytest.raises(ValueError, match="does not support async execution yet"):
        loader.validate_payload(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "write",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "after-write",
                                    "run_async": True,
                                }
                            ],
                        }
                    ]
                }
            }
        )


def test_hook_loader_runtime_sanitizes_unknown_agent_role_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    monkeypatch.setattr(
        "relay_teams.hooks.hook_loader.LOGGER.warning",
        lambda *args, **kwargs: None,
    )
    role_registry = _build_role_registry_with_embedded_hooks(tmp_path)
    (app_config_dir / "hooks.json").write_text(
        '{"hooks":{"TaskCreated":[{"hooks":[{"type":"agent","role_id":"MissingRole","prompt":"Review."}]}]}}',
        encoding="utf-8",
    )
    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=tmp_path / "missing-project",
        get_role_registry=lambda: role_registry,
    )

    snapshot = loader.load_snapshot()

    task_created_groups = snapshot.hooks[HookEventName.TASK_CREATED]
    assert len(task_created_groups) == 1
    assert task_created_groups[0].source.scope.value == "role"
    assert task_created_groups[0].group.role_ids == ("MainAgent",)


def test_hook_loader_skill_hooks_do_not_expand_beyond_attached_roles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    monkeypatch.setattr(
        "relay_teams.hooks.hook_loader.LOGGER.warning", lambda *args, **kwargs: None
    )
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Primary role.",
            version="1.0.0",
            tools=(),
            system_prompt="Main agent prompt.",
            source_path=tmp_path / "MainAgent.md",
            skills=("app:review-skill",),
        )
    )
    skill_registry = _FakeSkillRegistry(
        (
            Skill(
                ref="app:review-skill",
                metadata=SkillMetadata(
                    name="review-skill",
                    description="Review skill",
                    instructions="Use when reviewing.",
                    hooks={
                        "SubagentStart": [
                            {
                                "role_ids": ["OtherRole", "MainAgent"],
                                "hooks": [
                                    {"type": "command", "command": "skill-start"}
                                ],
                            }
                        ]
                    },
                ),
                directory=tmp_path / "skills" / "review-skill",
                scope=SkillScope.APP,
            ),
        )
    )
    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=tmp_path / "missing-project",
        get_role_registry=lambda: role_registry,
        get_skill_registry=lambda: cast(SkillRegistry, skill_registry),
    )

    snapshot = loader.load_snapshot()

    subagent_groups = snapshot.hooks[HookEventName.SUBAGENT_START]
    assert len(subagent_groups) == 1
    assert subagent_groups[0].group.role_ids == ("MainAgent",)


def test_hook_loader_runtime_sanitizes_unsupported_handler_and_runtime_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    monkeypatch.setattr(
        "relay_teams.hooks.hook_loader.LOGGER.warning",
        lambda *args, **kwargs: None,
    )
    (app_config_dir / "hooks.json").write_text(
        '{"hooks":{"PreCompact":[{"hooks":[{"type":"prompt","prompt":"Review."}]}],"PostToolUse":[{"matcher":"write","hooks":[{"type":"command","command":"after-write","run_async":true}]}]}}',
        encoding="utf-8",
    )
    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=tmp_path / "missing-project",
    )

    snapshot = loader.load_snapshot()

    assert snapshot.hooks == {}
