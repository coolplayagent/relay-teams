from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from relay_teams.audit import AuditEventFilter, AuditEventType, AuditService
from relay_teams.interfaces.server.deps import get_audit_service
from relay_teams.validation import OptionalIdentifierStr

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("")
async def list_audit_events(
    event_type: AuditEventType | None = None,
    trace_id: OptionalIdentifierStr = None,
    run_id: OptionalIdentifierStr = None,
    session_id: OptionalIdentifierStr = None,
    task_id: OptionalIdentifierStr = None,
    role_id: OptionalIdentifierStr = None,
    after_id: int = Query(default=0, ge=0),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    service: AuditService = Depends(get_audit_service),
) -> dict[str, object]:
    page = await service.list_events_async(
        AuditEventFilter(
            event_type=event_type,
            trace_id=trace_id,
            run_id=run_id,
            session_id=session_id,
            task_id=task_id,
            role_id=role_id,
            after_id=after_id,
            since=since,
            until=until,
            limit=limit,
        )
    )
    return page.model_dump(mode="json")
