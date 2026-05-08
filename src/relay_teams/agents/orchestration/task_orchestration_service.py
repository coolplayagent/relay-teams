# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from pydantic import JsonValue

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agent_runtimes.instances.enums import InstanceLifecycle, InstanceStatus
from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
from relay_teams.agent_runtimes.instances.models import create_subagent_instance
from relay_teams.agents.orchestration.claim_service import (
    BlockersNotResolvedError,
    ClaimConflictError,
    ClaimService,
)
from relay_teams.agents.orchestration.task_contracts import (
    TaskDraft,
    TaskExecutionServiceLike,
    TaskUpdate,
)
from relay_teams.agents.orchestration.policy_models import (
    DEFAULT_MAX_PARALLEL_DELEGATED_TASKS,
)
from relay_teams.agents.orchestration.role_contracts import (
    role_contract_precondition_failures,
)
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.ids import new_task_id
from relay_teams.agents.tasks.models import (
    SpecCheckpointEvaluation,
    TaskLifecyclePolicy,
    TaskEnvelope,
    TaskRecord,
    TaskSpec,
    TaskSpecArtifact,
    VerificationCommand,
    VerificationEvidenceBundle,
    VerificationPlan,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.hooks import (
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    HookService,
    TaskCreatedInput,
)
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.session_repository import SessionRepository


class TaskOrchestrationService:
    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        role_registry: RoleRegistry,
        agent_repo: AgentInstanceRepository,
        task_execution_service: TaskExecutionServiceLike,
        message_repo: MessageRepository,
        session_repo: SessionRepository | None = None,
        runtime_role_resolver: RuntimeRoleResolver | None = None,
        hook_service: HookService | None = None,
        run_event_hub: RunEventHub | None = None,
        run_intent_repo: RunIntentRepository | None = None,
        default_max_parallel_delegated_tasks: int = (
            DEFAULT_MAX_PARALLEL_DELEGATED_TASKS
        ),
    ) -> None:
        self._task_repo = task_repo
        self._role_registry = role_registry
        self._agent_repo = agent_repo
        self._task_execution_service = task_execution_service
        self._message_repo = message_repo
        self._session_repo = session_repo
        self._runtime_role_resolver = runtime_role_resolver
        self._hook_service = hook_service
        self._run_event_hub = run_event_hub
        self._run_intent_repo = run_intent_repo
        self._default_max_parallel_delegated_tasks = (
            default_max_parallel_delegated_tasks
        )
        self._execution_semaphores: dict[str, asyncio.Semaphore] = {}
        self._execution_semaphore_ref_counts: dict[str, int] = {}
        self._execution_semaphores_guard = asyncio.Lock()
        self._assignment_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._assignment_lock_ref_counts: dict[tuple[str, str], int] = {}
        self._assignment_locks_guard = asyncio.Lock()
        self._claim_service = ClaimService(task_repo)

    @property
    def task_repo(self) -> TaskRepository:
        return self._task_repo

    async def create_tasks(
        self,
        *,
        run_id: str,
        tasks: list[TaskDraft],
    ) -> dict[str, JsonValue]:
        if not tasks:
            raise ValueError("tasks must contain at least one task")

        root = await self._get_root_task_async(run_id)
        existing_records = await self._task_repo.list_by_trace_async(
            root.envelope.trace_id
        )
        existing_task_ids = {record.envelope.task_id for record in existing_records}
        existing_node_task_ids = _existing_node_task_ids(existing_records)
        _validate_task_graph_drafts(
            tasks=tuple(tasks),
            existing_task_ids=existing_task_ids,
            existing_node_task_ids=existing_node_task_ids,
            root_task_id=root.envelope.task_id,
        )

        prepared_drafts: list[_PreparedTaskDraft] = []
        task_ids_by_node_id = dict(existing_node_task_ids)
        for draft in tasks:
            task_id = new_task_id().value
            if draft.orchestration_node_id is not None:
                task_ids_by_node_id[draft.orchestration_node_id] = task_id
            prepared_drafts.append(_PreparedTaskDraft(task_id=task_id, draft=draft))

        for prepared in prepared_drafts:
            prepared.depends_on_task_ids = _resolved_dependency_task_ids(
                draft=prepared.draft,
                task_ids_by_node_id=task_ids_by_node_id,
            )
        for prepared in prepared_drafts:
            if prepared.draft.role_id is not None:
                await self._resolve_role_async(
                    run_id=root.envelope.trace_id,
                    role_id=prepared.draft.role_id,
                )

        existing_records_by_task_id = {
            record.envelope.task_id: record for record in existing_records
        }
        prepared_drafts_by_task_id = {
            prepared.task_id: prepared for prepared in prepared_drafts
        }
        resolved_spec_bindings_by_task_id: dict[str, _ResolvedSpecBinding] = {}
        prepared_envelopes: list[tuple[_PreparedTaskDraft, TaskEnvelope]] = []
        for prepared in prepared_drafts:
            draft = prepared.draft
            spec_binding = await self._resolve_draft_spec_binding(
                task_id=prepared.task_id,
                draft=draft,
                dependency_task_ids=prepared.depends_on_task_ids,
                existing_records_by_task_id=existing_records_by_task_id,
                prepared_drafts_by_task_id=prepared_drafts_by_task_id,
                resolved_spec_bindings_by_task_id=resolved_spec_bindings_by_task_id,
            )
            envelope = TaskEnvelope(
                task_id=prepared.task_id,
                session_id=root.envelope.session_id,
                parent_task_id=root.envelope.task_id,
                trace_id=root.envelope.trace_id,
                role_id=draft.role_id,
                title=_resolved_title(draft.title, draft.objective),
                objective=draft.objective,
                verification=_verification_for_task_draft(draft, spec_binding.spec),
                spec=spec_binding.spec,
                spec_artifact_id=spec_binding.spec_artifact_id,
                spec_source_task_id=spec_binding.spec_source_task_id,
                lifecycle=draft.lifecycle,
                orchestration_node_id=draft.orchestration_node_id,
                depends_on_task_ids=prepared.depends_on_task_ids,
            )
            prepared_envelopes.append((prepared, envelope))

        for _prepared, envelope in prepared_envelopes:
            await self._execute_task_created_hooks(envelope=envelope)

        created_records: list[TaskRecord] = []
        for prepared, envelope in prepared_envelopes:
            draft = prepared.draft
            record = await self._task_repo.create_async(envelope)
            if draft.role_id is not None:
                record = await self._assign_created_task_async(
                    record=record,
                    role_id=draft.role_id,
                    run_id=root.envelope.trace_id,
                )
            created_records.append(record)

        response: dict[str, JsonValue] = {
            "created_count": len(created_records),
            "tasks": [_task_projection(record) for record in created_records],
        }
        return response

    async def _execute_task_created_hooks(self, *, envelope: TaskEnvelope) -> None:
        if self._hook_service is None:
            return
        bundle = await self._hook_service.execute(
            event_input=TaskCreatedInput(
                event_name=HookEventName.TASK_CREATED,
                session_id=envelope.session_id,
                run_id=envelope.trace_id,
                trace_id=envelope.trace_id,
                task_id=envelope.task_id,
                role_id=envelope.role_id,
                created_task_id=envelope.task_id,
                parent_task_id=envelope.parent_task_id,
                title=envelope.title or "",
                objective=envelope.objective,
            ),
            run_event_hub=self._run_event_hub,
        )
        if (
            isinstance(bundle, HookDecisionBundle)
            and bundle.decision == HookDecisionType.DENY
        ):
            raise ValueError(bundle.reason or "Task creation denied by runtime hooks.")

    async def update_task_async(
        self,
        *,
        run_id: str | None,
        task_id: str,
        update: TaskUpdate,
    ) -> dict[str, JsonValue]:
        record = await self.get_task_async(task_id=task_id, run_id=run_id)
        if record.envelope.parent_task_id is None:
            raise ValueError("root coordinator task cannot be updated via task APIs")
        if record.status != TaskStatus.CREATED and not _is_handoff_only_update(update):
            raise ValueError("only created tasks can be updated")

        current = record.envelope
        next_objective = (
            str(update.objective).strip()
            if update.objective is not None
            else current.objective
        )
        if not next_objective:
            raise ValueError("objective must not be empty")

        handoff_only_update = _is_handoff_only_update(update)
        next_title = (
            current.title
            if handoff_only_update
            else (
                _resolved_title(update.title, next_objective)
                if update.title is not None
                else current.title or _resolved_title(None, next_objective)
            )
        )
        resolved_update = await self._resolve_update_spec_binding(
            current=current,
            update=update,
        )
        updated = await self._task_repo.update_envelope_async(
            task_id,
            current.model_copy(
                update=_task_update_fields(
                    resolved_update,
                    next_objective,
                    next_title,
                    current=current,
                    requested_update=update,
                ),
            ),
        )
        return {"task": _task_projection(updated)}

    async def list_delegated_tasks_async(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> dict[str, JsonValue]:
        records = [
            record
            for record in await self._task_repo.list_by_trace_async(run_id)
            if include_root or record.envelope.parent_task_id is not None
        ]
        return {
            "tasks": [_task_projection(record) for record in records],
        }

    async def list_run_tasks_async(
        self,
        *,
        run_id: str,
        include_root: bool = False,
    ) -> dict[str, JsonValue]:
        return await self.list_delegated_tasks_async(
            run_id=run_id,
            include_root=include_root,
        )

    async def list_tasks_async(self) -> tuple[TaskRecord, ...]:
        return await self._task_repo.list_all_async()

    async def dispatch_task(
        self,
        *,
        run_id: str | None,
        task_id: str,
        role_id: str,
        prompt: str = "",
    ) -> dict[str, JsonValue]:
        record = await self.get_task_async(task_id=task_id, run_id=run_id)
        resolved_run_id = run_id or record.envelope.trace_id
        if record.envelope.parent_task_id is None:
            raise ValueError("root coordinator task cannot be dispatched via task APIs")
        if record.status == TaskStatus.RUNNING:
            raise ValueError("task is already running")

        normalized_role_id = str(role_id).strip()
        if not normalized_role_id:
            raise ValueError("role_id must not be empty")
        role_definition = await self._resolve_role_definition_async(
            run_id=resolved_run_id,
            role_id=normalized_role_id,
        )

        normalized_prompt = prompt.strip()
        bound_role_id = str(record.envelope.role_id or "").strip()
        instance_id = record.assigned_instance_id or ""
        contract_checked = False

        if record.status == TaskStatus.CREATED:
            async with self._role_assignment_lock_slot(
                session_id=record.envelope.session_id,
                role_id=normalized_role_id,
            ) as assignment_lock:
                async with assignment_lock:
                    record = await self._task_repo.get_async(task_id)
                    bound_role_id = str(record.envelope.role_id or "").strip()
                    if record.status == TaskStatus.CREATED:
                        if bound_role_id and bound_role_id != normalized_role_id:
                            raise ValueError(
                                f"Task is already bound to role {bound_role_id}; create a replacement task to change roles."
                            )
                        contract_record = record
                        if not bound_role_id:
                            contract_record = record.model_copy(
                                update={
                                    "envelope": record.envelope.model_copy(
                                        update={"role_id": normalized_role_id}
                                    )
                                }
                            )
                        await self._assert_role_contract_preconditions_async(
                            role=role_definition,
                            record=contract_record,
                        )
                        contract_checked = True
                        if not bound_role_id:
                            record = await self._task_repo.update_envelope_async(
                                task_id,
                                contract_record.envelope,
                            )
                            bound_role_id = normalized_role_id
                        instance_id = await self._ensure_execution_instance_async(
                            session_id=record.envelope.session_id,
                            run_id=resolved_run_id,
                            role_id=bound_role_id,
                            task_id=task_id,
                        )
                        await self._task_repo.update_status_async(
                            task_id=task_id,
                            status=TaskStatus.ASSIGNED,
                            assigned_instance_id=instance_id,
                        )
                        record = await self._task_repo.get_async(task_id)
                    else:
                        if not bound_role_id:
                            raise ValueError(
                                "task must be bound to a role before it can be dispatched"
                            )
                        if bound_role_id != normalized_role_id:
                            raise ValueError(
                                f"Task is already bound to role {bound_role_id}; create a replacement task to change roles."
                            )
                        instance_id = record.assigned_instance_id or instance_id
        else:
            if not bound_role_id:
                raise ValueError(
                    "task must be bound to a role before it can be re-dispatched"
                )
            if bound_role_id != normalized_role_id:
                raise ValueError(
                    f"Task is already bound to role {bound_role_id}; create a replacement task to change roles."
                )
        if not contract_checked:
            await self._assert_role_contract_preconditions_async(
                role=role_definition,
                record=record,
            )
        if record.status == TaskStatus.COMPLETED:
            raise ValueError(
                f"Task '{record.envelope.title}' (role={bound_role_id}) "
                "is completed. Create a replacement task instead of re-dispatching this one."
            )
        elif record.status in {TaskStatus.FAILED, TaskStatus.TIMEOUT}:
            raise ValueError(
                f"Task '{record.envelope.title}' (role={bound_role_id}) "
                f"is {record.status.value}: "
                f"{record.error_message or 'unknown error'}. "
                "Create a replacement task instead of re-dispatching this one."
            )

        if not instance_id:
            instance_id = record.assigned_instance_id or ""
        if not instance_id:
            raise ValueError("task has no bound instance to dispatch")

        await self._assert_instance_available_async(
            task=record, instance_id=instance_id
        )

        # === OP-2: Atomic Claim ===
        claim_result = await self._claim_service.claim_task_async(
            task_id=task_id,
            instance_id=instance_id,
        )
        if not claim_result.success:
            raise ClaimConflictError(
                task_id=task_id,
                error_code=claim_result.error_code,
            )

        # === OP-2: Blocker check ===
        try:
            record = await self._task_repo.get_async(task_id)
        except KeyError:
            pass
        else:
            unresolved_blockers = await self._claim_service.check_blockers_async(
                record.envelope,
            )
            if unresolved_blockers:
                await self._claim_service.release_task_async(
                    task_id, claim_result.claim_token
                )
                raise BlockersNotResolvedError(
                    task_id=task_id,
                    unresolved_blockers=unresolved_blockers,
                )

        effective_prompt = (
            normalized_prompt
            or "Execute this task contract and return the requested result."
        )

        try:
            async with self._run_execution_slot(run_id=resolved_run_id):
                await self._task_execution_service.execute(
                    instance_id=instance_id,
                    role_id=bound_role_id,
                    task=record.envelope,
                    user_prompt_override=effective_prompt,
                )
        finally:
            await self._claim_service.release_task_async(
                task_id, claim_result.claim_token
            )
        refreshed = await self._task_repo.get_async(task_id)
        return {
            "task": _task_projection(refreshed),
        }

    @asynccontextmanager
    async def _run_execution_slot(self, *, run_id: str) -> AsyncIterator[None]:
        semaphore = await self._execution_semaphore_for_run(run_id=run_id)
        try:
            async with semaphore:
                yield
        finally:
            await self._release_execution_semaphore_for_run(run_id=run_id)

    async def _execution_semaphore_for_run(self, *, run_id: str) -> asyncio.Semaphore:
        max_parallel_tasks = await self._max_parallel_delegated_tasks_for_run(
            run_id=run_id
        )
        if max_parallel_tasks < 1:
            raise ValueError(
                "delegated task execution is disabled by the orchestration policy"
            )
        async with self._execution_semaphores_guard:
            semaphore = self._execution_semaphores.get(run_id)
            if semaphore is None:
                semaphore = asyncio.Semaphore(max_parallel_tasks)
                self._execution_semaphores[run_id] = semaphore
            self._execution_semaphore_ref_counts[run_id] = (
                self._execution_semaphore_ref_counts.get(run_id, 0) + 1
            )
            resolved_semaphore = semaphore
        return resolved_semaphore

    async def _max_parallel_delegated_tasks_for_run(self, *, run_id: str) -> int:
        if self._run_intent_repo is None:
            return self._default_max_parallel_delegated_tasks
        try:
            intent = await self._run_intent_repo.get_async(run_id)
        except KeyError:
            return self._default_max_parallel_delegated_tasks
        topology = intent.topology
        if topology is None:
            return self._default_max_parallel_delegated_tasks
        return topology.orchestration_policy.max_parallel_delegated_tasks

    async def _release_execution_semaphore_for_run(self, *, run_id: str) -> None:
        async with self._execution_semaphores_guard:
            remaining = self._execution_semaphore_ref_counts.get(run_id, 1) - 1
            if remaining <= 0:
                self._execution_semaphore_ref_counts.pop(run_id, None)
                self._execution_semaphores.pop(run_id, None)
                return
            self._execution_semaphore_ref_counts[run_id] = remaining

    @asynccontextmanager
    async def _role_assignment_lock_slot(
        self,
        *,
        session_id: str,
        role_id: str,
    ) -> AsyncIterator[asyncio.Lock]:
        key, assignment_lock = await self._retain_role_assignment_lock(
            session_id=session_id,
            role_id=role_id,
        )
        try:
            yield assignment_lock
        finally:
            await self._release_role_assignment_lock(key=key)

    async def _retain_role_assignment_lock(
        self,
        *,
        session_id: str,
        role_id: str,
    ) -> tuple[tuple[str, str], asyncio.Lock]:
        key = (session_id, role_id)
        async with self._assignment_locks_guard:
            assignment_lock = self._assignment_locks.get(key)
            if assignment_lock is None:
                assignment_lock = asyncio.Lock()
                self._assignment_locks[key] = assignment_lock
            self._assignment_lock_ref_counts[key] = (
                self._assignment_lock_ref_counts.get(key, 0) + 1
            )
        return key, assignment_lock

    async def _release_role_assignment_lock(self, *, key: tuple[str, str]) -> None:
        async with self._assignment_locks_guard:
            remaining = self._assignment_lock_ref_counts.get(key, 1) - 1
            if remaining <= 0:
                self._assignment_lock_ref_counts.pop(key, None)
                self._assignment_locks.pop(key, None)
                return
            self._assignment_lock_ref_counts[key] = remaining

    async def _ensure_execution_instance_async(
        self,
        *,
        session_id: str,
        run_id: str,
        role_id: str,
        task_id: str,
    ) -> str:
        existing = await self._agent_repo.get_session_role_instance_async(
            session_id, role_id
        )
        if existing is not None:
            if await self._instance_has_blocking_task_async(
                session_id=session_id,
                instance_id=existing.instance_id,
                task_id=task_id,
            ):
                return await self._create_ephemeral_role_clone_async(
                    session_id=session_id,
                    run_id=run_id,
                    role_id=role_id,
                    workspace_id=existing.workspace_id,
                    parent_instance_id=existing.instance_id,
                )
            await self._agent_repo.upsert_instance_async(
                run_id=run_id,
                trace_id=run_id,
                session_id=session_id,
                instance_id=existing.instance_id,
                role_id=existing.role_id,
                workspace_id=existing.workspace_id,
                conversation_id=existing.conversation_id,
                status=existing.status,
                lifecycle=InstanceLifecycle.REUSABLE,
            )
            return existing.instance_id

        session = (
            await self._session_repo.get_async(session_id)
            if self._session_repo
            else None
        )
        if session is None:
            raise RuntimeError(
                "TaskOrchestrationService requires session_repo to resolve workspace"
            )
        instance = create_subagent_instance(
            role_id,
            session_id=session_id,
            workspace_id=session.workspace_id,
        )
        await self._agent_repo.upsert_instance_async(
            run_id=run_id,
            trace_id=run_id,
            session_id=session_id,
            instance_id=instance.instance_id,
            role_id=instance.role_id,
            workspace_id=instance.workspace_id,
            conversation_id=instance.conversation_id,
            status=InstanceStatus.IDLE,
            lifecycle=InstanceLifecycle.REUSABLE,
        )
        return instance.instance_id

    async def _create_ephemeral_role_clone_async(
        self,
        *,
        session_id: str,
        run_id: str,
        role_id: str,
        workspace_id: str,
        parent_instance_id: str,
    ) -> str:
        instance = create_subagent_instance(
            role_id,
            session_id=session_id,
            workspace_id=workspace_id,
        )
        await self._agent_repo.upsert_instance_async(
            run_id=run_id,
            trace_id=run_id,
            session_id=session_id,
            instance_id=instance.instance_id,
            role_id=instance.role_id,
            workspace_id=instance.workspace_id,
            conversation_id=instance.conversation_id,
            status=InstanceStatus.IDLE,
            lifecycle=InstanceLifecycle.EPHEMERAL,
            parent_instance_id=parent_instance_id,
        )
        return instance.instance_id

    async def _instance_has_blocking_task_async(
        self,
        *,
        session_id: str,
        instance_id: str,
        task_id: str,
    ) -> bool:
        blocking_statuses = {
            TaskStatus.ASSIGNED,
            TaskStatus.RUNNING,
            TaskStatus.STOPPED,
        }
        for candidate in await self._task_repo.list_by_session_async(session_id):
            if candidate.envelope.task_id == task_id:
                continue
            if candidate.assigned_instance_id != instance_id:
                continue
            if candidate.status in blocking_statuses:
                return True
        return False

    async def _assert_instance_available_async(
        self, *, task: TaskRecord, instance_id: str
    ) -> None:
        blocking_statuses = {
            TaskStatus.ASSIGNED,
            TaskStatus.RUNNING,
            TaskStatus.STOPPED,
        }
        for candidate in await self._task_repo.list_by_session_async(
            task.envelope.session_id
        ):
            if candidate.envelope.task_id == task.envelope.task_id:
                continue
            if candidate.assigned_instance_id != instance_id:
                continue
            if candidate.status not in blocking_statuses:
                continue
            raise ValueError(
                f"Role {candidate.envelope.role_id or 'unassigned'} is busy with task "
                f"'{candidate.envelope.title}' (status={candidate.status.value}). "
                f"Wait for it to complete or use a different role."
            )

    async def _resolve_role_async(self, *, run_id: str, role_id: str) -> None:
        _ = await self._resolve_role_definition_async(run_id=run_id, role_id=role_id)

    async def _resolve_role_definition_async(
        self, *, run_id: str, role_id: str
    ) -> RoleDefinition:
        if self._runtime_role_resolver is not None:
            return await self._runtime_role_resolver.get_effective_role_async(
                run_id=run_id,
                role_id=role_id,
            )
        return self._role_registry.get(role_id)

    async def _assert_role_contract_preconditions_async(
        self,
        *,
        role: RoleDefinition,
        record: TaskRecord,
    ) -> None:
        records = await self._task_repo.list_by_trace_async(record.envelope.trace_id)
        failures = role_contract_precondition_failures(
            role=role,
            task=record.envelope,
            records_by_id={
                candidate.envelope.task_id: candidate for candidate in records
            },
        )
        if failures:
            raise ValueError(
                f"Role contract preconditions failed for {role.role_id}: "
                + "; ".join(failures)
            )

    async def _assign_created_task_async(
        self,
        *,
        record: TaskRecord,
        role_id: str,
        run_id: str,
    ) -> TaskRecord:
        async with self._role_assignment_lock_slot(
            session_id=record.envelope.session_id,
            role_id=role_id,
        ) as assignment_lock:
            async with assignment_lock:
                refreshed = await self._task_repo.get_async(record.envelope.task_id)
                if refreshed.status != TaskStatus.CREATED:
                    return refreshed
                instance_id = await self._ensure_execution_instance_async(
                    session_id=refreshed.envelope.session_id,
                    run_id=run_id,
                    role_id=role_id,
                    task_id=refreshed.envelope.task_id,
                )
                await self._task_repo.update_status_async(
                    task_id=refreshed.envelope.task_id,
                    status=TaskStatus.ASSIGNED,
                    assigned_instance_id=instance_id,
                )
                return await self._task_repo.get_async(refreshed.envelope.task_id)

    async def _resolve_draft_spec_binding(
        self,
        *,
        task_id: str,
        draft: TaskDraft,
        dependency_task_ids: tuple[str, ...],
        existing_records_by_task_id: dict[str, TaskRecord],
        prepared_drafts_by_task_id: dict[str, "_PreparedTaskDraft"],
        resolved_spec_bindings_by_task_id: dict[str, "_ResolvedSpecBinding"],
    ) -> "_ResolvedSpecBinding":
        resolved_binding = resolved_spec_bindings_by_task_id.get(task_id)
        if resolved_binding is not None:
            return resolved_binding
        spec = draft.spec
        spec_artifact_id = draft.spec_artifact_id
        spec_source_task_id = draft.spec_source_task_id
        spec_source_task_id_requested = spec_source_task_id is not None
        if spec_artifact_id is not None:
            artifact = await self._task_repo.get_spec_artifact_async(spec_artifact_id)
            if spec is None:
                spec = artifact.spec
            elif spec != artifact.spec:
                raise ValueError("spec_artifact_id references a different task spec")
            if spec_source_task_id is None:
                spec_source_task_id = artifact.source_task_id or artifact.task_id
            if artifact.task_id != task_id:
                spec_artifact_id = None

        if spec_source_task_id is not None and spec_source_task_id_requested:
            source_binding = await self._spec_binding_for_source_task_async(
                task_id=spec_source_task_id,
                existing_records_by_task_id=existing_records_by_task_id,
                prepared_drafts_by_task_id=prepared_drafts_by_task_id,
                resolved_spec_bindings_by_task_id=resolved_spec_bindings_by_task_id,
            )
            if source_binding.spec is None:
                raise ValueError(
                    f"spec_source_task_id has no bound spec: {spec_source_task_id}"
                )
            if spec is None:
                spec = source_binding.spec
            if spec_artifact_id is None and source_binding.task_id == task_id:
                spec_artifact_id = source_binding.spec_artifact_id

        if spec is None and spec_source_task_id is None:
            inherited = [
                await self._spec_binding_for_source_task_async(
                    task_id=dependency_task_id,
                    existing_records_by_task_id=existing_records_by_task_id,
                    prepared_drafts_by_task_id=prepared_drafts_by_task_id,
                    resolved_spec_bindings_by_task_id=resolved_spec_bindings_by_task_id,
                )
                for dependency_task_id in dependency_task_ids
            ]
            inherited = [binding for binding in inherited if binding.spec is not None]
            unique_specs = {
                binding.spec.model_dump_json()
                for binding in inherited
                if binding.spec is not None
            }
            if len(unique_specs) == 1 and inherited:
                source_binding = inherited[0]
                spec = source_binding.spec
                if source_binding.task_id == task_id:
                    spec_artifact_id = source_binding.spec_artifact_id
                spec_source_task_id = source_binding.task_id

        resolved_binding = _ResolvedSpecBinding(
            spec=spec,
            spec_artifact_id=spec_artifact_id,
            spec_source_task_id=spec_source_task_id,
        )
        resolved_spec_bindings_by_task_id[task_id] = resolved_binding
        return resolved_binding

    async def _resolve_update_spec_binding(
        self,
        *,
        current: TaskEnvelope,
        update: TaskUpdate,
    ) -> TaskUpdate:
        spec = update.spec
        spec_artifact_id = update.spec_artifact_id
        spec_source_task_id = update.spec_source_task_id
        spec_source_task_id_requested = spec_source_task_id is not None
        if spec_artifact_id is not None:
            artifact = await self._task_repo.get_spec_artifact_async(spec_artifact_id)
            if artifact.task_id != current.task_id:
                raise ValueError("spec_artifact_id references a different task")
            if spec is None:
                spec = artifact.spec
            elif spec != artifact.spec:
                raise ValueError("spec_artifact_id references a different task spec")
            if spec_source_task_id is None:
                spec_source_task_id = artifact.source_task_id or artifact.task_id
        if spec_source_task_id is not None and spec_source_task_id_requested:
            source_record = await self._task_repo.get_async(spec_source_task_id)
            source_spec = source_record.envelope.spec
            if source_spec is None:
                raise ValueError(
                    f"spec_source_task_id has no bound spec: {spec_source_task_id}"
                )
            if spec is None:
                spec = source_spec
            if (
                spec_artifact_id is None
                and source_record.envelope.task_id == current.task_id
            ):
                spec_artifact_id = source_record.envelope.spec_artifact_id
        if spec is None and (
            spec_artifact_id is not None or spec_source_task_id is not None
        ):
            spec = current.spec
        return update.model_copy(
            update={
                "spec": spec,
                "spec_artifact_id": spec_artifact_id,
                "spec_source_task_id": spec_source_task_id,
            }
        )

    async def _spec_binding_for_source_task_async(
        self,
        *,
        task_id: str,
        existing_records_by_task_id: dict[str, TaskRecord],
        prepared_drafts_by_task_id: dict[str, "_PreparedTaskDraft"],
        resolved_spec_bindings_by_task_id: dict[str, "_ResolvedSpecBinding"],
    ) -> "_SourceSpecBinding":
        resolved_binding = resolved_spec_bindings_by_task_id.get(task_id)
        if resolved_binding is not None:
            return _SourceSpecBinding(
                task_id=task_id,
                spec=resolved_binding.spec,
                spec_artifact_id=resolved_binding.spec_artifact_id,
            )
        record = existing_records_by_task_id.get(task_id)
        if record is not None:
            return _SourceSpecBinding(
                task_id=task_id,
                spec=record.envelope.spec,
                spec_artifact_id=record.envelope.spec_artifact_id,
            )
        prepared = prepared_drafts_by_task_id.get(task_id)
        if prepared is not None:
            resolved_binding = await self._resolve_draft_spec_binding(
                task_id=prepared.task_id,
                draft=prepared.draft,
                dependency_task_ids=prepared.depends_on_task_ids,
                existing_records_by_task_id=existing_records_by_task_id,
                prepared_drafts_by_task_id=prepared_drafts_by_task_id,
                resolved_spec_bindings_by_task_id=resolved_spec_bindings_by_task_id,
            )
            return _SourceSpecBinding(
                task_id=task_id,
                spec=resolved_binding.spec,
                spec_artifact_id=resolved_binding.spec_artifact_id,
            )
        source_record = await self._task_repo.get_async(task_id)
        return _SourceSpecBinding(
            task_id=task_id,
            spec=source_record.envelope.spec,
            spec_artifact_id=source_record.envelope.spec_artifact_id,
        )

    async def _get_root_task_async(self, run_id: str) -> TaskRecord:
        for record in await self._task_repo.list_by_trace_async(run_id):
            if record.envelope.parent_task_id is None:
                return record
        raise KeyError(f"No root task found for run_id={run_id}")

    async def get_task_async(
        self, *, task_id: str, run_id: str | None = None
    ) -> TaskRecord:
        record = await self._task_repo.get_async(task_id)
        if run_id is not None and record.envelope.trace_id != run_id:
            raise KeyError(f"Task {task_id} does not belong to run {run_id}")
        return record

    async def get_task_spec_artifact_async(
        self,
        *,
        task_id: str,
    ) -> TaskSpecArtifact:
        record = await self._task_repo.get_async(task_id)
        if record.envelope.spec_artifact_id is not None:
            return await self._task_repo.get_spec_artifact_async(
                record.envelope.spec_artifact_id
            )
        return await self._task_repo.get_latest_spec_artifact_for_task_async(task_id)

    async def get_task_evidence_bundle_async(
        self,
        *,
        task_id: str,
    ) -> VerificationEvidenceBundle:
        record = await self._task_repo.get_async(task_id)
        if record.envelope.evidence_bundle is None:
            raise KeyError(f"No evidence bundle found for task_id: {task_id}")
        return record.envelope.evidence_bundle

    async def list_task_spec_artifacts_async(
        self,
        *,
        task_id: str,
    ) -> tuple[TaskSpecArtifact, ...]:
        await self._task_repo.get_async(task_id)
        return await self._task_repo.list_spec_artifacts_by_task_async(task_id)

    async def list_spec_checkpoint_evaluations_async(
        self,
        *,
        task_id: str,
        checkpoint_seq: int | None = None,
    ) -> tuple[SpecCheckpointEvaluation, ...]:
        await self._task_repo.get_async(task_id)
        return await self._task_repo.list_spec_checkpoint_evaluations_async(
            task_id, checkpoint_seq
        )


class _PreparedTaskDraft:
    def __init__(self, *, task_id: str, draft: TaskDraft) -> None:
        self.task_id = task_id
        self.draft = draft
        self.depends_on_task_ids: tuple[str, ...] = ()


class _ResolvedSpecBinding:
    def __init__(
        self,
        *,
        spec: TaskSpec | None,
        spec_artifact_id: str | None,
        spec_source_task_id: str | None,
    ) -> None:
        self.spec = spec
        self.spec_artifact_id = spec_artifact_id
        self.spec_source_task_id = spec_source_task_id


class _SourceSpecBinding:
    def __init__(
        self,
        *,
        task_id: str,
        spec: TaskSpec | None,
        spec_artifact_id: str | None,
    ) -> None:
        self.task_id = task_id
        self.spec = spec
        self.spec_artifact_id = spec_artifact_id


def _resolved_title(title: str | None, objective: str) -> str:
    normalized = str(title or "").strip()
    if normalized:
        return normalized
    summary = " ".join(objective.strip().split())
    if not summary:
        raise ValueError("objective must not be empty")
    return summary[:80]


def _task_projection(record: TaskRecord) -> dict[str, JsonValue]:
    assigned_role_id = record.envelope.role_id
    assigned_instance_id = record.assigned_instance_id
    row: dict[str, JsonValue] = {
        "task_id": record.envelope.task_id,
        "title": record.envelope.title
        or _resolved_title(None, record.envelope.objective),
        "objective": record.envelope.objective,
        "status": record.status.value,
        "assigned_role_id": assigned_role_id,
        "assigned_instance_id": assigned_instance_id,
        "role_id": assigned_role_id,
        "instance_id": assigned_instance_id,
        "parent_task_id": record.envelope.parent_task_id,
    }
    if record.result:
        row["result"] = record.result
    if record.error_message:
        row["error"] = record.error_message
    row["verification"] = cast(
        JsonValue,
        record.envelope.verification.model_dump(mode="json"),
    )
    if record.envelope.spec is not None:
        row["spec"] = cast(JsonValue, record.envelope.spec.model_dump(mode="json"))
    if record.envelope.spec_artifact_id is not None:
        row["spec_artifact_id"] = record.envelope.spec_artifact_id
    if record.envelope.spec_source_task_id is not None:
        row["spec_source_task_id"] = record.envelope.spec_source_task_id
    if record.envelope.evidence_bundle is not None:
        row["evidence_bundle"] = cast(
            JsonValue,
            record.envelope.evidence_bundle.model_dump(mode="json"),
        )
    if record.envelope.handoff is not None:
        row["handoff"] = cast(
            JsonValue,
            record.envelope.handoff.model_dump(mode="json"),
        )
    if record.envelope.orchestration_node_id is not None:
        row["orchestration_node_id"] = record.envelope.orchestration_node_id
    if record.envelope.depends_on_task_ids:
        row["depends_on_task_ids"] = list(record.envelope.depends_on_task_ids)
    lifecycle = record.envelope.lifecycle
    if _has_non_default_lifecycle(lifecycle):
        row["lifecycle"] = cast(JsonValue, lifecycle.model_dump(mode="json"))
    return row


def _existing_node_task_ids(records: tuple[TaskRecord, ...]) -> dict[str, str]:
    node_task_ids: dict[str, str] = {}
    for record in records:
        node_id = record.envelope.orchestration_node_id
        if node_id is None:
            continue
        node_task_ids[node_id] = record.envelope.task_id
    return node_task_ids


def _validate_task_graph_drafts(
    *,
    tasks: tuple[TaskDraft, ...],
    existing_task_ids: set[str],
    existing_node_task_ids: dict[str, str],
    root_task_id: str,
) -> None:
    draft_node_ids = tuple(
        draft.orchestration_node_id
        for draft in tasks
        if draft.orchestration_node_id is not None
    )
    if len(draft_node_ids) != len(set(draft_node_ids)):
        raise ValueError("orchestration_node_id values must be unique")
    for node_id in draft_node_ids:
        if node_id in existing_node_task_ids:
            raise ValueError(f"orchestration_node_id already exists: {node_id}")

    known_node_ids = set(existing_node_task_ids) | set(draft_node_ids)
    for draft in tasks:
        for dependency_task_id in draft.depends_on_task_ids:
            if dependency_task_id == root_task_id:
                raise ValueError(
                    "depends_on_task_ids cannot reference the root coordinator task"
                )
            if dependency_task_id not in existing_task_ids:
                raise ValueError(
                    f"depends_on_task_ids references unknown task: {dependency_task_id}"
                )
        for dependency_node_id in draft.depends_on_node_ids:
            if dependency_node_id not in known_node_ids:
                raise ValueError(
                    "depends_on_node_ids references unknown orchestration node: "
                    f"{dependency_node_id}"
                )
            if dependency_node_id == draft.orchestration_node_id:
                raise ValueError("orchestration graph node cannot depend on itself")

    _assert_draft_node_graph_acyclic(tasks=tasks)


def _assert_draft_node_graph_acyclic(*, tasks: tuple[TaskDraft, ...]) -> None:
    batch_node_ids = {
        draft.orchestration_node_id
        for draft in tasks
        if draft.orchestration_node_id is not None
    }
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in batch_node_ids}
    indegree: dict[str, int] = {node_id: 0 for node_id in batch_node_ids}
    for draft in tasks:
        node_id = draft.orchestration_node_id
        if node_id is None:
            continue
        for dependency_node_id in draft.depends_on_node_ids:
            if dependency_node_id not in batch_node_ids:
                continue
            outgoing[dependency_node_id].append(node_id)
            indegree[node_id] += 1

    ready = [node_id for node_id in batch_node_ids if indegree[node_id] == 0]
    visited_count = 0
    while ready:
        node_id = ready.pop(0)
        visited_count += 1
        for downstream_node_id in outgoing[node_id]:
            indegree[downstream_node_id] -= 1
            if indegree[downstream_node_id] == 0:
                ready.append(downstream_node_id)
    if visited_count != len(batch_node_ids):
        raise ValueError("orchestration graph dependencies must be acyclic")


def _resolved_dependency_task_ids(
    *,
    draft: TaskDraft,
    task_ids_by_node_id: dict[str, str],
) -> tuple[str, ...]:
    dependency_task_ids = list(draft.depends_on_task_ids)
    for dependency_node_id in draft.depends_on_node_ids:
        dependency_task_ids.append(task_ids_by_node_id[dependency_node_id])
    return _unique_identifiers(dependency_task_ids)


def _unique_identifiers(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return tuple(unique_values)


def _verification_for_task_draft(
    draft: TaskDraft,
    resolved_spec: TaskSpec | None = None,
) -> VerificationPlan:
    if draft.verification is not None:
        return draft.verification
    return _verification_for_task_spec(resolved_spec)


def _verification_for_task_spec(spec: TaskSpec | None) -> VerificationPlan:
    if spec is None:
        return VerificationPlan(checklist=("non_empty_response",))
    return VerificationPlan(
        checklist=("non_empty_response",),
        acceptance_criteria=spec.acceptance_criteria,
        command_checks=tuple(
            VerificationCommand.model_validate({"command": command})
            for command in spec.verification_commands
        ),
        evidence_expectations=spec.evidence_expectations,
        strictness=spec.strictness,
        formal_checks=()
        if spec.formal_verification is None
        else (spec.formal_verification,),
    )


def _is_handoff_only_update(update: TaskUpdate) -> bool:
    return (
        update.handoff is not None
        and update.objective is None
        and update.title is None
        and update.spec is None
        and update.spec_artifact_id is None
        and update.spec_source_task_id is None
        and update.verification is None
        and update.lifecycle is None
    )


def _task_update_fields(
    update: TaskUpdate,
    next_objective: str,
    next_title: str | None,
    *,
    current: TaskEnvelope,
    requested_update: TaskUpdate,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "objective": next_objective,
        "title": next_title,
    }
    spec_changed = update.spec is not None and update.spec != current.spec
    spec_requested = requested_update.spec is not None
    should_store_spec = spec_requested or spec_changed
    if should_store_spec and update.spec is not None:
        fields["spec"] = update.spec
    if update.spec_artifact_id is not None:
        fields["spec_artifact_id"] = update.spec_artifact_id
    if update.spec_source_task_id is not None:
        fields["spec_source_task_id"] = update.spec_source_task_id
    if should_store_spec and update.spec is not None:
        if update.verification is None:
            fields["verification"] = _verification_for_task_spec(update.spec)
    if update.verification is not None:
        fields["verification"] = update.verification
    if update.lifecycle is not None:
        fields["lifecycle"] = update.lifecycle
    if update.handoff is not None:
        fields["handoff"] = update.handoff
    return fields


def _has_non_default_lifecycle(lifecycle: TaskLifecyclePolicy) -> bool:
    return lifecycle != TaskLifecyclePolicy()
