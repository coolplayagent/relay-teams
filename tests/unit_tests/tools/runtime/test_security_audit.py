from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from threading import get_ident
from typing import cast

import pytest
from pydantic import JsonValue

from relay_teams.audit import AuditEventFilter, AuditEventRepository, AuditEventType
from relay_teams.audit.service import AuditService
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime import execution as execution_module
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


def test_execute_tool_records_file_write_variant_audit_events(tmp_path: Path) -> None:
    audit_repository = AuditEventRepository(tmp_path / "audit.db")
    deps = _deps_with_audit(tmp_path, audit_repository)
    ctx = cast(ToolContext, cast(object, _FakeCtx(deps)))

    def write_file(relative_path: str, content: str) -> None:
        target_path = tmp_path / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")

    asyncio.run(
        execute_tool(
            ctx,
            tool_name="write_tmp",
            args_summary={"path": "scratch.txt"},
            tool_input={"path": "scratch.txt", "content": "tmp body"},
            action=lambda: (
                write_file("tmp/scratch.txt", "tmp body")
                or ToolResultProjection(
                    visible_data={"output": "Wrote tmp file."},
                    internal_data={},
                )
            ),
        )
    )
    asyncio.run(
        execute_tool(
            ctx,
            tool_name="edit",
            args_summary={"path": "src/edit.txt"},
            tool_input={"path": "src/edit.txt", "new_string": "edit body"},
            action=lambda: (
                write_file("src/edit.txt", "edit body")
                or ToolResultProjection(
                    visible_data={"output": "Edited file."},
                    internal_data={"created": False},
                )
            ),
        )
    )
    asyncio.run(
        execute_tool(
            ctx,
            tool_name="notebook_edit",
            args_summary={"path": "notebook.ipynb"},
            tool_input={"path": "notebook.ipynb", "new_source": "print(1)"},
            action=lambda: (
                write_file("notebook.ipynb", "print(1)")
                or ToolResultProjection(
                    visible_data={"output": "Edited notebook."},
                    internal_data={},
                )
            ),
        )
    )

    page = audit_repository.list_events(
        AuditEventFilter(event_type=AuditEventType.FILE_WRITE)
    )
    events = {event.target: event for event in page.items}
    assert events["tmp/scratch.txt"].action == "write_tmp_file"
    assert events["src/edit.txt"].action == "edit_file"
    assert events["notebook.ipynb"].action == "edit_notebook"
    assert events["tmp/scratch.txt"].metadata["input_content_length"] == 8
    assert events["src/edit.txt"].metadata["created"] is False


def test_execute_tool_hashes_file_write_digest_off_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_repository = AuditEventRepository(tmp_path / "audit.db")
    deps = _deps_with_audit(tmp_path, audit_repository)
    target_path = tmp_path / "src" / "audit.txt"
    event_loop_thread_id = get_ident()
    digest_thread_ids: list[int] = []
    original_digest = execution_module._workspace_file_digest

    def recording_digest(
        *,
        ctx: ToolContext,
        logical_path: str,
    ) -> tuple[str | None, int | None, str | None]:
        digest_thread_ids.append(get_ident())
        return original_digest(ctx=ctx, logical_path=logical_path)

    monkeypatch.setattr(
        execution_module,
        "_workspace_file_digest",
        recording_digest,
    )

    def action() -> ToolResultProjection:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("audit body", encoding="utf-8")
        return ToolResultProjection(
            visible_data={"output": "Wrote file successfully."},
            internal_data={"path": "src/audit.txt"},
        )

    asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, _FakeCtx(deps))),
            tool_name="write",
            args_summary={"path": "src/audit.txt"},
            tool_input={"path": "src/audit.txt", "content": "audit body"},
            action=action,
        )
    )

    assert digest_thread_ids
    assert all(thread_id != event_loop_thread_id for thread_id in digest_thread_ids)


def test_execute_tool_records_file_digest_error_when_target_missing(
    tmp_path: Path,
) -> None:
    audit_repository = AuditEventRepository(tmp_path / "audit.db")
    deps = _deps_with_audit(tmp_path, audit_repository)

    asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, _FakeCtx(deps))),
            tool_name="write",
            args_summary={"path": "missing.txt"},
            tool_input={"path": "missing.txt", "content": "not written"},
            action=lambda: ToolResultProjection(
                visible_data={"output": "Skipped write."},
                internal_data={},
            ),
        )
    )

    page = audit_repository.list_events(
        AuditEventFilter(event_type=AuditEventType.FILE_WRITE)
    )
    assert page.items[0].content_digest is None
    assert page.items[0].metadata["content_digest_error"] == "target is not a file"


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


def test_execute_tool_logs_audit_recording_failure_without_failing_tool(
    tmp_path: Path,
) -> None:
    deps = _FakeDeps(
        manager=_FakeApprovalManager(wait_result=("approve", "")),
        policy=_FakePolicy(needs_approval=False),
    )

    class FailingAuditService:
        async def record_event_async(self, event: object) -> object:
            _ = event
            raise RuntimeError("audit store unavailable")

    deps.audit_service = FailingAuditService()
    deps.workspace = _FakeWorkspace(tmp_path)

    result = asyncio.run(
        execute_tool(
            cast(ToolContext, cast(object, _FakeCtx(deps))),
            tool_name="shell",
            args_summary={"command": "uv run pytest"},
            tool_input={"command": "uv run pytest"},
            action=lambda: ToolResultProjection(
                visible_data={"status": "completed", "exit_code": 0},
                internal_data={"status": "completed", "exit_code": 0},
            ),
        )
    )

    assert cast(dict[str, JsonValue], result)["ok"] is True


def test_execute_tool_records_failed_audit_when_success_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_repository = AuditEventRepository(tmp_path / "audit.db")
    deps = _deps_with_audit(tmp_path, audit_repository)

    async def fail_persistence(**kwargs: object) -> None:
        _ = kwargs
        raise RuntimeError("tool result persistence unavailable")

    monkeypatch.setattr(
        execution_module,
        "_persist_and_publish_tool_result_async",
        fail_persistence,
    )

    with pytest.raises(RuntimeError, match="tool result persistence unavailable"):
        asyncio.run(
            execute_tool(
                cast(ToolContext, cast(object, _FakeCtx(deps))),
                tool_name="shell",
                args_summary={"command": "uv run pytest"},
                tool_input={"command": "uv run pytest"},
                action=lambda: ToolResultProjection(
                    visible_data={"status": "completed", "exit_code": 0},
                    internal_data={"status": "completed", "exit_code": 0},
                ),
            )
        )

    page = audit_repository.list_events(
        AuditEventFilter(event_type=AuditEventType.SHELL_COMMAND)
    )
    assert len(page.items) == 1
    assert page.items[0].command == "uv run pytest"
    assert page.items[0].outcome == "failed"


def test_execute_tool_records_failed_audit_when_approval_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_repository = AuditEventRepository(tmp_path / "audit.db")
    deps = _deps_with_audit(tmp_path, audit_repository)
    deps.tool_approval_policy = _FakePolicy(needs_approval=True)
    ctx = _FakeCtx(deps)
    ctx.tool_call_id = "call-shell-cleanup"

    async def fail_mark_completed(tool_call_id: str) -> object:
        assert tool_call_id == "call-shell-cleanup"
        raise RuntimeError("approval cleanup unavailable")

    monkeypatch.setattr(
        deps.approval_ticket_repo,
        "mark_completed_async",
        fail_mark_completed,
    )

    with pytest.raises(RuntimeError, match="approval cleanup unavailable"):
        asyncio.run(
            execute_tool(
                cast(ToolContext, cast(object, ctx)),
                tool_name="shell",
                args_summary={"command": "uv run pytest"},
                tool_input={"command": "uv run pytest"},
                action=lambda: ToolResultProjection(
                    visible_data={"status": "completed", "exit_code": 0},
                    internal_data={"status": "completed", "exit_code": 0},
                ),
            )
        )

    page = audit_repository.list_events(
        AuditEventFilter(event_type=AuditEventType.SHELL_COMMAND)
    )
    assert len(page.items) == 1
    assert page.items[0].command == "uv run pytest"
    assert page.items[0].outcome == "failed"


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
