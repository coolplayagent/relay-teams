# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, cast

from pydantic import JsonValue

from relay_teams.sessions.runs.user_question_models import (
    NONE_OF_THE_ABOVE_OPTION_LABEL,
    UserQuestionAnswer,
    UserQuestionAnswerSubmission,
    UserQuestionPrompt,
    UserQuestionRequestRecord,
    UserQuestionRequestStatus,
    UserQuestionSelection,
)


class UserQuestionAnswerStatus(str, Enum):
    NOT_PENDING = "not_pending"
    ANSWERED = "answered"
    INVALID_REPLY = "invalid_reply"


class UserQuestionRunService(Protocol):
    def list_user_questions(self, run_id: str) -> list[dict[str, JsonValue]]:
        raise NotImplementedError  # pragma: no cover

    def answer_user_question(
        self,
        *,
        run_id: str,
        question_id: str,
        answers: UserQuestionAnswerSubmission,
    ) -> dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover


class UserQuestionRunServiceAsync(Protocol):
    async def list_user_questions_async(
        self,
        run_id: str,
    ) -> list[dict[str, JsonValue]]:
        raise NotImplementedError  # pragma: no cover

    async def answer_user_question_async(
        self,
        *,
        run_id: str,
        question_id: str,
        answers: UserQuestionAnswerSubmission,
    ) -> dict[str, JsonValue]:
        raise NotImplementedError  # pragma: no cover


class UserQuestionSessionRunServiceAsync(UserQuestionRunServiceAsync, Protocol):
    async def list_user_questions_by_session_async(
        self,
        session_id: str,
    ) -> list[dict[str, JsonValue]]:
        raise NotImplementedError  # pragma: no cover


def format_user_question_request(
    *,
    question_id: str,
    questions: tuple[UserQuestionPrompt, ...],
) -> str:
    lines = [
        "需要你补充信息后我才能继续。",
        "",
        f"问题编号: {question_id}",
    ]
    if len(questions) > 1:
        lines.append("请按问题顺序逐行回复。")
    lines.append(
        "直接回复选项文字；如果选项都不合适，直接回复补充内容。多选请用逗号分隔。"
    )
    for index, question in enumerate(questions, start=1):
        lines.append("")
        heading = question.header.strip()
        if heading:
            lines.append(f"{index}. {heading}")
            lines.append(question.question)
        else:
            lines.append(f"{index}. {question.question}")
        option_lines = _format_options(question)
        if option_lines:
            lines.append("选项:")
            lines.extend(option_lines)
    return "\n".join(lines)


def format_user_question_event(payload_json: str) -> str | None:
    parsed = parse_user_question_event(payload_json)
    if parsed is None:
        return None
    question_id, questions = parsed
    return format_user_question_request(
        question_id=question_id,
        questions=questions,
    )


def parse_user_question_event(
    payload_json: str,
) -> tuple[str, tuple[UserQuestionPrompt, ...]] | None:
    payload = _json_object(payload_json)
    if payload is None:
        return None
    question_id = _payload_text(payload, "question_id")
    raw_questions = payload.get("questions")
    if not question_id or not isinstance(raw_questions, list):
        return None
    questions = _load_prompts(raw_questions)
    if not questions:
        return None
    return question_id, questions


def answer_pending_user_question(
    *,
    run_service: UserQuestionRunService,
    run_id: str,
    text: str,
) -> bool:
    return (
        answer_pending_user_question_status(
            run_service=run_service,
            run_id=run_id,
            text=text,
        )
        == UserQuestionAnswerStatus.ANSWERED
    )


def answer_pending_user_question_status(
    *,
    run_service: UserQuestionRunService,
    run_id: str,
    text: str,
) -> UserQuestionAnswerStatus:
    record = _latest_requested_question(run_service.list_user_questions(run_id))
    return _answer_pending_user_question_record(
        run_service=run_service,
        record=record,
        text=text,
    )


def _answer_pending_user_question_record(
    *,
    run_service: UserQuestionRunService,
    record: UserQuestionRequestRecord | None,
    text: str,
) -> UserQuestionAnswerStatus:
    if record is None:
        return UserQuestionAnswerStatus.NOT_PENDING
    submission = try_build_answer_submission(text=text, questions=record.questions)
    if submission is None:
        return UserQuestionAnswerStatus.INVALID_REPLY
    _ = run_service.answer_user_question(
        run_id=record.run_id,
        question_id=record.question_id,
        answers=submission,
    )
    return UserQuestionAnswerStatus.ANSWERED


async def answer_pending_user_question_async(
    *,
    run_service: UserQuestionRunServiceAsync,
    run_id: str,
    text: str,
) -> bool:
    return (
        await answer_pending_user_question_status_async(
            run_service=run_service,
            run_id=run_id,
            text=text,
        )
        == UserQuestionAnswerStatus.ANSWERED
    )


async def answer_pending_user_question_status_async(
    *,
    run_service: UserQuestionRunServiceAsync,
    run_id: str,
    text: str,
    message_created_at: datetime | None = None,
) -> UserQuestionAnswerStatus:
    record = _latest_requested_question(
        await run_service.list_user_questions_async(run_id),
        message_created_at=message_created_at,
    )
    return await _answer_pending_user_question_record_async(
        run_service=run_service,
        record=record,
        text=text,
    )


async def answer_pending_user_question_for_session_status_async(
    *,
    run_service: UserQuestionSessionRunServiceAsync,
    session_id: str,
    text: str,
    message_created_at: datetime | None = None,
) -> tuple[UserQuestionAnswerStatus, str | None]:
    record = _latest_requested_question(
        await run_service.list_user_questions_by_session_async(session_id),
        message_created_at=message_created_at,
    )
    status = await _answer_pending_user_question_record_async(
        run_service=run_service,
        record=record,
        text=text,
    )
    return status, record.run_id if record is not None else None


async def _answer_pending_user_question_record_async(
    *,
    run_service: UserQuestionRunServiceAsync,
    record: UserQuestionRequestRecord | None,
    text: str,
) -> UserQuestionAnswerStatus:
    if record is None:
        return UserQuestionAnswerStatus.NOT_PENDING
    submission = try_build_answer_submission(text=text, questions=record.questions)
    if submission is None:
        return UserQuestionAnswerStatus.INVALID_REPLY
    _ = await run_service.answer_user_question_async(
        run_id=record.run_id,
        question_id=record.question_id,
        answers=submission,
    )
    return UserQuestionAnswerStatus.ANSWERED


def is_user_question_requested(
    *,
    run_service: UserQuestionRunService,
    run_id: str,
    question_id: str,
) -> bool:
    return (
        _requested_question(
            run_service.list_user_questions(run_id),
            question_id=question_id,
        )
        is not None
    )


async def is_user_question_requested_async(
    *,
    run_service: UserQuestionRunServiceAsync,
    run_id: str,
    question_id: str,
) -> bool:
    return (
        _requested_question(
            await run_service.list_user_questions_async(run_id),
            question_id=question_id,
        )
        is not None
    )


def build_answer_submission(
    *,
    text: str,
    questions: tuple[UserQuestionPrompt, ...],
) -> UserQuestionAnswerSubmission:
    submission = try_build_answer_submission(text=text, questions=questions)
    if submission is None:
        raise ValueError("Reply must include one non-empty line for each question")
    return submission


def try_build_answer_submission(
    *,
    text: str,
    questions: tuple[UserQuestionPrompt, ...],
) -> UserQuestionAnswerSubmission | None:
    answer_texts = _answer_texts(text=text, questions=questions)
    if answer_texts is None:
        return None
    return UserQuestionAnswerSubmission(
        answers=tuple(
            _answer_for_prompt(text=answer_texts[index], question=question)
            for index, question in enumerate(questions)
        )
    )


def _answer_texts(
    *,
    text: str,
    questions: tuple[UserQuestionPrompt, ...],
) -> tuple[str, ...] | None:
    normalized = text.strip()
    question_count = len(questions)
    if question_count <= 1:
        return (normalized,)
    lines = tuple(line.strip() for line in normalized.splitlines() if line.strip())
    if len(lines) == question_count:
        return tuple(
            _strip_number_prefix(
                line,
                question=questions[index],
                position=index + 1,
            )
            for index, line in enumerate(lines)
        )
    stripped_lines = tuple(
        _strip_number_prefix(
            line,
            question=questions[min(index, question_count - 1)],
            position=index + 1,
        )
        for index, line in enumerate(lines)
    )
    if len(stripped_lines) > question_count:
        return stripped_lines[: question_count - 1] + (
            "\n".join(stripped_lines[question_count - 1 :]),
        )
    return None


def _answer_for_prompt(
    *, text: str, question: UserQuestionPrompt
) -> UserQuestionAnswer:
    normalized = text.strip()
    labels_by_key = {
        option.label.casefold(): option.label
        for option in question.options
        if option.label != NONE_OF_THE_ABOVE_OPTION_LABEL
    }
    selected_labels: list[str] = []
    candidates = _split_selection_candidates(normalized) if question.multiple else ()
    if question.multiple:
        for candidate in candidates:
            label = labels_by_key.get(candidate.casefold())
            if label is None:
                return _free_text_answer(normalized)
            if label not in selected_labels:
                selected_labels.append(label)
    else:
        label = labels_by_key.get(normalized.casefold())
        if label is not None:
            selected_labels.append(label)
    if selected_labels:
        return UserQuestionAnswer(
            selections=tuple(
                UserQuestionSelection(label=label) for label in selected_labels
            )
        )
    return _free_text_answer(normalized)


def _free_text_answer(text: str) -> UserQuestionAnswer:
    return UserQuestionAnswer(
        selections=(
            UserQuestionSelection(
                label=NONE_OF_THE_ABOVE_OPTION_LABEL,
                supplement=text or None,
            ),
        )
    )


def _split_selection_candidates(text: str) -> tuple[str, ...]:
    return tuple(
        item.strip() for item in re.split(r"[,，;；\n]+", text) if item.strip()
    )


def _strip_number_prefix(
    text: str,
    *,
    question: UserQuestionPrompt,
    position: int,
) -> str:
    normalized = text.strip()
    if _matches_option_label(normalized, question):
        return normalized
    return re.sub(rf"^\s*{position}[.)、]\s+", "", normalized).strip()


def _matches_option_label(text: str, question: UserQuestionPrompt) -> bool:
    normalized = text.casefold()
    return any(
        option.label.casefold() == normalized
        for option in question.options
        if option.label != NONE_OF_THE_ABOVE_OPTION_LABEL
    )


def _latest_requested_question(
    raw_records: list[dict[str, JsonValue]],
    *,
    message_created_at: datetime | None = None,
) -> UserQuestionRequestRecord | None:
    requested: list[UserQuestionRequestRecord] = []
    for item in raw_records:
        try:
            record = UserQuestionRequestRecord.model_validate(item)
        except ValueError:
            continue
        if record.status != UserQuestionRequestStatus.REQUESTED:
            continue
        if message_created_at is not None and _utc_datetime(
            record.created_at
        ) > _utc_datetime(message_created_at):
            continue
        requested.append(record)
    if not requested:
        return None
    return max(requested, key=lambda question_record: question_record.created_at)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _requested_question(
    raw_records: list[dict[str, JsonValue]],
    *,
    question_id: str,
) -> UserQuestionRequestRecord | None:
    normalized_question_id = question_id.strip()
    if not normalized_question_id:
        return None
    for item in raw_records:
        try:
            record = UserQuestionRequestRecord.model_validate(item)
        except ValueError:
            continue
        if (
            record.question_id == normalized_question_id
            and record.status == UserQuestionRequestStatus.REQUESTED
        ):
            return record
    return None


def _format_options(question: UserQuestionPrompt) -> list[str]:
    lines: list[str] = []
    for option in question.options:
        if option.label == NONE_OF_THE_ABOVE_OPTION_LABEL:
            continue
        if option.description:
            lines.append(f"- {option.label}: {option.description}")
        else:
            lines.append(f"- {option.label}")
    return lines


def _json_object(payload_json: str) -> dict[str, JsonValue] | None:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return {
        str(key): cast(JsonValue, value)
        for key, value in payload.items()
        if isinstance(key, str)
    }


def _payload_text(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _load_prompts(raw_questions: list[JsonValue]) -> tuple[UserQuestionPrompt, ...]:
    prompts: list[UserQuestionPrompt] = []
    for item in raw_questions:
        try:
            prompts.append(UserQuestionPrompt.model_validate(item))
        except ValueError:
            continue
    return tuple(prompts)
