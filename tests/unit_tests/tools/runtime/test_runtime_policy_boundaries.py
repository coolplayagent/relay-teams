# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import cast

from pydantic import JsonValue

from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.models import (
    AgentRuntimeRecord,
    RuntimeToolSnapshotEntry,
    RuntimeToolsSnapshot,
)
from relay_teams.computer import ComputerActionRisk
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime.execution import execute_tool
from relay_teams.tools.runtime.models import ToolApprovalRequest, ToolRuntimeDecision
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from tests.unit_tests.tools.runtime.test_execution import (
    _FakeApprovalManager,
    _FakeCtx,
    _FakeDeps,
    _tool_result_payloads,
)


class _PolicyDeps(_FakeDeps):
    agent_repo: object | None = None
    runtime_role_resolver: object | None = None


class _FailingRoleResolver:
    async def get_effective_role_async(self, *, run_id: str, role_id: str) -> object:
        _ = (run_id, role_id)
        raise RuntimeError("role unavailable")


class _RuntimeToolsAgentRepo:
    def __init__(self, runtime_tools_json: str) -> None:
        self._runtime_tools_json = runtime_tools_json

    async def get_instance_async(self, instance_id: str) -> AgentRuntimeRecord:
        return AgentRuntimeRecord(
            run_id="run-1",
            trace_id="trace-1",
            session_id="session-1",
            instance_id=instance_id,
            role_id="spec_coder",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            status=InstanceStatus.RUNNING,
            runtime_tools_json=self._runtime_tools_json,
        )


def test_policy_denies_tools_outside_role_allowlist_even_in_yolo() -> None:
    policy = ToolApprovalPolicy(yolo=True)

    decision = policy.evaluate(
        "write",
        role_id="reviewer",
        allowed_tools=("read",),
    )

    assert decision.runtime_decision == ToolRuntimeDecision.DENY
    assert decision.required is False
    assert "not authorized" in decision.reason


def test_policy_reports_approval_as_runtime_decision() -> None:
    decision = ToolApprovalPolicy().evaluate("shell", allowed_tools=("shell",))

    assert decision.runtime_decision == ToolRuntimeDecision.REQUIRE_APPROVAL
    assert decision.required is True


def test_policy_denied_tools_override_approval_and_yolo() -> None:
    decision = ToolApprovalPolicy(
        yolo=True, denied_tools=frozenset({"shell"})
    ).evaluate(
        "shell",
        allowed_tools=("shell",),
    )

    assert decision.runtime_decision == ToolRuntimeDecision.DENY
    assert decision.required is False
    assert "denied by runtime policy" in decision.reason


def test_policy_requires_approval_for_guarded_requests() -> None:
    decision = ToolApprovalPolicy().evaluate(
        "read",
        ToolApprovalRequest(risk_level=ComputerActionRisk.GUARDED),
    )

    assert decision.runtime_decision == ToolRuntimeDecision.REQUIRE_APPROVAL
    assert decision.required is True
    assert decision.risk_level == ComputerActionRisk.GUARDED


def test_execute_tool_denies_tool_outside_runtime_role_boundary() -> None:
    deps = _PolicyDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=ToolApprovalPolicy(yolo=True),
    )
    deps.role_registry.register(
        deps.role_registry.get("spec_coder").model_copy(update={"tools": ("read",)})
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-policy-deny"
    called = False

    def action() -> str:
        nonlocal called
        called = True
        return "should not run"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="write",
            args_summary={"path": "a.txt"},
            action=action,
        )
    )

    error = cast(dict[str, JsonValue], result["error"])
    meta = cast(dict[str, JsonValue], result["meta"])
    assert called is False
    assert result["ok"] is False
    assert error["type"] == "tool_policy_denied"
    assert meta["runtime_policy_decision"] == "deny"
    assert meta["approval_status"] == "denied_by_policy"
    tool_result_payloads = _tool_result_payloads(deps)
    assert len(tool_result_payloads) == 1
    assert tool_result_payloads[0]["tool_name"] == "write"
    assert tool_result_payloads[0]["tool_call_id"] == "call-policy-deny"
    assert tool_result_payloads[0]["error"] is True
    assert tool_result_payloads[0]["result"] == result


def test_execute_tool_allows_skill_and_mcp_tools_from_runtime_snapshot() -> None:
    snapshot = RuntimeToolsSnapshot(
        skill_tools=(RuntimeToolSnapshotEntry(source="skill", name="skill_lookup"),),
        mcp_tools=(
            RuntimeToolSnapshotEntry(
                source="mcp",
                name="docs_search",
                server_name="docs",
            ),
        ),
    )

    for tool_name in ("skill_lookup", "docs_search"):
        deps = _PolicyDeps(
            manager=_FakeApprovalManager(wait_result=("approve", "")),
            policy=ToolApprovalPolicy(yolo=True),
        )
        deps.role_registry.register(
            deps.role_registry.get("spec_coder").model_copy(update={"tools": ("read",)})
        )
        deps.agent_repo = _RuntimeToolsAgentRepo(snapshot.model_dump_json())
        ctx = _FakeCtx(deps)
        ctx.tool_call_id = f"call-policy-snapshot-{tool_name}"
        called = False

        def action() -> str:
            nonlocal called
            called = True
            return "ran"

        result = asyncio.run(
            execute_tool(
                cast(ToolContext, cast(object, ctx)),
                tool_name=tool_name,
                args_summary={},
                action=action,
            )
        )

        assert called is True
        assert result["ok"] is True
        assert result["data"] == "ran"


def test_execute_tool_treats_empty_role_tool_list_as_deny_all() -> None:
    deps = _PolicyDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=ToolApprovalPolicy(yolo=True),
    )
    deps.role_registry.register(
        deps.role_registry.get("spec_coder").model_copy(update={"tools": ()})
    )
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-policy-empty-tools"
    called = False

    def action() -> str:
        nonlocal called
        called = True
        return "should not run"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "a.txt"},
            action=action,
        )
    )

    meta = cast(dict[str, JsonValue], result["meta"])
    assert called is False
    assert result["ok"] is False
    assert meta["runtime_policy_decision"] == "deny"
    assert meta["approval_status"] == "denied_by_policy"


def test_execute_tool_denies_when_role_capabilities_cannot_be_resolved() -> None:
    deps = _PolicyDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=ToolApprovalPolicy(yolo=True),
    )
    deps.runtime_role_resolver = _FailingRoleResolver()
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-policy-resolution-failed"
    called = False

    def action() -> str:
        nonlocal called
        called = True
        return "should not run"

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, ctx)),
            tool_name="read",
            args_summary={"path": "a.txt"},
            action=action,
        )
    )

    error = cast(dict[str, JsonValue], result["error"])
    meta = cast(dict[str, JsonValue], result["meta"])
    assert called is False
    assert result["ok"] is False
    assert error["type"] == "tool_policy_denied"
    assert meta["runtime_policy_decision"] == "deny"
    assert meta["approval_status"] == "denied_by_policy"
