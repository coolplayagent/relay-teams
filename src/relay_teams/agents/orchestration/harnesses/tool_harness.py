# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai.toolsets.function import FunctionToolset

from relay_teams.agents.execution.coordination_agent_builder import (
    build_coordination_agent,
)
from relay_teams.agents.instances.models import (
    RuntimeToolSnapshotEntry,
    RuntimeToolsSnapshot,
)
from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.tools.registry.registry import ToolRegistry, ToolResolutionContext


class TaskToolHarness(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    role_registry: RoleRegistry
    tool_registry: object
    skill_registry: object
    mcp_registry: McpRegistry

    async def build_runtime_tools_snapshot(
        self,
        role: RoleDefinition,
        task: TaskEnvelope | None = None,
    ) -> RuntimeToolsSnapshot:
        skill_registry = cast(SkillRegistry, self.skill_registry)
        tool_registry = cast(ToolRegistry, self.tool_registry)
        skill_names = role.skills
        if task is not None and task.skills is not None:
            skill_names = task.skills
        resolved_skills = skill_registry.resolve_known(
            skill_names,
            strict=False,
            consumer=(
                "agents.orchestration.harnesses.tool_harness"
                ".build_runtime_tools_snapshot"
            ),
        )
        skill_tool_names = frozenset(
            tool.name for tool in skill_registry.get_toolset_tools(resolved_skills)
        )
        tool_agent = build_coordination_agent(
            model_name="snapshot-model",
            base_url="https://example.invalid/v1",
            api_key="snapshot",
            system_prompt="runtime-tools-snapshot",
            allowed_tools=tool_registry.resolve_names(
                role.tools,
                context=ToolResolutionContext(
                    session_id="" if task is None else task.session_id
                ),
            ),
            allowed_mcp_servers=(),
            allowed_skills=resolved_skills,
            tool_registry=tool_registry,
            role_registry=self.role_registry,
            mcp_registry=None,
            skill_registry=skill_registry,
        )
        local_tools: list[RuntimeToolSnapshotEntry] = []
        skill_tools: list[RuntimeToolSnapshotEntry] = []
        for toolset in tool_agent.toolsets:
            if not isinstance(toolset, FunctionToolset):
                continue
            for tool in toolset.tools.values():
                source: Literal["local", "skill"] = (
                    "skill" if tool.name in skill_tool_names else "local"
                )
                entry = self.tool_entry_from_definition(
                    source=source,
                    name=tool.tool_def.name,
                    description=tool.tool_def.description or "",
                    kind=self.normalize_tool_kind(tool.tool_def.kind),
                    strict=tool.tool_def.strict,
                    sequential=tool.tool_def.sequential,
                    parameters_json_schema=(
                        dict(tool.tool_def.parameters_json_schema)
                        if isinstance(tool.tool_def.parameters_json_schema, dict)
                        else {}
                    ),
                )
                if entry.source == "skill":
                    skill_tools.append(entry)
                else:
                    local_tools.append(entry)

        mcp_tools: list[RuntimeToolSnapshotEntry] = []
        for server_name in self.mcp_registry.resolve_server_names(
            role.mcp_servers,
            strict=False,
            consumer=(
                "agents.orchestration.harnesses.tool_harness"
                ".build_runtime_tools_snapshot"
            ),
        ):
            for tool in await self.mcp_registry.list_tool_schemas(server_name):
                mcp_tools.append(
                    self.tool_entry_from_definition(
                        source="mcp",
                        name=tool.name,
                        description=tool.description,
                        kind="function",
                        strict=None,
                        sequential=False,
                        parameters_json_schema=tool.input_schema,
                        server_name=server_name,
                    )
                )

        local_tools.sort(key=lambda item: item.name)
        skill_tools.sort(key=lambda item: item.name)
        mcp_tools.sort(key=lambda item: (item.server_name, item.name))
        return RuntimeToolsSnapshot(
            local_tools=tuple(local_tools),
            skill_tools=tuple(skill_tools),
            mcp_tools=tuple(mcp_tools),
        )

    @staticmethod
    def tool_entry_from_definition(
        *,
        source: Literal["local", "skill", "mcp"],
        name: str,
        description: str,
        kind: Literal["function", "output", "external", "unapproved"],
        strict: bool | None,
        sequential: bool,
        parameters_json_schema: Mapping[str, JsonValue],
        server_name: str = "",
    ) -> RuntimeToolSnapshotEntry:
        return RuntimeToolSnapshotEntry(
            source=source,
            name=name,
            description=description,
            server_name=server_name,
            kind=kind,
            strict=strict,
            sequential=sequential,
            parameters_json_schema=dict(parameters_json_schema),
        )

    @staticmethod
    def normalize_tool_kind(
        kind: str,
    ) -> Literal["function", "output", "external", "unapproved"]:
        if kind == "function":
            return "function"
        if kind == "output":
            return "output"
        if kind == "external":
            return "external"
        if kind == "unapproved":
            return "unapproved"
        return "function"
