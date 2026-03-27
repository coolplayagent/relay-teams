# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from agent_teams.agents.execution.user_prompts import (
    UserPromptBuildInput,
    UserPromptSkillCandidate,
    build_user_prompt,
)
from agent_teams.logger import get_logger, log_event
from agent_teams.retrieval import (
    RetrievalDocument,
    RetrievalQuery,
    RetrievalScopeConfig,
    RetrievalScopeKind,
    RetrievalService,
    RetrievalStats,
)
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.sessions.runs.run_models import RuntimePromptConversationContext
from agent_teams.skills.skill_models import Skill, SkillInstructionEntry, SkillScope
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.skills.skill_routing_models import (
    SkillPromptResult,
    SkillRouteCandidate,
    SkillRoutingContext,
    SkillRoutingDiagnostics,
    SkillRoutingFallbackReason,
    SkillRoutingMode,
    SkillRoutingResult,
)
from agent_teams.trace import trace_span

LOGGER = get_logger(__name__)
_SKILL_SCOPE_CONFIG = RetrievalScopeConfig(
    scope_kind=RetrievalScopeKind.SKILL,
    scope_id="skills",
)
_KEYWORD_SPLIT_PATTERN = re.compile(r"[^\w]+", re.UNICODE)
_DEFAULT_TOP_K = 8
_DEFAULT_SEARCH_LIMIT = 24


class SkillIndexService:
    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
        scope_config: RetrievalScopeConfig = _SKILL_SCOPE_CONFIG,
    ) -> None:
        self._retrieval_service = retrieval_service
        self._scope_config = scope_config

    @property
    def scope_config(self) -> RetrievalScopeConfig:
        return self._scope_config

    def replace_documents(self, *, skill_registry: SkillRegistry) -> RetrievalStats:
        documents = self.build_documents(skill_registry=skill_registry)
        return self._retrieval_service.replace_scope(
            config=self._scope_config,
            documents=documents,
        )

    def build_documents(
        self,
        *,
        skill_registry: SkillRegistry,
    ) -> tuple[RetrievalDocument, ...]:
        return tuple(
            _build_skill_document(skill=skill, scope_config=self._scope_config)
            for skill in _preferred_skills(skill_registry.list_skill_definitions())
        )


class SkillRoutingService:
    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
        scope_config: RetrievalScopeConfig = _SKILL_SCOPE_CONFIG,
        top_k: int = _DEFAULT_TOP_K,
        search_limit: int = _DEFAULT_SEARCH_LIMIT,
    ) -> None:
        self._retrieval_service = retrieval_service
        self._scope_config = scope_config
        self._top_k = top_k
        self._search_limit = search_limit

    def route(
        self,
        *,
        authorized_skills: tuple[str, ...],
        context: SkillRoutingContext,
    ) -> SkillRoutingResult:
        query_text = build_skill_routing_query_text(context)
        authorized_count = len(authorized_skills)
        if authorized_count <= self._top_k:
            return SkillRoutingResult(
                authorized_skills=authorized_skills,
                visible_skills=authorized_skills,
                diagnostics=SkillRoutingDiagnostics(
                    mode=SkillRoutingMode.PASSTHROUGH,
                    query_text=query_text,
                    authorized_count=authorized_count,
                    visible_skills=authorized_skills,
                ),
            )
        if not query_text:
            return self._fallback_result(
                authorized_skills=authorized_skills,
                query_text=query_text,
                reason=SkillRoutingFallbackReason.EMPTY_QUERY,
            )

        try:
            hits = self._retrieval_service.search(
                query=RetrievalQuery(
                    scope_kind=self._scope_config.scope_kind,
                    scope_id=self._scope_config.scope_id,
                    text=query_text,
                    limit=self._search_limit,
                )
            )
        except Exception:
            log_event(
                LOGGER,
                logging.WARNING,
                event="skills.routing.search_failed",
                message="Skill routing search failed; falling back to authorized skills",
                payload={
                    "scope_kind": self._scope_config.scope_kind.value,
                    "scope_id": self._scope_config.scope_id,
                },
            )
            return self._fallback_result(
                authorized_skills=authorized_skills,
                query_text=query_text,
                reason=SkillRoutingFallbackReason.SEARCH_FAILED,
            )

        authorized_set = set(authorized_skills)
        filtered_hits = tuple(hit for hit in hits if hit.document_id in authorized_set)
        if not filtered_hits:
            return self._fallback_result(
                authorized_skills=authorized_skills,
                query_text=query_text,
                reason=SkillRoutingFallbackReason.NO_HITS,
            )

        visible_skills = _select_visible_skills(
            authorized_skills=authorized_skills,
            hit_document_ids=tuple(hit.document_id for hit in filtered_hits),
            top_k=self._top_k,
        )
        candidates = tuple(
            SkillRouteCandidate(
                skill_name=hit.document_id,
                score=hit.score,
                rank=hit.rank,
                snippet=hit.snippet,
            )
            for hit in filtered_hits
        )
        return SkillRoutingResult(
            authorized_skills=authorized_skills,
            visible_skills=visible_skills,
            diagnostics=SkillRoutingDiagnostics(
                mode=SkillRoutingMode.SEARCH,
                query_text=query_text,
                authorized_count=authorized_count,
                visible_skills=visible_skills,
                candidates=candidates,
            ),
        )

    def _fallback_result(
        self,
        *,
        authorized_skills: tuple[str, ...],
        query_text: str,
        reason: SkillRoutingFallbackReason,
    ) -> SkillRoutingResult:
        return SkillRoutingResult(
            authorized_skills=authorized_skills,
            visible_skills=authorized_skills,
            diagnostics=SkillRoutingDiagnostics(
                mode=SkillRoutingMode.FALLBACK,
                query_text=query_text,
                authorized_count=len(authorized_skills),
                visible_skills=authorized_skills,
                fallback_reason=reason,
            ),
        )


class SkillRuntimeService:
    def __init__(
        self,
        *,
        skill_registry: SkillRegistry,
        retrieval_service: RetrievalService,
        top_k: int = _DEFAULT_TOP_K,
        search_limit: int = _DEFAULT_SEARCH_LIMIT,
    ) -> None:
        self._skill_registry = skill_registry
        self._index_service = SkillIndexService(retrieval_service=retrieval_service)
        self._routing_service = SkillRoutingService(
            retrieval_service=retrieval_service,
            top_k=top_k,
            search_limit=search_limit,
        )

    def rebuild_index(self) -> RetrievalStats:
        return self._index_service.replace_documents(
            skill_registry=self._skill_registry
        )

    def prepare_prompt(
        self,
        *,
        role: RoleDefinition,
        objective: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        conversation_context: RuntimePromptConversationContext | None = None,
        orchestration_prompt: str = "",
        skill_names: tuple[str, ...] | None = None,
        consumer: str,
    ) -> SkillPromptResult:
        authorized_skills = self._resolve_authorized_skills(
            skill_names=role.skills if skill_names is None else skill_names,
            consumer=consumer,
        )
        routing = self._route_for_authorized_skills(
            role=role,
            objective=objective,
            shared_state_snapshot=shared_state_snapshot,
            conversation_context=conversation_context,
            orchestration_prompt=orchestration_prompt,
            authorized_skills=authorized_skills,
        )
        authorized_skill_map = _skill_map_by_name(authorized_skills)
        instruction_entries = self._instruction_entries_for_names(
            skill_names=routing.authorized_skills,
            skill_map=authorized_skill_map,
        )
        if routing.diagnostics.mode == SkillRoutingMode.PASSTHROUGH:
            return SkillPromptResult(
                user_prompt=build_user_prompt(
                    UserPromptBuildInput(objective=objective.strip())
                ),
                system_prompt_skill_instructions=instruction_entries,
                routing=routing,
            )

        resolved_objective = objective.strip()
        if not resolved_objective:
            return SkillPromptResult(
                user_prompt="",
                system_prompt_skill_instructions=(),
                routing=routing,
            )
        visible_entries = self._instruction_entries_for_names(
            skill_names=routing.visible_skills,
            skill_map=authorized_skill_map,
        )
        user_prompt = build_user_prompt(
            UserPromptBuildInput(
                objective=resolved_objective,
                skill_candidates=tuple(
                    UserPromptSkillCandidate(
                        name=entry.name,
                        description=entry.description,
                    )
                    for entry in visible_entries
                ),
            )
        )
        return SkillPromptResult(
            user_prompt=user_prompt,
            system_prompt_skill_instructions=(),
            routing=routing,
        )

    def route_for_role(
        self,
        *,
        role: RoleDefinition,
        objective: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        conversation_context: RuntimePromptConversationContext | None,
        orchestration_prompt: str = "",
        skill_names: tuple[str, ...] | None = None,
        consumer: str,
    ) -> SkillRoutingResult:
        authorized_skills = self._resolve_authorized_skills(
            skill_names=role.skills if skill_names is None else skill_names,
            consumer=consumer,
        )
        return self._route_for_authorized_skills(
            role=role,
            objective=objective,
            shared_state_snapshot=shared_state_snapshot,
            conversation_context=conversation_context,
            orchestration_prompt=orchestration_prompt,
            authorized_skills=authorized_skills,
        )

    def _route_for_authorized_skills(
        self,
        *,
        role: RoleDefinition,
        objective: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        conversation_context: RuntimePromptConversationContext | None,
        orchestration_prompt: str,
        authorized_skills: tuple[Skill, ...],
    ) -> SkillRoutingResult:
        authorized_skill_names = tuple(
            skill.metadata.name for skill in authorized_skills
        )
        with trace_span(
            LOGGER,
            component="skills.runtime",
            operation="route_for_role",
            attributes={
                "role_id": role.role_id,
                "authorized_count": len(authorized_skill_names),
            },
        ):
            result = self._routing_service.route(
                authorized_skills=authorized_skill_names,
                context=SkillRoutingContext(
                    objective=objective.strip(),
                    role_name=role.name,
                    role_description=role.description,
                    shared_state_snapshot=shared_state_snapshot,
                    conversation_context=conversation_context,
                    orchestration_prompt=orchestration_prompt.strip(),
                ),
            )
        log_event(
            LOGGER,
            logging.DEBUG,
            event="skills.routing.decision",
            message="Computed skill routing decision",
            payload={
                "role_id": role.role_id,
                "mode": result.diagnostics.mode.value,
                "authorized_skills": list(result.authorized_skills),
                "visible_skills": list(result.visible_skills),
                "candidate_skills": [
                    candidate.skill_name for candidate in result.diagnostics.candidates
                ],
                "fallback_reason": (
                    None
                    if result.diagnostics.fallback_reason is None
                    else result.diagnostics.fallback_reason.value
                ),
            },
        )
        return result

    def _resolve_authorized_skills(
        self,
        *,
        skill_names: tuple[str, ...],
        consumer: str,
    ) -> tuple[Skill, ...]:
        resolved_refs = self._skill_registry.resolve_known(
            skill_names,
            strict=False,
            consumer=consumer,
        )
        ordered_names: list[str] = []
        preferred_by_name: dict[str, Skill] = {}
        for ref in resolved_refs:
            skill = self._skill_registry.get_skill_definition(ref)
            if skill is None:
                continue
            name = skill.metadata.name
            if name not in preferred_by_name:
                ordered_names.append(name)
                preferred_by_name[name] = skill
                continue
            current = preferred_by_name[name]
            if _skill_display_sort_key(skill) < _skill_display_sort_key(current):
                preferred_by_name[name] = skill
        return tuple(preferred_by_name[name] for name in ordered_names)

    def _instruction_entries_for_names(
        self,
        *,
        skill_names: tuple[str, ...],
        skill_map: dict[str, Skill],
    ) -> tuple[SkillInstructionEntry, ...]:
        entries: list[SkillInstructionEntry] = []
        for name in skill_names:
            skill = skill_map.get(name)
            if skill is None:
                continue
            description = skill.metadata.description.strip()
            if not description:
                continue
            entries.append(
                SkillInstructionEntry(
                    name=skill.metadata.name,
                    description=description,
                )
            )
        return tuple(entries)


def build_skill_routing_query_text(context: SkillRoutingContext) -> str:
    sections: list[str] = []
    if context.objective.strip():
        sections.append(f"Objective: {context.objective.strip()}")
    role_section_parts = [context.role_name.strip()]
    if context.role_description.strip():
        role_section_parts.append(context.role_description.strip())
    role_section = " - ".join(part for part in role_section_parts if part)
    if role_section:
        sections.append(f"Role: {role_section}")
    if context.shared_state_snapshot:
        shared_state_lines = [
            f"- {key}: {value}"
            for key, value in context.shared_state_snapshot
            if key.strip() and value.strip()
        ]
        if shared_state_lines:
            sections.append("Shared State:\n" + "\n".join(shared_state_lines))
    conversation_lines = _conversation_context_lines(context=context)
    if conversation_lines:
        sections.append("Conversation Context:\n" + "\n".join(conversation_lines))
    if context.orchestration_prompt.strip():
        sections.append(f"Orchestration Prompt: {context.orchestration_prompt.strip()}")
    return "\n\n".join(section for section in sections if section.strip())


def _build_skill_document(
    *,
    skill: Skill,
    scope_config: RetrievalScopeConfig,
) -> RetrievalDocument:
    metadata = skill.metadata
    return RetrievalDocument(
        scope_kind=scope_config.scope_kind,
        scope_id=scope_config.scope_id,
        document_id=metadata.name,
        title=metadata.name,
        body=_build_skill_document_body(skill),
        keywords=_build_skill_keywords(skill),
    )


def _build_skill_document_body(skill: Skill) -> str:
    metadata = skill.metadata
    sections: list[str] = []
    if metadata.description.strip():
        sections.append("Description\n" + metadata.description.strip())
    if metadata.instructions.strip():
        sections.append("Instructions\n" + metadata.instructions.strip())
    script_lines = [
        f"- {script.name}: {script.description.strip()}"
        for script in metadata.scripts.values()
        if script.description.strip()
    ]
    if script_lines:
        sections.append("Scripts\n" + "\n".join(script_lines))
    resource_lines = [
        f"- {resource.name}: {resource.description.strip()}"
        for resource in metadata.resources.values()
        if resource.description.strip()
    ]
    if resource_lines:
        sections.append("Resources\n" + "\n".join(resource_lines))
    return "\n\n".join(sections)


def _build_skill_keywords(skill: Skill) -> tuple[str, ...]:
    seen: set[str] = set()
    keywords: list[str] = []
    raw_values: list[str] = [
        skill.metadata.name,
        skill.metadata.description,
        skill.scope.value,
        *(script.name for script in skill.metadata.scripts.values()),
        *(resource.name for resource in skill.metadata.resources.values()),
    ]
    for raw_value in raw_values:
        for token in _normalize_keyword_tokens(raw_value):
            if token in seen:
                continue
            seen.add(token)
            keywords.append(token)
    return tuple(keywords)


def _normalize_keyword_tokens(value: str) -> tuple[str, ...]:
    tokens = [
        token.casefold()
        for token in _KEYWORD_SPLIT_PATTERN.split(value)
        if token and token.strip()
    ]
    return tuple(tokens)


def _conversation_context_lines(
    *,
    context: SkillRoutingContext,
) -> tuple[str, ...]:
    prompt_context = context.conversation_context
    if prompt_context is None:
        return ()
    lines: list[str] = []
    if prompt_context.source_provider:
        lines.append(f"- source_provider: {prompt_context.source_provider}")
    if prompt_context.source_kind:
        lines.append(f"- source_kind: {prompt_context.source_kind}")
    if prompt_context.feishu_chat_type:
        lines.append(f"- feishu_chat_type: {prompt_context.feishu_chat_type}")
    if prompt_context.im_force_direct_send:
        lines.append("- im_force_direct_send: true")
    return tuple(lines)


def _select_visible_skills(
    *,
    authorized_skills: Sequence[str],
    hit_document_ids: tuple[str, ...],
    top_k: int,
) -> tuple[str, ...]:
    visible_skills: list[str] = []
    seen: set[str] = set()
    for name in hit_document_ids:
        if name in seen:
            continue
        seen.add(name)
        visible_skills.append(name)
        if len(visible_skills) == top_k:
            return tuple(visible_skills)
    remaining = sorted(name for name in authorized_skills if name not in seen)
    for name in remaining:
        visible_skills.append(name)
        if len(visible_skills) == top_k:
            break
    return tuple(visible_skills)


def _preferred_skills(skills: Sequence[Skill]) -> tuple[Skill, ...]:
    preferred_by_name: dict[str, Skill] = {}
    for skill in sorted(skills, key=_skill_display_sort_key):
        preferred_by_name.setdefault(skill.metadata.name, skill)
    return tuple(preferred_by_name[name] for name in sorted(preferred_by_name))


def _skill_map_by_name(skills: Sequence[Skill]) -> dict[str, Skill]:
    return {skill.metadata.name: skill for skill in skills}


def _skill_display_sort_key(skill: Skill) -> tuple[str, int, str]:
    scope_priority = 0 if skill.scope == SkillScope.APP else 1
    return (skill.metadata.name, scope_priority, skill.ref)
