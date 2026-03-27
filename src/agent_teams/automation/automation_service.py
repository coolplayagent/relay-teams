# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import JsonValue

from agent_teams.automation.automation_bound_session_queue_service import (
    AutomationBoundSessionQueueService,
)
from agent_teams.automation.automation_delivery_service import AutomationDeliveryService
from agent_teams.automation.automation_event_repository import (
    AutomationEventRepository,
    AutomationExecutionEventRecord,
)
from agent_teams.automation.automation_models import (
    AutomationDeliveryEvent,
    AutomationExecutionHandle,
    AutomationFeishuBinding,
    AutomationFeishuBindingCandidate,
    AutomationProjectCreateInput,
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationProjectUpdateInput,
    AutomationScheduleMode,
)
from agent_teams.automation.automation_repository import (
    AutomationProjectNameConflictError,
    AutomationProjectRepository,
)
from agent_teams.automation.feishu_binding_service import (
    AutomationFeishuBindingService,
)
from agent_teams.media import content_parts_from_text
from agent_teams.sessions import ProjectKind
from agent_teams.sessions.runs.run_manager import RunManager
from agent_teams.sessions.runs.run_models import IntentInput
from agent_teams.sessions.session_service import SessionService


class AutomationService:
    def __init__(
        self,
        *,
        repository: AutomationProjectRepository,
        event_repository: AutomationEventRepository,
        session_service: SessionService,
        run_service: RunManager,
        feishu_binding_service: AutomationFeishuBindingService | None = None,
        delivery_service: AutomationDeliveryService | None = None,
        bound_session_queue_service: AutomationBoundSessionQueueService | None = None,
    ) -> None:
        self._repository = repository
        self._event_repository = event_repository
        self._session_service = session_service
        self._run_service = run_service
        self._feishu_binding_service = feishu_binding_service
        self._delivery_service = delivery_service
        self._bound_session_queue_service = bound_session_queue_service

    def create_project(
        self,
        payload: AutomationProjectCreateInput,
    ) -> AutomationProjectRecord:
        timezone_name = _validate_timezone(payload.timezone)
        delivery_binding = self._resolve_delivery_binding(
            payload.delivery_binding,
            existing_binding=None,
        )
        delivery_events = self._resolve_delivery_events(
            binding=delivery_binding,
            requested_events=payload.delivery_events,
            existing_events=(),
        )
        now = datetime.now(tz=UTC)
        automation_project_id = f"aut_{uuid.uuid4().hex[:12]}"
        record = AutomationProjectRecord(
            automation_project_id=automation_project_id,
            name=payload.name,
            display_name=payload.display_name or payload.name,
            status=(
                AutomationProjectStatus.ENABLED
                if payload.enabled
                else AutomationProjectStatus.DISABLED
            ),
            workspace_id=payload.workspace_id,
            prompt=payload.prompt,
            schedule_mode=payload.schedule_mode,
            cron_expression=_normalize_optional_text(payload.cron_expression),
            run_at=payload.run_at,
            timezone=timezone_name,
            run_config=payload.run_config,
            delivery_binding=delivery_binding,
            delivery_events=delivery_events,
            trigger_id=f"schedule-{automation_project_id}",
            next_run_at=(
                _next_run_at(
                    schedule_mode=payload.schedule_mode,
                    cron_expression=payload.cron_expression,
                    run_at=payload.run_at,
                    timezone_name=timezone_name,
                    after=now,
                )
                if payload.enabled
                else None
            ),
            created_at=now,
            updated_at=now,
        )
        return self._repository.create(record)

    def list_projects(self) -> tuple[AutomationProjectRecord, ...]:
        return self._repository.list_all()

    def get_project(self, automation_project_id: str) -> AutomationProjectRecord:
        return self._repository.get(automation_project_id)

    def list_feishu_bindings(
        self,
    ) -> tuple[AutomationFeishuBindingCandidate, ...]:
        if self._feishu_binding_service is None:
            return ()
        return self._feishu_binding_service.list_candidates()

    def update_project(
        self,
        automation_project_id: str,
        payload: AutomationProjectUpdateInput,
    ) -> AutomationProjectRecord:
        existing = self._repository.get(automation_project_id)
        timezone_name = _validate_timezone(payload.timezone or existing.timezone)
        schedule_mode = payload.schedule_mode or existing.schedule_mode
        cron_expression = _resolve_optional_text(
            candidate=payload.cron_expression,
            fallback=existing.cron_expression,
        )
        run_at = payload.run_at if payload.run_at is not None else existing.run_at
        if payload.schedule_mode == AutomationScheduleMode.CRON:
            run_at = None
        if payload.schedule_mode == AutomationScheduleMode.ONE_SHOT:
            cron_expression = None
        delivery_binding = self._resolve_delivery_binding(
            payload.delivery_binding,
            existing_binding=existing.delivery_binding,
        )
        delivery_events = self._resolve_delivery_events(
            binding=delivery_binding,
            requested_events=payload.delivery_events,
            existing_events=existing.delivery_events,
        )
        probe = AutomationProjectCreateInput(
            name=payload.name or existing.name,
            display_name=payload.display_name or existing.display_name,
            workspace_id=payload.workspace_id or existing.workspace_id,
            prompt=payload.prompt or existing.prompt,
            schedule_mode=schedule_mode,
            cron_expression=cron_expression,
            run_at=run_at,
            timezone=timezone_name,
            run_config=payload.run_config or existing.run_config,
            delivery_binding=delivery_binding,
            delivery_events=delivery_events,
            enabled=(
                payload.enabled
                if payload.enabled is not None
                else existing.status == AutomationProjectStatus.ENABLED
            ),
        )
        now = datetime.now(tz=UTC)
        updated = existing.model_copy(
            update={
                "name": probe.name,
                "display_name": probe.display_name or probe.name,
                "status": (
                    AutomationProjectStatus.ENABLED
                    if probe.enabled
                    else AutomationProjectStatus.DISABLED
                ),
                "workspace_id": probe.workspace_id,
                "prompt": probe.prompt,
                "schedule_mode": probe.schedule_mode,
                "cron_expression": _normalize_optional_text(probe.cron_expression),
                "run_at": probe.run_at,
                "timezone": timezone_name,
                "run_config": probe.run_config,
                "delivery_binding": delivery_binding,
                "delivery_events": delivery_events,
                "next_run_at": (
                    _next_run_at(
                        schedule_mode=probe.schedule_mode,
                        cron_expression=probe.cron_expression,
                        run_at=probe.run_at,
                        timezone_name=timezone_name,
                        after=now,
                    )
                    if probe.enabled
                    else None
                ),
                "updated_at": now,
            }
        )
        return self._repository.update(updated)

    def set_project_status(
        self,
        automation_project_id: str,
        status: AutomationProjectStatus,
    ) -> AutomationProjectRecord:
        existing = self._repository.get(automation_project_id)
        now = datetime.now(tz=UTC)
        updated = existing.model_copy(
            update={
                "status": status,
                "next_run_at": (
                    _next_run_at(
                        schedule_mode=existing.schedule_mode,
                        cron_expression=existing.cron_expression,
                        run_at=existing.run_at,
                        timezone_name=existing.timezone,
                        after=now,
                    )
                    if status == AutomationProjectStatus.ENABLED
                    else None
                ),
                "updated_at": now,
            }
        )
        return self._repository.update(updated)

    def delete_project(self, automation_project_id: str) -> None:
        _ = self._repository.get(automation_project_id)
        if self._delivery_service is not None:
            self._delivery_service.delete_project_deliveries(automation_project_id)
        if self._bound_session_queue_service is not None:
            self._bound_session_queue_service.delete_project_queue(
                automation_project_id
            )
        self._repository.delete(automation_project_id)

    def run_now(self, automation_project_id: str) -> dict[str, JsonValue]:
        project = self._repository.get(automation_project_id)
        execution_handle = self._materialize_execution(project, reason="manual")
        return {
            "automation_project_id": automation_project_id,
            "session_id": execution_handle.session_id,
            "run_id": execution_handle.run_id,
            "queued": execution_handle.queued,
            "reused_bound_session": execution_handle.reused_bound_session,
        }

    def list_project_sessions(
        self,
        automation_project_id: str,
    ) -> tuple[dict[str, object], ...]:
        project = self._repository.get(automation_project_id)
        sessions = list(
            self._session_service.list_sessions_by_project(
                project_kind=ProjectKind.AUTOMATION,
                project_id=automation_project_id,
            )
        )
        last_session_id = str(project.last_session_id or "").strip()
        if not last_session_id:
            return tuple(sessions)
        if any(
            str(item.get("session_id", "")).strip() == last_session_id
            for item in sessions
        ):
            return tuple(sessions)
        for session in self._session_service.list_sessions():
            if session.session_id != last_session_id:
                continue
            sessions.append(session.model_dump(mode="json"))
            break
        sessions.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        return tuple(sessions)

    def process_due_projects(self, now: datetime | None = None) -> tuple[str, ...]:
        effective_now = now or datetime.now(tz=UTC)
        processed: list[str] = []
        for project in self._repository.list_due(effective_now):
            self._materialize_execution(project, reason="schedule", now=effective_now)
            processed.append(project.automation_project_id)
        return tuple(processed)

    def _materialize_execution(
        self,
        project: AutomationProjectRecord,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> AutomationExecutionHandle:
        effective_now = now or datetime.now(tz=UTC)
        execution_event = self._record_execution_event(project, reason=reason)
        next_status = project.status
        next_run_at = project.next_run_at
        try:
            bound_session_handle = self._materialize_bound_session_execution(
                project=project,
                reason=reason,
            )
            if bound_session_handle is not None:
                next_run_at = _next_run_at_after_fire(
                    project=project, fired_at=effective_now
                )
                if project.schedule_mode == AutomationScheduleMode.ONE_SHOT:
                    next_status = AutomationProjectStatus.DISABLED
                self._repository.update(
                    project.model_copy(
                        update={
                            "status": next_status,
                            "last_session_id": bound_session_handle.session_id,
                            "last_run_started_at": (
                                effective_now
                                if bound_session_handle.run_id is not None
                                else project.last_run_started_at
                            ),
                            "last_error": None,
                            "next_run_at": next_run_at,
                            "updated_at": effective_now,
                        }
                    )
                )
                return bound_session_handle.model_copy(
                    update={"reused_bound_session": True}
                )

            title = (
                f"{project.display_name} run "
                f"{effective_now.astimezone(UTC).strftime('%Y-%m-%d %H:%M')}"
            )
            session = self._session_service.create_session(
                workspace_id=project.workspace_id,
                metadata={
                    "title": title,
                    "automation_project_id": project.automation_project_id,
                    "automation_trigger_event_id": execution_event.event_id,
                    "automation_reason": reason,
                },
                project_kind=ProjectKind.AUTOMATION,
                project_id=project.automation_project_id,
                session_mode=project.run_config.session_mode,
                orchestration_preset_id=project.run_config.orchestration_preset_id,
            )
            run_id, _ = self._run_service.create_run(
                IntentInput(
                    session_id=session.session_id,
                    input=content_parts_from_text(project.prompt),
                    execution_mode=project.run_config.execution_mode,
                    yolo=project.run_config.yolo,
                    thinking=project.run_config.thinking,
                    session_mode=project.run_config.session_mode,
                )
            )
            self._run_service.ensure_run_started(run_id)
            if self._delivery_service is not None:
                _ = self._delivery_service.register_run(
                    project=project,
                    session_id=session.session_id,
                    run_id=run_id,
                    reason=reason,
                )
            next_run_at = _next_run_at_after_fire(
                project=project, fired_at=effective_now
            )
            if project.schedule_mode == AutomationScheduleMode.ONE_SHOT:
                next_status = AutomationProjectStatus.DISABLED
            self._repository.update(
                project.model_copy(
                    update={
                        "status": next_status,
                        "last_session_id": session.session_id,
                        "last_run_started_at": effective_now,
                        "last_error": None,
                        "next_run_at": next_run_at,
                        "updated_at": effective_now,
                    }
                )
            )
            return AutomationExecutionHandle(
                session_id=session.session_id,
                run_id=run_id,
                queued=False,
                reused_bound_session=False,
            )
        except Exception as exc:
            next_run_at = _next_run_at_after_fire(
                project=project, fired_at=effective_now
            )
            if project.schedule_mode == AutomationScheduleMode.ONE_SHOT:
                next_status = AutomationProjectStatus.DISABLED
            self._repository.update(
                project.model_copy(
                    update={
                        "status": next_status,
                        "last_error": str(exc),
                        "next_run_at": next_run_at,
                        "updated_at": effective_now,
                    }
                )
            )
            raise

    def _resolve_delivery_binding(
        self,
        candidate: AutomationFeishuBinding | None,
        *,
        existing_binding: AutomationFeishuBinding | None,
    ) -> AutomationFeishuBinding | None:
        binding = candidate if candidate is not None else existing_binding
        if binding is None:
            return None
        if self._feishu_binding_service is None:
            raise ValueError("Feishu delivery binding service is unavailable")
        return self._feishu_binding_service.validate_binding(binding)

    def _resolve_delivery_events(
        self,
        *,
        binding: AutomationFeishuBinding | None,
        requested_events: tuple[AutomationDeliveryEvent, ...] | None,
        existing_events: tuple[AutomationDeliveryEvent, ...],
    ) -> tuple[AutomationDeliveryEvent, ...]:
        if binding is None:
            return ()
        if requested_events is not None:
            return _dedupe_delivery_events(requested_events)
        if existing_events:
            return _dedupe_delivery_events(existing_events)
        return (
            AutomationDeliveryEvent.STARTED,
            AutomationDeliveryEvent.COMPLETED,
            AutomationDeliveryEvent.FAILED,
        )

    def _materialize_bound_session_execution(
        self,
        *,
        project: AutomationProjectRecord,
        reason: str,
    ) -> AutomationExecutionHandle | None:
        if self._bound_session_queue_service is None:
            return None
        return self._bound_session_queue_service.materialize_execution(
            project=project,
            reason=reason,
        )

    def _record_execution_event(
        self,
        project: AutomationProjectRecord,
        *,
        reason: str,
    ) -> AutomationExecutionEventRecord:
        occurred_at = datetime.now(tz=UTC)
        return self._event_repository.create_event(
            AutomationExecutionEventRecord(
                event_id=f"aevt_{uuid.uuid4().hex[:16]}",
                automation_project_id=project.automation_project_id,
                reason=reason,
                payload={"automation_project_id": project.automation_project_id},
                metadata={"reason": reason},
                occurred_at=occurred_at,
                created_at=occurred_at,
            )
        )


def _validate_timezone(timezone_name: str) -> str:
    normalized = timezone_name.strip() or "UTC"
    try:
        _ = ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {normalized}") from exc
    return normalized


def _resolve_optional_text(
    *, candidate: str | None, fallback: str | None
) -> str | None:
    if candidate is None:
        return fallback
    normalized = candidate.strip()
    return normalized or None


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _next_run_at_after_fire(
    *,
    project: AutomationProjectRecord,
    fired_at: datetime,
) -> datetime | None:
    if project.schedule_mode == AutomationScheduleMode.ONE_SHOT:
        return None
    return _next_run_at(
        schedule_mode=project.schedule_mode,
        cron_expression=project.cron_expression,
        run_at=project.run_at,
        timezone_name=project.timezone,
        after=fired_at + timedelta(minutes=1),
    )


def _next_run_at(
    *,
    schedule_mode: AutomationScheduleMode,
    cron_expression: str | None,
    run_at: datetime | None,
    timezone_name: str,
    after: datetime,
) -> datetime | None:
    if schedule_mode == AutomationScheduleMode.ONE_SHOT:
        if run_at is None:
            return None
        if run_at.tzinfo is None:
            raise ValueError("run_at must include timezone information")
        if run_at <= after:
            return None
        return run_at.astimezone(UTC)
    if not cron_expression:
        raise ValueError("cron_expression is required for cron schedules")
    return next_cron_occurrence(
        cron_expression=cron_expression,
        timezone_name=timezone_name,
        after=after,
    )


def next_cron_occurrence(
    *,
    cron_expression: str,
    timezone_name: str,
    after: datetime,
) -> datetime:
    fields = cron_expression.split()
    if len(fields) != 5:
        raise ValueError("cron_expression must use five fields")
    minute_values = _parse_cron_field(fields[0], 0, 59)
    hour_values = _parse_cron_field(fields[1], 0, 23)
    day_values = _parse_cron_field(fields[2], 1, 31)
    month_values = _parse_cron_field(fields[3], 1, 12)
    weekday_values = _parse_cron_field(fields[4], 0, 6)
    zone = ZoneInfo(timezone_name)
    cursor = after.astimezone(zone).replace(second=0, microsecond=0) + timedelta(
        minutes=1
    )
    max_iterations = 366 * 24 * 60
    for _ in range(max_iterations):
        if (
            cursor.minute in minute_values
            and cursor.hour in hour_values
            and cursor.day in day_values
            and cursor.month in month_values
            and ((cursor.weekday() + 1) % 7) in weekday_values
        ):
            return cursor.astimezone(UTC)
        cursor += timedelta(minutes=1)
    raise ValueError("Unable to resolve next cron occurrence within one year")


def _parse_cron_field(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        safe_part = part.strip()
        if not safe_part:
            continue
        if safe_part == "*":
            values.update(range(minimum, maximum + 1))
            continue
        step = 1
        base = safe_part
        if "/" in safe_part:
            base, step_text = safe_part.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError("Cron step must be positive")
        if base == "*":
            start = minimum
            end = maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start = int(start_text)
            end = int(end_text)
        else:
            start = int(base)
            end = int(base)
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"Cron field value out of range: {field}")
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError(f"Invalid cron field: {field}")
    return values


def _dedupe_delivery_events(
    values: tuple[AutomationDeliveryEvent, ...],
) -> tuple[AutomationDeliveryEvent, ...]:
    ordered: list[AutomationDeliveryEvent] = []
    seen: set[AutomationDeliveryEvent] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


__all__ = [
    "AutomationProjectNameConflictError",
    "AutomationService",
    "next_cron_occurrence",
]
