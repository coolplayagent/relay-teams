# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, cast

from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import ModelRequest, UserContent, UserPromptPart

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.execution.prompt_instruction_state import (
    record_prompt_instruction_paths_loaded_async,
)
from relay_teams.agents.execution.system_prompts import (
    PromptBuildInput,
    PromptSkillInstruction,
    RuntimePromptBuilder,
    RuntimePromptSections,
    build_workspace_ssh_profile_prompt_metadata,
    compose_provider_system_prompt,
    compose_runtime_system_prompt,
)
from relay_teams.agents.execution.user_prompts import (
    UserPromptBuildInput,
    build_user_prompt,
)
from relay_teams.agents.orchestration.harnesses.tool_harness import TaskToolHarness
from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.media import MediaAssetService, merge_user_prompt_content
from relay_teams.persistence.scope_models import ScopeRef, ScopeType
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.roles.memory_injection import build_role_with_memory
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import (
    RuntimePromptConversationContext,
    RunTopologySnapshot,
)
from relay_teams.skills.skill_models import SkillInstructionEntry
from relay_teams.skills.skill_routing_service import SkillRuntimeService
from relay_teams.tools.workspace_tools.edit_state import READ_STATE_PREFIX
from relay_teams.workspace import WorkspaceHandle, WorkspaceManager

ProviderUserPromptContent = str | tuple[UserContent, ...]


class PreparedRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_sections: RuntimePromptSections
    runtime_tools_json: str
    user_prompt: str
    skill_instructions: tuple[PromptSkillInstruction, ...] = ()


class TaskPromptHarness(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    role_registry: RoleRegistry
    shared_store: SharedStateRepository
    message_repo: MessageRepository
    workspace_manager: WorkspaceManager
    prompt_builder: RuntimePromptBuilder
    tool_harness: TaskToolHarness
    skill_runtime_service: object | None = None
    role_memory_service: RoleMemoryService | None = None
    runtime_role_resolver: RuntimeRoleResolver | None = None
    run_intent_repo: RunIntentRepository | None = None
    media_asset_service: MediaAssetService | None = None

    def topology_for_run(self, run_id: str) -> RunTopologySnapshot | None:
        if self.run_intent_repo is None:
            return None
        try:
            return self.run_intent_repo.get(run_id).topology
        except KeyError:
            return None

    def conversation_context_for_run(
        self,
        run_id: str,
    ) -> RuntimePromptConversationContext | None:
        if self.run_intent_repo is None:
            return None
        try:
            return self.run_intent_repo.get(run_id).conversation_context
        except KeyError:
            return None

    async def topology_for_run_async(self, run_id: str) -> RunTopologySnapshot | None:
        if self.run_intent_repo is None:
            return None
        try:
            return (await self.run_intent_repo.get_async(run_id)).topology
        except KeyError:
            return None

    async def conversation_context_for_run_async(
        self,
        run_id: str,
    ) -> RuntimePromptConversationContext | None:
        if self.run_intent_repo is None:
            return None
        try:
            return (await self.run_intent_repo.get_async(run_id)).conversation_context
        except KeyError:
            return None

    def role_with_memory(
        self,
        *,
        role: RoleDefinition,
        role_id: str,
        workspace_id: str,
    ) -> RoleDefinition:
        return build_role_with_memory(
            role_registry=self.role_registry,
            role_memory_service=self.role_memory_service,
            role=role,
            role_id=role_id,
            workspace_id=workspace_id,
        )

    async def prepare_runtime_snapshot(
        self,
        *,
        role: RoleDefinition,
        task: TaskEnvelope,
        working_directory: Path | None,
        worktree_root: Path | None,
        workspace: WorkspaceHandle | None,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        objective: str,
    ) -> PreparedRuntimeSnapshot:
        topology = await self.topology_for_run_async(task.trace_id)
        conversation_context = await self.conversation_context_for_run_async(
            task.trace_id
        )
        runtime_tools = await self.tool_harness.build_runtime_tools_snapshot(
            role=role,
            task=task,
        )
        prompt_sections = await self.prompt_builder.build_sections(
            PromptBuildInput(
                role=role,
                task=task,
                topology=topology,
                shared_state_snapshot=shared_state_snapshot,
                working_directory=working_directory,
                worktree_root=worktree_root,
                workspace=workspace,
                ssh_profile_metadata=(
                    ()
                    if workspace is None
                    else build_workspace_ssh_profile_prompt_metadata(
                        workspace=workspace,
                        ssh_profile_service=(
                            self.workspace_manager.ssh_profile_service
                        ),
                        consumer=(
                            "agents.orchestration.harnesses.prompt_harness"
                            ".prepare_runtime_snapshot"
                        ),
                    )
                ),
                conversation_context=conversation_context,
                runtime_tools=runtime_tools,
            )
        )
        await record_prompt_instruction_paths_loaded_async(
            shared_store=self.shared_store,
            task_id=task.task_id,
            paths=prompt_sections.local_instruction_paths,
        )
        user_prompt, skill_instructions = self.build_user_prompt(
            role=role,
            objective=objective,
            shared_state_snapshot=shared_state_snapshot,
            conversation_context=conversation_context,
            orchestration_prompt=(
                "" if topology is None else topology.orchestration_prompt
            ),
            skill_names=task.skills,
        )
        return PreparedRuntimeSnapshot(
            prompt_sections=prompt_sections,
            runtime_tools_json=json.dumps(
                runtime_tools.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            user_prompt=user_prompt,
            skill_instructions=skill_instructions,
        )

    @staticmethod
    def compose_runtime_system_prompt(
        *,
        runtime_prompt_sections: RuntimePromptSections,
        skill_instructions: tuple[PromptSkillInstruction, ...],
    ) -> str:
        return compose_runtime_system_prompt(
            runtime_prompt_sections,
            skill_instructions=skill_instructions,
        )

    @staticmethod
    def compose_provider_system_prompt(
        *,
        runtime_prompt_sections: RuntimePromptSections,
        skill_instructions: tuple[PromptSkillInstruction, ...],
    ) -> str:
        return compose_provider_system_prompt(
            runtime_prompt_sections,
            skill_instructions=skill_instructions,
        )

    def shared_state_snapshot(
        self,
        *,
        session_id: str,
        role_id: str,
        conversation_id: str,
    ) -> tuple[tuple[str, str], ...]:
        scopes = (
            ScopeRef(scope_type=ScopeType.SESSION, scope_id=session_id),
            ScopeRef(scope_type=ScopeType.ROLE, scope_id=f"{session_id}:{role_id}"),
            ScopeRef(scope_type=ScopeType.CONVERSATION, scope_id=conversation_id),
        )
        return self.shared_store.snapshot_many(
            scopes,
            exclude_key_prefixes=(READ_STATE_PREFIX,),
        )

    async def shared_state_snapshot_async(
        self,
        *,
        session_id: str,
        role_id: str,
        conversation_id: str,
    ) -> tuple[tuple[str, str], ...]:
        scopes = (
            ScopeRef(scope_type=ScopeType.SESSION, scope_id=session_id),
            ScopeRef(scope_type=ScopeType.ROLE, scope_id=f"{session_id}:{role_id}"),
            ScopeRef(scope_type=ScopeType.CONVERSATION, scope_id=conversation_id),
        )
        return await self.shared_store.snapshot_many_async(
            scopes,
            exclude_key_prefixes=(READ_STATE_PREFIX,),
        )

    def ensure_committed_task_prompt(
        self,
        *,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        instance_id: str,
        task: TaskEnvelope,
        user_prompt_text: str,
        user_prompt_override: str | None,
    ) -> None:
        prompt = user_prompt_text.strip()
        override_prompt = str(user_prompt_override or "").strip()
        if override_prompt:
            self.message_repo.append_user_prompt_if_missing(
                session_id=task.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_role_id=role_id,
                instance_id=instance_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                content=override_prompt,
            )
            return

        task_history = self.message_repo.get_history_for_conversation_task(
            conversation_id,
            task.task_id,
        )
        if task_history:
            return
        if (
            task.parent_task_id is None
            and self.run_intent_repo is not None
            and self.media_asset_service is not None
        ):
            try:
                run_intent = self.run_intent_repo.get(task.trace_id)
            except KeyError:
                run_intent = None
            if run_intent is not None and run_intent.input:
                provider_content = (
                    self.media_asset_service.to_persisted_user_prompt_content(
                        parts=run_intent.input
                    )
                )
                merged_provider_content = self.merge_provider_prompt_content(
                    provider_content=provider_content,
                    user_prompt_text=prompt,
                )
                self.message_repo.prune_conversation_history_to_safe_boundary(
                    conversation_id
                )
                self.message_repo.append(
                    session_id=task.session_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_role_id=role_id,
                    instance_id=instance_id,
                    task_id=task.task_id,
                    trace_id=task.trace_id,
                    messages=[
                        ModelRequest(
                            parts=[UserPromptPart(content=merged_provider_content)]
                        )
                    ],
                )
                return
        if prompt:
            self.message_repo.append_user_prompt_if_missing(
                session_id=task.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_role_id=role_id,
                instance_id=instance_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                content=prompt,
            )
            return
        self.message_repo.append_user_prompt_if_missing(
            session_id=task.session_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_role_id=role_id,
            instance_id=instance_id,
            task_id=task.task_id,
            trace_id=task.trace_id,
            content=task.objective,
        )

    async def ensure_committed_task_prompt_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        instance_id: str,
        task: TaskEnvelope,
        user_prompt_text: str,
        user_prompt_override: str | None,
    ) -> None:
        prompt = user_prompt_text.strip()
        override_prompt = str(user_prompt_override or "").strip()
        if override_prompt:
            await self.message_repo.append_user_prompt_if_missing_async(
                session_id=task.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_role_id=role_id,
                instance_id=instance_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                content=override_prompt,
            )
            return

        task_history = await self.message_repo.get_history_for_conversation_task_async(
            conversation_id,
            task.task_id,
        )
        if task_history:
            return
        if (
            task.parent_task_id is None
            and self.run_intent_repo is not None
            and self.media_asset_service is not None
        ):
            try:
                run_intent = await self.run_intent_repo.get_async(task.trace_id)
            except KeyError:
                run_intent = None
            if run_intent is not None and run_intent.input:
                provider_content = (
                    self.media_asset_service.to_persisted_user_prompt_content(
                        parts=run_intent.input
                    )
                )
                merged_provider_content = self.merge_provider_prompt_content(
                    provider_content=provider_content,
                    user_prompt_text=prompt,
                )
                await (
                    self.message_repo.prune_conversation_history_to_safe_boundary_async(
                        conversation_id
                    )
                )
                await self.message_repo.append_async(
                    session_id=task.session_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_role_id=role_id,
                    instance_id=instance_id,
                    task_id=task.task_id,
                    trace_id=task.trace_id,
                    messages=[
                        ModelRequest(
                            parts=[UserPromptPart(content=merged_provider_content)]
                        )
                    ],
                )
                return
        if prompt:
            await self.message_repo.append_user_prompt_if_missing_async(
                session_id=task.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_role_id=role_id,
                instance_id=instance_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                content=prompt,
            )
            return
        await self.message_repo.append_user_prompt_if_missing_async(
            session_id=task.session_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_role_id=role_id,
            instance_id=instance_id,
            task_id=task.task_id,
            trace_id=task.trace_id,
            content=task.objective,
        )

    def build_user_prompt(
        self,
        *,
        role: RoleDefinition,
        objective: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        conversation_context: RuntimePromptConversationContext | None,
        orchestration_prompt: str,
        skill_names: Optional[tuple[str, ...]] = None,
    ) -> tuple[str, tuple[PromptSkillInstruction, ...]]:
        resolved_objective = objective.strip()
        if self.skill_runtime_service is None:
            return (
                build_user_prompt(UserPromptBuildInput(objective=resolved_objective)),
                (),
            )
        skill_runtime_service = cast(
            SkillRuntimeService,
            self.skill_runtime_service,
        )
        prepared_prompt = skill_runtime_service.prepare_prompt(
            role=role,
            objective=resolved_objective,
            shared_state_snapshot=shared_state_snapshot,
            conversation_context=conversation_context,
            orchestration_prompt=orchestration_prompt,
            skill_names=skill_names,
            consumer="agents.orchestration.harnesses.prompt_harness.prepare_prompt",
        )
        return (
            prepared_prompt.user_prompt,
            self.to_prompt_skill_instructions(
                prepared_prompt.system_prompt_skill_instructions
            ),
        )

    @staticmethod
    def to_prompt_skill_instructions(
        entries: tuple[SkillInstructionEntry, ...],
    ) -> tuple[PromptSkillInstruction, ...]:
        return tuple(
            PromptSkillInstruction(name=entry.name, description=entry.description)
            for entry in entries
        )

    @staticmethod
    def merge_provider_prompt_content(
        *,
        provider_content: ProviderUserPromptContent,
        user_prompt_text: str,
    ) -> ProviderUserPromptContent:
        appendix = TaskPromptHarness.user_prompt_skill_appendix(user_prompt_text)
        if not appendix:
            return provider_content
        return merge_user_prompt_content(provider_content, appendix)

    @staticmethod
    def user_prompt_skill_appendix(user_prompt_text: str) -> str:
        prompt = user_prompt_text.strip()
        heading = "## Skill Candidates"
        if heading not in prompt:
            return ""
        return prompt[prompt.index(heading) :].strip()

    @staticmethod
    def resolve_turn_objective(
        *,
        task: TaskEnvelope,
        user_prompt_override: str | None,
    ) -> str:
        prompt_override = str(user_prompt_override or "").strip()
        base_prompt = prompt_override or task.objective.strip()
        contract_prompt = TaskPromptHarness.task_contract_prompt(task)
        if not contract_prompt:
            return base_prompt
        return "\n\n".join(
            section for section in (base_prompt, contract_prompt) if section
        )

    @staticmethod
    def task_contract_prompt(task: TaskEnvelope) -> str:
        sections: list[str] = []
        spec = task.spec
        if spec is not None:
            lines = ["## Task Spec"]
            if spec.summary:
                lines.append(f"- Summary: {spec.summary}")
            lines.extend(_format_contract_items("Requirements", spec.requirements))
            lines.extend(_format_contract_items("Constraints", spec.constraints))
            lines.extend(
                _format_contract_items(
                    "Acceptance Criteria",
                    spec.acceptance_criteria,
                )
            )
            lines.extend(_format_contract_items("Out of Scope", spec.out_of_scope))
            lines.extend(
                _format_contract_items(
                    "Verification Commands",
                    spec.verification_commands,
                )
            )
            lines.extend(
                _format_contract_items(
                    "Evidence Expectations",
                    spec.evidence_expectations,
                )
            )
            lines.append(f"- Strictness: {spec.strictness.value}")
            sections.append("\n".join(lines))

        lifecycle = task.lifecycle
        lifecycle_lines: list[str] = []
        if lifecycle.timeout_seconds is not None:
            lifecycle_lines.append(f"- Timeout Seconds: {lifecycle.timeout_seconds:g}")
        if lifecycle.heartbeat_interval_seconds is not None:
            lifecycle_lines.append(
                "- Heartbeat Interval Seconds: "
                f"{lifecycle.heartbeat_interval_seconds:g}"
            )
        if lifecycle.timeout_seconds is not None:
            lifecycle_lines.append(f"- On Timeout: {lifecycle.on_timeout.value}")
            lifecycle_lines.append(
                "- Handoff Required: summarize completed work, incomplete work, "
                "key files, checks run, and next steps before stopping when possible."
            )
        if lifecycle_lines:
            sections.append("## Task Lifecycle\n" + "\n".join(lifecycle_lines))
        return "\n\n".join(sections)


def _format_contract_items(label: str, items: tuple[str, ...]) -> list[str]:
    if not items:
        return []
    return [f"- {label}:"] + [f"  - {item}" for item in items]
