from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from relay_teams.audit import AuditEventFilter, AuditEventRepository, AuditEventType
from relay_teams.audit.service import AuditService
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime.execution import execute_tool
from relay_teams.tools.runtime.models import ToolResultProjection
from tests.unit_tests.tools.runtime.test_execution import (
    _FakeApprovalManager,
    _FakeCtx,
    _FakeDeps,
    _FakePolicy,
)


class _FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve_path(self, relative_path: str, *, write: bool = False) -> Path:
        _ = write
        return (self.root / relative_path).resolve()


def test_execute_tool_records_file_write_audit_event(tmp_path: Path) -> None:
    audit_repository = AuditEventRepository(tmp_path / "audit.db")
    deps = _deps_with_audit(tmp_path, audit_repository)
    target_path = tmp_path / "src" / "audit.txt"

    def action() -> ToolResultProjection:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("audit body", encoding="utf-8")
        return ToolResultProjection(
            visible_data={"output": "Wrote file successfully."},
            internal_data={
                "path": "src/audit.txt",
                "created": True,
                "diff_summary": "+ 1: 1 line(s) added",
            },
        )

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, _FakeCtx(deps))),
            tool_name="write",
            args_summary={"path": "src/audit.txt", "content_len": 10},
            tool_input={"path": "src/audit.txt", "content": "audit body"},
            action=action,
        )
    )

    page = audit_repository.list_events(
        AuditEventFilter(event_type=AuditEventType.FILE_WRITE)
    )
    assert cast(dict[str, JsonValue], result)["ok"] is True
    assert len(page.items) == 1
    event = page.items[0]
    assert event.target == "src/audit.txt"
    assert event.role_id == "spec_coder"
    assert event.task_id == "task-1"
    assert event.content_digest == (
        "sha256:" + sha256("audit body".encode("utf-8")).hexdigest()
    )
    assert event.metadata["created"] is True


def test_execute_tool_records_shell_and_coordinator_audit_events(
    tmp_path: Path,
) -> None:
    audit_repository = AuditEventRepository(tmp_path / "audit.db")
    deps = _deps_with_audit(tmp_path, audit_repository)
    ctx = cast(ToolContext, cast(object, _FakeCtx(deps)))

    asyncio.run(
        execute_tool(
            ctx,
            tool_name="shell",
            args_summary={"command": "uv run pytest"},
            tool_input={"command": "uv run pytest", "workdir": "."},
            action=lambda: ToolResultProjection(
                visible_data={"status": "completed", "exit_code": 0},
                internal_data={"status": "completed", "exit_code": 0},
            ),
        )
    )
    asyncio.run(
        execute_tool(
            ctx,
            tool_name="orch_dispatch_task",
            args_summary={"task_id": "task-child", "role_id": "Reviewer"},
            tool_input={
                "task_id": "task-child",
                "role_id": "Reviewer",
                "prompt": "Review the implementation because the task needs audit coverage.",
            },
            action=lambda: ToolResultProjection(
                visible_data={"task": {"task_id": "task-child"}},
                internal_data={"task": {"task_id": "task-child"}},
            ),
        )
    )

    shell_page = audit_repository.list_events(
        AuditEventFilter(event_type=AuditEventType.SHELL_COMMAND)
    )
    decision_page = audit_repository.list_events(
        AuditEventFilter(event_type=AuditEventType.COORDINATOR_DECISION)
    )
    assert shell_page.items[0].command == "uv run pytest"
    assert shell_page.items[0].metadata["exit_code"] == 0
    assert decision_page.items[0].target == "task:task-child->role:Reviewer"
    assert decision_page.items[0].decision_reason is not None
    assert "audit coverage" in decision_page.items[0].decision_reason


def _deps_with_audit(
    tmp_path: Path,
    audit_repository: AuditEventRepository,
) -> _FakeDeps:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )
    deps.audit_service = AuditService(audit_repository)
    deps.workspace = _FakeWorkspace(tmp_path)
    return deps
