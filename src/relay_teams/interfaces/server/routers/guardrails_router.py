# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Query

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailAction,
    RuntimeGuardrailLayer,
)

router = APIRouter(prefix="/guardrails", tags=["Guardrails"])


class GuardrailAuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, JsonValue]] = Field(default_factory=list)
    total: int = 0
    next_offset: int | None = None


@router.get("/audit", response_model=GuardrailAuditResponse)
async def get_guardrail_audit(
    _run_id: str | None = Query(default=None, alias="run_id"),
    _task_id: str | None = Query(default=None, alias="task_id"),
    _role_id: str | None = Query(default=None, alias="role_id"),
    _layer: RuntimeGuardrailLayer | None = Query(default=None, alias="layer"),
    _action: RuntimeGuardrailAction | None = Query(default=None, alias="action"),
    _triggered_only: bool = Query(default=False, alias="triggered_only"),
    _since: str | None = Query(default=None, alias="since"),
    _until: str | None = Query(default=None, alias="until"),
    _limit: int = Query(default=100, ge=1, le=500, alias="limit"),
    _offset: int = Query(default=0, ge=0, alias="offset"),
) -> GuardrailAuditResponse:
    """List guardrail audit records with optional filters."""
    return GuardrailAuditResponse(
        items=[],
        total=0,
        next_offset=None,
    )
