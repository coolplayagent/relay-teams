# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Protocol

import pydantic

from relay_teams.logger import get_logger
from relay_teams.memory.models import (
    GlobalMemorySearchRequest,
    MemoryEntry,
    MemoryEntryStatus,
    MemoryQuery,
    MemorySearchRequest,
    MemoryTier,
)
from relay_teams.memory.service import MemoryBankService
from relay_teams.memory.skill_draft_models import (
    GenerateMemorySkillDraftsRequest,
    MemorySkillDraft,
    MemorySkillDraftApplyResult,
    MemorySkillDraftFile,
    MemorySkillDraftGenerationKind,
    MemorySkillDraftGenerationResult,
    MemorySkillDraftKind,
    MemorySkillDraftQuery,
    MemorySkillDraftQueryResult,
    MemorySkillDraftScopeKind,
    MemorySkillDraftStatus,
    UpdateMemorySkillDraftRequest,
    draft_to_summary,
)
from relay_teams.memory.skill_draft_repository import (
    MemorySkillDraftRepository,
    generate_memory_skill_draft_id,
)
from relay_teams.memory.skill_draft_validator import SkillDraftValidator
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.skills.clawhub_models import ClawHubSkillFile, ClawHubSkillWriteRequest
from relay_teams.skills.clawhub_skill_service import ClawHubSkillService

LOGGER = get_logger(__name__)


class LLMProviderResolver(Protocol):
    def __call__(self) -> LLMProvider | None:
        raise NotImplementedError


class _GeneratedMemorySkillDraft(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    draft_kind: MemorySkillDraftKind
    runtime_name: str
    description: str
    instructions: str
    source_memory_ids: tuple[str, ...] = ()
    files: tuple[MemorySkillDraftFile, ...] = ()


class _GeneratedMemorySkillDrafts(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    drafts: tuple[_GeneratedMemorySkillDraft, ...]


_SYSTEM_PROMPT = """\
You turn durable project memory into reusable Codex skills. Generate concise,
valid skill drafts only when multiple memories form reusable guidance. Do not
create one skill per memory entry. Prefer one integrated skill or SOP skill for
related workspace or cross-workspace patterns.

Return only valid JSON matching the requested schema."""

_RUNTIME_NAME_CLEANUP = re.compile(r"[^a-z0-9-]+")
_DUPLICATE_HYPHENS = re.compile(r"-+")


class MemorySkillSynthesisService:
    def __init__(
        self,
        *,
        draft_repository: MemorySkillDraftRepository,
        memory_bank_service: MemoryBankService,
        clawhub_skill_service: ClawHubSkillService,
        llm_provider_resolver: LLMProviderResolver,
        validator: SkillDraftValidator | None = None,
    ) -> None:
        self._draft_repo = draft_repository
        self._memory_bank = memory_bank_service
        self._clawhub_skill_service = clawhub_skill_service
        self._llm_provider_resolver = llm_provider_resolver
        self._validator = validator or SkillDraftValidator()

    async def generate_drafts_async(
        self, request: GenerateMemorySkillDraftsRequest
    ) -> MemorySkillDraftGenerationResult:
        if (
            request.scope_kind == MemorySkillDraftScopeKind.WORKSPACE
            and not _workspace_filter_for_request(request)
        ):
            return MemorySkillDraftGenerationResult(
                items=(),
                source_memory_count=0,
                error_message="workspace_id is required for workspace skill drafts",
            )
        try:
            source_entries = await self._load_source_entries_async(request)
        except ValueError as exc:
            return MemorySkillDraftGenerationResult(
                items=(),
                source_memory_count=0,
                error_message=str(exc),
            )
        if not source_entries:
            return MemorySkillDraftGenerationResult(
                items=(),
                source_memory_count=0,
                error_message="No eligible memory entries found",
            )

        provider = self._llm_provider_resolver()
        if provider is None:
            return MemorySkillDraftGenerationResult(
                items=(),
                source_memory_count=len(source_entries),
                error_message="No auxiliary LLM provider is configured",
            )

        try:
            generated = await self._generate_candidates_async(
                provider=provider,
                request=request,
                source_entries=tuple(source_entries),
            )
        except (ValueError, RuntimeError, pydantic.ValidationError) as exc:
            LOGGER.warning("memory skill draft generation failed: %s", exc)
            return MemorySkillDraftGenerationResult(
                items=(),
                source_memory_count=len(source_entries),
                error_message=str(exc),
            )

        candidates = generated.drafts[: request.max_drafts]
        if _looks_like_one_draft_per_memory(candidates, len(source_entries)):
            candidates = (
                _build_integrated_candidate(
                    request=request,
                    source_entries=tuple(source_entries),
                ),
            )

        drafts: list[MemorySkillDraft] = []
        for candidate in candidates:
            draft = self._draft_from_candidate(
                candidate=candidate,
                request=request,
                source_entries=tuple(source_entries),
            )
            drafts.append(await self._draft_repo.create_draft_async(draft))

        return MemorySkillDraftGenerationResult(
            items=tuple(draft_to_summary(draft) for draft in drafts),
            source_memory_count=len(source_entries),
        )

    async def list_drafts_async(
        self, query: MemorySkillDraftQuery
    ) -> MemorySkillDraftQueryResult:
        return await self._draft_repo.query_drafts_async(query)

    async def get_draft_async(self, draft_id: str) -> MemorySkillDraft | None:
        return await self._draft_repo.get_draft_async(draft_id)

    async def update_draft_async(
        self,
        draft_id: str,
        request: UpdateMemorySkillDraftRequest,
    ) -> MemorySkillDraft | None:
        draft = await self._draft_repo.get_draft_async(draft_id)
        if draft is None:
            return None
        if draft.status == MemorySkillDraftStatus.APPLIED:
            raise ValueError("Applied skill drafts cannot be edited")
        if draft.status == MemorySkillDraftStatus.APPLYING:
            raise ValueError("Applying skill drafts cannot be edited")
        now = datetime.now(tz=timezone.utc)
        update_data: dict[str, object] = {"updated_at": now}
        content_changed = False
        if request.runtime_name is not None:
            runtime_name = request.runtime_name.strip()
            if not runtime_name:
                raise ValueError("runtime_name must be non-empty")
            update_data["runtime_name"] = runtime_name
            content_changed = content_changed or runtime_name != draft.runtime_name
        if request.description is not None:
            description = request.description.strip()
            update_data["description"] = description
            content_changed = content_changed or description != draft.description
        if request.instructions is not None:
            instructions = request.instructions.rstrip()
            update_data["instructions"] = instructions
            content_changed = content_changed or instructions != draft.instructions
        if request.files is not None:
            update_data["files"] = request.files
            content_changed = content_changed or request.files != draft.files
        if content_changed:
            update_data["validation_messages"] = ()
            update_data["validated_at"] = None
            update_data["status"] = MemorySkillDraftStatus.DRAFT
        if request.status is not None:
            if request.status in (
                MemorySkillDraftStatus.APPLYING,
                MemorySkillDraftStatus.APPLIED,
            ):
                raise ValueError("Use the apply endpoint to apply a skill draft")
            if content_changed and request.status != MemorySkillDraftStatus.DRAFT:
                raise ValueError("Content edits must leave the draft in draft status")
            update_data["status"] = request.status
        updated = draft.model_copy(update=update_data)
        saved = await self._draft_repo.update_draft_async(
            updated,
            expected_status=draft.status,
        )
        if saved is None:
            raise ValueError("Memory skill draft state changed; retry the request")
        return saved

    async def validate_draft_async(self, draft_id: str) -> MemorySkillDraft | None:
        draft = await self._draft_repo.get_draft_async(draft_id)
        if draft is None:
            return None
        if draft.status == MemorySkillDraftStatus.APPLIED:
            return draft
        if draft.status == MemorySkillDraftStatus.APPLYING:
            raise ValueError("Applying skill drafts cannot be validated")
        now = datetime.now(tz=timezone.utc)
        validated = self._validator.validate(draft)
        status = (
            MemorySkillDraftStatus.DRAFT
            if self._validator.has_errors(validated)
            else MemorySkillDraftStatus.VALIDATED
        )
        updated = validated.model_copy(
            update={
                "status": status,
                "updated_at": now,
                "validated_at": now
                if status == MemorySkillDraftStatus.VALIDATED
                else None,
            }
        )
        saved = await self._draft_repo.update_draft_async(
            updated,
            expected_status=draft.status,
        )
        if saved is None:
            raise ValueError("Memory skill draft state changed; retry the request")
        return saved

    async def apply_draft_async(self, draft_id: str) -> MemorySkillDraftApplyResult:
        draft = await self._draft_repo.get_draft_async(draft_id)
        if draft is None:
            raise KeyError(f"Unknown memory skill draft: {draft_id}")
        if draft.status != MemorySkillDraftStatus.VALIDATED:
            raise ValueError("Only validated skill drafts can be applied")
        checked = self._validator.validate(draft)
        if self._validator.has_errors(checked):
            saved = await self._draft_repo.update_draft_async(
                checked.model_copy(
                    update={
                        "status": MemorySkillDraftStatus.DRAFT,
                        "updated_at": datetime.now(tz=timezone.utc),
                        "validated_at": None,
                    }
                ),
                expected_status=draft.status,
            )
            if saved is None:
                raise ValueError("Only validated skill drafts can be applied")
            raise ValueError("Skill draft validation failed")

        claimed = await self._draft_repo.claim_draft_apply_async(
            draft_id=draft.id,
            updated_at=datetime.now(tz=timezone.utc),
        )
        if claimed is None:
            raise ValueError("Only validated skill drafts can be applied")
        draft = claimed

        write_request = ClawHubSkillWriteRequest(
            runtime_name=draft.runtime_name,
            description=draft.description,
            instructions=draft.instructions,
            files=tuple(
                ClawHubSkillFile(
                    path=file.path,
                    content=file.content,
                    encoding=file.encoding,
                )
                for file in draft.files
            ),
        )
        try:
            detail = await asyncio.to_thread(
                self._clawhub_skill_service.save_skill,
                draft.runtime_name,
                write_request,
            )
        except asyncio.CancelledError:
            LOGGER.warning(
                "Memory skill draft apply was cancelled while skill write may still "
                "be running: %s",
                draft.id,
            )
            raise
        except Exception:
            LOGGER.warning(
                "Memory skill draft apply failed while skill write may have "
                "partially completed; leaving draft applying: %s",
                draft.id,
                exc_info=True,
            )
            raise
        now = datetime.now(tz=timezone.utc)
        applied = draft.model_copy(
            update={
                "status": MemorySkillDraftStatus.APPLIED,
                "updated_at": now,
                "applied_at": now,
                "applied_skill_id": detail.skill_id,
                "applied_ref": detail.ref or detail.runtime_name,
            }
        )
        saved = await self._draft_repo.complete_draft_apply_async(draft=applied)
        if saved is None:
            raise RuntimeError("Failed to complete skill draft apply")
        return MemorySkillDraftApplyResult(
            draft=saved,
            skill_id=detail.skill_id,
            ref=detail.ref or detail.runtime_name or draft.runtime_name,
        )

    async def _load_source_entries_async(
        self, request: GenerateMemorySkillDraftsRequest
    ) -> list[MemoryEntry]:
        if request.source_memory_ids:
            explicit_entries: list[MemoryEntry] = []
            workspace_ids = _workspace_filter_for_request(request)
            for memory_id in request.source_memory_ids:
                entry = await self._memory_bank.get_entry_async(memory_id)
                if entry is not None and entry.status == MemoryEntryStatus.ACTIVE:
                    _validate_explicit_source_entry_scope(
                        entry=entry,
                        request=request,
                        workspace_ids=workspace_ids,
                    )
                    explicit_entries.append(entry)
            return explicit_entries[: request.limit]

        if request.text_query.strip():
            return await self._search_source_entries_async(request)

        entries: list[MemoryEntry] = []
        workspace_ids = _workspace_filter_for_request(request)
        for workspace_id in workspace_ids or (None,):
            for tier in (MemoryTier.PERSISTENT, MemoryTier.MEDIUM_TERM):
                result = await self._memory_bank.list_entries_async(
                    MemoryQuery(
                        workspace_id=workspace_id,
                        tier=tier,
                        status=MemoryEntryStatus.ACTIVE,
                        min_confidence=request.min_confidence,
                        limit=request.limit,
                    )
                )
                for summary in result.items:
                    entry = await self._memory_bank.get_entry_async(summary.id)
                    if entry is not None:
                        entries.append(entry)
                    if len(entries) >= request.limit:
                        return entries
        return entries

    async def _search_source_entries_async(
        self, request: GenerateMemorySkillDraftsRequest
    ) -> list[MemoryEntry]:
        workspace_ids = _workspace_filter_for_request(request)
        hits = []
        if request.scope_kind == MemorySkillDraftScopeKind.WORKSPACE:
            workspace_id = (request.workspace_id or "").strip()
            if not workspace_id:
                return []
            result = await self._memory_bank.search_async(
                MemorySearchRequest(
                    workspace_id=workspace_id,
                    text_query=request.text_query,
                    status=MemoryEntryStatus.ACTIVE,
                    min_confidence=request.min_confidence,
                    limit=request.limit,
                )
            )
            hits = list(result.items)
        else:
            if workspace_ids:
                for workspace_id in workspace_ids:
                    result = await self._memory_bank.search_global_async(
                        GlobalMemorySearchRequest(
                            text_query=request.text_query,
                            workspace_id=workspace_id,
                            status=MemoryEntryStatus.ACTIVE,
                            min_confidence=request.min_confidence,
                            limit=request.limit,
                        )
                    )
                    hits.extend(result.items)
            else:
                result = await self._memory_bank.search_global_async(
                    GlobalMemorySearchRequest(
                        text_query=request.text_query,
                        workspace_id=None,
                        status=MemoryEntryStatus.ACTIVE,
                        min_confidence=request.min_confidence,
                        limit=request.limit,
                    )
                )
                hits = list(result.items)
        entries: list[MemoryEntry] = []
        seen_entry_ids: set[str] = set()
        for hit in hits:
            if hit.entry.id in seen_entry_ids:
                continue
            seen_entry_ids.add(hit.entry.id)
            entry = await self._memory_bank.get_entry_async(hit.entry.id)
            if entry is not None:
                entries.append(entry)
        if (
            request.scope_kind == MemorySkillDraftScopeKind.CROSS_WORKSPACE
            and len(workspace_ids) > 1
        ):
            allowed = set(workspace_ids)
            entries = [entry for entry in entries if entry.workspace_id in allowed]
        return entries[: request.limit]

    @staticmethod
    async def _generate_candidates_async(
        *,
        provider: LLMProvider,
        request: GenerateMemorySkillDraftsRequest,
        source_entries: tuple[MemoryEntry, ...],
    ) -> _GeneratedMemorySkillDrafts:
        llm_request = LLMRequest(
            run_id="memory-skill-draft-generation",
            trace_id="memory-skill-draft-generation",
            task_id="",
            session_id="",
            workspace_id=request.workspace_id or "",
            instance_id="memory-skill-synthesis",
            role_id="memory-skill-synthesis",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_generation_prompt(request, source_entries),
        )
        raw = await provider.generate(llm_request)
        clean = _strip_json_code_fences(raw)
        return _GeneratedMemorySkillDrafts.model_validate_json(clean)

    @staticmethod
    def _draft_from_candidate(
        *,
        candidate: _GeneratedMemorySkillDraft,
        request: GenerateMemorySkillDraftsRequest,
        source_entries: tuple[MemoryEntry, ...],
    ) -> MemorySkillDraft:
        now = datetime.now(tz=timezone.utc)
        source_ids = _candidate_source_ids(candidate, source_entries)
        workspace_ids = tuple(sorted({entry.workspace_id for entry in source_entries}))
        draft_kind = _resolve_candidate_kind(candidate.draft_kind, request.draft_kind)
        workspace_id: str | None = None
        if request.scope_kind == MemorySkillDraftScopeKind.WORKSPACE:
            workspace_id = (request.workspace_id or "").strip()
        return MemorySkillDraft(
            id=generate_memory_skill_draft_id(),
            status=MemorySkillDraftStatus.DRAFT,
            scope_kind=request.scope_kind,
            workspace_id=workspace_id,
            workspace_ids=workspace_ids,
            source_memory_ids=source_ids,
            draft_kind=draft_kind,
            runtime_name=_normalize_skill_name(candidate.runtime_name, draft_kind),
            description=candidate.description.strip(),
            instructions=candidate.instructions.rstrip(),
            files=candidate.files,
            created_at=now,
            updated_at=now,
        )


def _workspace_filter_for_request(
    request: GenerateMemorySkillDraftsRequest,
) -> tuple[str, ...]:
    if request.scope_kind == MemorySkillDraftScopeKind.WORKSPACE:
        workspace_id = (request.workspace_id or "").strip()
        return (workspace_id,) if workspace_id else ()
    return request.workspace_ids


def _validate_explicit_source_entry_scope(
    *,
    entry: MemoryEntry,
    request: GenerateMemorySkillDraftsRequest,
    workspace_ids: tuple[str, ...],
) -> None:
    if request.scope_kind == MemorySkillDraftScopeKind.WORKSPACE:
        workspace_id = workspace_ids[0] if workspace_ids else ""
        if not workspace_id or entry.workspace_id != workspace_id:
            message = (
                "Explicit source memory entries must belong to the workspace scope"
            )
            raise ValueError(message)
        return
    if workspace_ids and entry.workspace_id not in set(workspace_ids):
        message = "Explicit source memory entries must match workspace_ids"
        raise ValueError(message)


def _build_generation_prompt(
    request: GenerateMemorySkillDraftsRequest,
    entries: tuple[MemoryEntry, ...],
) -> str:
    schema = {
        "type": "object",
        "properties": {
            "drafts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "draft_kind": {"enum": ["skill", "sop_skill"]},
                        "runtime_name": {"type": "string"},
                        "description": {"type": "string"},
                        "instructions": {"type": "string"},
                        "source_memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "files": {"type": "array"},
                    },
                    "required": [
                        "draft_kind",
                        "runtime_name",
                        "description",
                        "instructions",
                        "source_memory_ids",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["drafts"],
        "additionalProperties": False,
    }
    target_kind = request.draft_kind.value
    memory_lines = "\n\n".join(_format_memory_for_prompt(entry) for entry in entries)
    return (
        "Create memory-derived skill drafts.\n\n"
        f"Scope: {request.scope_kind.value}\n"
        f"Requested kind: {target_kind}\n"
        "Rules:\n"
        "- Combine related memory entries into workspace or cross-workspace skills.\n"
        "- Do not create one skill per memory entry.\n"
        "- Use lowercase hyphen runtime names under 64 characters.\n"
        "- Use only essential skill instructions. Do not create README files.\n"
        "- Use sop_skill for repeatable operating procedures.\n\n"
        "Memory entries:\n"
        f"{memory_lines}\n\n"
        "Respond with JSON matching this schema:\n"
        f"{json.dumps(schema, indent=2)}"
    )


def _format_memory_for_prompt(entry: MemoryEntry) -> str:
    body = entry.content.body.strip()
    if len(body) > 1400:
        body = body[:1400].rstrip() + "..."
    return (
        f"ID: {entry.id}\n"
        f"Workspace: {entry.workspace_id}\n"
        f"Kind: {entry.kind.value}\n"
        f"Title: {entry.content.title}\n"
        f"Body: {body}\n"
        f"Context: {entry.content.context}\n"
        f"Outcome: {entry.content.outcome}"
    )


def _strip_json_code_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _looks_like_one_draft_per_memory(
    candidates: tuple[_GeneratedMemorySkillDraft, ...],
    source_count: int,
) -> bool:
    if source_count <= 1 or len(candidates) < source_count:
        return False
    return all(len(candidate.source_memory_ids) <= 1 for candidate in candidates)


def _build_integrated_candidate(
    *,
    request: GenerateMemorySkillDraftsRequest,
    source_entries: tuple[MemoryEntry, ...],
) -> _GeneratedMemorySkillDraft:
    draft_kind = (
        MemorySkillDraftKind.SOP_SKILL
        if request.draft_kind == MemorySkillDraftGenerationKind.SOP_SKILL
        else MemorySkillDraftKind.SKILL
    )
    workspace_token = request.workspace_id or "cross-workspace"
    name = _normalize_skill_name(f"{workspace_token}-memory-skill", draft_kind)
    if draft_kind == MemorySkillDraftKind.SOP_SKILL and not name.endswith("-sop"):
        name = f"{name}-sop"
    bullets = "\n".join(
        f"- {entry.content.title}: {entry.content.body[:500].strip()}"
        for entry in source_entries[:20]
    )
    instructions = (
        "# Memory-Derived Guidance\n\n"
        "Use this skill when a task matches the workspace practices, decisions, "
        "constraints, or procedures captured in the source memories.\n\n"
        "## Apply\n\n"
        f"{bullets}\n"
    )
    return _GeneratedMemorySkillDraft(
        draft_kind=draft_kind,
        runtime_name=name,
        description="Apply consolidated workspace memory as reusable task guidance.",
        instructions=instructions,
        source_memory_ids=tuple(entry.id for entry in source_entries),
    )


def _candidate_source_ids(
    candidate: _GeneratedMemorySkillDraft,
    source_entries: tuple[MemoryEntry, ...],
) -> tuple[str, ...]:
    allowed = {entry.id for entry in source_entries}
    selected = tuple(
        memory_id for memory_id in candidate.source_memory_ids if memory_id in allowed
    )
    if selected:
        return selected
    return tuple(entry.id for entry in source_entries)


def _resolve_candidate_kind(
    candidate_kind: MemorySkillDraftKind,
    requested_kind: MemorySkillDraftGenerationKind,
) -> MemorySkillDraftKind:
    if requested_kind == MemorySkillDraftGenerationKind.SKILL:
        return MemorySkillDraftKind.SKILL
    if requested_kind == MemorySkillDraftGenerationKind.SOP_SKILL:
        return MemorySkillDraftKind.SOP_SKILL
    return candidate_kind


def _normalize_skill_name(value: str, draft_kind: MemorySkillDraftKind) -> str:
    normalized = value.strip().lower().replace("_", "-")
    normalized = _RUNTIME_NAME_CLEANUP.sub("-", normalized)
    normalized = _DUPLICATE_HYPHENS.sub("-", normalized).strip("-")
    if not normalized:
        normalized = "memory-skill"
    normalized = normalized[:64].strip("-") or "memory-skill"
    if draft_kind == MemorySkillDraftKind.SOP_SKILL and not normalized.endswith("-sop"):
        suffix = "-sop"
        normalized = f"{normalized[: 64 - len(suffix)].strip('-')}{suffix}"
    return normalized
