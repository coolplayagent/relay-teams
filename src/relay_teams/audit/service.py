# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

from relay_teams.audit.models import (
    AuditEventCreate,
    AuditEventFilter,
    AuditEventPage,
    AuditEventRecord,
)
from relay_teams.audit.repository import AuditEventRepository
from relay_teams.logger import get_logger, log_event
from relay_teams.trace import get_trace_context, trace_span

LOGGER = get_logger(__name__)


class AuditService:
    def __init__(self, repository: AuditEventRepository) -> None:
        self._repository = repository

    def record_event(self, event: AuditEventCreate) -> AuditEventRecord:
        with trace_span(
            LOGGER,
            component="security.audit",
            operation=event.event_type.value,
            attributes={
                "audit_event_id": event.audit_event_id,
                "audit_event_type": event.event_type.value,
                "target": event.target,
            },
            trace_id=event.trace_id,
            run_id=event.run_id,
            session_id=event.session_id,
            task_id=event.task_id,
            instance_id=event.instance_id,
            role_id=event.role_id,
            tool_call_id=event.tool_call_id,
        ):
            enriched = _event_with_current_span(event)
            record = self._repository.append(enriched)
        _log_recorded_event(record)
        return record

    async def record_event_async(self, event: AuditEventCreate) -> AuditEventRecord:
        with trace_span(
            LOGGER,
            component="security.audit",
            operation=event.event_type.value,
            attributes={
                "audit_event_id": event.audit_event_id,
                "audit_event_type": event.event_type.value,
                "target": event.target,
            },
            trace_id=event.trace_id,
            run_id=event.run_id,
            session_id=event.session_id,
            task_id=event.task_id,
            instance_id=event.instance_id,
            role_id=event.role_id,
            tool_call_id=event.tool_call_id,
        ):
            enriched = _event_with_current_span(event)
            record = await self._repository.append_async(enriched)
        _log_recorded_event(record)
        return record

    def list_events(self, query: AuditEventFilter) -> AuditEventPage:
        return self._repository.list_events(query)

    async def list_events_async(self, query: AuditEventFilter) -> AuditEventPage:
        return await self._repository.list_events_async(query)


def _event_with_current_span(event: AuditEventCreate) -> AuditEventCreate:
    context = get_trace_context()
    return event.model_copy(
        update={
            "span_id": event.span_id or context.span_id,
            "parent_span_id": event.parent_span_id or context.parent_span_id,
        }
    )


def _log_recorded_event(record: AuditEventRecord) -> None:
    log_event(
        LOGGER,
        logging.INFO,
        event="security.audit.recorded",
        message="Security audit event recorded",
        payload={
            "audit_event_id": record.audit_event_id,
            "audit_event_type": record.event_type.value,
            "trace_id": record.trace_id,
            "run_id": record.run_id,
            "session_id": record.session_id,
            "task_id": record.task_id,
            "role_id": record.role_id,
            "target": record.target,
            "outcome": record.outcome,
        },
    )
