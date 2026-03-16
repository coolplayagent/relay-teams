# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.roles.memory_repository import RoleMemoryRepository
from agent_teams.roles.memory_models import RoleMemoryRecord


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
        del memory_date
        return self.get_reflection_record(
            role_id=role_id,
            workspace_id=workspace_id,
        ).content_markdown.strip()

    def get_reflection_record(
        self,
        *,
        role_id: str,
        workspace_id: str,
    ) -> RoleMemoryRecord:
        return self._repository.read_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        )

    def update_reflection_memory(
        self,
        *,
        role_id: str,
        workspace_id: str,
        content_markdown: str,
    ) -> RoleMemoryRecord:
        self._repository.write_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
            content_markdown=content_markdown.strip(),
        )
        return self.get_reflection_record(
            role_id=role_id,
            workspace_id=workspace_id,
        )

    def delete_reflection_memory(
        self,
        *,
        role_id: str,
        workspace_id: str,
    ) -> None:
        self._repository.delete_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        )

    def build_reflection_preview(
        self,
        *,
        role_id: str,
        workspace_id: str,
        max_chars: int = 180,
    ) -> str:
        text = self.build_injected_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        )
        normalized = " ".join(text.split())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max_chars - 3].rstrip() + "..."

    def record_task_result(
        self,
        *,
        role_id: str,
        workspace_id: str,
        session_id: str,
        task_id: str,
        objective: str,
        result: str,
        transcript_lines: tuple[str, ...],
        memory_date: str | None = None,
    ) -> None:
        del session_id, task_id, transcript_lines, memory_date
        trimmed_result = result.strip()
        trimmed_objective = objective.strip()
        current = self._repository.read_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
        ).content_markdown.strip()
        durable_entry = f"- {trimmed_objective}: {trimmed_result or '(empty)'}"
        if durable_entry in current.splitlines():
            return
        next_text = "\n".join(
            line for line in (current, durable_entry) if line.strip()
        ).strip()
        self._repository.write_role_memory(
            role_id=role_id,
            workspace_id=workspace_id,
            content_markdown=next_text,
        )
