# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import JsonValue

from relay_teams.gateway.user_questions import (
    UserQuestionAnswerStatus,
    answer_pending_user_question,
    answer_pending_user_question_async,
    answer_pending_user_question_for_session_status_async,
    answer_pending_user_question_status,
    answer_pending_user_question_status_async,
    build_answer_submission,
    format_user_question_event,
    format_user_question_request,
    is_user_question_requested,
    is_user_question_requested_async,
    parse_user_question_event,
)
from relay_teams.sessions.runs.user_question_models import (
    NONE_OF_THE_ABOVE_OPTION_LABEL,
    UserQuestionAnswerSubmission,
    UserQuestionPrompt,
)


def test_format_user_question_request_lists_options() -> None:
    text = format_user_question_request(
        question_id="question-1",
        questions=(
            UserQuestionPrompt.model_validate(
                {
                    "header": "Version",
                    "question": "Pick the release target",
                    "options": [
                        {"label": "1.0", "description": "Stable"},
                        {"label": "2.0"},
                    ],
                }
            ),
        ),
    )

    assert "question-1" in text
    assert "Pick the release target" in text
    assert "- 1.0: Stable" in text
    assert "- 2.0" in text


def test_format_user_question_request_notes_multi_question_order() -> None:
    text = format_user_question_request(
        question_id="question-1",
        questions=(
            UserQuestionPrompt.model_validate(
                {"question": "Pick version", "options": [{"label": "1.0"}]}
            ),
            UserQuestionPrompt.model_validate(
                {"question": "Pick action", "options": [{"label": "Ship"}]}
            ),
        ),
    )

    assert "1. Pick version" in text
    assert "2. Pick action" in text


def test_build_answer_submission_uses_matching_option() -> None:
    submission = build_answer_submission(
        text="Ship",
        questions=(
            UserQuestionPrompt.model_validate(
                {
                    "question": "Pick one",
                    "options": [{"label": "Ship"}, {"label": "Wait"}],
                }
            ),
        ),
    )

    assert submission.answers[0].selections[0].label == "Ship"
    assert submission.answers[0].selections[0].supplement is None


def test_build_answer_submission_uses_supplement_for_free_text() -> None:
    submission = build_answer_submission(
        text="Use the beta channel",
        questions=(
            UserQuestionPrompt.model_validate(
                {
                    "question": "Pick one",
                    "options": [{"label": "Ship"}, {"label": "Wait"}],
                }
            ),
        ),
    )

    selection = submission.answers[0].selections[0]
    assert selection.label == NONE_OF_THE_ABOVE_OPTION_LABEL
    assert selection.supplement == "Use the beta channel"


def test_build_answer_submission_splits_multiple_selection() -> None:
    submission = build_answer_submission(
        text="Docs, Tests",
        questions=(
            UserQuestionPrompt.model_validate(
                {
                    "question": "Pick items",
                    "multiple": True,
                    "options": [
                        {"label": "Docs"},
                        {"label": "Tests"},
                        {"label": "Deploy"},
                    ],
                }
            ),
        ),
    )

    assert tuple(item.label for item in submission.answers[0].selections) == (
        "Docs",
        "Tests",
    )


def test_build_answer_submission_preserves_unknown_multiple_selection() -> None:
    submission = build_answer_submission(
        text="Docs, Deployy",
        questions=(
            UserQuestionPrompt.model_validate(
                {
                    "question": "Pick items",
                    "multiple": True,
                    "options": [
                        {"label": "Docs"},
                        {"label": "Tests"},
                        {"label": "Deploy"},
                    ],
                }
            ),
        ),
    )

    selection = submission.answers[0].selections[0]
    assert selection.label == NONE_OF_THE_ABOVE_OPTION_LABEL
    assert selection.supplement == "Docs, Deployy"


class _FakeRunService:
    def __init__(self, records: list[dict[str, JsonValue]]) -> None:
        self.records = records
        self.answered: list[tuple[str, str]] = []

    def list_user_questions(self, run_id: str) -> list[dict[str, JsonValue]]:
        _ = run_id
        return self.records

    def list_user_questions_by_session(
        self,
        session_id: str,
    ) -> list[dict[str, JsonValue]]:
        _ = session_id
        return self.records

    def answer_user_question(
        self,
        *,
        run_id: str,
        question_id: str,
        answers: object,
    ) -> dict[str, JsonValue]:
        _ = answers
        self.answered.append((run_id, question_id))
        return {"run_id": run_id, "question_id": question_id}


class _FakeAsyncRunService:
    def __init__(self, records: list[dict[str, JsonValue]]) -> None:
        self.records = records
        self.answered: list[tuple[str, str]] = []

    async def list_user_questions_async(
        self, run_id: str
    ) -> list[dict[str, JsonValue]]:
        _ = run_id
        return self.records

    async def list_user_questions_by_session_async(
        self,
        session_id: str,
    ) -> list[dict[str, JsonValue]]:
        _ = session_id
        return self.records

    async def answer_user_question_async(
        self,
        *,
        run_id: str,
        question_id: str,
        answers: UserQuestionAnswerSubmission,
    ) -> dict[str, JsonValue]:
        _ = answers
        self.answered.append((run_id, question_id))
        return {"run_id": run_id, "question_id": question_id}


def _question_record(
    *,
    question_id: str = "question-1",
    run_id: str = "run-1",
    status: str = "requested",
) -> dict[str, JsonValue]:
    return {
        "question_id": question_id,
        "run_id": run_id,
        "session_id": "session-1",
        "task_id": "task-1",
        "instance_id": "instance-1",
        "role_id": "role-1",
        "tool_name": "ask_question",
        "questions": [
            {
                "question": "Proceed?",
                "options": [{"label": "Yes"}, {"label": "No"}],
            }
        ],
        "status": status,
        "answers": [],
        "created_at": "2026-05-12T00:00:00+00:00",
        "updated_at": "2026-05-12T00:00:00+00:00",
        "resolved_at": None,
    }


def test_parse_user_question_event_rejects_invalid_payloads() -> None:
    assert parse_user_question_event("{") is None
    assert parse_user_question_event("[]") is None
    assert parse_user_question_event('{"question_id": "", "questions": []}') is None
    assert (
        parse_user_question_event('{"question_id": "question-1", "questions": {}}')
        is None
    )
    assert (
        parse_user_question_event(
            '{"question_id": "question-1", "questions": [{"options": []}]}'
        )
        is None
    )


def test_format_user_question_event_formats_valid_payload() -> None:
    text = format_user_question_event(
        """
        {
            "question_id": "question-1",
            "ignored": "value",
            "questions": [{"question": "Proceed?", "options": [{"label": "Yes"}]}]
        }
        """
    )

    assert text is not None
    assert "question-1" in text
    assert "Proceed?" in text


def test_answer_pending_user_question_bool_wrapper() -> None:
    service = _FakeRunService([_question_record()])

    assert (
        answer_pending_user_question(
            run_service=service,
            run_id="run-1",
            text="Yes",
        )
        is True
    )


def test_answer_pending_user_question_returns_not_pending_for_bad_records() -> None:
    service = _FakeRunService([{"question_id": "bad"}])

    status = answer_pending_user_question_status(
        run_service=service,
        run_id="run-1",
        text="Yes",
    )

    assert status == UserQuestionAnswerStatus.NOT_PENDING
    assert service.answered == []


def test_is_user_question_requested_filters_empty_and_bad_records() -> None:
    service = _FakeRunService([{"question_id": "bad"}, _question_record()])

    assert (
        is_user_question_requested(
            run_service=service,
            run_id="run-1",
            question_id=" ",
        )
        is False
    )
    assert (
        is_user_question_requested(
            run_service=service,
            run_id="run-1",
            question_id=" question-1 ",
        )
        is True
    )


@pytest.mark.asyncio
async def test_answer_pending_user_question_async_bool_wrapper() -> None:
    service = _FakeAsyncRunService([_question_record()])

    assert (
        await answer_pending_user_question_async(
            run_service=service,
            run_id="run-1",
            text="Yes",
        )
        is True
    )
    assert service.answered == [("run-1", "question-1")]


@pytest.mark.asyncio
async def test_answer_pending_user_question_status_async_invalid_reply() -> None:
    service = _FakeAsyncRunService(
        [
            _question_record(),
            {
                **_question_record(question_id="question-2"),
                "questions": [
                    {
                        "question": "Proceed?",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    },
                    {
                        "question": "Notify?",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    },
                ],
                "created_at": "2026-05-12T00:01:00+00:00",
            },
        ]
    )

    status = await answer_pending_user_question_status_async(
        run_service=service,
        run_id="run-1",
        text="Yes",
    )

    assert status == UserQuestionAnswerStatus.INVALID_REPLY
    assert service.answered == []


@pytest.mark.asyncio
async def test_answer_pending_user_question_for_session_status_async_returns_run() -> (
    None
):
    service = _FakeAsyncRunService([_question_record()])

    status, run_id = await answer_pending_user_question_for_session_status_async(
        run_service=service,
        session_id="session-1",
        text="Yes",
    )

    assert status == UserQuestionAnswerStatus.ANSWERED
    assert run_id == "run-1"
    assert service.answered == [("run-1", "question-1")]


@pytest.mark.asyncio
async def test_is_user_question_requested_async_ignores_non_requested() -> None:
    service = _FakeAsyncRunService(
        [_question_record(question_id="question-1", status="answered")]
    )

    assert (
        await is_user_question_requested_async(
            run_service=service,
            run_id="run-1",
            question_id="question-1",
        )
        is False
    )


def test_build_answer_submission_rejects_partial_multi_question_reply() -> None:
    with pytest.raises(ValueError, match="one non-empty line"):
        build_answer_submission(
            text="Yes",
            questions=(
                UserQuestionPrompt.model_validate(
                    {
                        "question": "Proceed?",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    }
                ),
                UserQuestionPrompt.model_validate(
                    {
                        "question": "Notify users?",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    }
                ),
            ),
        )


def test_answer_pending_user_question_marks_partial_reply_invalid() -> None:
    service = _FakeRunService(
        [
            {
                "question_id": "question-1",
                "run_id": "run-1",
                "session_id": "session-1",
                "task_id": "task-1",
                "instance_id": "instance-1",
                "role_id": "role-1",
                "tool_name": "ask_question",
                "questions": [
                    {
                        "question": "Proceed?",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    },
                    {
                        "question": "Notify users?",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    },
                ],
                "status": "requested",
                "answers": [],
                "created_at": "2026-05-12T00:00:00+00:00",
                "updated_at": "2026-05-12T00:00:00+00:00",
                "resolved_at": None,
            }
        ]
    )

    status = answer_pending_user_question_status(
        run_service=service,
        run_id="run-1",
        text="Yes",
    )

    assert status == UserQuestionAnswerStatus.INVALID_REPLY
    assert service.answered == []


def test_build_answer_submission_preserves_decimal_option_labels() -> None:
    submission = build_answer_submission(
        text="1.0\n2. Notify users",
        questions=(
            UserQuestionPrompt.model_validate(
                {
                    "question": "Pick version",
                    "options": [{"label": "1.0"}, {"label": "2.0"}],
                }
            ),
            UserQuestionPrompt.model_validate(
                {
                    "question": "Pick action",
                    "options": [{"label": "Notify users"}, {"label": "Skip"}],
                }
            ),
        ),
    )

    assert submission.answers[0].selections[0].label == "1.0"
    assert submission.answers[1].selections[0].label == "Notify users"


def test_build_answer_submission_preserves_numbered_option_label() -> None:
    submission = build_answer_submission(
        text="1. High\n2. Now",
        questions=(
            UserQuestionPrompt.model_validate(
                {
                    "question": "Pick priority",
                    "options": [{"label": "1. High"}, {"label": "2. Low"}],
                }
            ),
            UserQuestionPrompt.model_validate(
                {
                    "question": "Pick timing",
                    "options": [{"label": "Now"}, {"label": "Later"}],
                }
            ),
        ),
    )

    assert submission.answers[0].selections[0].label == "1. High"
    assert submission.answers[1].selections[0].label == "Now"


def test_answer_pending_user_question_uses_latest_requested_question() -> None:
    service = _FakeRunService(
        [
            {
                "question_id": "question-old",
                "run_id": "run-1",
                "session_id": "session-1",
                "task_id": "task-1",
                "instance_id": "instance-1",
                "role_id": "role-1",
                "tool_name": "ask_question",
                "questions": [
                    {
                        "question": "Old question",
                        "options": [{"label": "Old"}],
                    }
                ],
                "status": "requested",
                "answers": [],
                "created_at": "2026-05-12T00:00:00+00:00",
                "updated_at": "2026-05-12T00:00:00+00:00",
                "resolved_at": None,
            },
            {
                "question_id": "question-new",
                "run_id": "run-1",
                "session_id": "session-1",
                "task_id": "task-1",
                "instance_id": "instance-1",
                "role_id": "role-1",
                "tool_name": "ask_question",
                "questions": [
                    {
                        "question": "New question",
                        "options": [{"label": "New"}],
                    }
                ],
                "status": "requested",
                "answers": [],
                "created_at": "2026-05-12T00:01:00+00:00",
                "updated_at": "2026-05-12T00:01:00+00:00",
                "resolved_at": None,
            },
        ]
    )

    status = answer_pending_user_question_status(
        run_service=service,
        run_id="run-1",
        text="New",
    )

    assert status == UserQuestionAnswerStatus.ANSWERED
    assert service.answered == [("run-1", "question-new")]
