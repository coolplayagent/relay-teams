# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from agent_teams.skills.discovery import SkillsDirectory
from agent_teams.skills.models import SkillScope
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry
from agent_teams.skills.registry import SkillRegistry
from agent_teams.shared_types.json_types import JsonObject
from agent_teams.tools.runtime import ToolContext
from agent_teams.trace import get_trace_context


def test_get_toolset_tools_builds_skill_tools_without_annotation_errors() -> None:
    registry = SkillRegistry(
        directory=SkillsDirectory(base_dir=Path(".agent_teams/skills"))
    )

    tools = registry.get_toolset_tools(("time",))

    names = {tool.name for tool in tools}
    assert names == {
        "list_skills",
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
    assert entries[0].instructions == "Use UTC for all timestamps."


def test_registry_from_skill_dirs_prefers_project_skill_over_user_skill(
    tmp_path: Path,
) -> None:
    user_skill_dir = tmp_path / "user" / ".agent_teams" / "skills" / "time"
    project_skill_dir = tmp_path / "project" / ".agent_teams" / "skills" / "time"
    user_skill_dir.mkdir(parents=True)
    project_skill_dir.mkdir(parents=True)

    (user_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: user timezone helper\n"
        "---\n"
        "Use the user's default timezone.\n",
        encoding="utf-8",
    )
    (project_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: project timezone helper\n"
        "---\n"
        "Use UTC for all project timestamps.\n",
        encoding="utf-8",
    )

    registry = SkillRegistry.from_skill_dirs(
        project_skills_dir=tmp_path / "project" / ".agent_teams" / "skills",
        user_skills_dir=tmp_path / "user" / ".agent_teams" / "skills",
    )

    skill = registry.get_skill_definition("time")
    entries = registry.get_instruction_entries(("time",))

    assert skill is not None
    assert skill.scope == SkillScope.PROJECT
    assert skill.metadata.description == "project timezone helper"
    assert entries[0].instructions == "Use UTC for all project timestamps."


def test_registry_from_skill_dirs_loads_user_skill_when_project_skill_missing(
    tmp_path: Path,
) -> None:
    user_skill_dir = tmp_path / "user" / ".agent_teams" / "skills" / "time"
    user_skill_dir.mkdir(parents=True)
    (user_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: time\n"
        "description: user timezone helper\n"
        "---\n"
        "Use the user's default timezone.\n",
        encoding="utf-8",
    )

    registry = SkillRegistry.from_skill_dirs(
        project_skills_dir=tmp_path / "project" / ".agent_teams" / "skills",
        user_skills_dir=tmp_path / "user" / ".agent_teams" / "skills",
    )

    skill = registry.get_skill_definition("time")

    assert skill is not None
    assert skill.scope == SkillScope.USER
    assert registry.list_names() == ("time",)


def test_registry_from_config_dirs_merges_user_and_project_skills(
    tmp_path: Path,
) -> None:
    project_config_dir = tmp_path / "project" / ".agent_teams"
    user_home_dir = tmp_path / "user"

    _write_skill(
        user_home_dir / ".agent_teams" / "skills" / "shared",
        name="shared",
        description="user shared skill",
        instructions="User instructions.",
    )
    _write_skill(
        user_home_dir / ".agent_teams" / "skills" / "user_only",
        name="user_only",
        description="user only skill",
        instructions="User only instructions.",
    )
    _write_skill(
        project_config_dir / "skills" / "shared",
        name="shared",
        description="project shared skill",
        instructions="Project instructions.",
    )
    _write_skill(
        project_config_dir / "skills" / "project_only",
        name="project_only",
        description="project only skill",
        instructions="Project only instructions.",
    )

    registry = SkillRegistry.from_config_dirs(
        project_config_dir=project_config_dir,
        user_home_dir=user_home_dir,
    )

    skills = registry.list_skill_definitions()
    shared_skill = registry.get_skill_definition("shared")
    user_only_skill = registry.get_skill_definition("user_only")

    assert tuple(skill.metadata.name for skill in skills) == (
        "project_only",
        "shared",
        "user_only",
    )
    assert shared_skill is not None
    assert shared_skill.scope == SkillScope.PROJECT
    assert user_only_skill is not None
    assert user_only_skill.scope == SkillScope.USER


def test_registry_from_config_dirs_creates_project_skills_directory(
    tmp_path: Path,
) -> None:
    project_config_dir = tmp_path / "project" / ".agent_teams"

    registry = SkillRegistry.from_config_dirs(project_config_dir=project_config_dir)

    assert (project_config_dir / "skills").is_dir()
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
    data = cast(JsonObject, result["data"])
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
