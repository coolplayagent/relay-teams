# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from pydantic import JsonValue, ValidationError

from relay_teams.logger import get_logger, log_event
from relay_teams.persistence import async_fetchall, async_fetchone
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.sessions.runs.user_question_models import (
    UserQuestionAnswer,
    UserQuestionPrompt,
    UserQuestionRequestRecord,
    UserQuestionRequestStatus,
)
from relay_teams.validation import (
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class UserQuestionStatusConflictError(RuntimeError):
    def __init__(
        self,
        *,
        question_id: str,
        expected_status: UserQuestionRequestStatus,
        actual_status: UserQuestionRequestStatus,
    ) -> None:
        super().__init__(
            "User question status conflict: "
            f"question_id={question_id} "
            f"expected={expected_status.value} actual={actual_status.value}"
        )
        self.question_id = question_id
        self.expected_status = expected_status
        self.actual_status = actual_status


class UserQuestionRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_questions (
                    question_id    TEXT PRIMARY KEY,
                    run_id         TEXT NOT NULL,
                    session_id     TEXT NOT NULL,
                    task_id        TEXT NOT NULL,
                    instance_id    TEXT NOT NULL,
                    role_id        TEXT NOT NULL,
                    tool_name      TEXT NOT NULL,
                    questions_json TEXT NOT NULL,
                    status         TEXT NOT NULL,
                    answers_json   TEXT NOT NULL DEFAULT '[]',
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL,
                    resolved_at    TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_questions_run_status ON user_questions(run_id, status, created_at ASC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_questions_session_status ON user_questions(session_id, status, created_at ASC)"
            )

        self._run_write(
            operation_name="init_tables",
            operation=operation,
        )

    async def _init_tables_async(self) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_questions (
                    question_id    TEXT PRIMARY KEY,
                    run_id         TEXT NOT NULL,
                    session_id     TEXT NOT NULL,
                    task_id        TEXT NOT NULL,
                    instance_id    TEXT NOT NULL,
                    role_id        TEXT NOT NULL,
                    tool_name      TEXT NOT NULL,
                    questions_json TEXT NOT NULL,
                    status         TEXT NOT NULL,
                    answers_json   TEXT NOT NULL DEFAULT '[]',
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL,
                    resolved_at    TEXT
                )
                """
            )
            await cursor.close()
            cursor = await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_questions_run_status ON user_questions(run_id, status, created_at ASC)"
            )
            await cursor.close()
            cursor = await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_questions_session_status ON user_questions(session_id, status, created_at ASC)"
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="init_tables_async",
            operation=lambda _conn: operation(),
        )

    def upsert_requested(
        self,
        *,
        question_id: str,
        run_id: str,
        session_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        tool_name: str,
        questions: tuple[UserQuestionPrompt, ...],
    ) -> UserQuestionRequestRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        questions_json = json.dumps(
            [question.model_dump(mode="json") for question in questions],
            ensure_ascii=False,
            sort_keys=True,
        )

        def operation() -> None:
            existing = self.get(question_id)
            created_at = (
                existing.created_at.isoformat() if existing is not None else now
            )
            self._conn.execute(
                """
                INSERT INTO user_questions(question_id, run_id, session_id, task_id, instance_id, role_id,
                                           tool_name, questions_json, status, answers_json, created_at, updated_at, resolved_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(question_id)
                DO UPDATE SET
                    run_id=excluded.run_id,
                    session_id=excluded.session_id,
                    task_id=excluded.task_id,
                    instance_id=excluded.instance_id,
                    role_id=excluded.role_id,
                    tool_name=excluded.tool_name,
                    questions_json=excluded.questions_json,
                    status=excluded.status,
                    answers_json=excluded.answers_json,
                    updated_at=excluded.updated_at,
                    resolved_at=excluded.resolved_at
                """,
                (
                    question_id,
                    run_id,
                    session_id,
                    task_id,
                    instance_id,
                    role_id,
                    tool_name,
                    questions_json,
                    UserQuestionRequestStatus.REQUESTED.value,
                    "[]",
                    created_at,
                    now,
                    None,
                ),
            )

        self._run_write(
            operation_name="upsert_requested",
            operation=operation,
        )
        record = self.get(question_id)
        if record is None:
            raise RuntimeError(f"Failed to persist user question {question_id}")
        return record

    async def upsert_requested_async(
        self,
        *,
        question_id: str,
        run_id: str,
        session_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        tool_name: str,
        questions: tuple[UserQuestionPrompt, ...],
    ) -> UserQuestionRequestRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        questions_json = json.dumps(
            [question.model_dump(mode="json") for question in questions],
            ensure_ascii=False,
            sort_keys=True,
        )
        existing = await self.get_async(question_id)
        created_at = existing.created_at.isoformat() if existing is not None else now

        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
                """
                INSERT INTO user_questions(question_id, run_id, session_id, task_id, instance_id, role_id,
                                           tool_name, questions_json, status, answers_json, created_at, updated_at, resolved_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(question_id)
                DO UPDATE SET
                    run_id=excluded.run_id,
                    session_id=excluded.session_id,
                    task_id=excluded.task_id,
                    instance_id=excluded.instance_id,
                    role_id=excluded.role_id,
                    tool_name=excluded.tool_name,
                    questions_json=excluded.questions_json,
                    status=excluded.status,
                    answers_json=excluded.answers_json,
                    updated_at=excluded.updated_at,
                    resolved_at=excluded.resolved_at
                """,
                (
                    question_id,
                    run_id,
                    session_id,
                    task_id,
                    instance_id,
                    role_id,
                    tool_name,
                    questions_json,
                    UserQuestionRequestStatus.REQUESTED.value,
                    "[]",
                    created_at,
                    now,
                    None,
                ),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="upsert_requested_async",
            operation=lambda _conn: operation(),
        )
        record = await self.get_async(question_id)
        if record is None:
            raise RuntimeError(f"Failed to persist user question {question_id}")
        return record

    def resolve(
        self,
        *,
        question_id: str,
        status: UserQuestionRequestStatus,
        answers: tuple[UserQuestionAnswer, ...] = (),
        expected_status: UserQuestionRequestStatus | None = None,
    ) -> UserQuestionRequestRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        answers_json = json.dumps(
            [answer.model_dump(mode="json") for answer in answers],
            ensure_ascii=False,
            sort_keys=True,
        )
        resolved_at = now if status != UserQuestionRequestStatus.REQUESTED else None
        rowcount = self._run_write(
            operation_name="resolve",
            operation=lambda: self._resolve_row(
                question_id=question_id,
                status=status,
                answers_json=answers_json,
                updated_at=now,
                resolved_at=resolved_at,
                expected_status=expected_status,
            ),
        )
        record = self.get(question_id)
        if record is None:
            raise KeyError(f"Unknown user question: {question_id}")
        if rowcount == 0 and expected_status is not None:
            raise UserQuestionStatusConflictError(
                question_id=question_id,
                expected_status=expected_status,
                actual_status=record.status,
            )
        return record

    async def resolve_async(
        self,
        *,
        question_id: str,
        status: UserQuestionRequestStatus,
        answers: tuple[UserQuestionAnswer, ...] = (),
        expected_status: UserQuestionRequestStatus | None = None,
    ) -> UserQuestionRequestRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        answers_json = json.dumps(
            [answer.model_dump(mode="json") for answer in answers],
            ensure_ascii=False,
            sort_keys=True,
        )
        resolved_at = now if status != UserQuestionRequestStatus.REQUESTED else None
        rowcount = await self._run_async_write(
            operation_name="resolve_async",
            operation=lambda conn: self._resolve_row_async(
                conn=conn,
                question_id=question_id,
                status=status,
                answers_json=answers_json,
                updated_at=now,
                resolved_at=resolved_at,
                expected_status=expected_status,
            ),
        )
        record = await self.get_async(question_id)
        if record is None:
            raise KeyError(f"Unknown user question: {question_id}")
        if rowcount == 0 and expected_status is not None:
            raise UserQuestionStatusConflictError(
                question_id=question_id,
                expected_status=expected_status,
                actual_status=record.status,
            )
        return record

    def mark_completed(self, question_id: str) -> UserQuestionRequestRecord | None:
        record = self.get(question_id)
        if record is None:
            return None
        return self.resolve(
            question_id=question_id,
            status=UserQuestionRequestStatus.COMPLETED,
            answers=record.answers,
        )

    async def mark_completed_async(
        self, question_id: str
    ) -> UserQuestionRequestRecord | None:
        record = await self.get_async(question_id)
        if record is None:
            return None
        return await self.resolve_async(
            question_id=question_id,
            status=UserQuestionRequestStatus.COMPLETED,
            answers=record.answers,
        )

    def get(self, question_id: str) -> UserQuestionRequestRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_questions WHERE question_id=?",
                (question_id,),
            ).fetchone()
        if row is None:
            return None
        return self._record_or_none(row, fallback_invalid_timestamps=True)

    async def get_async(self, question_id: str) -> UserQuestionRequestRecord | None:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                "SELECT * FROM user_questions WHERE question_id=?",
                (question_id,),
            )
        )
        if row is None:
            return None
        return self._record_or_none(row, fallback_invalid_timestamps=True)

    def list_by_run(
        self,
        run_id: str,
        *,
        include_resolved: bool = False,
    ) -> tuple[UserQuestionRequestRecord, ...]:
        query = (
            "SELECT * FROM user_questions WHERE run_id=? ORDER BY created_at ASC"
            if include_resolved
            else (
                "SELECT * FROM user_questions WHERE run_id=? AND status=? "
                "ORDER BY created_at ASC"
            )
        )
        params: tuple[object, ...] = (
            (run_id,)
            if include_resolved
            else (run_id, UserQuestionRequestStatus.REQUESTED.value)
        )
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return tuple(
            record for row in rows if (record := self._record_or_none(row)) is not None
        )

    async def list_by_run_async(
        self,
        run_id: str,
        *,
        include_resolved: bool = False,
    ) -> tuple[UserQuestionRequestRecord, ...]:
        query = (
            "SELECT * FROM user_questions WHERE run_id=? ORDER BY created_at ASC"
            if include_resolved
            else (
                "SELECT * FROM user_questions WHERE run_id=? AND status=? "
                "ORDER BY created_at ASC"
            )
        )
        params: tuple[object, ...] = (
            (run_id,)
            if include_resolved
            else (run_id, UserQuestionRequestStatus.REQUESTED.value)
        )
        rows = await self._run_async_read(
            lambda conn: async_fetchall(conn, query, params)
        )
        return tuple(
            record for row in rows if (record := self._record_or_none(row)) is not None
        )

    def list_by_session(
        self,
        session_id: str,
        *,
        include_resolved: bool = False,
    ) -> tuple[UserQuestionRequestRecord, ...]:
        query = (
            "SELECT * FROM user_questions WHERE session_id=? ORDER BY created_at ASC"
            if include_resolved
            else (
                "SELECT * FROM user_questions WHERE session_id=? AND status=? "
                "ORDER BY created_at ASC"
            )
        )
        params: tuple[object, ...] = (
            (session_id,)
            if include_resolved
            else (session_id, UserQuestionRequestStatus.REQUESTED.value)
        )
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return tuple(
            record for row in rows if (record := self._record_or_none(row)) is not None
        )

    async def list_by_session_async(
        self,
        session_id: str,
        *,
        include_resolved: bool = False,
    ) -> tuple[UserQuestionRequestRecord, ...]:
        query = (
            "SELECT * FROM user_questions WHERE session_id=? ORDER BY created_at ASC"
            if include_resolved
            else (
                "SELECT * FROM user_questions WHERE session_id=? AND status=? "
                "ORDER BY created_at ASC"
            )
        )
        params: tuple[object, ...] = (
            (session_id,)
            if include_resolved
            else (session_id, UserQuestionRequestStatus.REQUESTED.value)
        )
        rows = await self._run_async_read(
            lambda conn: async_fetchall(conn, query, params)
        )
        return tuple(
            record for row in rows if (record := self._record_or_none(row)) is not None
        )

    def delete_by_session(self, session_id: str) -> None:
        self._run_write(
            operation_name="delete_by_session",
            operation=lambda: self._conn.execute(
                "DELETE FROM user_questions WHERE session_id=?",
                (session_id,),
            ),
        )

    async def delete_by_session_async(self, session_id: str) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
                "DELETE FROM user_questions WHERE session_id=?",
                (session_id,),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="delete_by_session_async",
            operation=lambda _conn: operation(),
        )

    def _resolve_row(
        self,
        *,
        question_id: str,
        status: UserQuestionRequestStatus,
        answers_json: str,
        updated_at: str,
        resolved_at: str | None,
        expected_status: UserQuestionRequestStatus | None,
    ) -> int:
        if expected_status is None:
            cursor = self._conn.execute(
                """
                UPDATE user_questions
                SET status=?, answers_json=?, updated_at=?, resolved_at=?
                WHERE question_id=?
                """,
                (status.value, answers_json, updated_at, resolved_at, question_id),
            )
            return int(cursor.rowcount or 0)
        cursor = self._conn.execute(
            """
            UPDATE user_questions
            SET status=?, answers_json=?, updated_at=?, resolved_at=?
            WHERE question_id=? AND status=?
            """,
            (
                status.value,
                answers_json,
                updated_at,
                resolved_at,
                question_id,
                expected_status.value,
            ),
        )
        return int(cursor.rowcount or 0)

    # noinspection PyMethodMayBeStatic
    async def _resolve_row_async(
        self,
        *,
        conn: aiosqlite.Connection,
        question_id: str,
        status: UserQuestionRequestStatus,
        answers_json: str,
        updated_at: str,
        resolved_at: str | None,
        expected_status: UserQuestionRequestStatus | None,
    ) -> int:
        if expected_status is None:
            cursor = await conn.execute(
                """
                UPDATE user_questions
                SET status=?, answers_json=?, updated_at=?, resolved_at=?
                WHERE question_id=?
                """,
                (status.value, answers_json, updated_at, resolved_at, question_id),
            )
            rowcount = int(cursor.rowcount or 0)
            await cursor.close()
            return rowcount
        cursor = await conn.execute(
            """
            UPDATE user_questions
            SET status=?, answers_json=?, updated_at=?, resolved_at=?
            WHERE question_id=? AND status=?
            """,
            (
                status.value,
                answers_json,
                updated_at,
                resolved_at,
                question_id,
                expected_status.value,
            ),
        )
        rowcount = int(cursor.rowcount or 0)
        await cursor.close()
        return rowcount

    def delete_by_run(self, run_id: str) -> None:
        self._run_write(
            operation_name="delete_by_run",
            operation=lambda: self._conn.execute(
                "DELETE FROM user_questions WHERE run_id=?",
                (run_id,),
            ),
        )

    async def delete_by_run_async(self, run_id: str) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
                "DELETE FROM user_questions WHERE run_id=?",
                (run_id,),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="delete_by_run_async",
            operation=lambda _conn: operation(),
        )

    def _to_record(
        self,
        row: sqlite3.Row,
        *,
        fallback_invalid_timestamps: bool = False,
    ) -> UserQuestionRequestRecord:
        question_id = require_persisted_identifier(
            row["question_id"],
            field_name="question_id",
        )
        created_at, updated_at = _load_question_timestamps(
            row=row,
            question_id=question_id,
            fallback_invalid_timestamps=fallback_invalid_timestamps,
        )
        resolved_at = _optional_question_timestamp(
            row=row,
            question_id=question_id,
            field_name="resolved_at",
            fallback_invalid_timestamps=fallback_invalid_timestamps,
            fallback_value=updated_at,
        )
        return UserQuestionRequestRecord(
            question_id=question_id,
            run_id=require_persisted_identifier(row["run_id"], field_name="run_id"),
            session_id=require_persisted_identifier(
                row["session_id"],
                field_name="session_id",
            ),
            task_id=require_persisted_identifier(row["task_id"], field_name="task_id"),
            instance_id=require_persisted_identifier(
                row["instance_id"],
                field_name="instance_id",
            ),
            role_id=require_persisted_identifier(row["role_id"], field_name="role_id"),
            tool_name=require_persisted_identifier(
                row["tool_name"],
                field_name="tool_name",
            ),
            questions=_load_questions(row["questions_json"]),
            status=UserQuestionRequestStatus(str(row["status"])),
            answers=_load_answers(row["answers_json"]),
            created_at=created_at,
            updated_at=updated_at,
            resolved_at=resolved_at,
        )

    def _record_or_none(
        self,
        row: sqlite3.Row,
        *,
        fallback_invalid_timestamps: bool = False,
    ) -> UserQuestionRequestRecord | None:
        try:
            return self._to_record(
                row,
                fallback_invalid_timestamps=fallback_invalid_timestamps,
            )
        except (ValidationError, ValueError) as exc:
            _log_invalid_question_row(row=row, error=exc)
            return None


def _load_questions(raw_value: object) -> tuple[UserQuestionPrompt, ...]:
    decoded = _load_json_list(raw_value, field_name="questions_json")
    return tuple(UserQuestionPrompt.model_validate(item) for item in decoded)


def _load_answers(raw_value: object) -> tuple[UserQuestionAnswer, ...]:
    decoded = _load_json_list(raw_value, field_name="answers_json")
    return tuple(UserQuestionAnswer.model_validate(item) for item in decoded)


def _load_json_list(raw_value: object, *, field_name: str) -> list[object]:
    normalized = normalize_persisted_text(raw_value)
    if normalized is None:
        return []
    try:
        decoded = json.loads(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid persisted {field_name}") from exc
    if not isinstance(decoded, list):
        raise ValueError(f"{field_name} must decode to a list")
    return decoded


def _load_question_timestamps(
    *,
    row: sqlite3.Row,
    question_id: str,
    fallback_invalid_timestamps: bool,
) -> tuple[datetime, datetime]:
    created_at = parse_persisted_datetime_or_none(row["created_at"])
    updated_at = parse_persisted_datetime_or_none(row["updated_at"])
    if not fallback_invalid_timestamps:
        if created_at is None:
            _log_invalid_question_timestamp(
                question_id=question_id,
                field_name="created_at",
                raw_preview=_persisted_value_preview(row["created_at"]),
                fallback_iso=None,
            )
            raise ValueError("Invalid persisted created_at")
        if updated_at is None:
            _log_invalid_question_timestamp(
                question_id=question_id,
                field_name="updated_at",
                raw_preview=_persisted_value_preview(row["updated_at"]),
                fallback_iso=None,
            )
            raise ValueError("Invalid persisted updated_at")
        return created_at, updated_at
    fallback_now = datetime.now(tz=timezone.utc)
    if created_at is None:
        created_at = updated_at or fallback_now
        _log_invalid_question_timestamp(
            question_id=question_id,
            field_name="created_at",
            raw_preview=_persisted_value_preview(row["created_at"]),
            fallback_iso=created_at.isoformat(),
        )
    if updated_at is None:
        updated_at = created_at
        _log_invalid_question_timestamp(
            question_id=question_id,
            field_name="updated_at",
            raw_preview=_persisted_value_preview(row["updated_at"]),
            fallback_iso=updated_at.isoformat(),
        )
    return created_at, updated_at


def _optional_question_timestamp(
    *,
    row: sqlite3.Row,
    question_id: str,
    field_name: str,
    fallback_invalid_timestamps: bool,
    fallback_value: datetime | None,
) -> datetime | None:
    raw_value = row[field_name]
    normalized = normalize_persisted_text(raw_value)
    if normalized is None:
        return None
    parsed = parse_persisted_datetime_or_none(raw_value)
    if parsed is not None:
        return parsed
    _log_invalid_question_timestamp(
        question_id=question_id,
        field_name=field_name,
        raw_preview=_persisted_value_preview(raw_value),
        fallback_iso=fallback_value.isoformat() if fallback_value is not None else None,
    )
    if fallback_invalid_timestamps:
        return fallback_value
    raise ValueError(f"Invalid persisted {field_name}")


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_question_timestamp(
    *,
    question_id: str,
    field_name: str,
    raw_preview: str,
    fallback_iso: str | None,
) -> None:
    payload: dict[str, JsonValue] = {
        "question_id": question_id,
        "field_name": field_name,
        "raw_preview": raw_preview,
        "fallback_iso": fallback_iso,
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="sessions.user_question_repo.timestamp_invalid",
        message=(
            "Using fallback for invalid persisted user question timestamp"
            if fallback_iso is not None
            else "Invalid persisted user question timestamp"
        ),
        payload=payload,
    )


def _log_invalid_question_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "question_id": _persisted_value_preview(row["question_id"]),
        "run_id": _persisted_value_preview(row["run_id"]),
        "session_id": _persisted_value_preview(row["session_id"]),
        "created_at": _persisted_value_preview(row["created_at"]),
        "updated_at": _persisted_value_preview(row["updated_at"]),
        "resolved_at": _persisted_value_preview(row["resolved_at"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="sessions.user_question_repo.row_invalid",
        message="Skipping invalid persisted user question row",
        payload=payload,
    )
