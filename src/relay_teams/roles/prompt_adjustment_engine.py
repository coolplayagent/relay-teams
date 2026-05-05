# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.self_assessment_service import PromptAdjustmentRecommendation
from relay_teams.validation import RequiredIdentifierStr


class PromptAdjustmentStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"


class PromptAdjustmentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: RequiredIdentifierStr
    role_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    version: int = Field(default=1, ge=1)
    previous_prompt: str
    proposed_prompt: str
    recommendations: tuple[PromptAdjustmentRecommendation, ...] = ()
    status: PromptAdjustmentStatus = PromptAdjustmentStatus.PROPOSED
    trigger_source: str = ""
    triggered_by: str = ""
    proposed_at: datetime
    reviewed_at: datetime | None = None
    reviewed_by: str = ""
    rejection_reason: str = ""
    applied_at: datetime | None = None
    rolled_back_at: datetime | None = None
    rollback_reason: str = ""


def _new_decision_id() -> str:
    return f"pad-{uuid.uuid4().hex[:24]}"


class PromptAdjustmentRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_adjustments (
                    decision_id TEXT NOT NULL PRIMARY KEY,
                    role_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    previous_prompt TEXT NOT NULL,
                    proposed_prompt TEXT NOT NULL,
                    recommendations_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'proposed',
                    trigger_source TEXT NOT NULL DEFAULT '',
                    triggered_by TEXT NOT NULL DEFAULT '',
                    proposed_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    reviewed_by TEXT NOT NULL DEFAULT '',
                    rejection_reason TEXT NOT NULL DEFAULT '',
                    applied_at TEXT,
                    rolled_back_at TEXT,
                    rollback_reason TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pa_role_ws "
                "ON prompt_adjustments(role_id, workspace_id, status)"
            )

        self._run_write(operation_name="init_tables", operation=operation)

    def save_decision(
        self, decision: PromptAdjustmentDecision
    ) -> PromptAdjustmentDecision:
        now = decision.proposed_at.isoformat()
        rec_json = json.dumps(
            [r.model_dump() for r in decision.recommendations],
            ensure_ascii=False,
        )

        def operation() -> None:
            self._conn.execute(
                """
                INSERT INTO prompt_adjustments(
                    decision_id, role_id, workspace_id, version,
                    previous_prompt, proposed_prompt, recommendations_json,
                    status, trigger_source, triggered_by, proposed_at,
                    reviewed_at, reviewed_by, rejection_reason,
                    applied_at, rolled_back_at, rollback_reason
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id)
                DO UPDATE SET
                    role_id=excluded.role_id,
                    workspace_id=excluded.workspace_id,
                    version=excluded.version,
                    previous_prompt=excluded.previous_prompt,
                    proposed_prompt=excluded.proposed_prompt,
                    recommendations_json=excluded.recommendations_json,
                    status=excluded.status,
                    trigger_source=excluded.trigger_source,
                    triggered_by=excluded.triggered_by,
                    reviewed_at=excluded.reviewed_at,
                    reviewed_by=excluded.reviewed_by,
                    rejection_reason=excluded.rejection_reason,
                    applied_at=excluded.applied_at,
                    rolled_back_at=excluded.rolled_back_at,
                    rollback_reason=excluded.rollback_reason
                """,
                (
                    decision.decision_id,
                    decision.role_id,
                    decision.workspace_id,
                    decision.version,
                    decision.previous_prompt,
                    decision.proposed_prompt,
                    rec_json,
                    decision.status.value,
                    decision.trigger_source,
                    decision.triggered_by,
                    now,
                    decision.reviewed_at.isoformat() if decision.reviewed_at else None,
                    decision.reviewed_by,
                    decision.rejection_reason,
                    decision.applied_at.isoformat() if decision.applied_at else None,
                    decision.rolled_back_at.isoformat()
                    if decision.rolled_back_at
                    else None,
                    decision.rollback_reason,
                ),
            )

        self._run_write(operation_name="save_decision", operation=operation)
        _decision = self.get_decision(decision.decision_id)
        if _decision is None:
            raise ValueError(
                f"Failed to retrieve decision after save: {decision.decision_id}"
            )
        return _decision

    def get_decision(self, decision_id: str) -> PromptAdjustmentDecision | None:
        row = self._run_read(
            lambda: self._conn.execute(
                "SELECT * FROM prompt_adjustments WHERE decision_id=?",
                (decision_id,),
            ).fetchone()
        )
        if row is None:
            return None
        return _row_to_decision(row)

    def list_decisions(
        self,
        role_id: str,
        workspace_id: str,
        *,
        status: PromptAdjustmentStatus | None = None,
        limit: int = 50,
    ) -> tuple[PromptAdjustmentDecision, ...]:
        if status is not None:
            rows = self._run_read(
                lambda: self._conn.execute(
                    "SELECT * FROM prompt_adjustments WHERE role_id=? AND workspace_id=? AND status=? "
                    "ORDER BY proposed_at DESC LIMIT ?",
                    (role_id, workspace_id, status.value, limit),
                ).fetchall()
            )
        else:
            rows = self._run_read(
                lambda: self._conn.execute(
                    "SELECT * FROM prompt_adjustments WHERE role_id=? AND workspace_id=? "
                    "ORDER BY proposed_at DESC LIMIT ?",
                    (role_id, workspace_id, limit),
                ).fetchall()
            )
        return tuple(_row_to_decision(row) for row in rows)

    def get_latest_applied(
        self, role_id: str, workspace_id: str
    ) -> PromptAdjustmentDecision | None:
        row = self._run_read(
            lambda: self._conn.execute(
                "SELECT * FROM prompt_adjustments WHERE role_id=? AND workspace_id=? AND status=? "
                "ORDER BY applied_at DESC LIMIT 1",
                (role_id, workspace_id, PromptAdjustmentStatus.APPLIED.value),
            ).fetchone()
        )
        if row is None:
            return None
        return _row_to_decision(row)

    def update_status(
        self,
        decision_id: str,
        status: PromptAdjustmentStatus,
        reviewed_by: str,
        rejection_reason: str,
    ) -> PromptAdjustmentDecision:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._run_write(
            operation_name="update_status",
            operation=lambda: self._conn.execute(
                "UPDATE prompt_adjustments SET status=?, reviewed_at=?, reviewed_by=?, rejection_reason=? "
                "WHERE decision_id=?",
                (status.value, now, reviewed_by, rejection_reason, decision_id),
            ),
        )
        _decision = self.get_decision(decision_id)
        if _decision is None:
            raise ValueError(f"Failed to retrieve decision after update: {decision_id}")
        return _decision

    def _mark_applied(self, decision_id: str) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._run_write(
            operation_name="mark_applied",
            operation=lambda: self._conn.execute(
                "UPDATE prompt_adjustments SET status=?, applied_at=? WHERE decision_id=?",
                (PromptAdjustmentStatus.APPLIED.value, now, decision_id),
            ),
        )

    def mark_applied(self, decision_id: str) -> None:
        self._mark_applied(decision_id)

    def mark_rolled_back(self, decision_id: str, reason: str) -> None:
        self._mark_rolled_back(decision_id, reason)

    def _mark_rolled_back(self, decision_id: str, reason: str) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._run_write(
            operation_name="mark_rolled_back",
            operation=lambda: self._conn.execute(
                "UPDATE prompt_adjustments SET status=?, rolled_back_at=?, rollback_reason=? "
                "WHERE decision_id=?",
                (PromptAdjustmentStatus.ROLLED_BACK.value, now, reason, decision_id),
            ),
        )


def _row_to_decision(row: sqlite3.Row) -> PromptAdjustmentDecision:
    recommendations: tuple[PromptAdjustmentRecommendation, ...] = ()
    try:
        recs_raw = json.loads(str(row["recommendations_json"]))
        if isinstance(recs_raw, list):
            recommendations = tuple(
                PromptAdjustmentRecommendation(**r) for r in recs_raw
            )
    except (json.JSONDecodeError, TypeError, ValueError):
        # Intentional fallback: invalid JSON or malformed recommendation entries
        # degrade gracefully to empty recommendations rather than crashing.
        pass

    return PromptAdjustmentDecision(
        decision_id=str(row["decision_id"]),
        role_id=str(row["role_id"]),
        workspace_id=str(row["workspace_id"]),
        version=int(row["version"]),
        previous_prompt=str(row["previous_prompt"]),
        proposed_prompt=str(row["proposed_prompt"]),
        recommendations=recommendations,
        status=PromptAdjustmentStatus(str(row["status"])),
        trigger_source=str(row["trigger_source"]),
        triggered_by=str(row["triggered_by"]),
        proposed_at=datetime.fromisoformat(str(row["proposed_at"])),
        reviewed_at=(
            datetime.fromisoformat(str(row["reviewed_at"]))
            if row["reviewed_at"]
            else None
        ),
        reviewed_by=str(row["reviewed_by"]),
        rejection_reason=str(row["rejection_reason"]),
        applied_at=(
            datetime.fromisoformat(str(row["applied_at"]))
            if row["applied_at"]
            else None
        ),
        rolled_back_at=(
            datetime.fromisoformat(str(row["rolled_back_at"]))
            if row["rolled_back_at"]
            else None
        ),
        rollback_reason=str(row["rollback_reason"]),
    )


class SystemPromptAdjustmentEngine:
    def __init__(
        self,
        *,
        repository: PromptAdjustmentRepository,
        role_registry: RoleRegistry,
    ) -> None:
        self._repository = repository
        self._role_registry = role_registry

    def propose_adjustment(
        self,
        *,
        role_id: str,
        workspace_id: str,
        current_prompt: str,
        recommendations: tuple[PromptAdjustmentRecommendation, ...],
        trigger_source: str,
        triggered_by: str,
    ) -> PromptAdjustmentDecision:
        latest = self._repository.get_latest_applied(
            role_id=role_id, workspace_id=workspace_id
        )
        version = 1 if latest is None else latest.version + 1
        proposed_prompt = _merge_sections(current_prompt, recommendations)

        decision = PromptAdjustmentDecision(
            decision_id=_new_decision_id(),
            role_id=role_id,
            workspace_id=workspace_id,
            version=version,
            previous_prompt=current_prompt,
            proposed_prompt=proposed_prompt,
            recommendations=recommendations,
            status=PromptAdjustmentStatus.PROPOSED,
            trigger_source=trigger_source,
            triggered_by=triggered_by,
            proposed_at=datetime.now(tz=timezone.utc),
        )
        return self._repository.save_decision(decision)

    def approve_adjustment(
        self,
        *,
        decision_id: str,
        reviewed_by: str,
    ) -> PromptAdjustmentDecision:
        decision = self._repository.get_decision(decision_id)
        if decision is None:
            raise ValueError(f"Unknown decision: {decision_id}")
        if decision.status != PromptAdjustmentStatus.PROPOSED:
            raise ValueError(
                f"Decision {decision_id} is not in PROPOSED status: {decision.status.value}"
            )
        return self._repository.update_status(
            decision_id=decision_id,
            status=PromptAdjustmentStatus.APPROVED,
            reviewed_by=reviewed_by,
            rejection_reason="",
        )

    def reject_adjustment(
        self,
        *,
        decision_id: str,
        reviewed_by: str,
        reason: str,
    ) -> PromptAdjustmentDecision:
        decision = self._repository.get_decision(decision_id)
        if decision is None:
            raise ValueError(f"Unknown decision: {decision_id}")
        if decision.status != PromptAdjustmentStatus.PROPOSED:
            raise ValueError(
                f"Decision {decision_id} is not in PROPOSED status: {decision.status.value}"
            )
        return self._repository.update_status(
            decision_id=decision_id,
            status=PromptAdjustmentStatus.REJECTED,
            reviewed_by=reviewed_by,
            rejection_reason=reason,
        )

    def apply_adjustment(self, *, decision_id: str) -> RoleDefinition:
        decision = self._repository.get_decision(decision_id)
        if decision is None:
            raise ValueError(f"Unknown decision: {decision_id}")
        if decision.status != PromptAdjustmentStatus.APPROVED:
            raise ValueError(
                f"Decision {decision_id} is not in APPROVED status: {decision.status.value}"
            )

        role_def = self._role_registry.get(decision.role_id)
        updated_role = role_def.model_copy(
            update={"system_prompt": decision.proposed_prompt}
        )
        self._role_registry.register(updated_role)
        self._repository.mark_applied(decision_id)
        return updated_role

    def rollback_adjustment(self, *, decision_id: str, reason: str) -> RoleDefinition:
        decision = self._repository.get_decision(decision_id)
        if decision is None:
            raise ValueError(f"Unknown decision: {decision_id}")
        if decision.status != PromptAdjustmentStatus.APPLIED:
            raise ValueError(
                f"Decision {decision_id} is not in APPLIED status: {decision.status.value}"
            )

        role_def = self._role_registry.get(decision.role_id)
        updated_role = role_def.model_copy(
            update={"system_prompt": decision.previous_prompt}
        )
        self._role_registry.register(updated_role)
        self._repository.mark_rolled_back(decision_id, reason)
        return updated_role


def _merge_sections(
    current_prompt: str,
    recommendations: tuple[PromptAdjustmentRecommendation, ...],
) -> str:
    sections: dict[str, str] = {}
    remaining = current_prompt
    current_header: str | None = None
    current_body_lines: list[str] = []

    while remaining:
        next_header_idx = remaining.find("## ")
        if next_header_idx == -1:
            if current_header is not None:
                current_body_lines.append(remaining)
            else:
                current_header = "__preamble__"
                current_body_lines.append(remaining)
            break

        if next_header_idx > 0:
            prefix = remaining[:next_header_idx]
            if current_header is not None:
                current_body_lines.append(prefix)
            else:
                current_header = "__preamble__"
                current_body_lines.append(prefix)

        end_of_line = remaining.find("\n", next_header_idx)
        if end_of_line == -1:
            end_of_line = len(remaining)

        if current_header is not None:
            sections[current_header] = "".join(current_body_lines).strip()

        current_header = remaining[next_header_idx + 3 : end_of_line].strip()
        body_start = end_of_line + 1 if end_of_line < len(remaining) else len(remaining)
        remaining = remaining[body_start:]
        current_body_lines = []
        next_header = remaining.find("## ")
        if next_header == -1:
            current_body_lines.append(remaining)
            sections[current_header] = "".join(current_body_lines).strip()
            remaining = ""
        elif next_header > 0:
            current_body_lines.append(remaining[:next_header])
            remaining = remaining[next_header:]
        else:
            pass  # next_header == 0: header at current position, continue parsing

    if current_header is not None and current_header not in sections:
        sections[current_header] = "".join(current_body_lines).strip()

    for rec in recommendations:
        target = rec.target_section.strip()
        if target in sections:
            sections[target] = rec.recommended_text.strip()
        else:
            sections[target] = rec.recommended_text.strip()

    result_parts: list[str] = []
    if "__preamble__" in sections and sections["__preamble__"]:
        result_parts.append(sections.pop("__preamble__"))

    for header, body in sections.items():
        result_parts.append(f"## {header}\n{body}")

    return "\n\n".join(result_parts).strip()
