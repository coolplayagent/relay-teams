from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.sessions.runs.user_question_models import (
    UserQuestionAnswer,
    UserQuestionOption,
    UserQuestionPrompt,
    UserQuestionRequestStatus,
    UserQuestionSelection,
)
from relay_teams.sessions.runs.user_question_repository import (
    UserQuestionRepository,
    UserQuestionStatusConflictError,
)


def test_resolve_updates_requested_question_when_expected_status_matches(
    tmp_path: Path,
) -> None:
    repository = UserQuestionRepository(tmp_path / "user_question_resolve.db")
    repository.upsert_requested(
        question_id="call-1",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )

    resolved = repository.resolve(
        question_id="call-1",
        status=UserQuestionRequestStatus.ANSWERED,
        answers=(
            UserQuestionAnswer(
                selections=(UserQuestionSelection(label="Only"),),
            ),
        ),
        expected_status=UserQuestionRequestStatus.REQUESTED,
    )

    assert resolved.status == UserQuestionRequestStatus.ANSWERED
    assert resolved.answers[0].selections[0].label == "Only"


def test_resolve_raises_conflict_when_question_status_changed(tmp_path: Path) -> None:
    repository = UserQuestionRepository(tmp_path / "user_question_conflict.db")
    repository.upsert_requested(
        question_id="call-1",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )
    repository.resolve(
        question_id="call-1",
        status=UserQuestionRequestStatus.COMPLETED,
    )

    with pytest.raises(UserQuestionStatusConflictError) as exc_info:
        repository.resolve(
            question_id="call-1",
            status=UserQuestionRequestStatus.ANSWERED,
            answers=(
                UserQuestionAnswer(
                    selections=(UserQuestionSelection(label="Only"),),
                ),
            ),
            expected_status=UserQuestionRequestStatus.REQUESTED,
        )

    assert exc_info.value.question_id == "call-1"
    assert exc_info.value.expected_status == UserQuestionRequestStatus.REQUESTED
    assert exc_info.value.actual_status == UserQuestionRequestStatus.COMPLETED


@pytest.mark.asyncio
async def test_async_user_question_repository_methods_share_persisted_state(
    tmp_path: Path,
) -> None:
    repository = UserQuestionRepository(tmp_path / "user_question_async.db")

    try:
        requested = await repository.upsert_requested_async(
            question_id="call-async",
            run_id="run-1",
            session_id="session-1",
            task_id="task-1",
            instance_id="inst-1",
            role_id="Coordinator",
            tool_name="ask_question",
            questions=(
                UserQuestionPrompt(
                    question="Pick one",
                    options=(UserQuestionOption(label="Only", description="Only"),),
                    multiple=False,
                ),
            ),
        )
        by_run = await repository.list_by_run_async("run-1")
        resolved = await repository.resolve_async(
            question_id="call-async",
            status=UserQuestionRequestStatus.ANSWERED,
            answers=(
                UserQuestionAnswer(
                    selections=(UserQuestionSelection(label="Only"),),
                ),
            ),
            expected_status=UserQuestionRequestStatus.REQUESTED,
        )
        completed = await repository.mark_completed_async("call-async")
    finally:
        await repository.close_async()

    assert requested.status == UserQuestionRequestStatus.REQUESTED
    assert tuple(record.question_id for record in by_run) == ("call-async",)
    assert resolved.status == UserQuestionRequestStatus.ANSWERED
    assert completed is not None
    assert completed.status == UserQuestionRequestStatus.COMPLETED


@pytest.mark.asyncio
async def test_user_question_async_hot_paths_do_not_reinitialize_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = UserQuestionRepository(tmp_path / "user_question_no_reinit.db")
    questions = (
        UserQuestionPrompt(
            question="Pick one",
            options=(UserQuestionOption(label="Only", description="Only"),),
            multiple=False,
        ),
    )
    answers = (
        UserQuestionAnswer(
            selections=(UserQuestionSelection(label="Only"),),
        ),
    )

    async def _fail_init() -> None:
        raise AssertionError("async schema init should not run on hot paths")

    monkeypatch.setattr(repository, "_init_tables_async", _fail_init)

    try:
        requested = await repository.upsert_requested_async(
            question_id="call-async",
            run_id="run-1",
            session_id="session-1",
            task_id="task-1",
            instance_id="inst-1",
            role_id="Coordinator",
            tool_name="ask_question",
            questions=questions,
        )
        loaded = await repository.get_async("call-async")
        by_run = await repository.list_by_run_async("run-1")
        by_session = await repository.list_by_session_async("session-1")
        resolved = await repository.resolve_async(
            question_id="call-async",
            status=UserQuestionRequestStatus.ANSWERED,
            answers=answers,
            expected_status=UserQuestionRequestStatus.REQUESTED,
        )
        completed = await repository.mark_completed_async("call-async")
        await repository.upsert_requested_async(
            question_id="delete-by-session",
            run_id="run-2",
            session_id="session-delete",
            task_id="task-1",
            instance_id="inst-1",
            role_id="Coordinator",
            tool_name="ask_question",
            questions=questions,
        )
        await repository.delete_by_session_async("session-delete")
        await repository.upsert_requested_async(
            question_id="delete-by-run",
            run_id="run-delete",
            session_id="session-3",
            task_id="task-1",
            instance_id="inst-1",
            role_id="Coordinator",
            tool_name="ask_question",
            questions=questions,
        )
        await repository.delete_by_run_async("run-delete")
    finally:
        await repository.close_async()

    assert requested.status == UserQuestionRequestStatus.REQUESTED
    assert loaded is not None
    assert loaded.question_id == "call-async"
    assert tuple(record.question_id for record in by_run) == ("call-async",)
    assert tuple(record.question_id for record in by_session) == ("call-async",)
    assert resolved.status == UserQuestionRequestStatus.ANSWERED
    assert completed is not None
    assert completed.status == UserQuestionRequestStatus.COMPLETED


@pytest.mark.asyncio
async def test_user_question_async_conflict_and_missing_paths(
    tmp_path: Path,
) -> None:
    repository = UserQuestionRepository(tmp_path / "user_question_async_conflict.db")
    questions = (
        UserQuestionPrompt(
            question="Pick one",
            options=(UserQuestionOption(label="Only", description="Only"),),
            multiple=False,
        ),
    )

    try:
        missing_completion = await repository.mark_completed_async("missing")
        await repository.upsert_requested_async(
            question_id="call-async",
            run_id="run-1",
            session_id="session-1",
            task_id="task-1",
            instance_id="inst-1",
            role_id="Coordinator",
            tool_name="ask_question",
            questions=questions,
        )
        await repository.resolve_async(
            question_id="call-async",
            status=UserQuestionRequestStatus.COMPLETED,
        )
        with pytest.raises(UserQuestionStatusConflictError) as exc_info:
            await repository.resolve_async(
                question_id="call-async",
                status=UserQuestionRequestStatus.ANSWERED,
                expected_status=UserQuestionRequestStatus.REQUESTED,
            )
        with pytest.raises(KeyError):
            await repository.resolve_async(
                question_id="missing",
                status=UserQuestionRequestStatus.COMPLETED,
            )
        repository.delete_by_session("session-1")
        repository.delete_by_run("run-1")
    finally:
        await repository.close_async()

    assert missing_completion is None
    assert exc_info.value.question_id == "call-async"
    assert exc_info.value.expected_status == UserQuestionRequestStatus.REQUESTED
    assert exc_info.value.actual_status == UserQuestionRequestStatus.COMPLETED
