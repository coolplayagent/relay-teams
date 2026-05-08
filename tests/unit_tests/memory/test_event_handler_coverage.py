# -*- coding: utf-8 -*-
"""Coverage for event_handler.py RP-2 wiring (lines 107-120)."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.memory.event_handler import MemoryEventHandler


def _make_handler() -> tuple[MemoryEventHandler, MagicMock, MagicMock]:
    role_mem = MagicMock()
    role_mem.record_task_result_async = AsyncMock()
    role_mem.record_verification_outcome = AsyncMock()
    bank = MagicMock()
    bank.create_entry_async = AsyncMock()
    return (
        MemoryEventHandler(
            memory_bank_service=bank,
            role_memory_service=role_mem,
        ),
        role_mem,
        bank,
    )


@pytest.mark.asyncio
async def test_on_task_completed_calls_record_verification_outcome() -> None:
    """Lines 107-120: RP-2 wiring fires when verification_report is provided."""
    handler, role_mem, _bank = _make_handler()

    vr = MagicMock()
    vr.passed = True

    await handler.on_task_completed_async(
        task_id="t1",
        session_id="s1",
        workspace_id="w1",
        role_id="role1",
        run_id="r1",
        objective="do stuff",
        result="done",
        verification_report=vr,
    )
    role_mem.record_verification_outcome.assert_called_once()


@pytest.mark.asyncio
async def test_on_task_completed_no_verification_report() -> None:
    """No verification_report = no call to record_verification_outcome."""
    handler, role_mem, _bank = _make_handler()

    await handler.on_task_completed_async(
        task_id="t1",
        session_id="s1",
        workspace_id="w1",
        role_id="role1",
        run_id="r1",
        objective="do stuff",
        result="done",
        verification_report=None,
    )
    role_mem.record_verification_outcome.assert_not_called()


@pytest.mark.asyncio
async def test_on_task_completed_tolerates_verification_sqlite_failure() -> None:
    handler, role_mem, _bank = _make_handler()
    role_mem.record_verification_outcome = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )

    await handler.on_task_completed_async(
        task_id="t1",
        session_id="s1",
        workspace_id="w1",
        role_id="role1",
        run_id="r1",
        objective="do stuff",
        result="done",
        verification_report=MagicMock(),
    )

    role_mem.record_verification_outcome.assert_awaited_once()
