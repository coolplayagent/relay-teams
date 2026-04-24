from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError, model_validator

from relay_teams.hooks import HookLoader, HookEventName
from relay_teams.hooks.hook_loader import (
    _format_validation_error,
    _validate_handler_event_compatibility,
    _normalize_hook_group,
    filter_tolerant_hook_groups,
    normalize_hooks_payload,
    parse_tolerant_hooks_payload,
)
from relay_teams.hooks.hook_models import (
    HookHandlerConfig,
    HookHandlerType,
    HookMatcherGroup,
    HooksConfig,
)
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


def test_hook_loader_skips_empty_role_and_skill_groups_at_runtime(
    tmp_path: Path,
) -> None:
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
                            {"hooks": []},
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "role-hook",
                                    }
                                ]
                            },
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
                                        {"hooks": []},
                                        {
                                            "matcher": "shell",
                                            "hooks": [
                                                {
                                                    "type": "command",
                                                    "command": "skill-hook",
                                                }
                                            ],
                                        },
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

    assert len(snapshot.hooks[HookEventName.TASK_COMPLETED]) == 1
    assert len(snapshot.hooks[HookEventName.PRE_TOOL_USE]) == 1


def test_hook_loader_expands_wildcard_role_skills_for_skill_hooks(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="primary role",
            version="1",
            tools=(),
            skills=("*", "app:guardrail"),
            system_prompt="run",
        )
    )

    def _skill(ref: str, command: str) -> Skill:
        return Skill(
            ref=ref,
            metadata=SkillMetadata(
                name=ref.rsplit(":", maxsplit=1)[-1],
                description="skill",
                instructions="use hook",
                hooks=HooksConfig.model_validate(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": command,
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": command,
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ),
            ),
            directory=tmp_path / "skills" / ref.replace(":", "_"),
            source=SkillSource.USER_RELAY_TEAMS,
        )

    class _SkillRegistry:
        def list_skill_definitions(self) -> tuple[Skill, ...]:
            return (
                _skill("app:guardrail", "shell"),
                _skill("app:format", "write"),
            )

    loader = HookLoader(
        app_config_dir=app_config_dir,
        project_root=None,
        get_role_registry=lambda: role_registry,
        get_skill_registry=lambda: _SkillRegistry(),
    )

    snapshot = loader.load_snapshot()

    tool_groups = snapshot.hooks[HookEventName.PRE_TOOL_USE]
    assert [group.group.matcher for group in tool_groups] == ["shell", "write"]
    assert [group.group.role_ids for group in tool_groups] == [
        ("MainAgent",),
        ("MainAgent",),
    ]


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


def test_hook_loader_validate_payload_migrates_legacy_group_fields(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    config = loader.validate_payload(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "",
                        "if_condition": "Bash(git *)",
                        "tool_names": ["Read", "Write"],
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo ok",
                            }
                        ],
                    }
                ]
            }
        }
    )

    groups = config.hooks[HookEventName.PRE_TOOL_USE]
    assert [group.matcher for group in groups] == ["Read", "Write"]
    assert all(group.hooks[0].if_rule == "Bash(git *)" for group in groups)


def test_hook_loader_validate_payload_migrates_legacy_tool_names_when_matcher_is_wildcard(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    config = loader.validate_payload(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "tool_names": ["Read", "Write"],
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo ok",
                            }
                        ],
                    }
                ]
            }
        }
    )

    groups = config.hooks[HookEventName.PRE_TOOL_USE]
    assert [group.matcher for group in groups] == ["Read", "Write"]


def test_hook_loader_validate_payload_deduplicates_legacy_tool_names(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    config = loader.validate_payload(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "tool_names": ["Read", "Read", "Write", "Read"],
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo ok",
                            }
                        ],
                    }
                ]
            }
        }
    )

    groups = config.hooks[HookEventName.PRE_TOOL_USE]
    assert [group.matcher for group in groups] == ["Read", "Write"]


def test_hook_loader_validate_payload_preserves_legacy_tool_names_matcher_intersection(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    config = loader.validate_payload(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read*",
                        "tool_names": ["Read", "Review", "Write"],
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo ok",
                            }
                        ],
                    }
                ]
            }
        }
    )

    groups = config.hooks[HookEventName.PRE_TOOL_USE]
    assert [group.matcher for group in groups] == ["Read"]


def test_hook_loader_validate_payload_rejects_unrepresentable_legacy_tool_names(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    with pytest.raises(ValueError, match="tool_names"):
        loader.validate_payload(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Read*",
                            "tool_names": ["Write"],
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                }
                            ],
                        }
                    ]
                }
            }
        )


def test_hook_loader_validate_payload_rejects_malformed_entries_instead_of_dropping_them(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    with pytest.raises(ValueError, match="hooks.PreToolUse.0.hooks.1"):
        loader.validate_payload(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Write",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                },
                                "not-a-handler",
                            ],
                        }
                    ]
                }
            }
        )


def test_hook_loader_validate_payload_rejects_matcher_for_unsupported_event(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    with pytest.raises(ValueError, match="Matcher is not supported for Stop hooks"):
        loader.validate_payload(
            {
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "manual",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo stop",
                                }
                            ],
                        }
                    ]
                }
            }
        )


def test_hook_loader_validate_payload_rejects_if_for_non_tool_event(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    with pytest.raises(
        ValueError,
        match="Hook handler 'if' is only supported for tool events, not Stop",
    ):
        loader.validate_payload(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo stop",
                                    "if": "Bash(git *)",
                                }
                            ],
                        }
                    ]
                }
            }
        )


def test_hook_loader_validate_payload_rejects_prompt_session_start_handler(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    with pytest.raises(
        ValueError,
        match="SessionStart only supports command hook handlers",
    ):
        loader.validate_payload(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [
                                {
                                    "type": "prompt",
                                    "prompt": "review startup",
                                }
                            ],
                        }
                    ]
                }
            }
        )


def test_hook_loader_validate_payload_formats_model_validation_errors(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    with pytest.raises(
        ValueError,
        match=r"hooks\.PreToolUse\.0\.hooks\.0: Value error, command hook requires command",
    ):
        loader.validate_payload(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Write",
                            "hooks": [
                                {
                                    "type": "command",
                                }
                            ],
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


def test_hook_loader_tolerates_invalid_group_without_dropping_valid_runtime_groups(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    (app_config_dir / "hooks.json").write_text(
        (
            '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"echo ok"}]}],'
            '"SessionStart":[{"hooks":[{"type":"prompt","prompt":"review startup"}]}]}}'
        ),
        encoding="utf-8",
    )
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    snapshot = loader.load_snapshot()

    assert HookEventName.STOP in snapshot.hooks
    assert HookEventName.SESSION_START not in snapshot.hooks


def test_hook_loader_tolerant_load_rejects_non_mapping_payload_and_non_mapping_hooks(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    invalid_file = app_config_dir / "hooks.json"
    invalid_file.write_text('["not-a-dict"]', encoding="utf-8")
    assert loader.get_user_config() == HooksConfig()

    invalid_file.write_text('{"hooks":[]}', encoding="utf-8")
    assert loader.get_user_config() == HooksConfig()


def test_hook_loader_tolerant_load_skips_invalid_event_group_mappings(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    (app_config_dir / "hooks.json").write_text(
        (
            '{"hooks":{"PreToolUse":[{"matcher":"Read","hooks":[{"type":"command","command":"echo ok"}]}],'
            '"Stop":{"hooks":[{"type":"command","command":"echo invalid"}]},'
            '"123":[{"hooks":[{"type":"command","command":"echo ignored"}]}]}}'
        ),
        encoding="utf-8",
    )
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    snapshot = loader.load_snapshot()

    assert tuple(snapshot.hooks) == (HookEventName.PRE_TOOL_USE,)
    assert snapshot.hooks[HookEventName.PRE_TOOL_USE][0].group.matcher == "Read"


def test_hook_loader_tolerant_load_skips_invalid_event_name_and_invalid_group_entries(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    (app_config_dir / "hooks.json").write_text(
        (
            '{"hooks":{"PreToolUse":[{"matcher":"Read","hooks":[{"type":"command","command":"echo ok"}]},'
            '{"matcher":"Read","hooks":[{"type":"command"}]}],'
            '"NotAnEvent":[{"hooks":[{"type":"command","command":"echo ignored"}]}]}}'
        ),
        encoding="utf-8",
    )
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    snapshot = loader.load_snapshot()

    groups = snapshot.hooks[HookEventName.PRE_TOOL_USE]
    assert len(groups) == 1
    assert groups[0].group.hooks[0].command == "echo ok"


def test_hook_loader_tolerant_load_drops_invalid_agent_handlers_but_keeps_valid_group(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    (app_config_dir / "hooks.json").write_text(
        (
            '{"hooks":{"Stop":[{"hooks":['
            '{"type":"agent","role_id":"MissingRole","prompt":"review"},'
            '{"type":"command","command":"echo ok"}]}]}}'
        ),
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

    groups = snapshot.hooks[HookEventName.STOP]
    assert len(groups) == 1
    assert len(groups[0].group.hooks) == 1
    assert groups[0].group.hooks[0].command == "echo ok"


def test_hook_loader_tolerant_load_keeps_valid_sibling_handlers_in_invalid_group(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    (app_config_dir / "hooks.json").write_text(
        (
            '{"hooks":{"PreToolUse":[{"matcher":"Read","hooks":['
            '{"type":"command","command":"echo ok"},'
            '{"type":"command"}'
            "]}]}}"
        ),
        encoding="utf-8",
    )
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    snapshot = loader.load_snapshot()

    groups = snapshot.hooks[HookEventName.PRE_TOOL_USE]
    assert len(groups) == 1
    assert groups[0].group.matcher == "Read"
    assert len(groups[0].group.hooks) == 1
    assert groups[0].group.hooks[0].command == "echo ok"


def test_hook_loader_tolerant_load_merges_valid_salvaged_sibling_handlers(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    (app_config_dir / "hooks.json").write_text(
        (
            '{"hooks":{"PreToolUse":[{"matcher":"Read","hooks":['
            '{"type":"command","command":"echo one"},'
            '{"type":"command","command":"echo two"},'
            '{"type":"command"}'
            "]}]}}"
        ),
        encoding="utf-8",
    )
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    snapshot = loader.load_snapshot()

    groups = snapshot.hooks[HookEventName.PRE_TOOL_USE]
    assert len(groups) == 1
    assert [handler.command for handler in groups[0].group.hooks] == [
        "echo one",
        "echo two",
    ]


def test_hook_loader_tolerant_load_preserves_group_order_for_valid_duplicate_matchers(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    (app_config_dir / "hooks.json").write_text(
        (
            '{"hooks":{"PreToolUse":['
            '{"matcher":"*","hooks":[{"type":"command","command":"echo first"}]},'
            '{"matcher":"Read","hooks":[{"type":"command","command":"echo middle"}]},'
            '{"matcher":"*","hooks":[{"type":"command","command":"echo last"}]}'
            "]}}"
        ),
        encoding="utf-8",
    )
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    snapshot = loader.load_snapshot()

    groups = snapshot.hooks[HookEventName.PRE_TOOL_USE]
    assert [group.group.matcher for group in groups] == ["*", "Read", "*"]
    assert [group.group.hooks[0].command for group in groups] == [
        "echo first",
        "echo middle",
        "echo last",
    ]


def test_hook_loader_validate_payload_rejects_http_handler_for_command_only_event(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    with pytest.raises(
        ValueError,
        match="SessionStart only supports command hook handlers",
    ):
        loader.validate_payload(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "http",
                                    "url": "https://example.test/hook",
                                }
                            ],
                        }
                    ]
                }
            }
        )


def test_hook_loader_validate_payload_rejects_agent_for_command_http_only_event(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    with pytest.raises(
        ValueError,
        match="SessionEnd only supports command and http hook handlers",
    ):
        loader.validate_payload(
            {
                "hooks": {
                    "SessionEnd": [
                        {
                            "hooks": [
                                {
                                    "type": "agent",
                                    "role_id": "Reviewer",
                                    "prompt": "review the final answer",
                                }
                            ],
                        }
                    ]
                }
            }
        )


def test_normalize_hooks_payload_preserves_non_dict_entries() -> None:
    payload = {
        "hooks": {
            "PreToolUse": [
                "raw-group",
                {"hooks": [{"type": "command", "command": "ok"}]},
            ],
            123: "raw-value",
        }
    }

    normalized = normalize_hooks_payload(payload)

    assert normalized == payload


def test_normalize_hook_group_keeps_unrepresentable_legacy_tool_names_for_validation() -> (
    None
):
    normalized_groups = _normalize_hook_group(
        {
            "matcher": "Bash",
            "if_condition": "Bash(git *)",
            "tool_names": ["Read", "Write"],
            "hooks": [{"type": "command", "command": "echo ok"}],
        }
    )

    assert normalized_groups == [
        {
            "matcher": "Bash",
            "tool_names": ["Read", "Write"],
            "hooks": [{"type": "command", "command": "echo ok", "if": "Bash(git *)"}],
        }
    ]


def test_normalize_hook_group_migrates_legacy_if_condition_to_all_handlers() -> None:
    normalized_groups = _normalize_hook_group(
        {
            "matcher": "Write",
            "if_condition": "Bash(git *)",
            "hooks": [
                {"type": "command", "command": "echo one"},
                {"type": "command", "command": "echo two"},
            ],
        }
    )

    assert normalized_groups == [
        {
            "matcher": "Write",
            "hooks": [
                {"type": "command", "command": "echo one", "if": "Bash(git *)"},
                {"type": "command", "command": "echo two", "if": "Bash(git *)"},
            ],
        }
    ]


def test_hook_loader_validate_payload_rejects_unrepresentable_multi_handler_legacy_if_condition(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    with pytest.raises(ValueError, match="if_condition"):
        loader.validate_payload(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Write",
                            "if_condition": "Bash(git *)",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo one",
                                    "if": "Bash(status *)",
                                },
                                {
                                    "type": "command",
                                    "command": "echo two",
                                },
                            ],
                        }
                    ]
                }
            }
        )


def test_hook_loader_validate_payload_rejects_empty_hook_group(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    with pytest.raises(
        ValueError,
        match="hook matcher group must contain at least one handler",
    ):
        loader.validate_payload({"hooks": {"PreToolUse": [{"hooks": []}]}})


def test_hook_loader_validate_event_capabilities_wrapper_calls_shared_validator() -> (
    None
):
    HookLoader._validate_event_capabilities(
        config=HooksConfig.model_validate(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo ok",
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )


def test_hook_loader_validate_handler_event_compatibility_wrapper_calls_shared_validator() -> (
    None
):
    HookLoader._validate_handler_event_compatibility(
        event_name=HookEventName.STOP,
        handler=HookHandlerConfig(
            type=HookHandlerType.COMMAND,
            command="echo ok",
        ),
    )


def test_validate_handler_event_compatibility_rejects_if_for_stop() -> None:
    with pytest.raises(
        ValueError,
        match="Hook handler 'if' is only supported for tool events, not Stop",
    ):
        _validate_handler_event_compatibility(
            event_name=HookEventName.STOP,
            handler=HookHandlerConfig(
                type=HookHandlerType.COMMAND,
                command="echo stop",
                if_rule="Bash(git *)",
            ),
        )


def test_hook_loader_strict_single_file_returns_validated_config(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    app_config_dir.mkdir()
    config_path = app_config_dir / "hooks.json"
    config_path.write_text(
        '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"echo ok"}]}]}}',
        encoding="utf-8",
    )
    loader = HookLoader(app_config_dir=app_config_dir, project_root=None)

    config = loader._load_single_file(config_path, tolerant=False)

    assert config == HooksConfig.model_validate(
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo ok"}]}]}}
    )


def test_hook_loader_salvage_tolerant_group_handlers_rejects_non_dict_group(
    tmp_path: Path,
) -> None:
    loader = HookLoader(app_config_dir=tmp_path / "app", project_root=None)

    salvaged = loader._salvage_tolerant_group_handlers(
        destination={},
        path=tmp_path / "hooks.json",
        raw_event_name="PreToolUse",
        raw_group="not-a-group",
        group_index=0,
    )

    assert salvaged is False


def test_hook_loader_salvage_tolerant_group_handlers_rejects_non_list_hooks(
    tmp_path: Path,
) -> None:
    loader = HookLoader(app_config_dir=tmp_path / "app", project_root=None)

    salvaged = loader._salvage_tolerant_group_handlers(
        destination={},
        path=tmp_path / "hooks.json",
        raw_event_name="PreToolUse",
        raw_group={"hooks": "not-a-list"},
        group_index=0,
    )

    assert salvaged is False


def test_parse_tolerant_hooks_payload_rejects_non_mapping_payload_and_hooks() -> None:
    assert parse_tolerant_hooks_payload(["not-a-dict"]) == HooksConfig()
    assert parse_tolerant_hooks_payload({"hooks": []}) == HooksConfig()


def test_parse_tolerant_hooks_payload_skips_invalid_groups_and_empty_group_errors() -> (
    None
):
    config = parse_tolerant_hooks_payload(
        {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Read", "hooks": []},
                    {"matcher": "Write", "hooks": [{"type": "command"}]},
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "ok"}],
                    },
                ]
            }
        }
    )

    assert config == HooksConfig.model_validate(
        {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Read", "hooks": []},
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "ok"}],
                    },
                ]
            }
        }
    )


def test_filter_tolerant_hook_groups_keeps_only_supported_groups() -> None:
    config = filter_tolerant_hook_groups(
        config=HooksConfig(
            hooks={
                HookEventName.STOP: (
                    HookMatcherGroup(
                        hooks=(
                            HookHandlerConfig(
                                type=HookHandlerType.COMMAND,
                                command="echo ok",
                            ),
                        )
                    ),
                    HookMatcherGroup(
                        matcher="Read",
                        hooks=(
                            HookHandlerConfig(
                                type=HookHandlerType.COMMAND,
                                command="echo invalid",
                            ),
                        ),
                    ),
                ),
                HookEventName.PRE_TOOL_USE: (
                    HookMatcherGroup(
                        matcher="Write",
                        hooks=(
                            HookHandlerConfig(
                                type=HookHandlerType.COMMAND,
                                command="echo keep",
                            ),
                        ),
                    ),
                ),
            }
        )
    )

    assert config == HooksConfig(
        hooks={
            HookEventName.STOP: (
                HookMatcherGroup(
                    hooks=(
                        HookHandlerConfig(
                            type=HookHandlerType.COMMAND,
                            command="echo ok",
                        ),
                    )
                ),
            ),
            HookEventName.PRE_TOOL_USE: (
                HookMatcherGroup(
                    matcher="Write",
                    hooks=(
                        HookHandlerConfig(
                            type=HookHandlerType.COMMAND,
                            command="echo keep",
                        ),
                    ),
                ),
            ),
        }
    )


def test_format_validation_error_uses_message_without_location() -> None:
    class _Model(BaseModel):
        value: str

        @model_validator(mode="after")
        def _fail(self) -> "_Model":
            raise ValueError("model-level failure")

    with pytest.raises(ValidationError) as exc_info:
        _Model.model_validate({"value": "x"})

    assert (
        _format_validation_error(exc_info.value) == "Value error, model-level failure"
    )
