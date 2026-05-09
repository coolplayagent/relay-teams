# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from relay_teams.agents.orchestration.policy_models import OrchestrationPolicy
from relay_teams.agents.orchestration.task_contracts import (
    TaskDraft,
    TaskOrchestrationServiceLike,
)
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskSpec,
    VerificationCommand,
    VerificationPlan,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.logger import get_logger, log_event
from relay_teams.roles.role_models import RoleDefinition, RoleMode
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.roles.temporary_role_models import (
    TemporaryRoleSource,
    TemporaryRoleSpec,
)
from relay_teams.sessions.runs.run_models import RunTopologySnapshot
from relay_teams.validation import (
    OptionalIdentifierStr,
    RequiredIdentifierStr,
    normalize_identifier_tuple,
)

LOGGER = get_logger(__name__)

AUTO_PLANNER_NODE_ID = "auto_plan"
AUTO_LANE_NODE_PREFIX = "auto_lane_"
MIN_AUTO_PLAN_OBJECTIVE_CHARS = 240
MIN_AUTO_PLAN_WORDS = 40
MAX_AUTO_DELEGATION_LANES = 5


class TemporaryRolePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    template_role_id: OptionalIdentifierStr = None
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    model_profile: str = Field(default="default", min_length=1)

    @field_validator("tools", "mcp_servers", "skills", mode="before")
    @classmethod
    def _normalize_text_tuple(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value.strip(),) if value.strip() else ()
        if isinstance(value, Sequence):
            return tuple(str(item).strip() for item in value if str(item).strip())
        raise ValueError("capability lists must be strings or sequences")


class TaskSpecProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    requirements: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    evidence_expectations: tuple[str, ...] = ()
    approach: tuple[str, ...] = ()

    @field_validator(
        "requirements",
        "constraints",
        "acceptance_criteria",
        "out_of_scope",
        "verification_commands",
        "evidence_expectations",
        "approach",
        mode="before",
    )
    @classmethod
    def _normalize_projection_text(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value.strip(),) if value.strip() else ()
        if isinstance(value, Sequence):
            return tuple(str(item).strip() for item in value if str(item).strip())
        raise ValueError("task spec projection fields must be strings or sequences")

    @field_validator("summary", mode="before")
    @classmethod
    def _normalize_summary(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def to_task_spec(self, root_spec: TaskSpec | None) -> TaskSpec:
        if root_spec is None:
            return TaskSpec(
                summary=self.summary,
                requirements=self.requirements,
                constraints=self.constraints,
                acceptance_criteria=self.acceptance_criteria,
                out_of_scope=self.out_of_scope,
                verification_commands=self.verification_commands,
                evidence_expectations=self.evidence_expectations,
                approach=self.approach,
            )
        return root_spec.model_copy(
            update={
                "summary": self.summary or root_spec.summary,
                "requirements": _merge_text(root_spec.requirements, self.requirements),
                "constraints": _merge_text(root_spec.constraints, self.constraints),
                "acceptance_criteria": _merge_text(
                    root_spec.acceptance_criteria,
                    self.acceptance_criteria,
                ),
                "out_of_scope": _merge_text(root_spec.out_of_scope, self.out_of_scope),
                "verification_commands": _merge_text(
                    root_spec.verification_commands,
                    self.verification_commands,
                ),
                "evidence_expectations": _merge_text(
                    root_spec.evidence_expectations,
                    self.evidence_expectations,
                ),
                "approach": _merge_text(root_spec.approach, self.approach),
            }
        )


class DelegationLane(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lane_id: RequiredIdentifierStr
    title: str = Field(min_length=1)
    role_id: RequiredIdentifierStr
    objective: str = Field(min_length=1)
    depends_on_lane_ids: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    evidence_expectations: tuple[str, ...] = ()
    temporary_role: TemporaryRolePlan | None = None
    spec: TaskSpecProjection | None = None

    @field_validator("depends_on_lane_ids", mode="before")
    @classmethod
    def _normalize_dependency_ids(cls, value: object) -> tuple[str, ...]:
        return (
            normalize_identifier_tuple(value, field_name="delegation lane dependencies")
            or ()
        )

    @field_validator("acceptance_criteria", "evidence_expectations", mode="before")
    @classmethod
    def _normalize_text_items(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value.strip(),) if value.strip() else ()
        if isinstance(value, Sequence):
            return tuple(str(item).strip() for item in value if str(item).strip())
        raise ValueError("delegation lane text fields must be strings or sequences")

    def model_post_init(self, __context: object) -> None:
        if self.temporary_role is None:
            return
        if self.temporary_role.role_id != self.role_id:
            raise ValueError("temporary_role.role_id must match lane role_id")


class DelegationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    should_decompose: bool = True
    rationale: str = ""
    lanes: tuple[DelegationLane, ...] = ()

    def model_post_init(self, __context: object) -> None:
        if self.should_decompose and not self.lanes:
            raise ValueError("decomposed delegation plan must contain lanes")
        lane_ids = tuple(lane.lane_id for lane in self.lanes)
        if len(lane_ids) != len(set(lane_ids)):
            raise ValueError("delegation lane ids must be unique")
        lane_id_set = set(lane_ids)
        for lane in self.lanes:
            for dependency_id in lane.depends_on_lane_ids:
                if dependency_id == lane.lane_id:
                    raise ValueError("delegation lane cannot depend on itself")
                if dependency_id not in lane_id_set:
                    raise ValueError(
                        f"delegation lane references unknown dependency: {dependency_id}"
                    )
        _assert_lane_graph_acyclic(self.lanes)


class DelegationPlanningService:
    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        task_service: TaskOrchestrationServiceLike,
        role_registry: RoleRegistry,
        runtime_role_resolver: RuntimeRoleResolver | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._task_service = task_service
        self._role_registry = role_registry
        self._runtime_role_resolver = runtime_role_resolver

    def replace_role_registry(self, role_registry: RoleRegistry) -> None:
        self._role_registry = role_registry

    async def plan_and_create_tasks_async(
        self,
        *,
        root_task: TaskEnvelope,
        topology: RunTopologySnapshot | None,
        policy: OrchestrationPolicy,
    ) -> DelegationPlan | None:
        if not await self.should_plan_async(
            root_task=root_task,
            topology=topology,
            policy=policy,
        ):
            return None
        try:
            planner_output = await self._run_planner_task_async(
                root_task=root_task,
                policy=policy,
                available_role_ids=_available_planner_role_ids(
                    role_registry=self._role_registry,
                    topology=topology,
                    planner_role_id=policy.planner_role_id,
                ),
            )
            plan = parse_delegation_plan(planner_output)
            if not plan.should_decompose:
                return plan
            self._validate_plan_bounds(plan=plan, policy=policy)
            created_temporary_role_ids = await self._ensure_plan_roles_async(
                run_id=root_task.trace_id,
                session_id=root_task.session_id,
                plan=plan,
                policy=policy,
                topology=topology,
            )
            existing_auto_lane_task_ids = await self._auto_lane_task_ids_async(
                run_id=root_task.trace_id,
            )
            try:
                await self._task_service.create_tasks(
                    run_id=root_task.trace_id,
                    tasks=[
                        _lane_to_task_draft(root_task=root_task, lane=lane)
                        for lane in plan.lanes
                    ],
                )
            except Exception:
                await self._rollback_created_auto_lane_tasks_async(
                    run_id=root_task.trace_id,
                    lane_node_ids=tuple(
                        _lane_node_id(lane.lane_id) for lane in plan.lanes
                    ),
                    preserved_task_ids=existing_auto_lane_task_ids,
                )
                await self._rollback_created_temporary_roles_async(
                    run_id=root_task.trace_id,
                    role_ids=created_temporary_role_ids,
                )
                raise
            log_event(
                LOGGER,
                logging.INFO,
                event="coord.planning.tasks_created",
                message="DelegationPlanner delegation lanes created",
                payload={
                    "trace_id": root_task.trace_id,
                    "root_task_id": root_task.task_id,
                    "lane_count": len(plan.lanes),
                },
            )
            return plan
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="coord.planning.failed",
                message="DelegationPlanner delegation planning failed; falling back to coordinator",
                payload={
                    "trace_id": root_task.trace_id,
                    "root_task_id": root_task.task_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None

    async def should_plan_async(
        self,
        *,
        root_task: TaskEnvelope,
        topology: RunTopologySnapshot | None,
        policy: OrchestrationPolicy,
    ) -> bool:
        if not policy.auto_plan_long_tasks:
            return False
        if policy.max_orchestration_cycles < 1:
            return False
        if policy.max_parallel_delegated_tasks < 1:
            return False
        if topology is not None and topology.orchestration_graph is not None:
            return False
        allowed_role_ids = _topology_allowed_role_ids(topology)
        if allowed_role_ids and policy.planner_role_id not in allowed_role_ids:
            return False
        if await self._run_already_has_auto_plan_async(root_task.trace_id):
            return False
        try:
            planner_role = self._role_registry.get(policy.planner_role_id)
            _validate_planner_role(planner_role)
        except KeyError:
            log_event(
                LOGGER,
                logging.WARNING,
                event="coord.planning.role_missing",
                message="Planner role is not available; falling back to coordinator",
                payload={
                    "trace_id": root_task.trace_id,
                    "planner_role_id": policy.planner_role_id,
                },
            )
            return False
        except ValueError as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="coord.planning.role_invalid",
                message="Planner role is not a subagent; falling back to coordinator",
                payload={
                    "trace_id": root_task.trace_id,
                    "planner_role_id": policy.planner_role_id,
                    "error": str(exc),
                },
            )
            return False
        if policy.coordinator_inline_budget_steps == 0:
            return True
        objective = root_task.objective.strip()
        if len(objective) >= MIN_AUTO_PLAN_OBJECTIVE_CHARS:
            return True
        if len(objective.split()) >= MIN_AUTO_PLAN_WORDS:
            return True
        spec = root_task.spec
        if spec is None:
            return False
        spec_signal_count = (
            len(spec.requirements)
            + len(spec.acceptance_criteria)
            + len(spec.evidence_expectations)
            + len(spec.constraints)
        )
        return spec_signal_count >= 3

    async def _run_already_has_auto_plan_async(self, run_id: str) -> bool:
        for record in await self._task_repo.list_by_trace_async(run_id):
            node_id = record.envelope.orchestration_node_id or ""
            if node_id == AUTO_PLANNER_NODE_ID or node_id.startswith(
                AUTO_LANE_NODE_PREFIX
            ):
                return True
        return False

    async def _auto_lane_task_ids_async(self, *, run_id: str) -> frozenset[str]:
        return frozenset(
            record.envelope.task_id
            for record in await self._task_repo.list_by_trace_async(run_id)
            if (record.envelope.orchestration_node_id or "").startswith(
                AUTO_LANE_NODE_PREFIX
            )
        )

    async def _run_planner_task_async(
        self,
        *,
        root_task: TaskEnvelope,
        policy: OrchestrationPolicy,
        available_role_ids: tuple[str, ...],
    ) -> str:
        _validate_planner_role(self._role_registry.get(policy.planner_role_id))
        response = await self._task_service.create_tasks(
            run_id=root_task.trace_id,
            tasks=[
                TaskDraft(
                    title="Plan parallel delegation",
                    objective=_planner_objective(
                        root_task=root_task,
                        policy=policy,
                        available_role_ids=available_role_ids,
                    ),
                    role_id=policy.planner_role_id,
                    orchestration_node_id=AUTO_PLANNER_NODE_ID,
                    verification=VerificationPlan(
                        checklist=("non_empty_response",),
                        acceptance_criteria=(
                            "returns a valid delegation plan JSON object",
                        ),
                        evidence_expectations=("delegation plan JSON object",),
                    ),
                    spec=root_task.spec,
                    spec_source_task_id=root_task.task_id
                    if root_task.spec is not None
                    else None,
                )
            ],
        )
        planner_task_id = _first_task_id(response)
        await self._task_service.dispatch_task(
            run_id=root_task.trace_id,
            task_id=planner_task_id,
            role_id=policy.planner_role_id,
            prompt=(
                "Return only the delegation plan JSON object. "
                "Do not include Markdown fences or explanatory prose."
            ),
        )
        record = await self._task_repo.get_async(planner_task_id)
        result = str(record.result or "").strip()
        if not result:
            raise ValueError("DelegationPlanner completed without a result")
        return result

    @staticmethod
    def _validate_plan_bounds(
        *,
        plan: DelegationPlan,
        policy: OrchestrationPolicy,
    ) -> None:
        lane_count = len(plan.lanes)
        if lane_count > MAX_AUTO_DELEGATION_LANES:
            raise ValueError(
                "delegation plan lane count exceeds automatic lane limit: "
                f"{lane_count} > {MAX_AUTO_DELEGATION_LANES}"
            )
        temporary_role_ids = {
            lane.temporary_role.role_id
            for lane in plan.lanes
            if lane.temporary_role is not None
        }
        if len(temporary_role_ids) > policy.max_temporary_roles_per_run:
            raise ValueError(
                "delegation plan temporary role count exceeds policy limit: "
                f"{len(temporary_role_ids)} > {policy.max_temporary_roles_per_run}"
            )

    async def _ensure_plan_roles_async(
        self,
        *,
        run_id: str,
        session_id: str,
        plan: DelegationPlan,
        policy: OrchestrationPolicy,
        topology: RunTopologySnapshot | None = None,
    ) -> tuple[str, ...]:
        allowed_role_ids = _topology_allowed_role_ids(topology)
        temporary_role_ids = set(
            await self._existing_temporary_role_ids_async(run_id=run_id)
        )
        temporary_lanes = tuple(
            lane for lane in plan.lanes if lane.temporary_role is not None
        )
        _validate_temporary_role_definitions(temporary_lanes)
        created_temporary_role_ids: list[str] = []
        try:
            for lane in temporary_lanes:
                _validate_non_planner_lane_role(
                    role_id=lane.role_id,
                    planner_role_id=policy.planner_role_id,
                )
                if not policy.prefer_temporary_roles_for_long_tasks:
                    raise ValueError(
                        "delegation plan proposed a temporary role while temporary roles are disabled"
                    )
                temporary_role = lane.temporary_role
                if temporary_role is None:
                    continue
                temporary_role_exists = temporary_role.role_id in temporary_role_ids
                temporary_role_ids.add(temporary_role.role_id)
                if len(temporary_role_ids) > policy.max_temporary_roles_per_run:
                    raise ValueError("delegation plan exceeds temporary role limit")
                if temporary_role_exists:
                    await self._require_effective_role_async(
                        run_id=run_id,
                        role_id=temporary_role.role_id,
                    )
                    continue
                _validate_allowed_temporary_role_template(
                    role=temporary_role,
                    allowed_role_ids=allowed_role_ids,
                )
                created = await self._create_temporary_role_async(
                    run_id=run_id,
                    session_id=session_id,
                    role=temporary_role,
                )
                if created:
                    created_temporary_role_ids.append(temporary_role.role_id)
            for lane in plan.lanes:
                _validate_non_planner_lane_role(
                    role_id=lane.role_id,
                    planner_role_id=policy.planner_role_id,
                )
                if lane.role_id in temporary_role_ids:
                    await self._require_effective_role_async(
                        run_id=run_id,
                        role_id=lane.role_id,
                    )
                    continue
                _validate_allowed_static_lane_role(
                    role_id=lane.role_id,
                    allowed_role_ids=allowed_role_ids,
                )
                await self._require_effective_role_async(
                    run_id=run_id,
                    role_id=lane.role_id,
                )
        except Exception:
            await self._rollback_created_temporary_roles_async(
                run_id=run_id,
                role_ids=tuple(created_temporary_role_ids),
            )
            raise
        return tuple(created_temporary_role_ids)

    async def _existing_temporary_role_ids_async(
        self, *, run_id: str
    ) -> tuple[str, ...]:
        if self._runtime_role_resolver is None:
            return ()
        return await self._runtime_role_resolver.list_temporary_role_ids_async(
            run_id=run_id
        )

    async def _require_effective_role_async(self, *, run_id: str, role_id: str) -> None:
        if self._runtime_role_resolver is not None:
            role = await self._runtime_role_resolver.get_effective_role_async(
                run_id=run_id,
                role_id=role_id,
            )
            _validate_lane_role(role)
            return
        _validate_lane_role(self._role_registry.get(role_id))

    async def _create_temporary_role_async(
        self,
        *,
        run_id: str,
        session_id: str,
        role: TemporaryRolePlan,
    ) -> bool:
        if self._runtime_role_resolver is None:
            raise RuntimeError("temporary role creation requires RuntimeRoleResolver")
        if role.role_id in await self._existing_temporary_role_ids_async(run_id=run_id):
            await self._require_effective_role_async(
                run_id=run_id, role_id=role.role_id
            )
            return False
        try:
            existing = self._role_registry.get(role.role_id)
        except KeyError:
            existing = None
        if existing is not None:
            raise ValueError(
                f"temporary role id conflicts with static role: {role.role_id}"
            )
        await self._runtime_role_resolver.create_temporary_role_async(
            run_id=run_id,
            session_id=session_id,
            source=TemporaryRoleSource.META_AGENT_GENERATED,
            role=TemporaryRoleSpec(
                role_id=role.role_id,
                name=role.name,
                description=role.description,
                version="temporary",
                tools=role.tools,
                mcp_servers=role.mcp_servers,
                skills=role.skills,
                model_profile=role.model_profile,
                system_prompt=role.system_prompt,
                mode=RoleMode.SUBAGENT,
                template_role_id=role.template_role_id,
            ),
        )
        return True

    async def _rollback_created_temporary_roles_async(
        self,
        *,
        run_id: str,
        role_ids: tuple[str, ...],
    ) -> None:
        if self._runtime_role_resolver is None:
            return
        for role_id in reversed(role_ids):
            try:
                await self._runtime_role_resolver.delete_temporary_role_async(
                    run_id=run_id,
                    role_id=role_id,
                )
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="coord.planning.temp_role_rollback_failed",
                    message="Failed to roll back planner-created temporary role",
                    payload={
                        "trace_id": run_id,
                        "role_id": role_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )

    async def _rollback_created_auto_lane_tasks_async(
        self,
        *,
        run_id: str,
        lane_node_ids: tuple[str, ...],
        preserved_task_ids: frozenset[str],
    ) -> None:
        lane_node_id_set = set(lane_node_ids)
        for record in await self._task_repo.list_by_trace_async(run_id):
            node_id = record.envelope.orchestration_node_id or ""
            if (
                node_id not in lane_node_id_set
                or record.envelope.task_id in preserved_task_ids
            ):
                continue
            try:
                await self._task_repo.delete_async(record.envelope.task_id)
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="coord.planning.auto_lane_rollback_failed",
                    message="Failed to roll back planner-created auto-lane task",
                    payload={
                        "trace_id": run_id,
                        "task_id": record.envelope.task_id,
                        "orchestration_node_id": node_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )


def parse_delegation_plan(output: str) -> DelegationPlan:
    text = _extract_json_object(output.strip())
    payload: object = json.loads(text)
    return DelegationPlan.model_validate(payload)


def _extract_json_object(output: str) -> str:
    if output.startswith("{") and output.endswith("}"):
        return output
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("DelegationPlanner output does not contain a JSON object")
    return output[start : end + 1]


def _first_task_id(response: Mapping[str, JsonValue]) -> str:
    tasks_value = response.get("tasks")
    if not isinstance(tasks_value, list) or not tasks_value:
        raise ValueError("planner task creation response did not include tasks")
    first_task = tasks_value[0]
    if not isinstance(first_task, Mapping):
        raise ValueError("planner task creation response task is invalid")
    task_id = first_task.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("planner task creation response did not include task_id")
    return task_id


def _planner_objective(
    *,
    root_task: TaskEnvelope,
    policy: OrchestrationPolicy,
    available_role_ids: tuple[str, ...],
) -> str:
    available_roles = ", ".join(available_role_ids)
    spec_text = (
        root_task.spec.model_dump_json(indent=2)
        if root_task.spec is not None
        else "none"
    )
    return "\n".join(
        (
            "Create a bounded parallel delegation plan for the root task.",
            "",
            f"Root task id: {root_task.task_id}",
            f"Root objective: {root_task.objective}",
            f"Root spec JSON: {spec_text}",
            f"Maximum lanes: {MAX_AUTO_DELEGATION_LANES}",
            f"Maximum temporary roles: {policy.max_temporary_roles_per_run}",
            f"Planner role id: {policy.planner_role_id}",
            f"Available subagent roles: {available_roles or 'none'}",
            "",
            "Return only one JSON object matching the DelegationPlanner schema.",
            "Use temporary roles only when existing static roles are not precise enough.",
        )
    )


def _lane_to_task_draft(*, root_task: TaskEnvelope, lane: DelegationLane) -> TaskDraft:
    spec = (
        lane.spec.to_task_spec(root_task.spec)
        if lane.spec is not None
        else _default_lane_spec(root_task=root_task, lane=lane)
    )
    acceptance_criteria = _merge_text(
        spec.acceptance_criteria,
        lane.acceptance_criteria,
    )
    evidence_expectations = _merge_text(
        spec.evidence_expectations,
        lane.evidence_expectations,
    )
    spec = spec.model_copy(
        update={
            "acceptance_criteria": acceptance_criteria,
            "evidence_expectations": evidence_expectations,
        }
    )
    return TaskDraft(
        title=lane.title,
        objective=lane.objective,
        role_id=lane.role_id,
        orchestration_node_id=_lane_node_id(lane.lane_id),
        depends_on_node_ids=tuple(
            _lane_node_id(dependency_id) for dependency_id in lane.depends_on_lane_ids
        ),
        spec=spec,
        spec_source_task_id=root_task.task_id if root_task.spec is not None else None,
        verification=VerificationPlan(
            checklist=("non_empty_response",),
            acceptance_criteria=acceptance_criteria,
            command_checks=tuple(
                VerificationCommand.model_validate({"command": command})
                for command in spec.verification_commands
            ),
            evidence_expectations=evidence_expectations,
            strictness=spec.strictness,
            formal_checks=()
            if spec.formal_verification is None
            else (spec.formal_verification,),
        ),
    )


def _default_lane_spec(*, root_task: TaskEnvelope, lane: DelegationLane) -> TaskSpec:
    projection = TaskSpecProjection(
        summary=lane.title,
        acceptance_criteria=lane.acceptance_criteria,
        evidence_expectations=lane.evidence_expectations,
    )
    return projection.to_task_spec(root_task.spec)


def _lane_node_id(lane_id: str) -> str:
    return f"{AUTO_LANE_NODE_PREFIX}{lane_id}"


def _available_planner_role_ids(
    *,
    role_registry: RoleRegistry,
    topology: RunTopologySnapshot | None,
    planner_role_id: str,
) -> tuple[str, ...]:
    allowed_role_ids = _topology_allowed_role_ids(topology)
    return tuple(
        role.role_id
        for role in role_registry.list_subagent_roles()
        if role.role_id != planner_role_id
        and (not allowed_role_ids or role.role_id in allowed_role_ids)
    )


def _topology_allowed_role_ids(
    topology: RunTopologySnapshot | None,
) -> frozenset[str]:
    if topology is None:
        return frozenset()
    return frozenset(role_id for role_id in topology.allowed_role_ids if role_id)


def _validate_allowed_static_lane_role(
    *,
    role_id: str,
    allowed_role_ids: frozenset[str],
) -> None:
    if allowed_role_ids and role_id not in allowed_role_ids:
        raise ValueError(f"delegation lane role is not allowed by topology: {role_id}")


def _validate_allowed_temporary_role_template(
    *,
    role: TemporaryRolePlan,
    allowed_role_ids: frozenset[str],
) -> None:
    if not allowed_role_ids:
        return
    template_role_id = role.template_role_id
    if template_role_id is None or template_role_id not in allowed_role_ids:
        raise ValueError(
            "temporary delegation role template is not allowed by topology: "
            f"{template_role_id or '<none>'}"
        )


def _validate_lane_role(role: RoleDefinition) -> None:
    if role.mode != RoleMode.SUBAGENT:
        raise ValueError(
            f"delegation lane role must be a subagent role: {role.role_id}"
        )


def _validate_non_planner_lane_role(*, role_id: str, planner_role_id: str) -> None:
    if role_id == planner_role_id:
        raise ValueError(f"planner role cannot execute delegation lanes: {role_id}")


def _validate_temporary_role_definitions(lanes: tuple[DelegationLane, ...]) -> None:
    definitions: dict[str, TemporaryRolePlan] = {}
    for lane in lanes:
        role = lane.temporary_role
        if role is None:
            continue
        existing = definitions.get(role.role_id)
        if existing is None:
            definitions[role.role_id] = role
            continue
        if existing != role:
            raise ValueError(f"conflicting temporary role definition: {role.role_id}")


def _validate_planner_role(role: RoleDefinition) -> None:
    if role.mode != RoleMode.SUBAGENT:
        raise ValueError(f"planner role must be a subagent role: {role.role_id}")


def _merge_text(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in first + second:
        normalized = item.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return tuple(merged)


def _assert_lane_graph_acyclic(lanes: tuple[DelegationLane, ...]) -> None:
    outgoing: dict[str, list[str]] = {lane.lane_id: [] for lane in lanes}
    indegree: dict[str, int] = {lane.lane_id: 0 for lane in lanes}
    for lane in lanes:
        for dependency_id in lane.depends_on_lane_ids:
            outgoing[dependency_id].append(lane.lane_id)
            indegree[lane.lane_id] += 1
    ready = deque(lane_id for lane_id, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        lane_id = ready.popleft()
        visited += 1
        for downstream_id in outgoing[lane_id]:
            indegree[downstream_id] -= 1
            if indegree[downstream_id] == 0:
                ready.append(downstream_id)
    if visited != len(lanes):
        raise ValueError("delegation lane dependencies must be acyclic")
