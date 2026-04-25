# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.temporary_role_models import (
    TemporaryRoleSource,
    TemporaryRoleSpec,
)
from relay_teams.skills.discovery import SkillsDirectory
from relay_teams.skills.skill_models import SkillSource
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.skill_team_tools.activate_skill_roles import (
    _normalize_requested_role_ids,
    register as register_activate_skill_roles,
)
from relay_teams.tools.skill_team_tools.list_skill_roles import (
    register as register_list_skill_roles,
)


class _FakeAgent:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., object]] = {}

    def tool(
        self, *, description: str
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            _ = description
            self.tools[func.__name__] = func
            return func

        return decorator


class _CapturingRuntimeRoleResolver:
    def __init__(self, current_role: RoleDefinition) -> None:
        self._current_role = current_role
        self.calls: list[dict[str, object]] = []

    def get_effective_role(self, *, run_id: str | None, role_id: str) -> RoleDefinition:
        _ = run_id
        if role_id == self._current_role.role_id:
            return self._current_role
        raise KeyError(f"Unknown role_id: {role_id}")

    def create_temporary_role(
        self,
        *,
        run_id: str,
        session_id: str,
        role: TemporaryRoleSpec,
        source: TemporaryRoleSource = TemporaryRoleSource.META_AGENT_GENERATED,
    ) -> RoleDefinition:
        self.calls.append(
            {
                "run_id": run_id,
                "session_id": session_id,
                "role": role,
                "source": source,
            }
        )
        return role.to_role_definition()


async def _invoke_tool_action(
    action: Callable[..., object],
    raw_args: dict[str, object] | None,
) -> dict[str, JsonValue]:
    resolved_raw_args = {} if raw_args is None else raw_args
    tool_args = {
        name: resolved_raw_args[name]
        for name in inspect.signature(action).parameters
        if name in resolved_raw_args
    }
    result = action(**tool_args)
    if inspect.isawaitable(result):
        return cast(dict[str, JsonValue], await result)
    return cast(dict[str, JsonValue], result)


@pytest.mark.asyncio
async def test_list_skill_roles_returns_lightweight_role_summaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_list_skill_roles(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, JsonValue]]],
        fake_agent.tools["list_skill_roles"],
    )
    registry = _build_skill_registry(tmp_path)
    role_registry, current_role = _build_role_registry()
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            skill_registry=registry,
            role_registry=role_registry,
            runtime_role_resolver=None,
            run_id="run-1",
            session_id="session-1",
            role_id=current_role.role_id,
        ),
    )

    from relay_teams.tools.skill_team_tools import (
        list_skill_roles as list_skill_roles_module,
    )

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, JsonValue],
        action: Callable[..., object],
        raw_args: dict[str, object] | None = None,
        **_: object,
    ) -> dict[str, JsonValue]:
        del ctx, tool_name, args_summary
        return await _invoke_tool_action(action, raw_args)

    monkeypatch.setattr(
        list_skill_roles_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(cast(object, ctx), skill_name="team-review")

    assert result["skill"] == {
        "name": "team-review",
        "ref": "team-review",
        "source": "user_relay_teams",
    }
    roles = cast(list[dict[str, object]], result["roles"])
    assert roles[0]["role_id"] == "analyst"
    assert cast(str, roles[0]["effective_role_id"]).startswith(
        "skill_team_team_review_analyst_"
    )
    assert roles[0]["source_path"] == "agents/analyst.md"
    assert "system_prompt" not in roles[0]


@pytest.mark.asyncio
async def test_activate_skill_roles_creates_run_scoped_temporary_roles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_activate_skill_roles(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, JsonValue]]],
        fake_agent.tools["activate_skill_roles"],
    )
    registry = _build_skill_registry(tmp_path)
    role_registry, current_role = _build_role_registry()
    runtime_role_resolver = _CapturingRuntimeRoleResolver(current_role)
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            skill_registry=registry,
            role_registry=role_registry,
            runtime_role_resolver=runtime_role_resolver,
            run_id="run-1",
            session_id="session-1",
            role_id=current_role.role_id,
        ),
    )

    from relay_teams.tools.skill_team_tools import (
        activate_skill_roles as activate_skill_roles_module,
    )

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, JsonValue],
        action: Callable[..., object],
        raw_args: dict[str, object] | None = None,
        **_: object,
    ) -> dict[str, JsonValue]:
        del ctx, tool_name, args_summary
        return await _invoke_tool_action(action, raw_args)

    monkeypatch.setattr(
        activate_skill_roles_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(
        cast(object, ctx),
        skill_name="team-review",
        role_ids=[" analyst ", "analyst"],
    )

    assert len(runtime_role_resolver.calls) == 1
    created = runtime_role_resolver.calls[0]
    assert created["run_id"] == "run-1"
    assert created["session_id"] == "session-1"
    assert created["source"] == TemporaryRoleSource.SKILL_TEAM
    role_spec = cast(TemporaryRoleSpec, created["role"])
    assert role_spec.role_id.startswith("skill_team_team_review_analyst_")
    assert role_spec.system_prompt == "SYSTEM PROMPT FOR ANALYST."
    activated_roles = cast(list[dict[str, object]], result["activated_roles"])
    assert activated_roles[0]["role_id"] == "analyst"
    assert activated_roles[0]["effective_role_id"] == role_spec.role_id
    assert activated_roles[0]["tools"] == ["read", "office_read_markdown"]


def _build_role_registry() -> tuple[RoleRegistry, RoleDefinition]:
    current_role = RoleDefinition(
        role_id="MainAgent",
        name="Main Agent",
        description="Handles normal-mode runs.",
        version="1",
        tools=(),
        skills=("team-review",),
        system_prompt="Handle the run.",
    )
    registry = RoleRegistry()
    registry.register(current_role)
    return registry, current_role


def _build_skill_registry(tmp_path: Path) -> SkillRegistry:
    skill_dir = tmp_path / "skills" / "team-review"
    agents_dir = skill_dir / "agents"
    agents_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: team-review\n"
        "description: Coordinate a review team.\n"
        "---\n"
        "Use the review workflow.\n",
        encoding="utf-8",
    )
    (agents_dir / "analyst.md").write_text(
        "---\n"
        "role_id: analyst\n"
        "name: Research Analyst\n"
        "description: Collects evidence for review.\n"
        "version: 1\n"
        "mode: subagent\n"
        "tools:\n"
        "  - read\n"
        "---\n"
        "SYSTEM PROMPT FOR ANALYST.\n",
        encoding="utf-8",
    )
    return SkillRegistry(
        directory=SkillsDirectory(
            sources=((SkillSource.USER_RELAY_TEAMS, tmp_path / "skills"),)
        )
    )


def test_normalize_requested_role_ids_rejects_blank_values() -> None:
    with pytest.raises(ValueError, match="role_ids must contain"):
        _normalize_requested_role_ids(["  "])


@pytest.mark.parametrize(
    ("raw_role_ids", "expected"),
    [
        ([" analyst ", "analyst"], ("analyst",)),
        ('[" analyst ", "analyst"]', ("analyst",)),
        ("[' analyst ', 'analyst']", ("analyst",)),
        ("analyst", ("analyst",)),
    ],
)
def test_normalize_requested_role_ids_accepts_provider_string_forms(
    raw_role_ids: list[str] | str,
    expected: tuple[str, ...],
) -> None:
    assert _normalize_requested_role_ids(raw_role_ids) == expected


@pytest.mark.asyncio
async def test_activate_skill_roles_requires_runtime_role_resolver(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_activate_skill_roles(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, JsonValue]]],
        fake_agent.tools["activate_skill_roles"],
    )
    registry = _build_skill_registry(tmp_path)
    role_registry, current_role = _build_role_registry()
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            skill_registry=registry,
            role_registry=role_registry,
            runtime_role_resolver=None,
            run_id="run-1",
            session_id="session-1",
            role_id=current_role.role_id,
        ),
    )

    from relay_teams.tools.skill_team_tools import (
        activate_skill_roles as activate_skill_roles_module,
    )

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, JsonValue],
        action: Callable[..., object],
        raw_args: dict[str, object] | None = None,
        **_: object,
    ) -> dict[str, JsonValue]:
        del ctx, tool_name, args_summary
        return await _invoke_tool_action(action, raw_args)

    monkeypatch.setattr(
        activate_skill_roles_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    with pytest.raises(RuntimeError, match="Temporary role activation"):
        await tool(cast(object, ctx), skill_name="team-review", role_ids=["analyst"])


@pytest.mark.asyncio
async def test_activate_skill_roles_rejects_unknown_skill_role(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_activate_skill_roles(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, JsonValue]]],
        fake_agent.tools["activate_skill_roles"],
    )
    registry = _build_skill_registry(tmp_path)
    role_registry, current_role = _build_role_registry()
    ctx = SimpleNamespace(
        tool_call_id="call-1",
        deps=SimpleNamespace(
            skill_registry=registry,
            role_registry=role_registry,
            runtime_role_resolver=_CapturingRuntimeRoleResolver(current_role),
            run_id="run-1",
            session_id="session-1",
            role_id=current_role.role_id,
        ),
    )

    from relay_teams.tools.skill_team_tools import (
        activate_skill_roles as activate_skill_roles_module,
    )

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, JsonValue],
        action: Callable[..., object],
        raw_args: dict[str, object] | None = None,
        **_: object,
    ) -> dict[str, JsonValue]:
        del ctx, tool_name, args_summary
        return await _invoke_tool_action(action, raw_args)

    monkeypatch.setattr(
        activate_skill_roles_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    with pytest.raises(ValueError, match="Skill roles not found"):
        await tool(cast(object, ctx), skill_name="team-review", role_ids=["missing"])
