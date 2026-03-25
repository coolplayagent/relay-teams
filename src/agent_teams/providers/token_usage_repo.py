# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from pydantic import BaseModel, ConfigDict

from agent_teams.persistence.db import open_sqlite
from agent_teams.sessions.session_history_marker_models import SessionHistoryMarkerType
from agent_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)


class TokenUsageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    instance_id: str
    role_id: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    requests: int
    tool_calls: int
    recorded_at: datetime


class AgentTokenSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str
    role_id: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    total_tokens: int
    requests: int
    tool_calls: int


class RunTokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    total_input_tokens: int
    total_cached_input_tokens: int
    total_output_tokens: int
    total_reasoning_output_tokens: int
    total_tokens: int
    total_requests: int
    total_tool_calls: int
    by_agent: list[AgentTokenSummary]


class SessionTokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    total_input_tokens: int
    total_cached_input_tokens: int
    total_output_tokens: int
    total_reasoning_output_tokens: int
    total_tokens: int
    total_requests: int
    total_tool_calls: int
    by_role: dict[str, AgentTokenSummary]


class TokenUsageRepository:
    _NUMERIC_COLUMNS: tuple[str, ...] = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "requests",
        "tool_calls",
    )

    def __init__(
        self,
        db_path: Path,
        *,
        session_history_marker_repo: SessionHistoryMarkerRepository | None = None,
    ) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._session_history_marker_repo = session_history_marker_repo
        self._init_tables()

    def _init_tables(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS token_usage (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id    TEXT NOT NULL,
                    run_id        TEXT NOT NULL,
                    instance_id   TEXT NOT NULL,
                    role_id       TEXT NOT NULL,
                    input_tokens  INTEGER DEFAULT 0,
                    cached_input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    reasoning_output_tokens INTEGER DEFAULT 0,
                    requests      INTEGER DEFAULT 0,
                    tool_calls    INTEGER DEFAULT 0,
                    recorded_at   TEXT NOT NULL
                )
                """
            )
            columns = [
                str(row["name"])
                for row in self._conn.execute(
                    "PRAGMA table_info(token_usage)"
                ).fetchall()
            ]
            if "requests" not in columns:
                self._conn.execute(
                    "ALTER TABLE token_usage ADD COLUMN requests INTEGER NOT NULL DEFAULT 0"
                )
            if "tool_calls" not in columns:
                self._conn.execute(
                    "ALTER TABLE token_usage ADD COLUMN tool_calls INTEGER NOT NULL DEFAULT 0"
                )
            if "cached_input_tokens" not in columns:
                self._conn.execute(
                    """
                    ALTER TABLE token_usage
                    ADD COLUMN cached_input_tokens INTEGER NOT NULL DEFAULT 0
                    """
                )
            if "reasoning_output_tokens" not in columns:
                self._conn.execute(
                    """
                    ALTER TABLE token_usage
                    ADD COLUMN reasoning_output_tokens INTEGER NOT NULL DEFAULT 0
                    """
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_token_usage_run ON token_usage(run_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_token_usage_session ON token_usage(session_id)"
            )
            self._sanitize_numeric_columns()
            self._conn.commit()

    def record(
        self,
        *,
        session_id: str,
        run_id: str,
        instance_id: str,
        role_id: str,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_output_tokens: int = 0,
        requests: int = 0,
        tool_calls: int = 0,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO token_usage
                  (session_id, run_id, instance_id, role_id,
                   input_tokens, cached_input_tokens, output_tokens,
                   reasoning_output_tokens, requests, tool_calls, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    run_id,
                    instance_id,
                    role_id,
                    self._coerce_non_negative_int(input_tokens),
                    self._coerce_non_negative_int(cached_input_tokens),
                    self._coerce_non_negative_int(output_tokens),
                    self._coerce_non_negative_int(reasoning_output_tokens),
                    self._coerce_non_negative_int(requests),
                    self._coerce_non_negative_int(tool_calls),
                    now,
                ),
            )
            self._conn.commit()

    def get_by_run(self, run_id: str) -> RunTokenUsage:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM token_usage WHERE run_id=? ORDER BY id ASC",
                (run_id,),
            ).fetchall()

        by_instance: dict[str, AgentTokenSummary] = {}
        for row in rows:
            iid = str(row["instance_id"])
            input_tokens = self._row_int(row, "input_tokens")
            cached_input_tokens = self._row_int(row, "cached_input_tokens")
            output_tokens = self._row_int(row, "output_tokens")
            reasoning_output_tokens = self._row_int(row, "reasoning_output_tokens")
            requests = self._row_int(row, "requests")
            tool_calls = self._row_int(row, "tool_calls")
            if iid in by_instance:
                existing = by_instance[iid]
                by_instance[iid] = AgentTokenSummary(
                    instance_id=iid,
                    role_id=existing.role_id,
                    input_tokens=existing.input_tokens + input_tokens,
                    cached_input_tokens=(
                        existing.cached_input_tokens + cached_input_tokens
                    ),
                    output_tokens=existing.output_tokens + output_tokens,
                    reasoning_output_tokens=(
                        existing.reasoning_output_tokens + reasoning_output_tokens
                    ),
                    total_tokens=existing.total_tokens + input_tokens + output_tokens,
                    requests=existing.requests + requests,
                    tool_calls=existing.tool_calls + tool_calls,
                )
            else:
                by_instance[iid] = AgentTokenSummary(
                    instance_id=iid,
                    role_id=str(row["role_id"]),
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input_tokens,
                    output_tokens=output_tokens,
                    reasoning_output_tokens=reasoning_output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    requests=requests,
                    tool_calls=tool_calls,
                )

        agents = list(by_instance.values())
        total_input = sum(agent.input_tokens for agent in agents)
        total_output = sum(agent.output_tokens for agent in agents)
        total_cached_input = sum(agent.cached_input_tokens for agent in agents)
        total_reasoning_output = sum(agent.reasoning_output_tokens for agent in agents)
        return RunTokenUsage(
            run_id=run_id,
            total_input_tokens=total_input,
            total_cached_input_tokens=total_cached_input,
            total_output_tokens=total_output,
            total_reasoning_output_tokens=total_reasoning_output,
            total_tokens=total_input + total_output,
            total_requests=sum(agent.requests for agent in agents),
            total_tool_calls=sum(agent.tool_calls for agent in agents),
            by_agent=agents,
        )

    def get_by_session(
        self,
        session_id: str,
        *,
        include_cleared: bool = False,
    ) -> SessionTokenUsage:
        query = "SELECT * FROM token_usage WHERE session_id=?"
        params: tuple[str, ...] = (session_id,)
        if not include_cleared:
            cutoff = self._latest_clear_cutoff(session_id)
            if cutoff is not None:
                query += " AND recorded_at>?"
                params = (session_id, cutoff)
        query += " ORDER BY id ASC"
        with self._lock:
            rows = self._conn.execute(
                query,
                params,
            ).fetchall()

        by_role: dict[str, AgentTokenSummary] = {}
        for row in rows:
            role_id = str(row["role_id"])
            input_tokens = self._row_int(row, "input_tokens")
            cached_input_tokens = self._row_int(row, "cached_input_tokens")
            output_tokens = self._row_int(row, "output_tokens")
            reasoning_output_tokens = self._row_int(row, "reasoning_output_tokens")
            requests = self._row_int(row, "requests")
            tool_calls = self._row_int(row, "tool_calls")
            if role_id in by_role:
                existing = by_role[role_id]
                by_role[role_id] = AgentTokenSummary(
                    instance_id="",
                    role_id=role_id,
                    input_tokens=existing.input_tokens + input_tokens,
                    cached_input_tokens=(
                        existing.cached_input_tokens + cached_input_tokens
                    ),
                    output_tokens=existing.output_tokens + output_tokens,
                    reasoning_output_tokens=(
                        existing.reasoning_output_tokens + reasoning_output_tokens
                    ),
                    total_tokens=existing.total_tokens + input_tokens + output_tokens,
                    requests=existing.requests + requests,
                    tool_calls=existing.tool_calls + tool_calls,
                )
            else:
                by_role[role_id] = AgentTokenSummary(
                    instance_id="",
                    role_id=role_id,
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input_tokens,
                    output_tokens=output_tokens,
                    reasoning_output_tokens=reasoning_output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    requests=requests,
                    tool_calls=tool_calls,
                )

        roles = list(by_role.values())
        total_input = sum(role.input_tokens for role in roles)
        total_output = sum(role.output_tokens for role in roles)
        total_cached_input = sum(role.cached_input_tokens for role in roles)
        total_reasoning_output = sum(role.reasoning_output_tokens for role in roles)
        return SessionTokenUsage(
            session_id=session_id,
            total_input_tokens=total_input,
            total_cached_input_tokens=total_cached_input,
            total_output_tokens=total_output,
            total_reasoning_output_tokens=total_reasoning_output,
            total_tokens=total_input + total_output,
            total_requests=sum(role.requests for role in roles),
            total_tool_calls=sum(role.tool_calls for role in roles),
            by_role=by_role,
        )

    def delete_by_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM token_usage WHERE session_id=?", (session_id,)
            )
            self._conn.commit()

    def _latest_clear_cutoff(self, session_id: str) -> str | None:
        if self._session_history_marker_repo is None:
            return None
        latest_clear = self._session_history_marker_repo.get_latest(
            session_id,
            marker_type=SessionHistoryMarkerType.CLEAR,
        )
        if latest_clear is None:
            return None
        return latest_clear.created_at.isoformat()

    def _sanitize_numeric_columns(self) -> None:
        assignments = ", ".join(
            f"{column}=COALESCE({column}, 0)" for column in self._NUMERIC_COLUMNS
        )
        conditions = " OR ".join(
            f"{column} IS NULL" for column in self._NUMERIC_COLUMNS
        )
        self._conn.execute(f"UPDATE token_usage SET {assignments} WHERE {conditions}")

    def _row_int(self, row: sqlite3.Row, field_name: str) -> int:
        try:
            value = row[field_name]
        except IndexError:
            return 0
        return self._coerce_non_negative_int(value)

    def _coerce_non_negative_int(self, value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 0
            try:
                return max(0, int(stripped))
            except ValueError:
                try:
                    return max(0, int(float(stripped)))
                except ValueError:
                    return 0
        return 0
