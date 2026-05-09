# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from relay_teams.agents.orchestration import (
    delegation_planning as delegation_planning_module,
)
from relay_teams.agents.orchestration.delegation_planning import (
    AUTO_LANE_NODE_PREFIX,
    AUTO_PLANNER_NODE_ID,
    DelegationPlan,
    DelegationLane,
    DelegationPlanningService,
    TaskSpecProjection,
    TemporaryRolePlan,
    parse_delegation_plan,
)
from relay_teams.agents.orchestration.graph_models import (
    OrchestrationGraph,
    OrchestrationGraphNode,
)
from relay_teams.agents.orchestration.policy_models import OrchestrationPolicy
from relay_teams.agents.orchestration.task_contracts import TaskDraft, TaskUpdate
from relay_teams.agents.orchestration.coordinator import CoordinatorGraph
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.ids import new_task_id
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskRecord,
    TaskSpec,
    VerificationPlan,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.roles.role_models import RoleDefinition, RoleMode
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.roles.temporary_role_models import TemporaryRoleSpec
from relay_teams.roles.temporary_role_repository import TemporaryRoleRepository
from relay_teams.sessions.runs.run_models import RunTopologySnapshot
from relay_teams.sessions.session_models import SessionMode


def test_parse_delegation_plan_rejects_unknown_dependency() -> None:
    with pytest.raises(ValueError, match="unknown dependency"):
        parse_delegation_plan(
            """
            {
              "should_decompose": true,
              "rationale": "complex",
              "lanes": [
                {
                  "lane_id": "implementation",
                  "title": "Implementation",
                  "role_id": "Crafter",
                  "objective": "Implement the change.",
                  "depends_on_lane_ids": ["missing"]
                }
              ]
            }
            """
        )


def test_delegation_plan_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="acyclic"):
        DelegationPlan.model_validate(
            {
                "should_decompose": True,
                "lanes": [
                    {
                        "lane_id": "a",
                        "title": "A",
                        "role_id": "Crafter",
                        "objective": "Do A.",
                        "depends_on_lane_ids": ["b"],
                    },
                    {
                        "lane_id": "b",
                        "title": "B",
                        "role_id": "Gater",
                        "objective": "Do B.",
                        "depends_on_lane_ids": ["a"],
                    },
                ],
            }
        )


def test_delegation_models_normalize_and_validate_boundaries() -> None:
    temp_role = TemporaryRolePlan.model_validate(
        {
            "role_id": "tmp_role",
            "name": "Tmp Role",
            "description": "temporary",
            "system_prompt": "Plan only.",
            "tools": " read ",
            "mcp_servers": None,
            "skills": [" skill ", ""],
        }
    )
    assert temp_role.tools == ("read",)
    assert temp_role.mcp_servers == ()
    assert temp_role.skills == ("skill",)

    with pytest.raises(ValueError, match="capability lists"):
        TemporaryRolePlan.model_validate(
            {
                "role_id": "tmp_bad",
                "name": "Tmp Bad",
                "description": "temporary",
                "system_prompt": "Plan only.",
                "tools": 123,
            }
        )

    projection = TaskSpecProjection.model_validate(
        {
            "summary": None,
            "requirements": " keep runtime ",
            "constraints": None,
            "acceptance_criteria": [" lane done ", ""],
        }
    )
    spec = projection.to_task_spec(None)
    assert spec.summary == ""
    assert spec.requirements == ("keep runtime",)
    assert spec.constraints == ()
    assert spec.acceptance_criteria == ("lane done",)

    with pytest.raises(ValueError, match="task spec projection fields"):
        TaskSpecProjection.model_validate({"requirements": 123})

    lane = DelegationLane.model_validate(
        {
            "lane_id": "lane",
            "title": "Lane",
            "role_id": "Crafter",
            "objective": "Do lane work.",
            "depends_on_lane_ids": None,
            "acceptance_criteria": " done ",
            "evidence_expectations": None,
        }
    )
    assert lane.depends_on_lane_ids == ()
    assert lane.acceptance_criteria == ("done",)
    assert lane.evidence_expectations == ()

    with pytest.raises(ValueError, match="delegation lane text fields"):
        DelegationLane.model_validate(
            {
                "lane_id": "bad_text",
                "title": "Bad",
                "role_id": "Crafter",
                "objective": "Do bad work.",
                "acceptance_criteria": 123,
            }
        )
    with pytest.raises(ValueError, match="temporary_role.role_id"):
        DelegationLane.model_validate(
            {
                "lane_id": "mismatch",
                "title": "Mismatch",
                "role_id": "tmp_lane",
                "objective": "Do mismatch work.",
                "temporary_role": {
                    "role_id": "tmp_other",
                    "name": "Other",
                    "description": "temporary",
                    "system_prompt": "Plan only.",
                },
            }
        )


def test_delegation_plan_rejects_empty_duplicate_and_self_dependency() -> None:
    with pytest.raises(ValueError, match="must contain lanes"):
        DelegationPlan.model_validate({"should_decompose": True, "lanes": []})
    with pytest.raises(ValueError, match="ids must be unique"):
        DelegationPlan.model_validate(
            {
                "should_decompose": True,
                "lanes": [
                    {
                        "lane_id": "same",
                        "title": "A",
                        "role_id": "Crafter",
                        "objective": "Do A.",
                    },
                    {
                        "lane_id": "same",
                        "title": "B",
                        "role_id": "Gater",
                        "objective": "Do B.",
                    },
                ],
            }
        )
    with pytest.raises(ValueError, match="cannot depend on itself"):
        DelegationPlan.model_validate(
            {
                "should_decompose": True,
                "lanes": [
                    {
                        "lane_id": "self",
                        "title": "Self",
                        "role_id": "Crafter",
                        "objective": "Do self work.",
                        "depends_on_lane_ids": ["self"],
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_planning_service_creates_planner_temp_role_and_lane_tasks(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "planning.db")
    temp_role_repo = TemporaryRoleRepository(tmp_path / "planning.db")
    role_registry = _role_registry()
    runtime_role_resolver = RuntimeRoleResolver(
        role_registry=role_registry,
        temporary_role_repository=temp_role_repo,
    )
    root_task = TaskEnvelope(
        task_id="root",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective=(
            "Implement a complex orchestration change with planning, temporary "
            "roles, spec preservation, runtime preservation, and documentation."
        ),
        verification=VerificationPlan(),
        spec=TaskSpec(
            summary="Preserve runtime features",
            requirements=("Use temporary roles",),
            constraints=("Do not bypass TaskExecutionService",),
            acceptance_criteria=("DelegationPlanner creates bounded lanes",),
            evidence_expectations=("Show delegated lane evidence",),
        ),
    )
    _ = await task_repo.create_async(root_task)

    fake_task_service = _FakeTaskService(
        task_repo=task_repo,
        planner_output="""
        {
          "should_decompose": true,
          "rationale": "Long orchestration work benefits from parallel lanes.",
          "lanes": [
            {
              "lane_id": "implementation",
              "title": "Implementation lane",
              "role_id": "tmp_parallel_crafter",
              "objective": "Implement the runtime-preserving delegation change.",
              "acceptance_criteria": ["Implementation keeps runtime path intact"],
              "evidence_expectations": ["List tests and files changed"],
              "temporary_role": {
                "role_id": "tmp_parallel_crafter",
                "name": "Parallel Crafter",
                "description": "Run-scoped implementation role.",
                "system_prompt": "Implement a focused lane and report evidence.",
                "template_role_id": "Crafter"
              },
              "spec": {
                "summary": "Implementation lane",
                "requirements": ["Use temporary roles"],
                "constraints": ["Do not bypass TaskExecutionService"],
                "acceptance_criteria": ["Implementation keeps runtime path intact"],
                "evidence_expectations": ["List tests and files changed"]
              }
            }
          ]
        }
        """,
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=fake_task_service,
        role_registry=role_registry,
        runtime_role_resolver=runtime_role_resolver,
    )

    plan = await service.plan_and_create_tasks_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
    )

    assert plan is not None
    assert plan.should_decompose
    temp_role = await temp_role_repo.get_async(
        run_id="run-1",
        role_id="tmp_parallel_crafter",
    )
    assert temp_role.role.template_role_id == "Crafter"
    records = await task_repo.list_by_trace_async("run-1")
    node_ids = {
        record.envelope.orchestration_node_id
        for record in records
        if record.envelope.orchestration_node_id is not None
    }
    assert AUTO_PLANNER_NODE_ID in node_ids
    assert f"{AUTO_LANE_NODE_PREFIX}implementation" in node_ids
    lane_record = next(
        record
        for record in records
        if record.envelope.orchestration_node_id
        == f"{AUTO_LANE_NODE_PREFIX}implementation"
    )
    assert lane_record.envelope.role_id == "tmp_parallel_crafter"
    assert lane_record.envelope.spec is not None
    assert (
        "DelegationPlanner creates bounded lanes"
        in lane_record.envelope.spec.acceptance_criteria
    )
    assert (
        "Implementation keeps runtime path intact"
        in lane_record.envelope.spec.acceptance_criteria
    )


@pytest.mark.asyncio
async def test_planning_service_creates_static_lanes_with_dependencies(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "static-lanes.db")
    root_task = _root_task(
        objective=" ".join(f"word{i}" for i in range(45)),
        spec=TaskSpec(
            summary="Root spec",
            requirements=("Preserve existing runtime",),
            constraints=("Use the task service",),
            acceptance_criteria=("lane done",),
            verification_commands=("pytest tests/unit_tests/agents/orchestration",),
            evidence_expectations=("report evidence",),
        ),
    )
    _ = await task_repo.create_async(root_task)
    fake_task_service = _FakeTaskService(
        task_repo=task_repo,
        planner_output="""
        {
          "should_decompose": true,
          "rationale": "Two static lanes are enough.",
          "lanes": [
            {
              "lane_id": "research",
              "title": "Research lane",
              "role_id": "Crafter",
              "objective": "Research the implementation surface.",
              "acceptance_criteria": ["lane done", "research done"],
              "evidence_expectations": ["report evidence"]
            },
            {
              "lane_id": "verify",
              "title": "Verification lane",
              "role_id": "Gater",
              "objective": "Verify the implementation surface.",
              "depends_on_lane_ids": ["research"],
              "acceptance_criteria": ["verification done"],
              "evidence_expectations": ["verification evidence"]
            }
          ]
        }
        """,
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=fake_task_service,
        role_registry=_role_registry(),
    )

    plan = await service.plan_and_create_tasks_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(),
    )

    assert plan is not None
    records = await task_repo.list_by_trace_async(root_task.trace_id)
    lane_records = {
        record.envelope.orchestration_node_id: record.envelope
        for record in records
        if record.envelope.orchestration_node_id is not None
        and record.envelope.orchestration_node_id.startswith(AUTO_LANE_NODE_PREFIX)
    }
    research = lane_records[f"{AUTO_LANE_NODE_PREFIX}research"]
    verify = lane_records[f"{AUTO_LANE_NODE_PREFIX}verify"]
    assert research.spec is not None
    assert research.spec.acceptance_criteria == ("lane done", "research done")
    assert research.verification.command_checks[0].command == (
        "pytest",
        "tests/unit_tests/agents/orchestration",
    )
    assert verify.depends_on_task_ids == (research.task_id,)
    assert verify.spec_source_task_id == root_task.task_id


@pytest.mark.asyncio
async def test_planning_service_skip_and_fallback_paths(tmp_path: Path) -> None:
    root_task = _root_task(objective="short")
    task_repo = TaskRepository(tmp_path / "skip.db")
    _ = await task_repo.create_async(root_task)
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
    )

    assert not await service.should_plan_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(auto_plan_long_tasks=False),
    )
    assert not await service.should_plan_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(max_parallel_delegated_tasks=0),
    )
    assert not await service.should_plan_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(max_orchestration_cycles=0),
    )
    assert not await service.should_plan_async(
        root_task=root_task,
        topology=_topology_with_graph(),
        policy=OrchestrationPolicy(),
    )
    assert not await service.should_plan_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(planner_role_id="MissingPlanner"),
    )
    assert not await service.should_plan_async(
        root_task=root_task,
        topology=_topology_with_allowed_roles(("Crafter",)),
        policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
    )
    assert not await service.should_plan_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(),
    )

    existing_auto_task = root_task.model_copy(
        update={
            "task_id": "auto-existing",
            "parent_task_id": root_task.task_id,
            "role_id": "DelegationPlanner",
            "orchestration_node_id": AUTO_PLANNER_NODE_ID,
        }
    )
    _ = await task_repo.create_async(existing_auto_task)
    long_task = _root_task(task_id="long", objective="x" * 250)
    assert not await service.should_plan_async(
        root_task=long_task,
        topology=None,
        policy=OrchestrationPolicy(),
    )

    true_task_repo = TaskRepository(tmp_path / "true.db")
    true_service = DelegationPlanningService(
        task_repo=true_task_repo,
        task_service=_FakeTaskService(task_repo=true_task_repo, planner_output=""),
        role_registry=_role_registry(),
    )
    assert await true_service.should_plan_async(
        root_task=_root_task(task_id="inline", objective="short"),
        topology=None,
        policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
    )
    assert await true_service.should_plan_async(
        root_task=_root_task(task_id="long-chars", objective="x" * 250),
        topology=None,
        policy=OrchestrationPolicy(),
    )
    assert await true_service.should_plan_async(
        root_task=_root_task(
            task_id="long-words",
            objective=" ".join(f"word{i}" for i in range(45)),
        ),
        topology=None,
        policy=OrchestrationPolicy(),
    )
    assert await true_service.should_plan_async(
        root_task=_root_task(
            task_id="spec-heavy",
            objective="short",
            spec=TaskSpec(
                summary="Spec heavy",
                requirements=("one",),
                constraints=("two",),
                acceptance_criteria=("three",),
            ),
        ),
        topology=None,
        policy=OrchestrationPolicy(),
    )

    fallback_plan = await service.plan_and_create_tasks_async(
        root_task=_root_task(task_id="fallback", objective="x" * 250),
        topology=None,
        policy=OrchestrationPolicy(),
    )
    assert fallback_plan is None


@pytest.mark.asyncio
async def test_planning_service_skips_primary_planner_role(tmp_path: Path) -> None:
    task_repo = TaskRepository(tmp_path / "primary-planner.db")
    role_registry = _role_registry()
    role_registry.register(
        _role("DelegationPlanner", "Delegation Planner", RoleMode.PRIMARY, ("read",))
    )
    root_task = _root_task(objective="x" * 250)
    _ = await task_repo.create_async(root_task)
    fake_task_service = _FakeTaskService(
        task_repo=task_repo,
        planner_output='{"should_decompose": false, "lanes": []}',
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=fake_task_service,
        role_registry=role_registry,
    )

    assert not await service.should_plan_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
    )
    assert (
        await service.plan_and_create_tasks_async(
            root_task=root_task,
            topology=None,
            policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
        )
        is None
    )
    assert fake_task_service.created_drafts == []


@pytest.mark.asyncio
async def test_planning_service_respects_topology_allowed_roles(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "allowlist.db")
    root_task = _root_task(objective="x" * 250)
    _ = await task_repo.create_async(root_task)
    fake_task_service = _FakeTaskService(
        task_repo=task_repo,
        planner_output="""
        {
          "should_decompose": true,
          "rationale": "Use a disallowed static role.",
          "lanes": [
            {
              "lane_id": "verify",
              "title": "Verification lane",
              "role_id": "Gater",
              "objective": "Verify with a role outside the preset allowlist."
            }
          ]
        }
        """,
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=fake_task_service,
        role_registry=_role_registry(),
    )

    plan = await service.plan_and_create_tasks_async(
        root_task=root_task,
        topology=_topology_with_allowed_roles(("DelegationPlanner", "Crafter")),
        policy=OrchestrationPolicy(),
    )

    assert plan is None
    assert (
        "Available subagent roles: Crafter"
        in fake_task_service.created_drafts[0].objective
    )
    assert "Gater" not in fake_task_service.created_drafts[0].objective


@pytest.mark.asyncio
async def test_planning_service_allows_existing_temporary_role_with_allowlist(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "allow-existing-temp.db")
    temp_role_repo = TemporaryRoleRepository(tmp_path / "allow-existing-temp.db")
    runtime_role_resolver = RuntimeRoleResolver(
        role_registry=_role_registry(),
        temporary_role_repository=temp_role_repo,
    )
    _ = await runtime_role_resolver.create_temporary_role_async(
        run_id="run-1",
        session_id="session-1",
        role=TemporaryRoleSpec(
            role_id="tmp_existing",
            name="Tmp Existing",
            description="temporary",
            system_prompt="Do existing work.",
        ),
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
        runtime_role_resolver=runtime_role_resolver,
    )
    plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "tmp",
                    "title": "Temporary reuse lane",
                    "role_id": "tmp_existing",
                    "objective": "Reuse an existing run-scoped role.",
                }
            ],
        }
    )

    created_role_ids = await service._ensure_plan_roles_async(
        run_id="run-1",
        session_id="session-1",
        plan=plan,
        policy=OrchestrationPolicy(),
        topology=_topology_with_allowed_roles(("DelegationPlanner", "Crafter")),
    )

    assert created_role_ids == ()


@pytest.mark.asyncio
async def test_planning_service_accepts_temp_role_reference_before_definition(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "temp-reference-before-definition.db")
    temp_role_repo = TemporaryRoleRepository(
        tmp_path / "temp-reference-before-definition.db"
    )
    runtime_role_resolver = RuntimeRoleResolver(
        role_registry=_role_registry(),
        temporary_role_repository=temp_role_repo,
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
        runtime_role_resolver=runtime_role_resolver,
    )
    plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "use_tmp",
                    "title": "Use temporary lane",
                    "role_id": "tmp_late",
                    "objective": "Reuse a temporary role defined later.",
                },
                {
                    "lane_id": "define_tmp",
                    "title": "Define temporary lane",
                    "role_id": "tmp_late",
                    "objective": "Define and use the temporary role.",
                    "temporary_role": {
                        "role_id": "tmp_late",
                        "name": "Tmp Late",
                        "description": "temporary",
                        "system_prompt": "Do temporary work.",
                        "template_role_id": "Crafter",
                    },
                },
            ],
        }
    )

    created_role_ids = await service._ensure_plan_roles_async(
        run_id="run-1",
        session_id="session-1",
        plan=plan,
        policy=OrchestrationPolicy(max_temporary_roles_per_run=1),
        topology=_topology_with_allowed_roles(("DelegationPlanner", "Crafter")),
    )

    assert created_role_ids == ("tmp_late",)
    record = await temp_role_repo.get_async(run_id="run-1", role_id="tmp_late")
    assert record.role.template_role_id == "Crafter"


@pytest.mark.asyncio
async def test_planning_service_rejects_conflicting_temporary_role_definitions(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "conflicting-temp-definitions.db")
    temp_role_repo = TemporaryRoleRepository(
        tmp_path / "conflicting-temp-definitions.db"
    )
    runtime_role_resolver = RuntimeRoleResolver(
        role_registry=_role_registry(),
        temporary_role_repository=temp_role_repo,
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
        runtime_role_resolver=runtime_role_resolver,
    )
    plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "tmp_a",
                    "title": "Tmp A",
                    "role_id": "tmp_conflict",
                    "objective": "Use the first temporary role definition.",
                    "temporary_role": {
                        "role_id": "tmp_conflict",
                        "name": "Tmp Conflict",
                        "description": "temporary",
                        "system_prompt": "First prompt.",
                    },
                },
                {
                    "lane_id": "tmp_b",
                    "title": "Tmp B",
                    "role_id": "tmp_conflict",
                    "objective": "Use the conflicting temporary role definition.",
                    "temporary_role": {
                        "role_id": "tmp_conflict",
                        "name": "Tmp Conflict",
                        "description": "temporary",
                        "system_prompt": "Second prompt.",
                    },
                },
            ],
        }
    )

    with pytest.raises(ValueError, match="conflicting temporary role definition"):
        await service._ensure_plan_roles_async(
            run_id="run-1",
            session_id="session-1",
            plan=plan,
            policy=OrchestrationPolicy(max_temporary_roles_per_run=1),
        )
    assert await temp_role_repo.list_by_run_async("run-1") == ()


@pytest.mark.asyncio
async def test_planning_service_rejects_disallowed_temporary_role_template(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "temp-allowlist.db")
    temp_role_repo = TemporaryRoleRepository(tmp_path / "temp-allowlist.db")
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
        runtime_role_resolver=RuntimeRoleResolver(
            role_registry=_role_registry(),
            temporary_role_repository=temp_role_repo,
        ),
    )
    plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "tmp",
                    "title": "Temporary lane",
                    "role_id": "tmp_disallowed",
                    "objective": "Use a disallowed template.",
                    "temporary_role": {
                        "role_id": "tmp_disallowed",
                        "name": "Tmp Disallowed",
                        "description": "temporary",
                        "system_prompt": "Do temp work.",
                        "template_role_id": "Gater",
                    },
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="template is not allowed"):
        await service._ensure_plan_roles_async(
            run_id="run-1",
            session_id="session-1",
            plan=plan,
            policy=OrchestrationPolicy(),
            topology=_topology_with_allowed_roles(("DelegationPlanner", "Crafter")),
        )


@pytest.mark.asyncio
async def test_planning_service_replace_role_registry_affects_planning(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "replace-registry.db")
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=RoleRegistry(),
    )
    root_task = _root_task(objective="x" * 250)
    assert not await service.should_plan_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(),
    )

    service.replace_role_registry(_role_registry())

    assert await service.should_plan_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(),
    )


@pytest.mark.asyncio
async def test_planning_service_returns_non_decomposed_plan(tmp_path: Path) -> None:
    task_repo = TaskRepository(tmp_path / "non-decomposed.db")
    root_task = _root_task(objective="x" * 250)
    _ = await task_repo.create_async(root_task)
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(
            task_repo=task_repo,
            planner_output='{"should_decompose": false, "rationale": "simple", "lanes": []}',
        ),
        role_registry=_role_registry(),
    )

    plan = await service.plan_and_create_tasks_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(),
    )

    assert plan is not None
    assert not plan.should_decompose


@pytest.mark.asyncio
async def test_planning_service_rejects_temporary_role_boundaries(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "boundaries.db")
    temp_role_repo = TemporaryRoleRepository(tmp_path / "boundaries.db")
    runtime_role_resolver = RuntimeRoleResolver(
        role_registry=_role_registry(),
        temporary_role_repository=temp_role_repo,
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
        runtime_role_resolver=runtime_role_resolver,
    )
    plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "tmp",
                    "title": "Tmp",
                    "role_id": "tmp_lane",
                    "objective": "Do temp work.",
                    "temporary_role": {
                        "role_id": "tmp_lane",
                        "name": "Tmp Lane",
                        "description": "temporary",
                        "system_prompt": "Do temp work.",
                    },
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="temporary roles are disabled"):
        await service._ensure_plan_roles_async(
            run_id="run-1",
            session_id="session-1",
            plan=plan,
            policy=OrchestrationPolicy(prefer_temporary_roles_for_long_tasks=False),
        )
    with pytest.raises(ValueError, match="temporary role limit"):
        await service._ensure_plan_roles_async(
            run_id="run-1",
            session_id="session-1",
            plan=plan,
            policy=OrchestrationPolicy(max_temporary_roles_per_run=0),
        )

    no_resolver_service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
    )
    temporary_role = plan.lanes[0].temporary_role
    assert temporary_role is not None
    with pytest.raises(RuntimeError, match="requires RuntimeRoleResolver"):
        await no_resolver_service._create_temporary_role_async(
            run_id="run-1",
            session_id="session-1",
            role=temporary_role,
        )

    static_conflict = TemporaryRolePlan(
        role_id="Crafter",
        name="Static Conflict",
        description="temporary",
        system_prompt="Do temp work.",
    )
    with pytest.raises(ValueError, match="conflicts with static role"):
        await service._create_temporary_role_async(
            run_id="run-1",
            session_id="session-1",
            role=static_conflict,
        )


@pytest.mark.asyncio
async def test_planning_service_counts_existing_temporary_roles_against_limit(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "existing-temp-limit.db")
    temp_role_repo = TemporaryRoleRepository(tmp_path / "existing-temp-limit.db")
    runtime_role_resolver = RuntimeRoleResolver(
        role_registry=_role_registry(),
        temporary_role_repository=temp_role_repo,
    )
    _ = await runtime_role_resolver.create_temporary_role_async(
        run_id="run-1",
        session_id="session-1",
        role=TemporaryRoleSpec(
            role_id="tmp_existing",
            name="Tmp Existing",
            description="temporary",
            system_prompt="Do existing work.",
        ),
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
        runtime_role_resolver=runtime_role_resolver,
    )
    plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "tmp",
                    "title": "Tmp",
                    "role_id": "tmp_lane",
                    "objective": "Do temp work.",
                    "temporary_role": {
                        "role_id": "tmp_lane",
                        "name": "Tmp Lane",
                        "description": "temporary",
                        "system_prompt": "Do temp work.",
                    },
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="temporary role limit"):
        await service._ensure_plan_roles_async(
            run_id="run-1",
            session_id="session-1",
            plan=plan,
            policy=OrchestrationPolicy(max_temporary_roles_per_run=1),
        )


@pytest.mark.asyncio
async def test_planning_service_reuses_existing_temporary_role_without_overwrite(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "reuse-existing-temp.db")
    temp_role_repo = TemporaryRoleRepository(tmp_path / "reuse-existing-temp.db")
    runtime_role_resolver = RuntimeRoleResolver(
        role_registry=_role_registry(),
        temporary_role_repository=temp_role_repo,
    )
    _ = await runtime_role_resolver.create_temporary_role_async(
        run_id="run-1",
        session_id="session-1",
        role=TemporaryRoleSpec(
            role_id="tmp_existing",
            name="Tmp Existing",
            description="temporary",
            system_prompt="Keep this prompt.",
            tools=("read",),
        ),
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
        runtime_role_resolver=runtime_role_resolver,
    )
    plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "tmp",
                    "title": "Tmp",
                    "role_id": "tmp_existing",
                    "objective": "Reuse existing temp work.",
                    "temporary_role": {
                        "role_id": "tmp_existing",
                        "name": "Tmp Replacement",
                        "description": "replacement",
                        "system_prompt": "Do not store this prompt.",
                        "tools": ["write"],
                    },
                }
            ],
        }
    )

    await service._ensure_plan_roles_async(
        run_id="run-1",
        session_id="session-1",
        plan=plan,
        policy=OrchestrationPolicy(max_temporary_roles_per_run=1),
    )

    record = await temp_role_repo.get_async(run_id="run-1", role_id="tmp_existing")
    assert record.role.name == "Tmp Existing"
    assert record.role.system_prompt == "Keep this prompt."
    assert record.role.tools == ("read",)


@pytest.mark.asyncio
async def test_planning_service_rolls_back_created_temp_role_on_lane_failure(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "rollback-lane-failure.db")
    temp_role_repo = TemporaryRoleRepository(tmp_path / "rollback-lane-failure.db")
    root_task = _root_task(objective="x" * 250)
    _ = await task_repo.create_async(root_task)
    runtime_role_resolver = RuntimeRoleResolver(
        role_registry=_role_registry(),
        temporary_role_repository=temp_role_repo,
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_PartiallyFailingLaneCreationTaskService(
            task_repo=task_repo,
            planner_output="""
            {
              "should_decompose": true,
              "rationale": "Create then fail lane task creation.",
              "lanes": [
                {
                  "lane_id": "tmp",
                  "title": "Tmp",
                  "role_id": "tmp_lane",
                  "objective": "Do temp work.",
                  "temporary_role": {
                    "role_id": "tmp_lane",
                    "name": "Tmp Lane",
                    "description": "temporary",
                    "system_prompt": "Do temp work."
                  }
                }
              ]
            }
            """,
        ),
        role_registry=_role_registry(),
        runtime_role_resolver=runtime_role_resolver,
    )

    assert (
        await service.plan_and_create_tasks_async(
            root_task=root_task,
            topology=None,
            policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
        )
        is None
    )
    assert await temp_role_repo.list_by_run_async("run-1") == ()
    records = await task_repo.list_by_trace_async("run-1")
    assert all(
        not (record.envelope.orchestration_node_id or "").startswith(
            AUTO_LANE_NODE_PREFIX
        )
        for record in records
    )
    assert any(
        record.envelope.orchestration_node_id == AUTO_PLANNER_NODE_ID
        for record in records
    )


@pytest.mark.asyncio
async def test_planning_service_rolls_back_temp_role_on_later_validation_failure(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "rollback-validation-failure.db")
    temp_role_repo = TemporaryRoleRepository(
        tmp_path / "rollback-validation-failure.db"
    )
    runtime_role_resolver = RuntimeRoleResolver(
        role_registry=_role_registry(),
        temporary_role_repository=temp_role_repo,
    )
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
        runtime_role_resolver=runtime_role_resolver,
    )
    plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "tmp",
                    "title": "Tmp",
                    "role_id": "tmp_lane",
                    "objective": "Do temp work.",
                    "temporary_role": {
                        "role_id": "tmp_lane",
                        "name": "Tmp Lane",
                        "description": "temporary",
                        "system_prompt": "Do temp work.",
                    },
                },
                {
                    "lane_id": "coord",
                    "title": "Coordinator lane",
                    "role_id": "Coordinator",
                    "objective": "Improperly use a primary role.",
                },
            ],
        }
    )

    with pytest.raises(ValueError, match="must be a subagent role"):
        await service._ensure_plan_roles_async(
            run_id="run-1",
            session_id="session-1",
            plan=plan,
            policy=OrchestrationPolicy(),
        )
    assert await temp_role_repo.list_by_run_async("run-1") == ()


@pytest.mark.asyncio
async def test_planning_service_rejects_primary_lane_roles(tmp_path: Path) -> None:
    task_repo = TaskRepository(tmp_path / "primary-role.db")
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
    )
    plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "coord",
                    "title": "Coordinator lane",
                    "role_id": "Coordinator",
                    "objective": "Improperly use a primary role.",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="must be a subagent role"):
        await service._ensure_plan_roles_async(
            run_id="run-1",
            session_id="session-1",
            plan=plan,
            policy=OrchestrationPolicy(),
        )


@pytest.mark.asyncio
async def test_planning_service_rejects_planner_as_lane_role(tmp_path: Path) -> None:
    task_repo = TaskRepository(tmp_path / "planner-lane-role.db")
    service = DelegationPlanningService(
        task_repo=task_repo,
        task_service=_FakeTaskService(task_repo=task_repo, planner_output=""),
        role_registry=_role_registry(),
    )
    plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "plan",
                    "title": "Planner lane",
                    "role_id": "DelegationPlanner",
                    "objective": "Improperly use the planner as a worker lane.",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="planner role cannot execute"):
        await service._ensure_plan_roles_async(
            run_id="run-1",
            session_id="session-1",
            plan=plan,
            policy=OrchestrationPolicy(),
        )


def test_parse_delegation_plan_rejects_missing_json_and_bad_task_ids() -> None:
    with pytest.raises(ValueError, match="does not contain a JSON object"):
        parse_delegation_plan("no json here")

    with pytest.raises(ValueError, match="did not include tasks"):
        delegation_planning_module._first_task_id({"tasks": []})
    with pytest.raises(ValueError, match="task is invalid"):
        delegation_planning_module._first_task_id({"tasks": ["bad"]})
    with pytest.raises(ValueError, match="did not include task_id"):
        delegation_planning_module._first_task_id({"tasks": [{}]})


def test_validate_plan_bounds_rejects_lane_and_temp_role_limits() -> None:
    lanes = [
        {
            "lane_id": f"lane{i}",
            "title": f"Lane {i}",
            "role_id": "Crafter",
            "objective": f"Do lane {i}.",
        }
        for i in range(6)
    ]
    with pytest.raises(ValueError, match="lane count"):
        DelegationPlanningService._validate_plan_bounds(
            plan=DelegationPlan.model_validate(
                {"should_decompose": True, "lanes": lanes}
            ),
            policy=OrchestrationPolicy(),
        )

    temp_plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "tmp",
                    "title": "Tmp",
                    "role_id": "tmp_lane",
                    "objective": "Do temp work.",
                    "temporary_role": {
                        "role_id": "tmp_lane",
                        "name": "Tmp Lane",
                        "description": "temporary",
                        "system_prompt": "Do temp work.",
                    },
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="temporary role count"):
        DelegationPlanningService._validate_plan_bounds(
            plan=temp_plan,
            policy=OrchestrationPolicy(max_temporary_roles_per_run=0),
        )

    shared_temp_plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "tmp_a",
                    "title": "Tmp A",
                    "role_id": "tmp_shared",
                    "objective": "Do temp work A.",
                    "temporary_role": {
                        "role_id": "tmp_shared",
                        "name": "Tmp Shared",
                        "description": "temporary",
                        "system_prompt": "Do shared temp work.",
                    },
                },
                {
                    "lane_id": "tmp_b",
                    "title": "Tmp B",
                    "role_id": "tmp_shared",
                    "objective": "Do temp work B.",
                    "temporary_role": {
                        "role_id": "tmp_shared",
                        "name": "Tmp Shared",
                        "description": "temporary",
                        "system_prompt": "Do shared temp work.",
                    },
                },
            ],
        }
    )
    DelegationPlanningService._validate_plan_bounds(
        plan=shared_temp_plan,
        policy=OrchestrationPolicy(max_temporary_roles_per_run=1),
    )


@pytest.mark.asyncio
async def test_coordinator_auto_delegation_planning_accepts_plan() -> None:
    root_task = _root_task()
    accepted_plan = DelegationPlan.model_validate(
        {
            "should_decompose": True,
            "lanes": [
                {
                    "lane_id": "lane",
                    "title": "Lane",
                    "role_id": "Crafter",
                    "objective": "Do lane work.",
                }
            ],
        }
    )
    coordinator = CoordinatorGraph.model_construct(
        planning_service=_FakePlanningService(accepted_plan)
    )

    accepted = await coordinator._run_auto_delegation_planning_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(),
    )

    assert accepted


@pytest.mark.asyncio
async def test_coordinator_auto_delegation_planning_rejects_empty_plan() -> None:
    root_task = _root_task()
    coordinator = CoordinatorGraph.model_construct(
        planning_service=_FakePlanningService(
            DelegationPlan(should_decompose=False, rationale="simple")
        )
    )

    accepted = await coordinator._run_auto_delegation_planning_async(
        root_task=root_task,
        topology=None,
        policy=OrchestrationPolicy(),
    )

    assert not accepted


class _FakeTaskService:
    def __init__(self, *, task_repo: TaskRepository, planner_output: str) -> None:
        self._task_repo = task_repo
        self._planner_output = planner_output
        self.created_drafts: list[TaskDraft] = []

    async def create_tasks(
        self,
        *,
        run_id: str,
        tasks: list[TaskDraft],
    ) -> dict[str, JsonValue]:
        self.created_drafts.extend(tasks)
        root = await self._root_task_async(run_id)
        created: list[JsonValue] = []
        node_to_task_id = {
            record.envelope.orchestration_node_id: record.envelope.task_id
            for record in await self._task_repo.list_by_trace_async(run_id)
            if record.envelope.orchestration_node_id is not None
        }
        for draft in tasks:
            task_id = new_task_id().value
            if draft.orchestration_node_id is not None:
                node_to_task_id[draft.orchestration_node_id] = task_id
            envelope = TaskEnvelope(
                task_id=task_id,
                session_id=root.envelope.session_id,
                parent_task_id=root.envelope.task_id,
                trace_id=run_id,
                role_id=draft.role_id,
                title=draft.title,
                objective=draft.objective,
                spec=draft.spec,
                spec_source_task_id=draft.spec_source_task_id,
                verification=draft.verification or VerificationPlan(),
                orchestration_node_id=draft.orchestration_node_id,
                depends_on_task_ids=tuple(
                    node_to_task_id[node_id] for node_id in draft.depends_on_node_ids
                ),
            )
            record = await self._task_repo.create_async(envelope)
            await self._task_repo.update_status_async(
                task_id=record.envelope.task_id,
                status=TaskStatus.ASSIGNED,
                assigned_instance_id=f"inst-{record.envelope.task_id}",
            )
            created.append(cast(JsonValue, {"task_id": record.envelope.task_id}))
        return {"tasks": created}

    async def dispatch_task(
        self,
        *,
        run_id: str | None,
        task_id: str,
        role_id: str,
        prompt: str = "",
    ) -> dict[str, JsonValue]:
        del run_id, role_id, prompt
        await self._task_repo.update_status_async(
            task_id=task_id,
            status=TaskStatus.COMPLETED,
            result=self._planner_output,
        )
        return {"task": {"task_id": task_id, "status": TaskStatus.COMPLETED.value}}

    async def update_task_async(
        self,
        *,
        run_id: str | None,
        task_id: str,
        update: TaskUpdate,
    ) -> dict[str, JsonValue]:
        del run_id, task_id, update
        raise NotImplementedError

    async def list_delegated_tasks_async(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> dict[str, JsonValue]:
        del run_id, include_root
        return {"tasks": []}

    async def list_run_tasks_async(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> dict[str, JsonValue]:
        del run_id, include_root
        return {"tasks": []}

    async def _root_task_async(self, run_id: str) -> TaskRecord:
        for record in await self._task_repo.list_by_trace_async(run_id):
            if record.envelope.parent_task_id is None:
                return record
        raise KeyError(run_id)


class _PartiallyFailingLaneCreationTaskService(_FakeTaskService):
    async def create_tasks(
        self,
        *,
        run_id: str,
        tasks: list[TaskDraft],
    ) -> dict[str, JsonValue]:
        if any(
            (draft.orchestration_node_id or "").startswith(AUTO_LANE_NODE_PREFIX)
            for draft in tasks
        ):
            _ = await super().create_tasks(run_id=run_id, tasks=tasks[:1])
            raise RuntimeError("partial lane creation failed")
        return await super().create_tasks(run_id=run_id, tasks=tasks)


class _FakePlanningService:
    def __init__(self, plan: DelegationPlan | None) -> None:
        self._plan = plan

    async def plan_and_create_tasks_async(
        self,
        *,
        root_task: TaskEnvelope,
        topology: RunTopologySnapshot | None,
        policy: OrchestrationPolicy,
    ) -> DelegationPlan | None:
        del root_task, topology, policy
        return self._plan


def _root_task(
    *,
    task_id: str = "root",
    objective: str = "Coordinate implementation.",
    spec: TaskSpec | None = None,
) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=task_id,
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective=objective,
        verification=VerificationPlan(),
        spec=spec,
    )


def _topology_with_graph() -> RunTopologySnapshot:
    return RunTopologySnapshot(
        session_mode=SessionMode.NORMAL,
        main_agent_role_id="MainAgent",
        normal_root_role_id="Coordinator",
        coordinator_role_id="Coordinator",
        orchestration_graph=OrchestrationGraph(
            nodes=(
                OrchestrationGraphNode(
                    node_id="start",
                    role_id="Crafter",
                    objective="Do graph work.",
                ),
            ),
        ),
    )


def _topology_with_allowed_roles(role_ids: tuple[str, ...]) -> RunTopologySnapshot:
    return RunTopologySnapshot(
        session_mode=SessionMode.NORMAL,
        main_agent_role_id="MainAgent",
        normal_root_role_id="Coordinator",
        coordinator_role_id="Coordinator",
        allowed_role_ids=role_ids,
    )


def _role_registry() -> RoleRegistry:
    registry = RoleRegistry()
    for role in (
        _role("MainAgent", "Main Agent", RoleMode.PRIMARY, ()),
        _role("Coordinator", "Coordinator", RoleMode.PRIMARY, ("orch_create_tasks",)),
        _role("DelegationPlanner", "Delegation Planner", RoleMode.SUBAGENT, ("read",)),
        _role("Crafter", "Crafter", RoleMode.SUBAGENT, ("read", "edit")),
        _role("Gater", "Gater", RoleMode.SUBAGENT, ("read",)),
    ):
        registry.register(role)
    return registry


def _role(
    role_id: str,
    name: str,
    mode: RoleMode,
    tools: tuple[str, ...],
) -> RoleDefinition:
    return RoleDefinition(
        role_id=role_id,
        name=name,
        description=f"{name} role",
        version="1.0",
        mode=mode,
        tools=tools,
        system_prompt=f"You are {name}.",
    )
