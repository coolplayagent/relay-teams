from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from agent_teams.shared_types.json_types import JsonObject
from agent_teams.roles.registry import RoleRegistry
from agent_teams.tools.runtime import ToolContext, ToolDeps, execute_tool
from agent_teams.workflow.runtime_graph import load_graph, save_graph
from agent_teams.workflow.models import TaskEnvelope, VerificationPlan


class TaskSpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_name: str = Field(min_length=1, description="Name of the task")
    objective: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


WorkflowType = Literal["spec_flow", "custom"]


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def create_workflow_graph(
        ctx: ToolContext,
        objective: str,
        workflow_type: WorkflowType = "custom",
        tasks: list[TaskSpecModel] | None = None,
    ) -> JsonObject:
        def _action() -> dict[str, object]:
            parsed_tasks = tasks
            existing = load_graph(ctx.deps.shared_store, task_id=ctx.deps.task_id)
            if existing is not None:
                return {
                    "ok": True,
                    "created": False,
                    "message": (
                        "A workflow already exists for this task. Use "
                        "dispatch_tasks to continue, or start a new run "
                        "for a fresh workflow."
                    ),
                    "workflow_id": existing.get("workflow_id"),
                    "workflow_type": existing.get("workflow_type"),
                    "existing_tasks": _format_tasks_for_response(existing),
                }

            workflow_id = f"workflow_{uuid4().hex[:8]}"
            if workflow_type == "spec_flow":
                parsed_tasks = _create_spec_flow_template(objective=objective)
            elif not parsed_tasks:
                raise ValueError(
                    "tasks is required for custom workflow. "
                    'Example: [{"task_name": "code", "objective": "Write hello.py", '
                    '"role_id": "spec_coder", "depends_on": []}]'
                )

            _validate_role_depends(ctx.deps.role_registry, parsed_tasks)
            _detect_cycle(parsed_tasks)

            name_to_task_id: dict[str, str] = {}
            for spec in parsed_tasks:
                task_id = f"task_{uuid4().hex[:12]}"
                name_to_task_id[spec.task_name] = task_id

            for spec in parsed_tasks:
                task_envelope = TaskEnvelope(
                    task_id=name_to_task_id[spec.task_name],
                    session_id=ctx.deps.session_id,
                    trace_id=ctx.deps.trace_id,
                    parent_task_id=ctx.deps.task_id,
                    objective=spec.objective,
                    verification=VerificationPlan(checklist=("non_empty_response",)),
                )
                ctx.deps.task_repo.create(task_envelope)

            graph: dict[str, object] = {
                "workflow_id": workflow_id,
                "workflow_type": workflow_type,
                "objective": objective,
                "trace_id": ctx.deps.trace_id,
                "session_id": ctx.deps.session_id,
                "tasks": {
                    spec.task_name: {
                        "task_id": name_to_task_id[spec.task_name],
                        "role_id": spec.role_id,
                        "depends_on": spec.depends_on,
                        "depends_on_task_ids": [
                            name_to_task_id[dep]
                            for dep in spec.depends_on
                            if dep in name_to_task_id
                        ],
                    }
                    for spec in parsed_tasks
                },
            }
            save_graph(ctx.deps.shared_store, task_id=ctx.deps.task_id, graph=graph)

            task_list = [
                {
                    "task_name": spec.task_name,
                    "task_id": name_to_task_id[spec.task_name],
                    "role_id": spec.role_id,
                    "depends_on": spec.depends_on,
                }
                for spec in parsed_tasks
            ]
            return {
                "ok": True,
                "created": True,
                "message": (
                    f"Workflow created successfully with {len(task_list)} tasks. "
                    f'Use workflow_id="{workflow_id}" in dispatch_tasks to execute.'
                ),
                "workflow_id": workflow_id,
                "workflow_type": workflow_type,
                "tasks": task_list,
                "next_action": (
                    'Call dispatch_tasks(action="next") with this workflow_id to start executing tasks.'
                ),
            }

        return await execute_tool(
            ctx,
            tool_name="create_workflow_graph",
            args_summary={
                "workflow_type": workflow_type,
                "objective_len": len(objective),
                "has_tasks": tasks is not None,
                "task_count": 4
                if workflow_type == "spec_flow"
                else (len(tasks) if tasks else 0),
            },
            action=_action,
        )


def _validate_role_depends(
    role_registry: RoleRegistry, tasks: list[TaskSpecModel]
) -> None:
    available_roles = {r.role_id for r in role_registry.list_roles()}
    role_to_tasks: dict[str, list[str]] = {}
    for task in tasks:
        role_to_tasks.setdefault(task.role_id, []).append(task.task_name)

    for task in tasks:
        if task.role_id not in available_roles:
            raise ValueError(
                f"Invalid role_id '{task.role_id}'. Available roles: {sorted(available_roles)}"
            )
        role_def = role_registry.get(task.role_id)
        for required_role in list(role_def.depends_on) or []:
            if required_role not in role_to_tasks:
                raise ValueError(
                    f"Role '{task.role_id}' depends on '{required_role}', "
                    f"but '{required_role}' is not in the task list. "
                    f"Available roles in tasks: {list(role_to_tasks.keys())}. "
                    "Use list_available_roles to see all available roles."
                )


def _detect_cycle(tasks: list[TaskSpecModel]) -> None:
    graph: dict[str, list[str]] = {task.task_name: task.depends_on for task in tasks}
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        for dep in graph.get(node, []):
            if dep not in visited:
                if dfs(dep):
                    return True
            elif dep in rec_stack:
                return True
        rec_stack.remove(node)
        return False

    for task in tasks:
        if task.task_name not in visited and dfs(task.task_name):
            raise ValueError("Circular dependency detected in tasks")


def _create_spec_flow_template(objective: str) -> list[TaskSpecModel]:
    return [
        TaskSpecModel(
            task_name="spec",
            objective=(
                f'Input: user requirement "{objective}". '
                "Output: a structured requirement specification with clear goals, scope, and acceptance criteria."
            ),
            role_id="spec_spec",
            depends_on=[],
        ),
        TaskSpecModel(
            task_name="design",
            objective=(
                f'Input: spec.md from previous stage for "{objective}". '
                "Output: an implementation-ready technical design describing architecture, interfaces, and testing."
            ),
            role_id="spec_design",
            depends_on=["spec"],
        ),
        TaskSpecModel(
            task_name="code",
            objective=(
                f'Input: design.md from previous stage for "{objective}". '
                "Output: code changes and tests that implement the approved design."
            ),
            role_id="spec_coder",
            depends_on=["design"],
        ),
        TaskSpecModel(
            task_name="verify",
            objective=(
                f'Input: implementation output and design artifacts for "{objective}". '
                "Output: a verification verdict (PASS/FAIL) with concrete findings and coverage gaps."
            ),
            role_id="spec_verify",
            depends_on=["code"],
        ),
    ]


def _format_tasks_for_response(graph: dict[str, object]) -> dict[str, dict[str, str]]:
    tasks = graph.get("tasks", {})
    if not isinstance(tasks, dict):
        return {}
    return {
        name: {"task_id": info.get("task_id", ""), "role_id": info.get("role_id", "")}
        for name, info in tasks.items()
    }
