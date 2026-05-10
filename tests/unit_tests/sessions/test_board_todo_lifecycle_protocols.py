from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol, cast

import pytest

from relay_teams.sessions.runs.run_service import BoardTodoLifecycleServiceLike
from relay_teams.sessions.session_service import BoardTodoSessionLifecycleService


class _MarkRunCompletedStub(Protocol):
    def __call__(self, self_obj: object, *, run_id: str) -> Awaitable[None]:
        raise NotImplementedError


class _MarkSessionDeletedStub(Protocol):
    def __call__(self, self_obj: object, *, session_id: str) -> None:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_run_service_board_todo_lifecycle_protocol_stub_raises() -> None:
    service = cast(BoardTodoLifecycleServiceLike, object())

    with pytest.raises(NotImplementedError):
        method = cast(
            _MarkRunCompletedStub,
            getattr(BoardTodoLifecycleServiceLike, "mark_run_completed_async"),
        )
        await method(
            service,
            run_id="run",
        )


def test_session_service_board_todo_lifecycle_protocol_stub_raises() -> None:
    service = cast(BoardTodoSessionLifecycleService, object())

    with pytest.raises(NotImplementedError):
        method = cast(
            _MarkSessionDeletedStub,
            getattr(BoardTodoSessionLifecycleService, "mark_session_deleted"),
        )
        method(
            service,
            session_id="session",
        )
