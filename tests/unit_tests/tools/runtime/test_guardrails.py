# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.tools.runtime.guardrails import (
    MAX_RECORDED_GUARDRAIL_OBSERVATIONS,
    RuntimeGuardrailAction,
    RuntimeGuardrailContext,
    RuntimeGuardrailFinding,
    RuntimeGuardrailPolicy,
    RuntimeGuardrailLayer,
    RuntimeGuardrailReport,
    RuntimeGuardrailRule,
    RuntimeGuardrailRuleType,
    RuntimeGuardrailStatus,
    build_runtime_guardrail_report,
    evaluate_in_execution_guardrails,
    evaluate_pre_execution_guardrails,
    generate_runtime_guardrail_report_async,
    load_runtime_guardrail_state_async,
    record_runtime_guardrail_findings_async,
    record_runtime_guardrail_tool_call_async,
    runtime_guardrail_report_from_event_payload,
)


def _context() -> RuntimeGuardrailContext:
    return RuntimeGuardrailContext(
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="gater",
        tool_name="shell",
        tool_call_id="call-1",
        session_mode="orchestration",
        run_kind="conversation",
    )


@pytest.mark.asyncio
async def test_pre_execution_guardrails_deny_unauthorized_tool() -> None:
    context = _context()

    evaluation = evaluate_pre_execution_guardrails(
        policy=RuntimeGuardrailPolicy(),
        context=context,
        tool_input={"command": "pytest -q"},
        allowed_tools=("read",),
        denied_tools=(),
        call_count=1,
    )

    assert evaluation.blocked is True
    assert evaluation.findings[0].rule_type == RuntimeGuardrailRuleType.TOOL_ALLOWLIST
    assert evaluation.findings[0].action == RuntimeGuardrailAction.DENY


@pytest.mark.asyncio
async def test_pre_execution_guardrails_deny_destructive_shell_pattern() -> None:
    context = _context()

    evaluation = evaluate_pre_execution_guardrails(
        policy=RuntimeGuardrailPolicy(),
        context=context,
        tool_input={"command": "rm -rf build"},
        allowed_tools=("shell",),
        denied_tools=(),
        call_count=1,
    )

    assert evaluation.blocked is True
    assert evaluation.findings[0].rule_id == "destructive_shell_pattern"


@pytest.mark.asyncio
async def test_in_execution_guardrails_can_block_large_output() -> None:
    context = _context()
    policy = RuntimeGuardrailPolicy(
        rules=(
            RuntimeGuardrailRule(
                rule_id="tiny-output",
                layer=RuntimeGuardrailLayer.IN_EXECUTION,
                rule_type=RuntimeGuardrailRuleType.OUTPUT_SIZE,
                action=RuntimeGuardrailAction.DENY,
                max_bytes=10,
            ),
        )
    )

    evaluation = evaluate_in_execution_guardrails(
        policy=policy,
        context=context,
        tool_input={"command": "printf large"},
        result_envelope={"ok": True, "data": {"content": "x" * 64}, "meta": {}},
    )

    assert evaluation.blocked is True
    assert evaluation.findings[0].rule_id == "tiny-output"


@pytest.mark.asyncio
async def test_guardrail_report_summarizes_recorded_findings(tmp_path: Path) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    context = _context()
    call_count = await record_runtime_guardrail_tool_call_async(
        shared_store=shared_store,
        context=context,
    )
    evaluation = evaluate_pre_execution_guardrails(
        policy=RuntimeGuardrailPolicy(),
        context=context,
        tool_input={"command": "rm -rf build"},
        allowed_tools=("shell",),
        denied_tools=(),
        call_count=call_count,
    )
    _ = await record_runtime_guardrail_findings_async(
        shared_store=shared_store,
        context=context,
        findings=evaluation.findings,
    )

    report = await generate_runtime_guardrail_report_async(
        shared_store=shared_store,
        task_id=context.task_id,
        run_id=context.run_id,
        session_id=context.session_id,
        role_id=context.role_id,
    )

    assert isinstance(report, RuntimeGuardrailReport)
    assert report.status == RuntimeGuardrailStatus.BLOCKED
    assert report.total_tool_calls == 1
    assert report.blocked_count == 1
    assert report.checks[0].passed is False


@pytest.mark.asyncio
async def test_guardrail_state_preserves_concurrent_updates(tmp_path: Path) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    contexts = tuple(
        _context().model_copy(update={"tool_call_id": f"call-{index}"})
        for index in range(1, 25)
    )

    counts = await asyncio.gather(
        *(
            record_runtime_guardrail_tool_call_async(
                shared_store=shared_store,
                context=context,
            )
            for context in contexts
        )
    )
    evaluations = tuple(
        evaluate_pre_execution_guardrails(
            policy=RuntimeGuardrailPolicy(),
            context=context,
            tool_input={"command": f"rm -rf build-{index}"},
            allowed_tools=("shell",),
            denied_tools=(),
            call_count=1,
        )
        for index, context in enumerate(contexts, start=1)
    )
    _ = await asyncio.gather(
        *(
            record_runtime_guardrail_findings_async(
                shared_store=shared_store,
                context=context,
                findings=evaluation.findings,
            )
            for context, evaluation in zip(contexts, evaluations, strict=True)
        )
    )

    state = await load_runtime_guardrail_state_async(
        shared_store=shared_store,
        task_id="task-1",
    )

    assert sorted(counts) == list(range(1, 25))
    assert state is not None
    assert state.tool_call_counts == {"shell": 24}
    assert len(state.observations) == 24
    assert {observation.tool_call_id for observation in state.observations} == {
        context.tool_call_id for context in contexts
    }


@pytest.mark.asyncio
async def test_guardrail_report_preserves_counts_after_observation_truncation(
    tmp_path: Path,
) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    context = _context()
    blocked_finding = RuntimeGuardrailFinding(
        layer=RuntimeGuardrailLayer.PRE_EXECUTION,
        rule_id="blocked-shell",
        rule_type=RuntimeGuardrailRuleType.SHELL_DESTRUCTIVE_PATTERN,
        action=RuntimeGuardrailAction.DENY,
        message="blocked",
    )
    warning_finding = RuntimeGuardrailFinding(
        layer=RuntimeGuardrailLayer.IN_EXECUTION,
        rule_id="large-output",
        rule_type=RuntimeGuardrailRuleType.OUTPUT_SIZE,
        action=RuntimeGuardrailAction.WARN,
        message="large output",
    )

    _ = await record_runtime_guardrail_findings_async(
        shared_store=shared_store,
        context=context,
        findings=(blocked_finding,),
    )
    for index in range(MAX_RECORDED_GUARDRAIL_OBSERVATIONS + 5):
        _ = await record_runtime_guardrail_findings_async(
            shared_store=shared_store,
            context=context.model_copy(update={"tool_call_id": f"warn-{index}"}),
            findings=(warning_finding,),
        )

    report = await generate_runtime_guardrail_report_async(
        shared_store=shared_store,
        task_id=context.task_id,
        run_id=context.run_id,
        session_id=context.session_id,
        role_id=context.role_id,
    )

    assert len(report.observations) == MAX_RECORDED_GUARDRAIL_OBSERVATIONS
    deny_obs = [
        o for o in report.observations if o.action == RuntimeGuardrailAction.DENY
    ]
    warn_obs = [
        o for o in report.observations if o.action == RuntimeGuardrailAction.WARN
    ]
    assert len(deny_obs) == 1
    assert len(warn_obs) == MAX_RECORDED_GUARDRAIL_OBSERVATIONS - 1
    assert report.blocked_count == 1
    assert report.warning_count == MAX_RECORDED_GUARDRAIL_OBSERVATIONS + 5
    assert report.status == RuntimeGuardrailStatus.BLOCKED


def test_guardrail_report_without_state_is_passed() -> None:
    report = build_runtime_guardrail_report(
        state=None,
        task_id="task-1",
        run_id="run-1",
        session_id="session-1",
        role_id="gater",
    )

    assert report.status == RuntimeGuardrailStatus.PASSED
    assert report.total_tool_calls == 0


def test_runtime_guardrail_report_from_event_payload_parses_valid_reports() -> None:
    report = RuntimeGuardrailReport(
        task_id="task-1",
        run_id="run-1",
        session_id="session-1",
        role_id="gater",
        status=RuntimeGuardrailStatus.WARNING,
        warning_count=1,
    )

    parsed = runtime_guardrail_report_from_event_payload(report.model_dump_json())

    assert parsed is not None
    assert parsed.status == RuntimeGuardrailStatus.WARNING
    assert runtime_guardrail_report_from_event_payload("{") is None
    assert runtime_guardrail_report_from_event_payload("[]") is None
