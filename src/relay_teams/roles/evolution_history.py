# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.memory.models import (
    CreateMemoryEntryRequest,
    MemoryContent,
    MemoryEntry,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryQuery,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.service import MemoryBankService
from relay_teams.roles.maturity_scoring import MaturityLevel
from relay_teams.roles.prompt_adjustment_engine import (
    PromptAdjustmentRepository,
    PromptAdjustmentStatus,
)
from relay_teams.validation import RequiredIdentifierStr


class RoleEvolutionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: RequiredIdentifierStr
    role_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    event_type: str
    timestamp: datetime
    summary: str = Field(min_length=1)
    trigger_source: str = ""
    decision_id: str | None = None
    maturity_level_before: MaturityLevel | None = None
    maturity_level_after: MaturityLevel | None = None
    metadata: dict[str, str] = Field(default_factory=dict, max_length=10)


class RoleEvolutionTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    events: tuple[RoleEvolutionEvent, ...] = ()
    current_prompt_version: int = Field(default=1, ge=1)
    current_maturity_level: MaturityLevel | None = None
    lifetime_adjustment_count: int = Field(default=0, ge=0)


_EVOLUTION_EVENT_KIND = MemoryEntryKind.INSIGHT
_EVOLUTION_EVENT_SOURCE = MemorySourceKind.REFLECTION
_EVOLUTION_TAG = "role_evolution"


class RoleEvolutionHistoryService:
    def __init__(
        self,
        *,
        adjustment_repository: PromptAdjustmentRepository,
        memory_bank_service: MemoryBankService,
    ) -> None:
        self._adjustment_repository = adjustment_repository
        self._memory_bank_service = memory_bank_service

    async def record_event_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
        event_type: str,
        summary: str,
        trigger_source: str = "",
        decision_id: str | None = None,
        maturity_level_before: MaturityLevel | None = None,
        maturity_level_after: MaturityLevel | None = None,
    ) -> RoleEvolutionEvent:
        event = RoleEvolutionEvent(
            event_id=f"rev-{uuid.uuid4().hex[:24]}",
            role_id=role_id,
            workspace_id=workspace_id,
            event_type=event_type,
            timestamp=datetime.now(tz=timezone.utc),
            summary=summary,
            trigger_source=trigger_source,
            decision_id=decision_id,
            maturity_level_before=maturity_level_before,
            maturity_level_after=maturity_level_after,
            metadata={
                k: v
                for k, v in {
                    "event_type": event_type,
                    "trigger_source": trigger_source,
                }.items()
                if v
            },
        )

        await self._memory_bank_service.create_entry_async(
            CreateMemoryEntryRequest(
                tier=MemoryTier.MEDIUM_TERM,
                scope=MemoryScope.ROLE,
                workspace_id=workspace_id,
                role_id=role_id,
                kind=_EVOLUTION_EVENT_KIND,
                content=MemoryContent(
                    title=f"Role Evolution: {event_type} (role {role_id})",
                    body=summary,
                    context=json.dumps(
                        {
                            "event_type": event_type,
                            "trigger_source": trigger_source,
                            "decision_id": decision_id,
                            "maturity_level_before": maturity_level_before.value
                            if maturity_level_before
                            else None,
                            "maturity_level_after": maturity_level_after.value
                            if maturity_level_after
                            else None,
                        }
                    ),
                ),
                tags=(_EVOLUTION_TAG, event_type),
                source=_EVOLUTION_EVENT_SOURCE,
                source_ref=event.event_id,
            )
        )
        return event

    async def get_timeline_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
        limit: int = 50,
    ) -> RoleEvolutionTimeline:
        result = await self._memory_bank_service.list_entries_async(
            MemoryQuery(
                workspace_id=workspace_id,
                role_id=role_id,
                scope=MemoryScope.ROLE,
                kind=_EVOLUTION_EVENT_KIND,
                status=MemoryEntryStatus.ACTIVE,
                limit=limit,
                offset=0,
            )
        )

        events: list[RoleEvolutionEvent] = []
        for summary in result.items:
            entry = await self._memory_bank_service.get_entry_async(summary.id)
            if entry is not None:
                event = _entry_to_evolution_event(entry)
                if event is not None:
                    events.append(event)

        events.sort(key=lambda e: e.timestamp, reverse=True)
        return _build_timeline(
            role_id=role_id,
            workspace_id=workspace_id,
            events=tuple(events),
            adjustment_repository=self._adjustment_repository,
        )

    async def get_current_state_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
    ) -> RoleEvolutionTimeline:
        return await self.get_timeline_async(
            role_id=role_id,
            workspace_id=workspace_id,
            limit=100,
        )


def _entry_to_evolution_event(entry: MemoryEntry) -> RoleEvolutionEvent | None:
    try:
        ctx = json.loads(entry.content.context) if entry.content.context else {}
    except json.JSONDecodeError:
        ctx = {}

    maturity_before: MaturityLevel | None = None
    maturity_after: MaturityLevel | None = None
    if "maturity_level_before" in ctx and ctx["maturity_level_before"]:
        try:
            maturity_before = MaturityLevel(ctx["maturity_level_before"])
        except ValueError:
            maturity_before = None  # invalid maturity string; keep default
    if "maturity_level_after" in ctx and ctx["maturity_level_after"]:
        try:
            maturity_after = MaturityLevel(ctx["maturity_level_after"])
        except ValueError:
            maturity_after = None  # invalid maturity string; keep default

    return RoleEvolutionEvent(
        event_id=entry.source_ref or entry.id,
        role_id=entry.role_id or "",
        workspace_id=entry.workspace_id,
        event_type=ctx.get("event_type", "unknown"),
        timestamp=entry.created_at,
        summary=entry.content.body,
        trigger_source=ctx.get("trigger_source", ""),
        decision_id=ctx.get("decision_id"),
        maturity_level_before=maturity_before,
        maturity_level_after=maturity_after,
        metadata=dict(entry.metadata),
    )


def _build_timeline(
    *,
    role_id: str,
    workspace_id: str,
    events: tuple[RoleEvolutionEvent, ...],
    adjustment_repository: PromptAdjustmentRepository,
) -> RoleEvolutionTimeline:
    latest_applied = adjustment_repository.get_latest_applied(
        role_id=role_id, workspace_id=workspace_id
    )
    current_prompt_version = 1 if latest_applied is None else latest_applied.version

    all_applied = adjustment_repository.list_decisions(
        role_id=role_id,
        workspace_id=workspace_id,
        status=PromptAdjustmentStatus.APPLIED,
        limit=1000,
    )
    lifetime_adjustment_count = len(all_applied)

    current_maturity_level: MaturityLevel | None = None
    for event in events:
        if (
            event.event_type == "maturity_scored"
            and event.maturity_level_after is not None
        ):
            current_maturity_level = event.maturity_level_after
            break

    return RoleEvolutionTimeline(
        role_id=role_id,
        workspace_id=workspace_id,
        events=events,
        current_prompt_version=current_prompt_version,
        current_maturity_level=current_maturity_level,
        lifetime_adjustment_count=lifetime_adjustment_count,
    )
