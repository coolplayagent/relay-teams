# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from relay_teams.tools.runtime.guardrail_audit_repository import (
    GuardrailAuditEntry,
    GuardrailAuditRepository,
)


class TestGuardrailAuditRepository:
    def test_init_creates_table(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "audit.db"
            GuardrailAuditRepository(db_path)
            assert db_path.exists()

    def test_insert_and_query(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "audit.db"
            repo = GuardrailAuditRepository(db_path)
            entry = GuardrailAuditEntry(
                task_id="task-1",
                role_id="role-1",
                tool_name="shell",
                rule_id="max_execution_time",
                action="block",
                severity="error",
                message="Execution exceeded time limit",
            )
            row_id = repo.insert_entry(entry)
            assert row_id > 0

            results, total = repo.query_entries(task_id="task-1")
            assert total == 1
            assert results[0].task_id == "task-1"
            assert results[0].tool_name == "shell"
            assert results[0].action == "block"

    def test_query_with_no_filters(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "audit.db"
            repo = GuardrailAuditRepository(db_path)
            repo.insert_entry(
                GuardrailAuditEntry(task_id="t1", role_id="r1", action="allow")
            )
            repo.insert_entry(
                GuardrailAuditEntry(task_id="t2", role_id="r2", action="block")
            )
            results, total = repo.query_entries()
            assert total == 2
            assert len(results) == 2

    def test_query_with_role_filter(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "audit.db"
            repo = GuardrailAuditRepository(db_path)
            repo.insert_entry(GuardrailAuditEntry(task_id="t1", role_id="r1"))
            repo.insert_entry(GuardrailAuditEntry(task_id="t2", role_id="r2"))
            results, total = repo.query_entries(role_id="r1")
            assert total == 1
            assert results[0].role_id == "r1"

    def test_query_with_action_filter(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "audit.db"
            repo = GuardrailAuditRepository(db_path)
            repo.insert_entry(GuardrailAuditEntry(task_id="t1", action="allow"))
            repo.insert_entry(GuardrailAuditEntry(task_id="t2", action="block"))
            results, total = repo.query_entries(action="block")
            assert total == 1
            assert results[0].action == "block"

    def test_query_with_limit_and_offset(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "audit.db"
            repo = GuardrailAuditRepository(db_path)
            for i in range(5):
                repo.insert_entry(GuardrailAuditEntry(task_id=f"t{i}"))
            results, total = repo.query_entries(limit=2, offset=0)
            assert total == 5
            assert len(results) == 2

    def test_double_init_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "audit.db"
            GuardrailAuditRepository(db_path)
            GuardrailAuditRepository(db_path)
            repo = GuardrailAuditRepository(db_path)
            repo.insert_entry(GuardrailAuditEntry(task_id="t1"))
            results, total = repo.query_entries()
            assert total == 1
