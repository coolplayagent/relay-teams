from __future__ import annotations

import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from agent_teams.core.models import TaskEnvelope, VerificationPlan
from agent_teams.roles.registry import RoleRegistry
from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool
from agent_teams.workflow.runtime_graph import load_graph, save_graph, workflow_tag


class TaskSpecModel(BaseModel):
    task_name: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    role_depends_on: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    dod: list[str] = Field(default_factory=list)
    parent_instruction: str | None = None


WorkflowType = Literal["spec_flow", "custom"]


def _parse_tasks_json(tasks_json: str | None) -> list[TaskSpecModel] | None:
    if tasks_json is None:
        return None
    parsed = json.loads(tasks_json)
    if isinstance(parsed, list):
        return [TaskSpecModel(**item) for item in parsed]
    return None


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
        required_roles = list(role_def.depends_on) or []
        for required_role in required_roles:
            if required_role not in role_to_tasks:
                raise ValueError(
                    f"Role '{task.role_id}' depends on '{required_role}', "
                    f"but '{required_role}' is not in the task list. "
                    f"Available roles in tasks: {list(role_to_tasks.keys())}. "
                    f"Use list_available_roles to see all available roles."
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
        if task.task_name not in visited:
            if dfs(task.task_name):
                raise ValueError("Circular dependency detected in tasks")


def _create_spec_flow_template(
    objective: str,
    parent_instruction: str | None,
    workflow_id: str,
) -> list[TaskSpecModel]:
    return [
        TaskSpecModel(
            task_name="spec",
            objective=f"Build requirement spec for: {objective}",
            role_id="spec_spec",
            depends_on=[],
            scope=["workflow:" + workflow_id, "stage:spec"],
            dod=[
                "spec_document_written",
                "acceptance_criteria_defined",
                "non_empty_response",
            ],
            parent_instruction=parent_instruction
            or "This is a workflow task. Produce a complete spec and publish via write_stage_doc.",
        ),
        TaskSpecModel(
            task_name="design",
            objective=f"Design technical approach for: {objective}",
            role_id="spec_design",
            depends_on=["spec"],
            scope=["workflow:" + workflow_id, "stage:design"],
            dod=[
                "design_document_written",
                "implementation_plan_defined",
                "non_empty_response",
            ],
            parent_instruction="Read spec stage output and produce implementable technical design document.",
        ),
        TaskSpecModel(
            task_name="code",
            objective=f"Implement code for: {objective}",
            role_id="spec_coder",
            depends_on=["design"],
            scope=["workflow:" + workflow_id, "stage:code"],
            dod=["implementation_done", "tests_updated", "non_empty_response"],
            parent_instruction="Implement according to spec and design. Keep changes minimal and testable.",
        ),
        TaskSpecModel(
            task_name="verify",
            objective=f"Verify implementation quality for: {objective}",
            role_id="spec_verify",
            depends_on=["code"],
            scope=["workflow:" + workflow_id, "stage:verify"],
            dod=[
                "verification_document_written",
                "pass_fail_decision",
                "non_empty_response",
            ],
            parent_instruction="Validate final implementation against design/spec and publish verification doc.",
        ),
    ]


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def create_workflow_graph(
        ctx: ToolContext,
        workflow_type: WorkflowType = "spec_flow",
        objective: str = "",
        tasks: str | None = None,
        parent_instruction: str | None = None,
    ) -> str:
        """
        Create a workflow graph to orchestrate tasks.

        Args:
            workflow_type: Type of workflow. Use "spec_flow" for standard 4-stage (spec->design->code->verify).
                          Use "custom" when you want to define your own tasks.
            objective: The goal/objective of the workflow.
            tasks: JSON string array of task specifications. Each task needs: task_name, objective, role_id, depends_on.
                   Only needed when workflow_type is "custom". Optional fields: scope, dod, parent_instruction.
            parent_instruction: Optional instruction passed to child tasks.

        Returns:
            Workflow created successfully with task IDs. Use the returned workflow_id in dispatch_ready_tasks.
        """

        def _action() -> str:
            parsed_tasks = _parse_tasks_json(tasks)

            existing = load_graph(ctx.deps.shared_store, task_id=ctx.deps.task_id)
            if existing is not None:
                return json.dumps(
                    {
                        "ok": True,
                        "created": False,
                        "message": "A workflow already exists for this task. Use dispatch_ready_tasks to continue, or start a new run for a fresh workflow.",
                        "workflow_id": existing.get("workflow_id"),
                        "workflow_type": existing.get("workflow_type"),
                        "existing_tasks": _format_tasks_for_response(existing),
                    },
                    ensure_ascii=False,
                )

            workflow_id = f"workflow_{uuid4().hex[:8]}"

            if workflow_type == "spec_flow":
                parsed_tasks = _create_spec_flow_template(
                    objective=objective,
                    parent_instruction=parent_instruction,
                    workflow_id=workflow_id,
                )
            else:
                if not parsed_tasks:
                    raise ValueError(
                        "tasks is required for custom workflow. "
                        'Example: [{"task_name": "code", "objective": "Write hello.py", "role_id": "spec_coder", "depends_on": []}]'
                    )

            try:
                _validate_role_depends(ctx.deps.role_registry, parsed_tasks)
                _detect_cycle(parsed_tasks)
            except ValueError as e:
                raise ValueError(
                    f"Workflow validation failed: {str(e)}. "
                    "Fix the issues and retry create_workflow_graph with corrected tasks."
                )

            name_to_task_id: dict[str, str] = {}

            for spec in parsed_tasks:
                task_id = f"task_{uuid4().hex[:12]}"
                name_to_task_id[spec.task_name] = task_id

            for spec in parsed_tasks:
                task_id = name_to_task_id[spec.task_name]

                task_envelope = TaskEnvelope(
                    task_id=task_id,
                    session_id=ctx.deps.session_id,
                    trace_id=ctx.deps.trace_id,
                    parent_task_id=ctx.deps.task_id,
                    objective=spec.objective,
                    parent_instruction=spec.parent_instruction,
                    scope=tuple(spec.scope),
                    dod=tuple(spec.dod),
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

            return json.dumps(
                {
                    "ok": True,
                    "created": True,
                    "message": f'Workflow created successfully with {len(task_list)} tasks. Use workflow_id="{workflow_id}" in dispatch_ready_tasks to execute.',
                    "workflow_id": workflow_id,
                    "workflow_type": workflow_type,
                    "tasks": task_list,
                    "next_action": "Call dispatch_ready_tasks with this workflow_id to start executing tasks.",
                },
                ensure_ascii=False,
            )

        return execute_tool(
            ctx,
            tool_name="create_workflow_graph",
            args_summary={
                "workflow_type": workflow_type,
                "objective_len": len(objective),
                "has_tasks": tasks is not None,
                "task_count": 4
                if workflow_type == "spec_flow"
                else (len(json.loads(tasks)) if tasks else 0),
                "has_parent_instruction": bool(parent_instruction),
            },
            action=_action,
        )


def _format_tasks_for_response(graph: dict[str, object]) -> dict[str, dict[str, str]]:
    tasks = graph.get("tasks", {})
    if not isinstance(tasks, dict):
        return {}
    return {
        name: {"task_id": info.get("task_id", ""), "role_id": info.get("role_id", "")}
        for name, info in tasks.items()
    }
