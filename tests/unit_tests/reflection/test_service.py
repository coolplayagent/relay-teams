# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from agent_teams.agents.enums import InstanceStatus
from agent_teams.reflection.config_manager import ReflectionConfigManager
from agent_teams.reflection.models import (
    DailyDigestDocument,
    DailyRawMemoryDocument,
    DailyReflectionResult,
    LongTermMemoryDocument,
)
from agent_teams.reflection.repository import ReflectionJobRepository
from agent_teams.reflection.service import (
    ConsolidationPromptInput,
    ReflectionPromptInput,
    ReflectionService,
)
from agent_teams.agents.agent_repo import AgentInstanceRepository
from agent_teams.agents.execution.message_repo import MessageRepository
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repo import TaskRepository
from agent_teams.workspace import WorkspaceManager
from agent_teams.agents.tasks.enums import TaskStatus
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


class _FakeReflectionModelClient:
    async def generate_daily_reflection(
        self,
        prompt_input: ReflectionPromptInput,
    ) -> DailyReflectionResult:
        assert prompt_input.objective == "Write summary"
        return DailyReflectionResult(
            raw_document=DailyRawMemoryDocument(
                memory_date=prompt_input.memory_date,
                session_facts=("User wants concise summaries.",),
                observations=("The agent produced a stable summary.",),
                decisions=("Prefer concise output.",),
                failures_and_recoveries=("No failures.",),
                open_threads=("Track new release notes.",),
                candidate_long_term_learnings=("User prefers concise summaries.",),
            ),
            digest_document=DailyDigestDocument(
                memory_date=prompt_input.memory_date,
                summary_items=("Prefer concise output.", "Track new release notes."),
            ),
        )

    async def consolidate_long_term_memory(
        self,
        prompt_input: ConsolidationPromptInput,
    ) -> LongTermMemoryDocument:
        assert (
            "User prefers concise summaries."
            in prompt_input.candidate_long_term_learnings
        )
        return LongTermMemoryDocument(
            role_identity=("Acts as a concise writing subagent.",),
            stable_user_project_preferences=("Keep summaries concise.",),
            proven_strategies=("Lead with the final result.",),
            reusable_constraints_and_boundaries=(
                "Do not expand beyond requested scope.",
            ),
            important_ongoing_tendencies=("Monitor release note work.",),
        )


@pytest.mark.asyncio
async def test_service_processes_daily_and_long_term_memory(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_dir = tmp_path / ".config" / "agent-teams"
    config_dir.mkdir(parents=True)
    db_path = tmp_path / "reflection_service.db"
    workspace_id = f"workspace-{tmp_path.name}"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    shared_store = SharedStateRepository(db_path)
    workspace_manager = WorkspaceManager(
        project_root=project_root, shared_store=shared_store
    )

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="root-task",
            trace_id="run-1",
            objective="Write summary",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    task_repo.update_status("task-1", TaskStatus.COMPLETED, result="Done")
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer_agent",
        workspace_id=workspace_id,
        conversation_id="conversation-1",
        status=InstanceStatus.COMPLETED,
    )
    message_repo.append(
        session_id="session-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        agent_role_id="writer_agent",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="Summarize the release notes")])
        ],
    )

    service = ReflectionService(
        config_manager=ReflectionConfigManager(config_dir=config_dir),
        repository=ReflectionJobRepository(db_path),
        workspace_manager=workspace_manager,
        message_repo=message_repo,
        task_repo=task_repo,
        agent_repo=agent_repo,
        model_client=_FakeReflectionModelClient(),
    )

    _ = service.enqueue_daily_reflection(
        session_id="session-1",
        run_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer_agent",
        workspace_id=workspace_id,
        conversation_id="conversation-1",
    )

    assert await service.process_next_job() is True
    assert await service.process_next_job() is True

    daily_raw = (
        workspace_manager.locations_for(workspace_id).workspace_dir
        / "memory"
        / "daily"
        / "raw"
    )
    daily_digest = (
        workspace_manager.locations_for(workspace_id).workspace_dir
        / "memory"
        / "daily"
        / "digest"
    )
    raw_files = tuple(daily_raw.glob("*.md"))
    digest_files = tuple(daily_digest.glob("*.md"))
    assert len(raw_files) == 1
    assert len(digest_files) == 1
    assert "Prefer concise output." in digest_files[0].read_text(encoding="utf-8")

    long_term_path = (
        config_dir
        / "memory"
        / "session_roles"
        / "session-1"
        / "writer_agent"
        / "MEMORY.md"
    )
    assert long_term_path.exists()
    long_term_text = long_term_path.read_text(encoding="utf-8")
    assert "Keep summaries concise." in long_term_text

    injected = service.build_injected_memory(
        session_id="session-1",
        role_id="writer_agent",
        workspace_id=workspace_id,
    )
    assert "## Long-Term Memory" in injected
    assert "## Today's Memory Digest" in injected
