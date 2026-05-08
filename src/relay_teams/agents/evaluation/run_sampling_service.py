# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.logger import get_logger, log_event
from relay_teams.memory.models import MemoryEntryKind, MemoryQuery
from relay_teams.memory.service import MemoryBankService
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_state_models import RunStateRecord, RunStateStatus
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr

_LOGGER = get_logger(__name__)


class SamplingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_size: int = Field(default=50, ge=1, le=10_000)
    include_only_failed: bool = True
    include_only_completed: bool = False
    lookback_days: int = Field(default=30, ge=1, le=365)
    workspace_ids: tuple[str, ...] = ()
    role_ids: tuple[str, ...] = ()
    max_events_per_run: int = Field(default=500, ge=10, le=10_000)
    seed: int | None = None


class SampledRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: RequiredIdentifierStr = Field(min_length=1)
    session_id: RequiredIdentifierStr = Field(min_length=1)
    workspace_id: RequiredIdentifierStr = Field(min_length=1)
    role_id: OptionalIdentifierStr = None
    status: Literal["completed", "failed"]
    completed_at: datetime
    event_count: int = Field(ge=0)
    has_verification_report: bool = False


class RunSamplingService:
    def __init__(
        self,
        *,
        event_log: EventLog,
        memory_bank_service: MemoryBankService,
        config: SamplingConfig | None = None,
    ) -> None:
        self._event_log = event_log
        self._memory_bank_service = memory_bank_service
        self._config = config or SamplingConfig()

    async def sample_runs(
        self,
        *,
        config: SamplingConfig | None = None,
    ) -> tuple[SampledRun, ...]:
        cfg = config or self._config

        lookback_cutoff = datetime.now(tz=timezone.utc) - timedelta(
            days=cfg.lookback_days,
        )

        all_states = await self._event_log.list_run_states_async()

        # Filter by lookback window and status
        filtered: list[RunStateRecord] = []
        for state in all_states:
            if state.updated_at < lookback_cutoff:
                continue
            if cfg.include_only_failed and state.status != RunStateStatus.FAILED:
                continue
            if cfg.include_only_completed and state.status not in (
                RunStateStatus.COMPLETED,
                RunStateStatus.FAILED,
            ):
                continue
            filtered.append(state)

        # Exclude already-classified runs
        classified_run_ids: set[str] = set()
        # Query per unique workspace_id in filtered runs
        seen_workspace_ids: set[str] = set()
        for state in filtered:
            wid = state.session_id  # use session_id as workspace proxy
            if wid not in seen_workspace_ids:
                seen_workspace_ids.add(wid)
                ws_query = MemoryQuery(
                    workspace_id=wid,
                    kind=MemoryEntryKind.FAILURE_MODE,
                    limit=100,
                )
                try:
                    result = await self._memory_bank_service.list_entries_async(
                        ws_query
                    )
                    if result.total_count > 0:
                        # Session has classified runs; exclude all runs from it
                        classified_run_ids.update(
                            s.run_id for s in filtered if s.session_id == wid
                        )
                except (ValueError, OSError):
                    # Non-fatal: skip classified-run check for this session
                    log_event(
                        _LOGGER,
                        logging.WARNING,
                        event="memory_query_failed",
                        message=f"Failed to query classified runs for session {wid}",
                    )

        # Filter out classified runs
        available = [
            state for state in filtered if state.run_id not in classified_run_ids
        ]

        # Apply workspace and role filters
        if cfg.workspace_ids:
            available = [s for s in available if s.session_id in cfg.workspace_ids]
        # Role filtering is approximate since RunStateRecord doesn't have role_id directly
        # We skip role filtering at this stage; it can be refined post-sampling

        if len(available) < cfg.sample_size:
            log_event(
                _LOGGER,
                logging.WARNING,
                event="run_sampling.insufficient_pool",
                message=(
                    f"Only {len(available)} runs available, "
                    f"fewer than requested {cfg.sample_size}"
                ),
                payload={
                    "available": len(available),
                    "requested": cfg.sample_size,
                },
            )
            selected = available
        else:
            import random

            rng = random.Random(cfg.seed)
            selected = rng.sample(available, cfg.sample_size)

        # Build SampledRun models
        results: list[SampledRun] = []
        for state in selected:
            status: Literal["completed", "failed"]
            if state.status == RunStateStatus.FAILED:
                status = "failed"
            else:
                status = "completed"

            results.append(
                SampledRun(
                    run_id=state.run_id,
                    session_id=state.session_id,
                    workspace_id=state.session_id,  # session_id as workspace proxy
                    role_id=None,  # RunStateRecord doesn't carry role_id
                    status=status,
                    completed_at=state.updated_at,
                    event_count=state.last_event_id,
                    has_verification_report=False,
                ),
            )

        return tuple(results)
