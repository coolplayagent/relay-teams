# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
import asyncio
from pathlib import Path

import pytest

from relay_teams.agents.tasks.enums import TaskSpecStrictness
from relay_teams.tools.runtime.guardrail_audit_repository import (
    GuardrailAuditRepository,
    _truncate,
)
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailAction,
    RuntimeGuardrailEvaluation,
    RuntimeGuardrailFinding,
    RuntimeGuardrailLayer,
    RuntimeGuardrailRuleType,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_audit.db"


@pytest.fixture
def repo(db_path: Path) -> GuardrailAuditRepository:
    return GuardrailAuditRepository(db_path)


def _make_evaluation(
    *,
    action: RuntimeGuardrailAction = RuntimeGuardrailAction.ALLOW,
    findings: list[RuntimeGuardrailFinding] | None = None,
) -> RuntimeGuardrailEvaluation:
    if findings is None:
        findings = []
    return RuntimeGuardrailEvaluation(
        findings=tuple(findings),
    )


def test_truncate_short():
    assert _truncate("hello") == "hello"


def test_truncate_long():
    long_text = "x" * 3000
    result = _truncate(long_text)
    assert len(result) == 2000
    assert result.endswith("...")


def test_init_tables(db_path: Path):
    GuardrailAuditRepository(db_path)
    conn = sqlite3.connect(str(db_path))
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {t[0] for t in tables}
    conn.close()
    assert "guardrail_audit" in table_names


def test_record_evaluation_no_findings(repo: GuardrailAuditRepository):
    evaluation = _make_evaluation()
    row_id = asyncio.run(
        repo.record_evaluation_async(
            evaluation=evaluation,
            strictness=TaskSpecStrictness.MEDIUM,
            session_id="sess-1",
            run_id="run-1",
            task_id="task-1",
            role_id="role-1",
        )
    )
    assert row_id > 0


def test_record_evaluation_with_triggered_finding(
    repo: GuardrailAuditRepository,
):
    finding = RuntimeGuardrailFinding(
        rule_id="test-rule",
        layer=RuntimeGuardrailLayer.PRE_EXECUTION,
        rule_type=RuntimeGuardrailRuleType.TOOL_DENYLIST,
        action=RuntimeGuardrailAction.DENY,
        message="Blocked dangerous tool",
        details={"command": "rm -rf /"},
    )
    evaluation = _make_evaluation(
        action=RuntimeGuardrailAction.DENY,
        findings=[finding],
    )
    row_id = asyncio.run(
        repo.record_evaluation_async(
            evaluation=evaluation,
            strictness=TaskSpecStrictness.HIGH,
            task_id="task-2",
        )
    )
    assert row_id > 0


def test_query_empty(repo: GuardrailAuditRepository):
    results = asyncio.run(repo.query_evaluations_async())
    assert results == []


def test_query_by_run_id(
    repo: GuardrailAuditRepository,
):
    evaluation = _make_evaluation()
    asyncio.run(
        repo.record_evaluation_async(
            evaluation=evaluation,
            strictness=TaskSpecStrictness.MEDIUM,
            run_id="run-specific",
        )
    )
    results = asyncio.run(repo.query_evaluations_async(run_id="run-specific"))
    assert len(results) >= 1

    results_miss = asyncio.run(repo.query_evaluations_async(run_id="nonexistent"))
    assert len(results_miss) == 0


def test_query_triggered_only(
    repo: GuardrailAuditRepository,
):
    finding = RuntimeGuardrailFinding(
        rule_id="deny-rule",
        layer=RuntimeGuardrailLayer.PRE_EXECUTION,
        rule_type=RuntimeGuardrailRuleType.TOOL_DENYLIST,
        action=RuntimeGuardrailAction.DENY,
        message="Blocked",
        details={},
    )
    triggered = _make_evaluation(findings=[finding])
    asyncio.run(
        repo.record_evaluation_async(
            evaluation=triggered,
            strictness=TaskSpecStrictness.MEDIUM,
            run_id="run-triggered",
        )
    )

    clean = _make_evaluation()
    asyncio.run(
        repo.record_evaluation_async(
            evaluation=clean,
            strictness=TaskSpecStrictness.MEDIUM,
            run_id="run-clean",
        )
    )

    all_results = asyncio.run(repo.query_evaluations_async())
    assert len(all_results) >= 2

    triggered_results = asyncio.run(repo.query_evaluations_async(triggered_only=True))
    assert len(triggered_results) >= 1
