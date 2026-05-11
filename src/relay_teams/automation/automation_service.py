# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import JsonValue

from relay_teams.validation import (
    require_cascade_delete,
    require_force_delete,
)

from relay_teams.automation.automation_bound_session_queue_service import (
    AutomationBoundSessionQueueService,
)
from relay_teams.automation.prompt_building import build_automation_prompt
from relay_teams.automation.automation_delivery_service import AutomationDeliveryService
from relay_teams.automation.automation_event_repository import (
    AutomationEventRepository,
    AutomationExecutionEventRecord,
)
from relay_teams.automation.automation_models import (
    AutomationDeliveryBinding,
    AutomationDeliveryBindingCandidate,
    AutomationDeliveryEvent,
    AutomationExecutionHandle,
    AutomationFeishuBinding,
    AutomationFeishuBindingCandidate,
    AutomationIntervalUnit,
    AutomationProjectCreateInput,
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationProjectUpdateInput,
    AutomationRunConfig,
    AutomationScheduleMode,
    AutomationXiaolubanBinding,
)
from relay_teams.automation.automation_repository import AutomationProjectRepository
from relay_teams.automation.errors import AutomationProjectNameConflictError
from relay_teams.automation.feishu_binding_service import (
    AutomationFeishuBindingService,
)
from relay_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from relay_teams.automation.xiaoluban_binding_service import (
    AutomationXiaolubanBindingService,
)
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressBusyPolicy,
    GatewaySessionIngressRequest,
    GatewaySessionIngressService,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.media import content_parts_from_text
from relay_teams.roles import RoleRegistry
from relay_teams.sessions.session_models import ProjectKind, SessionMode
from relay_teams.sessions.runs.run_service import SessionRunService
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.session_service import SessionService
from relay_teams.workspace import WorkspaceService


LOGGER = get_logger(__name__)


class AutomationService:
    def __init__(
        self,
        *,
        repository: AutomationProjectRepository,
        event_repository: AutomationEventRepository,
        session_service: SessionService,
        run_service: SessionRunService,
        feishu_binding_service: AutomationFeishuBindingService | None = None,
        xiaoluban_binding_service: AutomationXiaolubanBindingService | None = None,
        delivery_service: AutomationDeliveryService | None = None,
        bound_session_queue_service: AutomationBoundSessionQueueService | None = None,
        workspace_service: WorkspaceService | None = None,
        session_ingress_service: GatewaySessionIngressService | None = None,
        role_registry: RoleRegistry | None = None,
        get_role_registry: (Callable[[], RoleRegistry | None]) | None = None,
        orchestration_settings_service: OrchestrationSettingsService | None = None,
        get_shell_safety_policy_enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._repository = repository
        self._event_repository = event_repository
        self._session_service = session_service
        self._run_service = run_service
        self._feishu_binding_service = feishu_binding_service
        self._xiaoluban_binding_service = xiaoluban_binding_service
        self._delivery_service = delivery_service
        self._bound_session_queue_service = bound_session_queue_service
        self._workspace_service = workspace_service
        self._session_ingress_service = session_ingress_service
        self._get_role_registry = (
            get_role_registry
            if get_role_registry is not None
            else lambda: role_registry
        )
        self._orchestration_settings_service = orchestration_settings_service
        self._get_shell_safety_policy_enabled = get_shell_safety_policy_enabled or (
            lambda: True
        )

    def _get_active_role_registry(self) -> RoleRegistry | None:
        return self._get_role_registry()

    def create_project(
        self,
        payload: AutomationProjectCreateInput,
    ) -> AutomationProjectRecord:
        timezone_name = _validate_timezone(payload.timezone)
        self._validate_workspace(payload.workspace_id)
        run_config = self._validate_run_config_for_write(payload.run_config)
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
            interval_every=payload.interval_every,
            interval_unit=payload.interval_unit,
            run_at=payload.run_at,
            timezone=timezone_name,
            run_config=run_config,
            delivery_binding=delivery_binding,
            delivery_events=delivery_events,
            trigger_id=f"schedule-{automation_project_id}",
            next_run_at=(
                _next_run_at(
                    schedule_mode=payload.schedule_mode,
                    cron_expression=payload.cron_expression,
                    interval_every=payload.interval_every,
                    interval_unit=payload.interval_unit,
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

    async def create_project_async(
        self,
        payload: AutomationProjectCreateInput,
    ) -> AutomationProjectRecord:
        timezone_name = _validate_timezone(payload.timezone)
        await self._validate_workspace_async(payload.workspace_id)
        run_config = self._validate_run_config_for_write(payload.run_config)
        delivery_binding = await self._resolve_delivery_binding_async(
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
            interval_every=payload.interval_every,
            interval_unit=payload.interval_unit,
            run_at=payload.run_at,
            timezone=timezone_name,
            run_config=run_config,
            delivery_binding=delivery_binding,
            delivery_events=delivery_events,
            trigger_id=f"schedule-{automation_project_id}",
            next_run_at=(
                _next_run_at(
                    schedule_mode=payload.schedule_mode,
                    cron_expression=payload.cron_expression,
                    interval_every=payload.interval_every,
                    interval_unit=payload.interval_unit,
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
        return await self._repository.create_async(record)

    def list_projects(self) -> tuple[AutomationProjectRecord, ...]:
        return self._repository.list_all()

    async def list_projects_async(self) -> tuple[AutomationProjectRecord, ...]:
        return await self._repository.list_all_async()

    def get_project(self, automation_project_id: str) -> AutomationProjectRecord:
        return self._repository.get(automation_project_id)

    async def get_project_async(
        self, automation_project_id: str
    ) -> AutomationProjectRecord:
        return await self._repository.get_async(automation_project_id)

    def list_feishu_bindings(
        self,
    ) -> tuple[AutomationFeishuBindingCandidate, ...]:
        if self._feishu_binding_service is None:
            return ()
        return self._feishu_binding_service.list_candidates()

    async def list_feishu_bindings_async(
        self,
    ) -> tuple[AutomationFeishuBindingCandidate, ...]:
        return await asyncio.to_thread(self.list_feishu_bindings)

    def list_delivery_bindings(
        self,
    ) -> tuple[AutomationDeliveryBindingCandidate, ...]:
        candidates: list[AutomationDeliveryBindingCandidate] = []
        if self._feishu_binding_service is not None:
            candidates.extend(self._feishu_binding_service.list_candidates())
        if self._xiaoluban_binding_service is not None:
            candidates.extend(self._xiaoluban_binding_service.list_candidates())
        return tuple(candidates)

    async def list_delivery_bindings_async(
        self,
    ) -> tuple[AutomationDeliveryBindingCandidate, ...]:
        return await asyncio.to_thread(self.list_delivery_bindings)

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
        interval_every = (
            payload.interval_every
            if payload.interval_every is not None
            else existing.interval_every
        )
        interval_unit = payload.interval_unit or existing.interval_unit
        run_at = payload.run_at if payload.run_at is not None else existing.run_at
        if payload.schedule_mode == AutomationScheduleMode.CRON:
            run_at = None
            interval_every = None
            interval_unit = None
        if payload.schedule_mode == AutomationScheduleMode.INTERVAL:
            cron_expression = None
            run_at = None
        if payload.schedule_mode == AutomationScheduleMode.ONE_SHOT:
            cron_expression = None
            interval_every = None
            interval_unit = None
        run_config = (
            self._validate_run_config_for_write(payload.run_config)
            if payload.run_config is not None
            else existing.run_config
        )
        if "delivery_binding" in payload.model_fields_set:
            delivery_binding = self._resolve_delivery_binding(
                payload.delivery_binding,
                existing_binding=None,
            )
        else:
            delivery_binding = existing.delivery_binding
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
            interval_every=interval_every,
            interval_unit=interval_unit,
            run_at=run_at,
            timezone=timezone_name,
            run_config=run_config,
            delivery_binding=delivery_binding,
            delivery_events=delivery_events,
            enabled=(
                payload.enabled
                if payload.enabled is not None
                else existing.status == AutomationProjectStatus.ENABLED
            ),
        )
        self._validate_workspace(probe.workspace_id)
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
                "interval_every": probe.interval_every,
                "interval_unit": probe.interval_unit,
                "run_at": probe.run_at,
                "timezone": timezone_name,
                "run_config": probe.run_config,
                "delivery_binding": delivery_binding,
                "delivery_events": delivery_events,
                "next_run_at": (
                    _next_run_at(
                        schedule_mode=probe.schedule_mode,
                        cron_expression=probe.cron_expression,
                        interval_every=probe.interval_every,
                        interval_unit=probe.interval_unit,
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

    async def update_project_async(
        self,
        automation_project_id: str,
        payload: AutomationProjectUpdateInput,
    ) -> AutomationProjectRecord:
        existing = await self._repository.get_async(automation_project_id)
        timezone_name = _validate_timezone(payload.timezone or existing.timezone)
        schedule_mode = payload.schedule_mode or existing.schedule_mode
        cron_expression = _resolve_optional_text(
            candidate=payload.cron_expression,
            fallback=existing.cron_expression,
        )
        interval_every = (
            payload.interval_every
            if payload.interval_every is not None
            else existing.interval_every
        )
        interval_unit = payload.interval_unit or existing.interval_unit
        run_at = payload.run_at if payload.run_at is not None else existing.run_at
        if payload.schedule_mode == AutomationScheduleMode.CRON:
            run_at = None
            interval_every = None
            interval_unit = None
        if payload.schedule_mode == AutomationScheduleMode.INTERVAL:
            cron_expression = None
            run_at = None
        if payload.schedule_mode == AutomationScheduleMode.ONE_SHOT:
            cron_expression = None
            interval_every = None
            interval_unit = None
        run_config = (
            self._validate_run_config_for_write(payload.run_config)
            if payload.run_config is not None
            else existing.run_config
        )
        if "delivery_binding" in payload.model_fields_set:
            delivery_binding = await self._resolve_delivery_binding_async(
                payload.delivery_binding,
                existing_binding=None,
            )
        else:
            delivery_binding = existing.delivery_binding
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
            interval_every=interval_every,
            interval_unit=interval_unit,
            run_at=run_at,
            timezone=timezone_name,
            run_config=run_config,
            delivery_binding=delivery_binding,
            delivery_events=delivery_events,
            enabled=(
                payload.enabled
                if payload.enabled is not None
                else existing.status == AutomationProjectStatus.ENABLED
            ),
        )
        await self._validate_workspace_async(probe.workspace_id)
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
                "interval_every": probe.interval_every,
                "interval_unit": probe.interval_unit,
                "run_at": probe.run_at,
                "timezone": timezone_name,
                "run_config": probe.run_config,
                "delivery_binding": delivery_binding,
                "delivery_events": delivery_events,
                "next_run_at": (
                    _next_run_at(
                        schedule_mode=probe.schedule_mode,
                        cron_expression=probe.cron_expression,
                        interval_every=probe.interval_every,
                        interval_unit=probe.interval_unit,
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
        return await self._repository.update_async(updated)

    def set_project_status(
        self,
        automation_project_id: str,
        status: AutomationProjectStatus,
    ) -> AutomationProjectRecord:
        existing = self._repository.get(automation_project_id)
        if status == AutomationProjectStatus.ENABLED:
            self._validate_workspace(existing.workspace_id)
        now = datetime.now(tz=UTC)
        updated = existing.model_copy(
            update={
                "status": status,
                "next_run_at": (
                    _next_run_at(
                        schedule_mode=existing.schedule_mode,
                        cron_expression=existing.cron_expression,
                        interval_every=existing.interval_every,
                        interval_unit=existing.interval_unit,
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

    async def set_project_status_async(
        self,
        automation_project_id: str,
        status: AutomationProjectStatus,
    ) -> AutomationProjectRecord:
        existing = await self._repository.get_async(automation_project_id)
        if status == AutomationProjectStatus.ENABLED:
            await self._validate_workspace_async(existing.workspace_id)
        now = datetime.now(tz=UTC)
        updated = existing.model_copy(
            update={
                "status": status,
                "next_run_at": (
                    _next_run_at(
                        schedule_mode=existing.schedule_mode,
                        cron_expression=existing.cron_expression,
                        interval_every=existing.interval_every,
                        interval_unit=existing.interval_unit,
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
        return await self._repository.update_async(updated)

    def delete_project(
        self,
        automation_project_id: str,
        *,
        force: bool = False,
        cascade: bool = False,
    ) -> None:
        project = self._repository.get(automation_project_id)
        if project.status == AutomationProjectStatus.ENABLED:
            require_force_delete(
                force,
                message="Cannot delete enabled automation project without force",
            )
        if self._has_dependent_project_data(automation_project_id):
            require_cascade_delete(
                cascade,
                message="Cannot delete automation project without cascade while deliveries or queue records exist",
            )
        if self._delivery_service is not None:
            self._delivery_service.delete_project_deliveries(automation_project_id)
        if self._bound_session_queue_service is not None:
            self._bound_session_queue_service.delete_project_queue(
                automation_project_id
            )
        self._repository.delete(automation_project_id)

    async def delete_project_async(
        self,
        automation_project_id: str,
        *,
        force: bool = False,
        cascade: bool = False,
    ) -> None:
        project = await self._repository.get_async(automation_project_id)
        if project.status == AutomationProjectStatus.ENABLED:
            require_force_delete(
                force,
                message="Cannot delete enabled automation project without force",
            )
        if await self._has_dependent_project_data_async(automation_project_id):
            require_cascade_delete(
                cascade,
                message="Cannot delete automation project without cascade while deliveries or queue records exist",
            )
        if self._delivery_service is not None:
            await asyncio.to_thread(
                self._delivery_service.delete_project_deliveries,
                automation_project_id,
            )
        if self._bound_session_queue_service is not None:
            await asyncio.to_thread(
                self._bound_session_queue_service.delete_project_queue,
                automation_project_id,
            )
        await self._repository.delete_async(automation_project_id)

    def _has_dependent_project_data(self, automation_project_id: str) -> bool:
        if (
            self._delivery_service is not None
            and self._delivery_service.has_project_deliveries(automation_project_id)
        ):
            return True
        if (
            self._bound_session_queue_service is not None
            and self._bound_session_queue_service.has_project_queue(
                automation_project_id
            )
        ):
            return True
        return False

    async def _has_dependent_project_data_async(
        self, automation_project_id: str
    ) -> bool:
        if self._delivery_service is not None and await asyncio.to_thread(
            self._delivery_service.has_project_deliveries,
            automation_project_id,
        ):
            return True
        if self._bound_session_queue_service is not None and await asyncio.to_thread(
            self._bound_session_queue_service.has_project_queue,
            automation_project_id,
        ):
            return True
        return False

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

    async def run_now_async(self, automation_project_id: str) -> dict[str, JsonValue]:
        project = await self._repository.get_async(automation_project_id)
        execution_task = asyncio.create_task(
            self._materialize_execution_async(project, reason="manual")
        )
        try:
            execution_handle = await asyncio.shield(execution_task)
        except asyncio.CancelledError:
            try:
                _ = await _await_execution_after_cancellation(execution_task)
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.ERROR,
                    event="automation.run_now.cancelled_start_failed",
                    message="Automation run startup failed after caller cancellation",
                    payload={
                        "automation_project_id": automation_project_id,
                        "error": str(exc),
                    },
                    exc_info=exc,
                )
            raise
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
        return self._list_project_sessions_for_record(project)

    def _list_project_sessions_for_record(
        self,
        project: AutomationProjectRecord,
    ) -> tuple[dict[str, object], ...]:
        sessions = list(
            self._session_service.list_sessions_by_project(
                project_kind=ProjectKind.AUTOMATION,
                project_id=project.automation_project_id,
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

    async def list_project_sessions_async(
        self,
        automation_project_id: str,
    ) -> tuple[dict[str, object], ...]:
        project = await self._repository.get_async(automation_project_id)
        return await asyncio.to_thread(self._list_project_sessions_for_record, project)

    def process_due_projects(
        self,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        effective_now = now or datetime.now(tz=UTC)
        processed: list[str] = []
        for project in self._repository.list_due(effective_now):
            self._materialize_execution(project, reason="schedule", now=effective_now)
            processed.append(project.automation_project_id)
        return tuple(processed)

    async def process_due_projects_async(
        self,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        effective_now = now or datetime.now(tz=UTC)
        processed: list[str] = []
        for project in await self._repository.list_due_async(effective_now):
            await self._materialize_execution_async(
                project, reason="schedule", now=effective_now
            )
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
        try:
            bound_session_handle = self._materialize_bound_session_execution(
                project=project,
                reason=reason,
            )
            if bound_session_handle is not None:
                next_run_at = _next_run_at_after_materialize(
                    project=project, fired_at=effective_now, reason=reason
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
            runtime_run_config = self._coerce_run_config_for_execution(project)
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
                session_mode=runtime_run_config.session_mode,
                normal_root_role_id=runtime_run_config.normal_root_role_id,
                orchestration_preset_id=runtime_run_config.orchestration_preset_id,
            )
            intent = IntentInput(
                session_id=session.session_id,
                input=content_parts_from_text(
                    build_automation_prompt(
                        project_name=project.display_name,
                        prompt=project.prompt,
                    )
                ),
                execution_mode=runtime_run_config.execution_mode,
                yolo=runtime_run_config.yolo,
                shell_safety_policy_enabled=self._get_shell_safety_policy_enabled(),
                thinking=runtime_run_config.thinking,
                session_mode=runtime_run_config.session_mode,
            )
            run_id = self._start_unbound_run(intent)
            if self._delivery_service is not None:
                _ = self._delivery_service.register_run(
                    project=project,
                    session_id=session.session_id,
                    run_id=run_id,
                    reason=reason,
                )
            next_run_at = _next_run_at_after_materialize(
                project=project, fired_at=effective_now, reason=reason
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
            next_run_at = _next_run_at_after_materialize(
                project=project, fired_at=effective_now, reason=reason
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

    async def _materialize_execution_async(
        self,
        project: AutomationProjectRecord,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> AutomationExecutionHandle:
        effective_now = now or datetime.now(tz=UTC)
        execution_event = await self._record_execution_event_async(
            project, reason=reason
        )
        next_status = project.status
        try:
            bound_session_handle = (
                await self._materialize_bound_session_execution_async(
                    project=project,
                    reason=reason,
                )
            )
            if bound_session_handle is not None:
                next_run_at = _next_run_at_after_materialize(
                    project=project, fired_at=effective_now, reason=reason
                )
                if project.schedule_mode == AutomationScheduleMode.ONE_SHOT:
                    next_status = AutomationProjectStatus.DISABLED
                await self._repository.update_async(
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
            runtime_run_config = self._coerce_run_config_for_execution(project)
            session = await self._session_service.create_session_async(
                workspace_id=project.workspace_id,
                metadata={
                    "title": title,
                    "automation_project_id": project.automation_project_id,
                    "automation_trigger_event_id": execution_event.event_id,
                    "automation_reason": reason,
                },
                project_kind=ProjectKind.AUTOMATION,
                project_id=project.automation_project_id,
                session_mode=runtime_run_config.session_mode,
                normal_root_role_id=runtime_run_config.normal_root_role_id,
                orchestration_preset_id=runtime_run_config.orchestration_preset_id,
            )
            intent = IntentInput(
                session_id=session.session_id,
                input=content_parts_from_text(
                    build_automation_prompt(
                        project_name=project.display_name,
                        prompt=project.prompt,
                    )
                ),
                execution_mode=runtime_run_config.execution_mode,
                yolo=runtime_run_config.yolo,
                shell_safety_policy_enabled=await asyncio.to_thread(
                    self._get_shell_safety_policy_enabled
                ),
                thinking=runtime_run_config.thinking,
                session_mode=runtime_run_config.session_mode,
            )
            run_id = await self._start_unbound_run_async(intent)
            if self._delivery_service is not None:
                _ = await asyncio.to_thread(
                    self._delivery_service.register_run,
                    project=project,
                    session_id=session.session_id,
                    run_id=run_id,
                    reason=reason,
                )
            next_run_at = _next_run_at_after_materialize(
                project=project, fired_at=effective_now, reason=reason
            )
            if project.schedule_mode == AutomationScheduleMode.ONE_SHOT:
                next_status = AutomationProjectStatus.DISABLED
            await self._repository.update_async(
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
            next_run_at = _next_run_at_after_materialize(
                project=project, fired_at=effective_now, reason=reason
            )
            if project.schedule_mode == AutomationScheduleMode.ONE_SHOT:
                next_status = AutomationProjectStatus.DISABLED
            await self._repository.update_async(
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

    def _start_unbound_run(self, intent: IntentInput) -> str:
        if self._session_ingress_service is not None:
            result = self._session_ingress_service.require_started(
                GatewaySessionIngressRequest(
                    intent=intent,
                    busy_policy=GatewaySessionIngressBusyPolicy.START_IF_IDLE,
                )
            )
            if result.run_id is None:
                raise RuntimeError("automation_run_not_started")
            return result.run_id
        run_id, _ = self._run_service.create_run(intent)
        self._run_service.ensure_run_started(run_id)
        return run_id

    async def _start_unbound_run_async(self, intent: IntentInput) -> str:
        if self._session_ingress_service is not None:
            result = await self._session_ingress_service.require_started_async(
                GatewaySessionIngressRequest(
                    intent=intent,
                    busy_policy=GatewaySessionIngressBusyPolicy.START_IF_IDLE,
                )
            )
            if result.run_id is None:
                raise RuntimeError("automation_run_not_started")
            return result.run_id
        run_id, _ = await self._run_service.create_run_async(intent)
        await self._run_service.ensure_run_started_async(run_id)
        return run_id

    def _resolve_delivery_binding(
        self,
        candidate: AutomationDeliveryBinding | None,
        *,
        existing_binding: AutomationDeliveryBinding | None,
    ) -> AutomationDeliveryBinding | None:
        binding = candidate if candidate is not None else existing_binding
        if binding is None:
            return None
        if isinstance(binding, AutomationFeishuBinding):
            if self._feishu_binding_service is None:
                raise ValueError("Feishu delivery binding service is unavailable")
            return self._feishu_binding_service.validate_binding(binding)
        if isinstance(binding, AutomationXiaolubanBinding):
            if self._xiaoluban_binding_service is None:
                raise ValueError("Xiaoluban delivery binding service is unavailable")
            return self._xiaoluban_binding_service.validate_binding(binding)
        raise ValueError(f"Unsupported delivery binding provider: {binding.provider}")

    async def _resolve_delivery_binding_async(
        self,
        candidate: AutomationDeliveryBinding | None,
        *,
        existing_binding: AutomationDeliveryBinding | None,
    ) -> AutomationDeliveryBinding | None:
        binding = candidate if candidate is not None else existing_binding
        if binding is None:
            return None
        return await asyncio.to_thread(
            self._resolve_delivery_binding,
            candidate,
            existing_binding=existing_binding,
        )

    @staticmethod
    def _resolve_delivery_events(
        *,
        binding: AutomationDeliveryBinding | None,
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

    def _validate_run_config_for_write(
        self,
        run_config: AutomationRunConfig,
    ) -> AutomationRunConfig:
        role_registry = self._get_active_role_registry()
        if run_config.session_mode == SessionMode.NORMAL:
            normalized_role_id = str(run_config.normal_root_role_id or "").strip()
            if not normalized_role_id:
                return run_config.model_copy(
                    update={
                        "normal_root_role_id": None,
                        "orchestration_preset_id": None,
                    }
                )
            if role_registry is None:
                raise ValueError("Role registry is unavailable")
            resolved_role_id = role_registry.resolve_normal_mode_role_id(
                normalized_role_id
            )
            return run_config.model_copy(
                update={
                    "normal_root_role_id": resolved_role_id,
                    "orchestration_preset_id": None,
                }
            )

        normalized_preset_id = str(run_config.orchestration_preset_id or "").strip()
        if not normalized_preset_id:
            raise ValueError(
                "orchestration_preset_id is required in orchestration mode"
            )
        if self._orchestration_settings_service is None:
            raise ValueError("Orchestration settings service is unavailable")
        settings = self._orchestration_settings_service.get_orchestration_config()
        if not any(
            preset.preset_id == normalized_preset_id for preset in settings.presets
        ):
            raise ValueError(f"Unknown orchestration preset: {normalized_preset_id}")
        return run_config.model_copy(
            update={
                "normal_root_role_id": None,
                "orchestration_preset_id": normalized_preset_id,
            }
        )

    def _coerce_run_config_for_execution(
        self,
        project: AutomationProjectRecord,
    ) -> AutomationRunConfig:
        run_config = project.run_config
        role_registry = self._get_active_role_registry()
        if run_config.session_mode == SessionMode.NORMAL:
            normalized_role_id = str(run_config.normal_root_role_id or "").strip()
            if not normalized_role_id or role_registry is None:
                return run_config.model_copy(
                    update={
                        "normal_root_role_id": None,
                        "orchestration_preset_id": None,
                    }
                )
            try:
                resolved_role_id = role_registry.resolve_normal_mode_role_id(
                    normalized_role_id
                )
            except ValueError as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="automation.run_config.invalid_normal_root_role_id",
                    message="Ignoring invalid persisted automation normal-mode role",
                    payload={
                        "automation_project_id": project.automation_project_id,
                        "normal_root_role_id": normalized_role_id,
                        "error": str(exc),
                    },
                )
                resolved_role_id = None
            return run_config.model_copy(
                update={
                    "normal_root_role_id": resolved_role_id,
                    "orchestration_preset_id": None,
                }
            )

        normalized_preset_id = str(run_config.orchestration_preset_id or "").strip()
        if not normalized_preset_id or self._orchestration_settings_service is None:
            return run_config.model_copy(
                update={
                    "normal_root_role_id": None,
                    "orchestration_preset_id": normalized_preset_id or None,
                }
            )
        settings = self._orchestration_settings_service.get_orchestration_config()
        if any(preset.preset_id == normalized_preset_id for preset in settings.presets):
            return run_config.model_copy(
                update={
                    "normal_root_role_id": None,
                    "orchestration_preset_id": normalized_preset_id,
                }
            )
        log_event(
            LOGGER,
            logging.WARNING,
            event="automation.run_config.invalid_orchestration_preset_id",
            message="Ignoring invalid persisted automation orchestration preset",
            payload={
                "automation_project_id": project.automation_project_id,
                "orchestration_preset_id": normalized_preset_id,
            },
        )
        return run_config.model_copy(
            update={
                "normal_root_role_id": None,
                "orchestration_preset_id": None,
            }
        )

    def _materialize_bound_session_execution(
        self,
        *,
        project: AutomationProjectRecord,
        reason: str,
    ) -> AutomationExecutionHandle | None:
        if project.delivery_binding is None:
            return None
        if project.delivery_binding.provider != "feishu":
            return None
        if self._bound_session_queue_service is None:
            raise RuntimeError(
                "Automation bound session execution service is unavailable"
            )
        return asyncio.run(
            self._bound_session_queue_service.materialize_execution(
                project=project,
                reason=reason,
            )
        )

    async def _materialize_bound_session_execution_async(
        self,
        *,
        project: AutomationProjectRecord,
        reason: str,
    ) -> AutomationExecutionHandle | None:
        if project.delivery_binding is None:
            return None
        if project.delivery_binding.provider != "feishu":
            return None
        if self._bound_session_queue_service is None:
            raise RuntimeError(
                "Automation bound session execution service is unavailable"
            )
        return await self._bound_session_queue_service.materialize_execution(
            project=project,
            reason=reason,
        )

    def _validate_workspace(self, workspace_id: str) -> None:
        if self._workspace_service is None:
            return
        try:
            _ = self._workspace_service.require_workspace(workspace_id)
        except KeyError as exc:
            raise ValueError(f"Unknown workspace: {workspace_id}") from exc

    async def _validate_workspace_async(self, workspace_id: str) -> None:
        if self._workspace_service is None:
            return
        try:
            _ = await self._workspace_service.require_workspace_async(workspace_id)
        except KeyError as exc:
            raise ValueError(f"Unknown workspace: {workspace_id}") from exc

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

    async def _record_execution_event_async(
        self,
        project: AutomationProjectRecord,
        *,
        reason: str,
    ) -> AutomationExecutionEventRecord:
        occurred_at = datetime.now(tz=UTC)
        return await self._event_repository.create_event_async(
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


async def _await_execution_after_cancellation(
    task: asyncio.Task[AutomationExecutionHandle],
) -> AutomationExecutionHandle:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            _clear_current_task_cancellation_requests()
    return task.result()


def _clear_current_task_cancellation_requests() -> None:
    current_task = asyncio.current_task()
    if current_task is None:
        return
    while current_task.cancelling():
        current_task.uncancel()


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
    after = (
        fired_at
        if project.schedule_mode == AutomationScheduleMode.INTERVAL
        else fired_at + timedelta(minutes=1)
    )
    return _next_run_at(
        schedule_mode=project.schedule_mode,
        cron_expression=project.cron_expression,
        interval_every=project.interval_every,
        interval_unit=project.interval_unit,
        run_at=project.run_at,
        timezone_name=project.timezone,
        after=after,
    )


def _next_run_at_after_materialize(
    *,
    project: AutomationProjectRecord,
    fired_at: datetime,
    reason: str,
) -> datetime | None:
    if project.schedule_mode == AutomationScheduleMode.ONE_SHOT:
        return None
    if reason != "schedule":
        return project.next_run_at
    return _next_run_at_after_fire(project=project, fired_at=fired_at)


def _next_run_at(
    *,
    schedule_mode: AutomationScheduleMode,
    cron_expression: str | None,
    interval_every: int | None,
    interval_unit: AutomationIntervalUnit | None,
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
    if schedule_mode == AutomationScheduleMode.INTERVAL:
        if interval_every is None:
            raise ValueError("interval_every is required for interval schedules")
        if interval_unit is None:
            raise ValueError("interval_unit is required for interval schedules")
        return after.astimezone(UTC) + _interval_delta(
            interval_every=interval_every,
            interval_unit=interval_unit,
        )
    if not cron_expression:
        raise ValueError("cron_expression is required for cron schedules")
    return next_cron_occurrence(
        cron_expression=cron_expression,
        timezone_name=timezone_name,
        after=after,
    )


def _interval_delta(
    *,
    interval_every: int,
    interval_unit: AutomationIntervalUnit,
) -> timedelta:
    if interval_unit == AutomationIntervalUnit.MINUTES:
        return timedelta(minutes=interval_every)
    if interval_unit == AutomationIntervalUnit.HOURS:
        return timedelta(hours=interval_every)
    return timedelta(days=interval_every)


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
