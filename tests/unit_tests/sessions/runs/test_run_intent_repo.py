# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.sessions.runs.enums import ApprovalMode, ExecutionMode
from agent_teams.sessions.runs.models import IntentInput, RunThinkingConfig
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository


def test_run_intent_repo_round_trips_approval_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "run_intent.db"
    repo = RunIntentRepository(db_path)

    repo.upsert(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            intent="ship it",
            execution_mode=ExecutionMode.AI,
            approval_mode=ApprovalMode.YOLO,
        ),
    )

    record = repo.get("run-1")

    assert record.intent == "ship it"
    assert record.execution_mode == ExecutionMode.AI
    assert record.approval_mode == ApprovalMode.YOLO


def test_run_intent_repo_round_trips_thinking_config(tmp_path: Path) -> None:
    db_path = tmp_path / "run_intent_thinking.db"
    repo = RunIntentRepository(db_path)

    repo.upsert(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            intent="ship it",
            execution_mode=ExecutionMode.AI,
            approval_mode=ApprovalMode.STANDARD,
            thinking=RunThinkingConfig(enabled=True, effort="medium"),
        ),
    )

    record = repo.get("run-1")

    assert record.thinking.enabled is True
    assert record.thinking.effort == "medium"
