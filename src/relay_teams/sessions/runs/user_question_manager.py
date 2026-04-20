# -*- coding: utf-8 -*-
from __future__ import annotations

import threading

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.sessions.runs.user_question_models import UserQuestionAnswerSubmission

DEFAULT_USER_QUESTION_TIMEOUT_SECONDS = 20 * 60.0


class UserQuestionClosedError(RuntimeError):
    pass


class _UserQuestionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    instance_id: str
    role_id: str
    event: threading.Event = Field(default_factory=threading.Event)
    answers: UserQuestionAnswerSubmission | None = None
    close_reason: str | None = None


class UserQuestionManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._questions: dict[str, dict[str, _UserQuestionEntry]] = {}

    def open_question(
        self,
        *,
        run_id: str,
        question_id: str,
        instance_id: str,
        role_id: str,
    ) -> None:
        with self._lock:
            self._questions.setdefault(run_id, {})[question_id] = _UserQuestionEntry(
                instance_id=instance_id,
                role_id=role_id,
            )

    def resolve_question(
        self,
        *,
        run_id: str,
        question_id: str,
        answers: UserQuestionAnswerSubmission,
    ) -> None:
        with self._lock:
            entry = self._questions.get(run_id, {}).get(question_id)
        if entry is None:
            raise KeyError(
                f"No open user question for run={run_id} question_id={question_id}"
            )
        if entry.close_reason is not None:
            raise UserQuestionClosedError(
                f"User question already closed: run={run_id} question_id={question_id}"
            )
        entry.answers = answers
        entry.event.set()

    def get_question(self, *, run_id: str, question_id: str) -> dict[str, str] | None:
        with self._lock:
            entry = self._questions.get(run_id, {}).get(question_id)
        if entry is None:
            return None
        if entry.close_reason is not None:
            return None
        return {
            "question_id": question_id,
            "instance_id": entry.instance_id,
            "role_id": entry.role_id,
        }

    def wait_for_answer(
        self,
        *,
        run_id: str,
        question_id: str,
        timeout: float = DEFAULT_USER_QUESTION_TIMEOUT_SECONDS,
    ) -> UserQuestionAnswerSubmission:
        with self._lock:
            entry = self._questions.get(run_id, {}).get(question_id)
        if entry is None:
            raise KeyError(
                f"No user question registered for run={run_id} question_id={question_id}"
            )
        triggered = entry.event.wait(timeout=timeout)
        if not triggered:
            raise TimeoutError(
                f"User question timed out after {timeout}s: run={run_id} question_id={question_id}"
            )
        if entry.answers is None:
            if entry.close_reason is not None:
                raise UserQuestionClosedError(
                    f"User question closed: run={run_id} question_id={question_id} "
                    f"reason={entry.close_reason}"
                )
            raise RuntimeError(
                f"User question resolved without answers: run={run_id} question_id={question_id}"
            )
        return entry.answers

    def close_question(
        self,
        *,
        run_id: str,
        question_id: str,
        reason: str = "closed",
    ) -> None:
        with self._lock:
            run_questions = self._questions.get(run_id, {})
            entry = run_questions.get(question_id)
            if entry is None:
                return
            run_questions.pop(question_id, None)
            if not run_questions and run_id in self._questions:
                self._questions.pop(run_id, None)
        entry.close_reason = reason
        entry.event.set()

    def mark_question_closed(
        self,
        *,
        run_id: str,
        question_id: str,
        reason: str = "closed",
    ) -> None:
        with self._lock:
            entry = self._questions.get(run_id, {}).get(question_id)
        if entry is None:
            return
        entry.close_reason = reason
        entry.event.set()

    def mark_questions_closed_for_run(
        self,
        *,
        run_id: str,
        reason: str = "closed",
    ) -> None:
        with self._lock:
            entries = list(self._questions.get(run_id, {}).values())
        for entry in entries:
            entry.close_reason = reason
            entry.event.set()

    def mark_questions_closed_for_instance(
        self,
        *,
        run_id: str,
        instance_id: str,
        reason: str = "closed",
    ) -> tuple[str, ...]:
        with self._lock:
            run_questions = self._questions.get(run_id, {})
            question_ids = [
                question_id
                for question_id, entry in run_questions.items()
                if entry.instance_id == instance_id
            ]
            entries = [
                run_questions[question_id]
                for question_id in question_ids
                if question_id in run_questions
            ]
        for entry in entries:
            entry.close_reason = reason
            entry.event.set()
        return tuple(question_ids)
