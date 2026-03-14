# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from agent_teams.roles.memory_models import MemoryKind
from agent_teams.roles.memory_repository import RoleMemoryRepository


class RoleMemoryService:
    def __init__(self, *, repository: RoleMemoryRepository) -> None:
        self._repository = repository

    def build_injected_memory(
        self,
        *,
        role_id: str,
        workspace_id: str,
        memory_date: str | None = None,
    ) -> str:
        resolved_date = memory_date or date.today().isoformat()
        durable = self._repository.read_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        ).content_markdown.strip()
        daily_digest = self._repository.read_daily_memory(
            role_id=role_id,
            workspace_id=workspace_id,
            memory_date=resolved_date,
            kind=MemoryKind.DIGEST,
        ).content_markdown.strip()
        sections: list[str] = []
        if durable:
            sections.append(f"## Role Memory\n{durable}")
        if daily_digest:
            sections.append(f"## Daily Memory\n{daily_digest}")
        return "\n\n".join(sections)

    def record_task_result(
        self,
        *,
        role_id: str,
        workspace_id: str,
        session_id: str,
        task_id: str,
        objective: str,
        result: str,
        transcript_lines: Sequence[str],
        memory_date: str | None = None,
    ) -> None:
        resolved_date = memory_date or date.today().isoformat()
        trimmed_result = result.strip()
        trimmed_objective = objective.strip()
        raw_lines = [
            f"# {resolved_date}",
            "",
            f"- session_id: {session_id}",
            f"- task_id: {task_id}",
            f"- objective: {trimmed_objective}",
            "",
            "## Result",
            trimmed_result or "(empty)",
        ]
        if transcript_lines:
            raw_lines.extend(("", "## Transcript", *transcript_lines))
        raw_markdown = "\n".join(raw_lines).strip()
        digest_text = trimmed_result or trimmed_objective or task_id
        digest_markdown = f"- {digest_text}"
        self._repository.write_daily_memory(
            role_id=role_id,
            workspace_id=workspace_id,
            memory_date=resolved_date,
            kind=MemoryKind.RAW,
            content_markdown=raw_markdown,
            source_session_id=session_id,
            source_task_id=task_id,
        )
        self._repository.write_daily_memory(
            role_id=role_id,
            workspace_id=workspace_id,
            memory_date=resolved_date,
            kind=MemoryKind.DIGEST,
            content_markdown=digest_markdown,
            source_session_id=session_id,
            source_task_id=task_id,
        )
        current = self._repository.read_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        ).content_markdown.strip()
        durable_entry = f"- {trimmed_objective}: {trimmed_result or '(empty)'}"
        if durable_entry not in current.splitlines():
            next_text = "\n".join(
                line for line in (current, durable_entry) if line.strip()
            ).strip()
            self._repository.write_role_memory(
                role_id=role_id,
                workspace_id=workspace_id,
                content_markdown=next_text,
            )
