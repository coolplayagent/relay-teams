from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_teams.state.db import open_sqlite


class TokenUsageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    instance_id: str
    role_id: str
    input_tokens: int
    output_tokens: int
    requests: int
    tool_calls: int
    recorded_at: datetime


class AgentTokenSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str
    role_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    requests: int
    tool_calls: int


class RunTokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_requests: int
    total_tool_calls: int
    by_agent: list[AgentTokenSummary]


class SessionTokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_requests: int
    total_tool_calls: int
    by_role: dict[str, AgentTokenSummary]


class TokenUsageRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS token_usage (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id    TEXT NOT NULL,
                run_id        TEXT NOT NULL,
                instance_id   TEXT NOT NULL,
                role_id       TEXT NOT NULL,
                input_tokens  INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                requests      INTEGER DEFAULT 0,
                tool_calls    INTEGER DEFAULT 0,
                recorded_at   TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_token_usage_run ON token_usage(run_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_token_usage_session ON token_usage(session_id)"
        )
        self._conn.commit()

    def record(
        self,
        *,
        session_id: str,
        run_id: str,
        instance_id: str,
        role_id: str,
        input_tokens: int,
        output_tokens: int,
        requests: int,
        tool_calls: int,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO token_usage
              (session_id, run_id, instance_id, role_id,
               input_tokens, output_tokens, requests, tool_calls, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                run_id,
                instance_id,
                role_id,
                input_tokens,
                output_tokens,
                requests,
                tool_calls,
                now,
            ),
        )
        self._conn.commit()

    def get_by_run(self, run_id: str) -> RunTokenUsage:
        rows = self._conn.execute(
            "SELECT * FROM token_usage WHERE run_id=? ORDER BY id ASC",
            (run_id,),
        ).fetchall()

        # Aggregate per instance (same instance may have multiple rows if it
        # ran through multiple agent.iter() cycles due to injection restarts)
        by_instance: dict[str, AgentTokenSummary] = {}
        for row in rows:
            iid = str(row["instance_id"])
            if iid in by_instance:
                existing = by_instance[iid]
                by_instance[iid] = AgentTokenSummary(
                    instance_id=iid,
                    role_id=existing.role_id,
                    input_tokens=existing.input_tokens + int(row["input_tokens"]),
                    output_tokens=existing.output_tokens + int(row["output_tokens"]),
                    total_tokens=existing.total_tokens
                    + int(row["input_tokens"])
                    + int(row["output_tokens"]),
                    requests=existing.requests + int(row["requests"]),
                    tool_calls=existing.tool_calls + int(row["tool_calls"]),
                )
            else:
                by_instance[iid] = AgentTokenSummary(
                    instance_id=iid,
                    role_id=str(row["role_id"]),
                    input_tokens=int(row["input_tokens"]),
                    output_tokens=int(row["output_tokens"]),
                    total_tokens=int(row["input_tokens"]) + int(row["output_tokens"]),
                    requests=int(row["requests"]),
                    tool_calls=int(row["tool_calls"]),
                )

        agents = list(by_instance.values())
        total_input = sum(a.input_tokens for a in agents)
        total_output = sum(a.output_tokens for a in agents)
        return RunTokenUsage(
            run_id=run_id,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_tokens=total_input + total_output,
            total_requests=sum(a.requests for a in agents),
            total_tool_calls=sum(a.tool_calls for a in agents),
            by_agent=agents,
        )

    def get_by_session(self, session_id: str) -> SessionTokenUsage:
        rows = self._conn.execute(
            "SELECT * FROM token_usage WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        ).fetchall()

        # Aggregate per role_id across all runs in the session
        by_role: dict[str, AgentTokenSummary] = {}
        for row in rows:
            rid = str(row["role_id"])
            if rid in by_role:
                existing = by_role[rid]
                by_role[rid] = AgentTokenSummary(
                    instance_id="",  # multiple instances collapsed by role
                    role_id=rid,
                    input_tokens=existing.input_tokens + int(row["input_tokens"]),
                    output_tokens=existing.output_tokens + int(row["output_tokens"]),
                    total_tokens=existing.total_tokens
                    + int(row["input_tokens"])
                    + int(row["output_tokens"]),
                    requests=existing.requests + int(row["requests"]),
                    tool_calls=existing.tool_calls + int(row["tool_calls"]),
                )
            else:
                by_role[rid] = AgentTokenSummary(
                    instance_id="",
                    role_id=rid,
                    input_tokens=int(row["input_tokens"]),
                    output_tokens=int(row["output_tokens"]),
                    total_tokens=int(row["input_tokens"]) + int(row["output_tokens"]),
                    requests=int(row["requests"]),
                    tool_calls=int(row["tool_calls"]),
                )

        roles = list(by_role.values())
        total_input = sum(r.input_tokens for r in roles)
        total_output = sum(r.output_tokens for r in roles)
        return SessionTokenUsage(
            session_id=session_id,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_tokens=total_input + total_output,
            total_requests=sum(r.requests for r in roles),
            total_tool_calls=sum(r.tool_calls for r in roles),
            by_role=by_role,
        )

    def delete_by_session(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM token_usage WHERE session_id=?", (session_id,))
        self._conn.commit()
