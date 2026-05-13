# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from relay_teams.logger import get_logger
from relay_teams.memory.models import (
    ApplyMemoryEvolutionDraftRequest,
    CreateMemoryEvolutionDraftRequest,
    MemoryEntry,
    MemoryEntryStatus,
    MemoryEvolutionDraft,
    MemoryEvolutionDraftQuery,
    MemoryEvolutionDraftQueryResult,
    MemoryEvolutionStatus,
    MemoryEvolutionTarget,
    RejectMemoryEvolutionDraftRequest,
)
from relay_teams.memory.repository import (
    MemoryBankRepository,
    generate_memory_evolution_draft_id,
)
from relay_teams.skills.clawhub_models import ClawHubSkillWriteRequest
from relay_teams.skills.clawhub_skill_service import ClawHubSkillService

LOGGER = get_logger(__name__)
_MAX_MEMORY_METADATA_KEYS = 20


class MemoryEvolutionService:
    def __init__(
        self,
        *,
        repository: MemoryBankRepository,
        skill_service: ClawHubSkillService,
    ) -> None:
        self._repo = repository
        self._skill_service = skill_service

    async def create_draft_async(
        self, request: CreateMemoryEvolutionDraftRequest
    ) -> MemoryEvolutionDraft:
        workspace_id = request.workspace_id.strip()
        if not workspace_id:
            message = "workspace_id is required when creating a memory evolution draft"
            raise ValueError(message)
        request = request.model_copy(update={"workspace_id": workspace_id})
        source_entries = await self._load_active_source_entries_async(request)
        now = datetime.now(tz=timezone.utc)
        description = request.description.strip() or _default_description(
            target=request.target,
            runtime_name=request.runtime_name,
            entries=source_entries,
        )
        instructions = _render_skill_instructions(
            target=request.target,
            runtime_name=request.runtime_name,
            description=description,
            objective=request.objective,
            entries=source_entries,
        )
        draft = MemoryEvolutionDraft(
            draft_id=generate_memory_evolution_draft_id(),
            workspace_id=workspace_id,
            source_memory_ids=request.source_memory_ids,
            target=request.target,
            status=MemoryEvolutionStatus.DRAFT,
            skill_id=request.skill_id,
            runtime_name=request.runtime_name,
            description=description,
            instructions=instructions,
            created_at=now,
            updated_at=now,
        )
        return await self._repo.create_evolution_draft_async(draft=draft)

    async def get_draft_async(
        self, workspace_id: str, draft_id: str
    ) -> MemoryEvolutionDraft | None:
        draft = await self._repo.get_evolution_draft_async(draft_id)
        if draft is None or draft.workspace_id != workspace_id:
            return None
        return draft

    async def list_drafts_async(
        self, query: MemoryEvolutionDraftQuery
    ) -> MemoryEvolutionDraftQueryResult:
        return await self._repo.list_evolution_drafts_async(query)

    async def apply_draft_async(
        self,
        workspace_id: str,
        draft_id: str,
        request: ApplyMemoryEvolutionDraftRequest,
    ) -> MemoryEvolutionDraft | None:
        draft = await self.get_draft_async(workspace_id, draft_id)
        if draft is None:
            return None
        if draft.status != MemoryEvolutionStatus.DRAFT:
            message = f"Memory evolution draft is not applicable: {draft.status.value}"
            raise ValueError(message)

        now = datetime.now(tz=timezone.utc)
        skill_id = (request.skill_id or draft.skill_id).strip()
        runtime_name = (request.runtime_name or draft.runtime_name).strip()
        description = (
            draft.description if request.description is None else request.description
        ).strip()
        instructions = (
            draft.instructions if request.instructions is None else request.instructions
        ).strip()
        if not description:
            description = _default_description(
                target=draft.target,
                runtime_name=runtime_name,
                entries=await self._load_source_entries_by_id_async(draft),
            )
        if not instructions:
            message = (
                "instructions must be non-empty when applying a memory evolution draft"
            )
            raise ValueError(message)

        saved = await asyncio.to_thread(
            self._skill_service.save_skill,
            skill_id,
            ClawHubSkillWriteRequest(
                runtime_name=runtime_name,
                description=description,
                instructions=instructions,
                files=(),
            ),
        )
        applied_ref = saved.ref or runtime_name
        applied = draft.model_copy(
            update={
                "status": MemoryEvolutionStatus.APPLIED,
                "skill_id": skill_id,
                "runtime_name": runtime_name,
                "description": description,
                "instructions": instructions,
                "applied_skill_ref": applied_ref,
                "updated_at": now,
                "applied_at": now,
            }
        )
        updated = await self._repo.update_evolution_draft_async(draft=applied)
        await self._mark_source_memories_applied_async(updated)
        LOGGER.info(
            "Applied Memory Bank evolution draft %s as skill %s",
            updated.draft_id,
            applied_ref,
        )
        return updated

    async def reject_draft_async(
        self,
        workspace_id: str,
        draft_id: str,
        request: RejectMemoryEvolutionDraftRequest,
    ) -> MemoryEvolutionDraft | None:
        draft = await self.get_draft_async(workspace_id, draft_id)
        if draft is None:
            return None
        if draft.status != MemoryEvolutionStatus.DRAFT:
            message = f"Memory evolution draft is not rejectable: {draft.status.value}"
            raise ValueError(message)
        now = datetime.now(tz=timezone.utc)
        rejected = draft.model_copy(
            update={
                "status": MemoryEvolutionStatus.REJECTED,
                "rejection_reason": request.reason.strip(),
                "updated_at": now,
                "rejected_at": now,
            }
        )
        return await self._repo.update_evolution_draft_async(draft=rejected)

    async def _load_active_source_entries_async(
        self, request: CreateMemoryEvolutionDraftRequest
    ) -> tuple[MemoryEntry, ...]:
        entries: list[MemoryEntry] = []
        for memory_id in request.source_memory_ids:
            entry = await self._repo.get_by_id_async(memory_id)
            if entry is None:
                message = f"Unknown source memory entry: {memory_id}"
                raise ValueError(message)
            if entry.workspace_id != request.workspace_id:
                message = (
                    f"Source memory entry belongs to a different workspace: {memory_id}"
                )
                raise ValueError(message)
            if entry.status != MemoryEntryStatus.ACTIVE:
                message = f"Source memory entry is not active: {memory_id}"
                raise ValueError(message)
            entries.append(entry)
        return tuple(entries)

    async def _load_source_entries_by_id_async(
        self, draft: MemoryEvolutionDraft
    ) -> tuple[MemoryEntry, ...]:
        entries: list[MemoryEntry] = []
        for memory_id in draft.source_memory_ids:
            entry = await self._repo.get_by_id_async(memory_id)
            if entry is not None and entry.workspace_id == draft.workspace_id:
                entries.append(entry)
        return tuple(entries)

    async def _mark_source_memories_applied_async(
        self, draft: MemoryEvolutionDraft
    ) -> None:
        for entry in await self._load_source_entries_by_id_async(draft):
            metadata = _with_evolution_metadata(entry.metadata, draft)
            updated_entry = entry.model_copy(
                update={
                    "metadata": metadata,
                    "version": entry.version + 1,
                    "updated_at": draft.updated_at,
                }
            )
            await self._repo.update_entry_async(entry.id, entry=updated_entry)


def _default_description(
    *,
    target: MemoryEvolutionTarget,
    runtime_name: str,
    entries: tuple[MemoryEntry, ...],
) -> str:
    if entries:
        title = entries[0].content.title.strip()
        if title:
            return f"{_target_label(target)} distilled from Memory Bank: {title}"
    return f"{_target_label(target)} distilled from Memory Bank for {runtime_name}"


def _render_skill_instructions(
    *,
    target: MemoryEvolutionTarget,
    runtime_name: str,
    description: str,
    objective: str,
    entries: tuple[MemoryEntry, ...],
) -> str:
    if target == MemoryEvolutionTarget.SOP_SKILL:
        return _render_sop_skill_instructions(
            runtime_name=runtime_name,
            description=description,
            objective=objective,
            entries=entries,
        )
    return _render_general_skill_instructions(
        runtime_name=runtime_name,
        description=description,
        objective=objective,
        entries=entries,
    )


def _render_general_skill_instructions(
    *,
    runtime_name: str,
    description: str,
    objective: str,
    entries: tuple[MemoryEntry, ...],
) -> str:
    purpose = objective.strip() or description.strip()
    return "\n".join(
        (
            f"# {runtime_name}",
            "",
            "## Purpose",
            purpose,
            "",
            "## Source Memory",
            _render_source_memory(entries),
            "",
            "## Operating Guidance",
            "- Use this skill when the task matches the source memory context.",
            "- Apply the captured constraints, decisions, and preferences directly.",
            "- Prefer current repository evidence when it conflicts with older memory.",
            "",
            "## Verification",
            "- Confirm the final result still satisfies the source memory constraints.",
            "- Record any new durable lesson back into Memory Bank.",
        )
    )


def _render_sop_skill_instructions(
    *,
    runtime_name: str,
    description: str,
    objective: str,
    entries: tuple[MemoryEntry, ...],
) -> str:
    purpose = objective.strip() or description.strip()
    return "\n".join(
        (
            f"# {runtime_name}",
            "",
            "## Purpose",
            purpose,
            "",
            "## Preconditions",
            "- Confirm the current task matches the source memory context.",
            "- Inspect current repository state before applying remembered guidance.",
            "- Treat source memory as guidance, not as a substitute for fresh evidence.",
            "",
            "## Procedure",
            "1. Read the relevant task, repository files, and current constraints.",
            "2. Compare current evidence with the source memory below.",
            "3. Apply the remembered SOP only where it remains compatible.",
            "4. Keep implementation changes scoped to the requested outcome.",
            "5. Update Memory Bank when the SOP needs correction or extension.",
            "",
            "## Source Memory",
            _render_source_memory(entries),
            "",
            "## Failure Modes",
            _render_failure_modes(entries),
            "",
            "## Verification",
            "- Run the smallest relevant checks for the affected behavior.",
            "- Verify docs, APIs, UI, or runtime prompts are refreshed when contracts change.",
            "- Report any source-memory conflicts in the final response.",
        )
    )


def _render_source_memory(entries: tuple[MemoryEntry, ...]) -> str:
    lines: list[str] = []
    for index, entry in enumerate(entries, start=1):
        lines.extend(
            (
                f"{index}. {entry.content.title}",
                f"   - ID: {entry.id}",
                f"   - Kind: {entry.kind.value}",
                f"   - Confidence: {entry.confidence_score:.2f}",
                f"   - Body: {_single_line(entry.content.body)}",
            )
        )
        if entry.content.context:
            lines.append(f"   - Context: {_single_line(entry.content.context)}")
        if entry.content.outcome:
            lines.append(f"   - Outcome: {_single_line(entry.content.outcome)}")
    return "\n".join(lines) if lines else "- No source memory entries were provided."


def _render_failure_modes(entries: tuple[MemoryEntry, ...]) -> str:
    failure_entries = [
        entry for entry in entries if entry.kind.value in {"failure_mode", "constraint"}
    ]
    if not failure_entries:
        return "- Do not apply stale memory without checking current repository state."
    return "\n".join(
        f"- {_single_line(entry.content.body)}" for entry in failure_entries
    )


def _single_line(value: str) -> str:
    return " ".join(value.strip().split())


def _target_label(target: MemoryEvolutionTarget) -> str:
    if target == MemoryEvolutionTarget.SOP_SKILL:
        return "SOP skill"
    return "Skill"


def _with_evolution_metadata(
    metadata: dict[str, str],
    draft: MemoryEvolutionDraft,
) -> dict[str, str]:
    updated = dict(metadata)
    updated["evolution_draft_id"] = draft.draft_id
    updated["evolution_skill_ref"] = draft.applied_skill_ref or draft.runtime_name
    while len(updated) > _MAX_MEMORY_METADATA_KEYS:
        removable = sorted(
            key
            for key in updated
            if key not in {"evolution_draft_id", "evolution_skill_ref"}
        )
        if not removable:
            break
        del updated[removable[0]]
    return updated
