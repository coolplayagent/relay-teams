# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import asyncio
from pathlib import Path
from typing import cast

from agent_teams.skills.discovery import SkillsDirectory
from agent_teams.skills.skill_models import SkillScope
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.skills.skill_registry import SkillRegistry

from agent_teams.tools.runtime import ToolContext
from agent_teams.trace import get_trace_context


def test_get_toolset_tools_builds_skill_tools_without_annotation_errors() -> None:
    registry = SkillRegistry(
        directory=SkillsDirectory(base_dir=Path(".agent_teams/skills"))
    )

    tools = registry.get_toolset_tools(("time",))

    names = {tool.name for tool in tools}
    assert names == {
        "load_skill",
        "read_skill_resource",
        "run_skill_script",
    }


def test_get_instruction_entries_returns_structured_data(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "time"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: timezone helper\n"
        "---\n"
        "Use UTC for all timestamps.\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(directory=SkillsDirectory(base_dir=tmp_path / "skills"))

    entries = registry.get_instruction_entries(("time",))

    assert len(entries) == 1
    assert entries[0].name == "time"
    assert entries[0].description == "timezone helper"


def test_registry_from_skill_dirs_prefers_project_skill_over_user_skill(
    tmp_path: Path,
) -> None:
    builtin_skill_dir = tmp_path / "builtin" / "skills" / "time"
    app_skill_dir = tmp_path / ".config" / "agent-teams" / "skills" / "time"
    builtin_skill_dir.mkdir(parents=True)
    app_skill_dir.mkdir(parents=True)

    (builtin_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: builtin timezone helper\n"
        "---\n"
        "Use the builtin timezone.\n",
        encoding="utf-8",
    )
    (app_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: app timezone helper\n"
        "---\n"
        "Use UTC for all app timestamps.\n",
        encoding="utf-8",
    )

    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".config" / "agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )

    skill = registry.get_skill_definition("time")
    entries = registry.get_instruction_entries(("time",))

    assert skill is not None
    assert skill.scope == SkillScope.APP
    assert skill.metadata.description == "app timezone helper"
    assert entries[0].description == "app timezone helper"


def test_registry_from_skill_dirs_loads_user_skill_when_project_skill_missing(
    tmp_path: Path,
) -> None:
    builtin_skill_dir = tmp_path / "builtin" / "skills" / "time"
    builtin_skill_dir.mkdir(parents=True)
    (builtin_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: builtin timezone helper\n"
        "---\n"
        "Use the builtin timezone.\n",
        encoding="utf-8",
    )

    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / ".config" / "agent-teams" / "skills",
        builtin_skills_dir=tmp_path / "builtin" / "skills",
    )

    skill = registry.get_skill_definition("time")

    assert skill is not None
    assert skill.scope == SkillScope.BUILTIN
    assert registry.list_names() == ("time",)


def test_registry_from_config_dirs_merges_builtin_and_app_skills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_config_dir = tmp_path / ".config" / "agent-teams"
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    monkeypatch.setattr(
        "agent_teams.skills.discovery.get_builtin_skills_dir_path",
        lambda: builtin_skills_dir.resolve(),
    )

    _write_skill(
        builtin_skills_dir / "shared",
        name="shared",
        description="builtin shared skill",
        instructions="Builtin instructions.",
    )
    _write_skill(
        builtin_skills_dir / "builtin_only",
        name="builtin_only",
        description="builtin only skill",
        instructions="Builtin only instructions.",
    )
    _write_skill(
        app_config_dir / "skills" / "shared",
        name="shared",
        description="app shared skill",
        instructions="App instructions.",
    )
    _write_skill(
        app_config_dir / "skills" / "app_only",
        name="app_only",
        description="app only skill",
        instructions="App only instructions.",
    )

    registry = SkillRegistry.from_config_dirs(app_config_dir=app_config_dir)

    skills = registry.list_skill_definitions()
    shared_skill = registry.get_skill_definition("shared")
    builtin_only_skill = registry.get_skill_definition("builtin_only")

    assert tuple(skill.metadata.name for skill in skills) == (
        "app_only",
        "builtin_only",
        "shared",
    )
    assert shared_skill is not None
    assert shared_skill.scope == SkillScope.APP
    assert builtin_only_skill is not None
    assert builtin_only_skill.scope == SkillScope.BUILTIN


def test_registry_from_config_dirs_creates_app_skills_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_config_dir = tmp_path / ".config" / "agent-teams"
    monkeypatch.setattr(
        "agent_teams.skills.discovery.get_builtin_skills_dir_path",
        lambda: (tmp_path / "builtin" / "skills").resolve(),
    )

    registry = SkillRegistry.from_config_dirs(app_config_dir=app_config_dir)

    assert (app_config_dir / "skills").is_dir()
    assert registry.list_skill_definitions() == ()


def test_run_skill_script_binds_nested_trace_context(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "time"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: timezone helper\n"
        "---\n"
        "- trace_context: Returns active trace context.\n",
        encoding="utf-8",
    )
    (scripts_dir / "trace_context.py").write_text(
        "# -*- coding: utf-8 -*-\n"
        "from __future__ import annotations\n\n"
        "from agent_teams.trace import get_trace_context\n\n"
        "def run(ctx):\n"
        "    current = get_trace_context()\n"
        "    return {\n"
        "        'trace_id': current.trace_id,\n"
        "        'run_id': current.run_id,\n"
        "        'task_id': current.task_id,\n"
        "        'session_id': current.session_id,\n"
        "        'instance_id': current.instance_id,\n"
        "        'role_id': current.role_id,\n"
        "        'tool_call_id': current.tool_call_id,\n"
        "        'span_id': current.span_id,\n"
        "        'parent_span_id': current.parent_span_id,\n"
        "    }\n",
        encoding="utf-8",
    )
    registry = SkillRegistry(directory=SkillsDirectory(base_dir=tmp_path / "skills"))

    result = asyncio.run(
        registry.run_skill_script(
            cast(ToolContext, cast(object, _FakeCtx())),
            skill_name="time",
            script_name="trace_context",
        )
    )

    assert result["ok"] is True
    data = cast(dict[str, JsonValue], result["data"])
    assert data["trace_id"] == "trace-1"
    assert data["run_id"] == "run-1"
    assert data["task_id"] == "task-1"
    assert data["session_id"] == "session-1"
    assert data["instance_id"] == "inst-1"
    assert data["role_id"] == "spec_coder"
    assert data["tool_call_id"] == "toolcall-1"
    assert isinstance(data["span_id"], str)
    assert isinstance(data["parent_span_id"], str)
    assert data["span_id"] != data["parent_span_id"]
    assert get_trace_context().trace_id is None


def _write_skill(
    skill_dir: Path, *, name: str, description: str, instructions: str
) -> None:
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{instructions}\n",
        encoding="utf-8",
    )


class _FakeRunEventHub:
    def publish(self, event: object) -> None:
        _ = event


class _FakeRunControlManager:
    def raise_if_cancelled(
        self,
        *,
        run_id: str,
        instance_id: str | None = None,
    ) -> None:
        _ = (run_id, instance_id)


class _FakeApprovalManager:
    def open_approval(self, **kwargs: object) -> None:
        _ = kwargs

    def wait_for_approval(self, **kwargs: object) -> tuple[str, str]:
        _ = kwargs
        return ("approve", "")

    def close_approval(self, **kwargs: object) -> None:
        _ = kwargs


class _FakePolicy:
    timeout_seconds = 0.01

    def requires_approval(self, tool_name: str) -> bool:
        _ = tool_name
        return False


class _FakeRunRuntimeRepo:
    def ensure(
        self,
        *,
        run_id: str,
        session_id: str,
        root_task_id: str,
    ) -> None:
        _ = (run_id, session_id, root_task_id)

    def update(self, run_id: str, **kwargs: object) -> None:
        _ = (run_id, kwargs)


class _FakeDeps:
    def __init__(self) -> None:
        self.run_id = "run-1"
        self.trace_id = "trace-1"
        self.task_id = "task-1"
        self.session_id = "session-1"
        self.instance_id = "inst-1"
        self.role_id = "spec_coder"
        self.role_registry = RoleRegistry()
        self.role_registry.register(
            RoleDefinition(
                role_id="spec_coder",
                name="Spec Coder",
                description="Implements requested changes.",
                version="1",
                tools=(),
                system_prompt="Implement tasks.",
            )
        )
        self.run_event_hub = _FakeRunEventHub()
        self.run_control_manager = _FakeRunControlManager()
        self.tool_approval_manager = _FakeApprovalManager()
        self.tool_approval_policy = _FakePolicy()
        self.run_runtime_repo = _FakeRunRuntimeRepo()
        self.notification_service = None


class _FakeCtx:
    def __init__(self) -> None:
        self.deps = _FakeDeps()
        self.tool_call_id = "toolcall-1"
        self.retry = 0
