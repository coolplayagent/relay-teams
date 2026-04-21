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
