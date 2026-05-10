# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from relay_teams.roles.prompt_adjustment_engine import SystemPromptAdjustmentEngine
from relay_teams.roles.self_assessment_service import PromptAdjustmentRecommendation
from relay_teams.roles.temporary_role_repository import TemporaryRoleRepository
from relay_teams.validation import RequiredIdentifierStr


class TemporaryKnowledgeCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_role_id: RequiredIdentifierStr
    target_template_role_id: RequiredIdentifierStr
    captured_at: datetime
    effective_prompt: str
    original_prompt: str
    prompt_diff_markdown: str


class TemporaryRoleKnowledgeCaptureService:
    def __init__(
        self,
        *,
        adjustment_engine: SystemPromptAdjustmentEngine,
        temporary_role_repository: TemporaryRoleRepository,
        min_diff_chars: int = 50,
    ) -> None:
        self._adjustment_engine = adjustment_engine
        self._temporary_role_repository = temporary_role_repository
        self._min_diff_chars = min_diff_chars

    async def capture_on_subagent_stop(
        self,
        *,
        subagent_role_id: str,
        subagent_run_id: str,
        effective_prompt: str,
        original_prompt: str,
        workspace_id: str,
        _session_id: str,
    ) -> TemporaryKnowledgeCapture | None:

        try:
            record = await self._temporary_role_repository.get_async(
                run_id=subagent_run_id,
                role_id=subagent_role_id,
            )
        except KeyError:
            return None

        template_role_id = record.role.template_role_id
        if template_role_id is None:
            return None

        diff = _compute_prompt_diff(
            effective=effective_prompt,
            original=original_prompt,
            min_diff_chars=self._min_diff_chars,
        )
        if diff is None:
            return None

        captured_at = datetime.now(tz=timezone.utc)
        capture = TemporaryKnowledgeCapture(
            source_role_id=subagent_role_id,
            target_template_role_id=template_role_id,
            captured_at=captured_at,
            effective_prompt=effective_prompt,
            original_prompt=original_prompt,
            prompt_diff_markdown=diff,
        )

        await self._adjustment_engine.propose_adjustment_async(
            role_id=template_role_id,
            workspace_id=workspace_id,
            current_prompt=original_prompt,
            recommendations=(
                PromptAdjustmentRecommendation(
                    target_section="strategy",
                    current_text="",
                    recommended_text=_extract_additions(
                        effective_prompt, original_prompt
                    ),
                    rationale=f"Captured from temporary role {subagent_role_id}",
                    priority=3,
                    confidence=0.5,
                ),
            ),
            trigger_source="temporary_role_capture",
            triggered_by=subagent_role_id,
        )

        return capture

    @staticmethod
    async def capture_all_for_session(
        *,
        _session_id: str,
        _workspace_id: str,
    ) -> tuple[TemporaryKnowledgeCapture, ...]:
        return ()  # placeholder: will iterate temp roles via injected services


def _compute_prompt_diff(
    *,
    effective: str,
    original: str,
    min_diff_chars: int,
) -> str | None:
    e = effective.strip()
    o = original.strip()
    if e == o:
        return None

    common_len = 0
    for a, b in zip(e, o):
        if a != b:
            break
        common_len += 1

    additions = e[common_len:]
    removals = o[common_len:]

    diff_parts: list[str] = []
    for line in removals.splitlines():
        stripped = line.strip()
        if stripped:
            diff_parts.append(f"- {stripped}")
    for line in additions.splitlines():
        stripped = line.strip()
        if stripped:
            diff_parts.append(f"+ {stripped}")

    diff_text = "\n".join(diff_parts).strip()
    if len(diff_text) < min_diff_chars:
        return None

    return diff_text


def _extract_additions(effective: str, original: str) -> str:
    e = effective.strip()
    o = original.strip()
    if e == o:
        return ""

    additions = e[len(o) :] if e.startswith(o) else e
    lines = [line.strip() for line in additions.splitlines() if line.strip()]
    return "\n".join(lines)
