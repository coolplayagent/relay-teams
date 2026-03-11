# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from agent_teams.logger import get_logger, log_event
from agent_teams.paths import get_project_config_dir
from agent_teams.providers.http_client_factory import build_llm_http_client
from agent_teams.providers.model_config import ModelEndpointConfig
from agent_teams.reflection.config_manager import ReflectionConfigManager
from agent_teams.reflection.models import (
    DailyDigestDocument,
    DailyMemoryKind,
    DailyRawMemoryDocument,
    DailyReflectionResult,
    LongTermMemoryDocument,
    MemoryFileView,
    MemoryOwnerScope,
    ReflectionConfig,
    ReflectionJobCreate,
    ReflectionJobRecord,
    ReflectionJobType,
)
from agent_teams.reflection.repository import ReflectionJobRepository
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.workspace import WorkspaceManager

LOGGER = get_logger(__name__)


class ReflectionPromptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    role_id: str
    task_id: str
    objective: str
    result: str
    transcript_lines: tuple[str, ...]
    existing_daily_raw_markdown: str
    existing_daily_digest_markdown: str
    existing_long_term_markdown: str
    memory_date: str


class ConsolidationPromptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    role_id: str
    trigger_date: str
    existing_long_term_markdown: str
    candidate_long_term_learnings: tuple[str, ...]
    daily_digest_items: tuple[str, ...]


class ReflectionModelClient(Protocol):
    async def generate_daily_reflection(
        self,
        prompt_input: ReflectionPromptInput,
    ) -> DailyReflectionResult: ...

    async def consolidate_long_term_memory(
        self,
        prompt_input: ConsolidationPromptInput,
    ) -> LongTermMemoryDocument: ...


class PydanticAIReflectionModelClient:
    def __init__(
        self,
        *,
        llm_profiles: dict[str, ModelEndpointConfig],
        get_config: Callable[[], ReflectionConfig],
    ) -> None:
        self._llm_profiles = llm_profiles
        self._get_config = get_config

    def replace_llm_profiles(
        self,
        llm_profiles: dict[str, ModelEndpointConfig],
    ) -> None:
        self._llm_profiles = llm_profiles

    async def generate_daily_reflection(
        self,
        prompt_input: ReflectionPromptInput,
    ) -> DailyReflectionResult:
        system_prompt = (
            "You generate structured daily memory for a software subagent. "
            "Return strict JSON only. Do not include markdown fences."
        )
        user_prompt = json.dumps(
            {
                "task": {
                    "session_id": prompt_input.session_id,
                    "role_id": prompt_input.role_id,
                    "task_id": prompt_input.task_id,
                    "objective": prompt_input.objective,
                    "result": prompt_input.result,
                    "memory_date": prompt_input.memory_date,
                },
                "rules": {
                    "digest_max_items": 8,
                    "only_include_high_signal_items": True,
                    "no_transcript_quotes": True,
                },
                "transcript_lines": list(prompt_input.transcript_lines),
                "existing_daily_raw_markdown": prompt_input.existing_daily_raw_markdown,
                "existing_daily_digest_markdown": prompt_input.existing_daily_digest_markdown,
                "existing_long_term_markdown": prompt_input.existing_long_term_markdown,
                "response_schema": {
                    "raw_document": {
                        "memory_date": prompt_input.memory_date,
                        "session_facts": ["..."],
                        "observations": ["..."],
                        "decisions": ["..."],
                        "failures_and_recoveries": ["..."],
                        "open_threads": ["..."],
                        "candidate_long_term_learnings": ["..."],
                    },
                    "digest_document": {
                        "memory_date": prompt_input.memory_date,
                        "summary_items": ["..."],
                    },
                },
            },
            ensure_ascii=False,
        )
        raw_result = await self._run_json_prompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return DailyReflectionResult.model_validate(raw_result)

    async def consolidate_long_term_memory(
        self,
        prompt_input: ConsolidationPromptInput,
    ) -> LongTermMemoryDocument:
        system_prompt = (
            "You maintain a stable long-term memory for a software subagent role. "
            "Return strict JSON only. Never store temporary task status, dates, or one-off TODOs."
        )
        user_prompt = json.dumps(
            {
                "task": {
                    "session_id": prompt_input.session_id,
                    "role_id": prompt_input.role_id,
                    "trigger_date": prompt_input.trigger_date,
                },
                "rules": {
                    "only_cross_task_reusable_information": True,
                    "avoid_time_sensitive_facts": True,
                    "avoid_unverified_claims": True,
                },
                "existing_long_term_markdown": prompt_input.existing_long_term_markdown,
                "candidate_long_term_learnings": list(
                    prompt_input.candidate_long_term_learnings
                ),
                "daily_digest_items": list(prompt_input.daily_digest_items),
                "response_schema": {
                    "role_identity": ["..."],
                    "stable_user_project_preferences": ["..."],
                    "proven_strategies": ["..."],
                    "reusable_constraints_and_boundaries": ["..."],
                    "important_ongoing_tendencies": ["..."],
                },
            },
            ensure_ascii=False,
        )
        raw_result = await self._run_json_prompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return LongTermMemoryDocument.model_validate(raw_result)

    async def _run_json_prompt(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, object]:
        config = self._resolve_model_config()
        model_settings: OpenAIChatModelSettings = {
            "temperature": config.sampling.temperature,
            "top_p": config.sampling.top_p,
            "max_tokens": config.sampling.max_tokens,
        }
        model = OpenAIChatModel(
            config.model,
            provider=OpenAIProvider(
                base_url=config.base_url,
                api_key=config.api_key,
                http_client=build_llm_http_client(
                    connect_timeout_seconds=config.connect_timeout_seconds
                ),
            ),
        )
        agent = Agent(
            model=model,
            output_type=str,
            system_prompt=system_prompt,
            model_settings=model_settings,
            retries=2,
        )
        result = await agent.run(user_prompt)
        raw_text = str(result.output).strip()
        normalized = _strip_code_fence(raw_text)
        parsed = json.loads(normalized)
        if isinstance(parsed, dict):
            return cast(dict[str, object], parsed)
        raise ValueError("Reflection model did not return a JSON object")

    def _resolve_model_config(self) -> ModelEndpointConfig:
        config = self._get_config()
        if config.model_profile in self._llm_profiles:
            return self._llm_profiles[config.model_profile]
        if "default" in self._llm_profiles:
            return self._llm_profiles["default"]
        raise ValueError("No reflection model profile is available")


class ReflectionService:
    def __init__(
        self,
        *,
        config_manager: ReflectionConfigManager,
        repository: ReflectionJobRepository,
        workspace_manager: WorkspaceManager,
        message_repo: MessageRepository,
        task_repo: TaskRepository,
        agent_repo: AgentInstanceRepository,
        model_client: ReflectionModelClient,
    ) -> None:
        self.config_manager = config_manager
        self.repository = repository
        self.workspace_manager = workspace_manager
        self.message_repo = message_repo
        self.task_repo = task_repo
        self.agent_repo = agent_repo
        self.model_client = model_client
        self._worker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        config = self.get_config()
        if not config.enabled:
            return
        self.repository.reset_running_to_queued()
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None

    def get_config(self) -> ReflectionConfig:
        return self.config_manager.get_reflection_config()

    def enqueue_daily_reflection(
        self,
        *,
        session_id: str,
        run_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
    ) -> ReflectionJobRecord | None:
        config = self.get_config()
        if not config.enabled:
            return None
        trigger_date = self._today_string()
        return self.repository.enqueue(
            ReflectionJobCreate(
                job_type=ReflectionJobType.DAILY_REFLECTION,
                session_id=session_id,
                run_id=run_id,
                task_id=task_id,
                instance_id=instance_id,
                role_id=role_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                memory_owner_scope=MemoryOwnerScope.SESSION_ROLE,
                memory_owner_id=self._memory_owner_id(
                    session_id=session_id, role_id=role_id
                ),
                trigger_date=trigger_date,
            )
        )

    def list_jobs(self, *, limit: int = 50) -> tuple[ReflectionJobRecord, ...]:
        return self.repository.list_jobs(limit=limit)

    def retry_job(self, job_id: str) -> ReflectionJobRecord:
        return self.repository.retry(job_id)

    def replace_llm_profiles(
        self, llm_profiles: dict[str, ModelEndpointConfig]
    ) -> None:
        if isinstance(self.model_client, PydanticAIReflectionModelClient):
            self.model_client.replace_llm_profiles(llm_profiles)

    def read_long_term_memory(
        self,
        *,
        session_id: str,
        role_id: str,
    ) -> MemoryFileView:
        path = self._long_term_memory_path(session_id=session_id, role_id=role_id)
        if not path.exists():
            return MemoryFileView(path=path, exists=False, content="")
        return MemoryFileView(
            path=path, exists=True, content=path.read_text(encoding="utf-8")
        )

    def read_daily_memory(
        self,
        *,
        instance_id: str,
        memory_date: str,
        kind: DailyMemoryKind,
    ) -> MemoryFileView:
        instance = self.agent_repo.get_instance(instance_id)
        path = self._daily_memory_path(
            workspace_id=instance.workspace_id,
            memory_date=memory_date,
            kind=kind,
        )
        if not path.exists():
            return MemoryFileView(path=path, exists=False, content="")
        return MemoryFileView(
            path=path, exists=True, content=path.read_text(encoding="utf-8")
        )

    def build_injected_memory(
        self,
        *,
        session_id: str,
        role_id: str,
        workspace_id: str,
    ) -> str:
        config = self.get_config()
        if not config.enabled:
            return ""
        long_term_text = self._render_long_term_injection(
            self._read_long_term_document(session_id=session_id, role_id=role_id)
        )
        daily_text = self._render_daily_digest_injection(
            self._read_daily_digest_document(
                workspace_id=workspace_id,
                memory_date=self._today_string(),
            )
        )
        long_term_trimmed = long_term_text[
            : config.max_long_term_injection_chars
        ].strip()
        daily_trimmed = daily_text[: config.max_daily_digest_injection_chars].strip()
        composed = _join_non_empty((long_term_trimmed, daily_trimmed))
        if len(composed) <= config.max_injected_memory_chars:
            return composed
        overflow = len(composed) - config.max_injected_memory_chars
        if daily_trimmed:
            daily_trimmed = daily_trimmed[
                : max(0, len(daily_trimmed) - overflow)
            ].strip()
        composed = _join_non_empty((long_term_trimmed, daily_trimmed))
        if len(composed) <= config.max_injected_memory_chars:
            return composed
        overflow = len(composed) - config.max_injected_memory_chars
        if long_term_trimmed:
            long_term_trimmed = long_term_trimmed[
                : max(0, len(long_term_trimmed) - overflow)
            ].strip()
        return _join_non_empty((long_term_trimmed, daily_trimmed))

    def delete_session_artifacts(
        self,
        *,
        session_id: str,
        role_ids: Sequence[str],
    ) -> None:
        self.repository.delete_by_session(session_id)
        memory_root = self._role_memory_root()
        for role_id in role_ids:
            path = self._long_term_memory_path(session_id=session_id, role_id=role_id)
            if path.exists():
                path.unlink()
            parent = path.parent
            while parent != memory_root and parent.exists():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

    async def process_next_job(self) -> bool:
        job = self.repository.claim_next_job(
            max_retry_attempts=self.get_config().max_retry_attempts
        )
        if job is None:
            return False
        try:
            if job.job_type == ReflectionJobType.DAILY_REFLECTION:
                await self._process_daily_reflection_job(job)
            else:
                await self._process_long_term_consolidation_job(job)
            self.repository.mark_completed(job.job_id)
            return True
        except Exception as exc:
            self.repository.mark_failed(job.job_id, last_error=str(exc))
            log_event(
                LOGGER,
                logging.ERROR,
                event="reflection.job.failed",
                message="Reflection job failed",
                payload={
                    "job_id": job.job_id,
                    "job_type": job.job_type.value,
                    "role_id": job.role_id,
                    "instance_id": job.instance_id,
                },
                exc_info=exc,
            )
            return True

    async def _worker_loop(self) -> None:
        while True:
            processed = await self.process_next_job()
            if processed:
                continue
            await asyncio.sleep(self.get_config().poll_interval_seconds)

    async def _process_daily_reflection_job(self, job: ReflectionJobRecord) -> None:
        task_record = self.task_repo.get(job.task_id)
        transcript = self._collect_transcript(
            self.message_repo.get_history_for_conversation_task(
                job.conversation_id, job.task_id
            )
        )
        existing_raw_path = self._daily_memory_path(
            workspace_id=job.workspace_id,
            memory_date=job.trigger_date,
            kind=DailyMemoryKind.RAW,
        )
        existing_digest_path = self._daily_memory_path(
            workspace_id=job.workspace_id,
            memory_date=job.trigger_date,
            kind=DailyMemoryKind.DIGEST,
        )
        prompt_input = ReflectionPromptInput(
            session_id=job.session_id,
            role_id=job.role_id,
            task_id=job.task_id,
            objective=task_record.envelope.objective,
            result=task_record.result or "",
            transcript_lines=transcript,
            existing_daily_raw_markdown=existing_raw_path.read_text(encoding="utf-8")
            if existing_raw_path.exists()
            else "",
            existing_daily_digest_markdown=existing_digest_path.read_text(
                encoding="utf-8"
            )
            if existing_digest_path.exists()
            else "",
            existing_long_term_markdown=self.read_long_term_memory(
                session_id=job.session_id, role_id=job.role_id
            ).content,
            memory_date=job.trigger_date,
        )
        result = await self.model_client.generate_daily_reflection(prompt_input)
        self._write_daily_raw(job.workspace_id, result.raw_document)
        self._write_daily_digest(job.workspace_id, result.digest_document)
        self._cleanup_expired_daily_files(job.workspace_id)
        if not self.repository.has_job_for_owner_date(
            job_type=ReflectionJobType.LONG_TERM_CONSOLIDATION,
            memory_owner_id=job.memory_owner_id,
            trigger_date=job.trigger_date,
        ):
            _ = self.repository.enqueue(
                ReflectionJobCreate(
                    job_type=ReflectionJobType.LONG_TERM_CONSOLIDATION,
                    session_id=job.session_id,
                    run_id=job.run_id,
                    task_id=job.task_id,
                    instance_id=job.instance_id,
                    role_id=job.role_id,
                    workspace_id=job.workspace_id,
                    conversation_id=job.conversation_id,
                    memory_owner_scope=job.memory_owner_scope,
                    memory_owner_id=job.memory_owner_id,
                    trigger_date=job.trigger_date,
                )
            )

    async def _process_long_term_consolidation_job(
        self, job: ReflectionJobRecord
    ) -> None:
        daily_raw = self._read_daily_raw_document(
            workspace_id=job.workspace_id,
            memory_date=job.trigger_date,
        )
        daily_digest = self._read_daily_digest_document(
            workspace_id=job.workspace_id,
            memory_date=job.trigger_date,
        )
        document = await self.model_client.consolidate_long_term_memory(
            ConsolidationPromptInput(
                session_id=job.session_id,
                role_id=job.role_id,
                trigger_date=job.trigger_date,
                existing_long_term_markdown=self.read_long_term_memory(
                    session_id=job.session_id,
                    role_id=job.role_id,
                ).content,
                candidate_long_term_learnings=daily_raw.candidate_long_term_learnings,
                daily_digest_items=daily_digest.summary_items,
            )
        )
        self._write_long_term_memory(
            session_id=job.session_id,
            role_id=job.role_id,
            document=document,
        )

    def _read_daily_raw_document(
        self,
        *,
        workspace_id: str,
        memory_date: str,
    ) -> DailyRawMemoryDocument:
        path = self._daily_memory_path(
            workspace_id=workspace_id,
            memory_date=memory_date,
            kind=DailyMemoryKind.RAW,
        )
        if not path.exists():
            return DailyRawMemoryDocument(memory_date=memory_date)
        return _parse_daily_raw_markdown(path.read_text(encoding="utf-8"), memory_date)

    def _read_daily_digest_document(
        self,
        *,
        workspace_id: str,
        memory_date: str,
    ) -> DailyDigestDocument:
        path = self._daily_memory_path(
            workspace_id=workspace_id,
            memory_date=memory_date,
            kind=DailyMemoryKind.DIGEST,
        )
        if not path.exists():
            return DailyDigestDocument(memory_date=memory_date)
        return _parse_daily_digest_markdown(
            path.read_text(encoding="utf-8"), memory_date
        )

    def _read_long_term_document(
        self,
        *,
        session_id: str,
        role_id: str,
    ) -> LongTermMemoryDocument:
        path = self._long_term_memory_path(session_id=session_id, role_id=role_id)
        if not path.exists():
            return LongTermMemoryDocument()
        return _parse_long_term_markdown(path.read_text(encoding="utf-8"))

    def _write_daily_raw(
        self, workspace_id: str, document: DailyRawMemoryDocument
    ) -> None:
        path = self._daily_memory_path(
            workspace_id=workspace_id,
            memory_date=document.memory_date,
            kind=DailyMemoryKind.RAW,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_daily_raw_markdown(document), encoding="utf-8")

    def _write_daily_digest(
        self, workspace_id: str, document: DailyDigestDocument
    ) -> None:
        path = self._daily_memory_path(
            workspace_id=workspace_id,
            memory_date=document.memory_date,
            kind=DailyMemoryKind.DIGEST,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_daily_digest_markdown(document), encoding="utf-8")

    def _write_long_term_memory(
        self,
        *,
        session_id: str,
        role_id: str,
        document: LongTermMemoryDocument,
    ) -> None:
        path = self._long_term_memory_path(session_id=session_id, role_id=role_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_long_term_markdown(document), encoding="utf-8")

    def _render_long_term_injection(self, document: LongTermMemoryDocument) -> str:
        sections = [
            ("## Long-Term Memory", None),
            ("### Role Identity", document.role_identity),
            (
                "### Stable User / Project Preferences",
                document.stable_user_project_preferences,
            ),
            ("### Proven Strategies", document.proven_strategies),
            (
                "### Reusable Constraints And Boundaries",
                document.reusable_constraints_and_boundaries,
            ),
            ("### Important Ongoing Tendencies", document.important_ongoing_tendencies),
        ]
        lines: list[str] = []
        for title, items in sections:
            lines.append(title)
            if items is None:
                continue
            lines.extend(f"- {item}" for item in items if item.strip())
        return "\n".join(lines).strip()

    def _render_daily_digest_injection(self, document: DailyDigestDocument) -> str:
        if not document.summary_items:
            return ""
        lines = ["## Today's Memory Digest"]
        lines.extend(f"- {item}" for item in document.summary_items if item.strip())
        return "\n".join(lines).strip()

    def _collect_transcript(self, messages: Sequence[ModelMessage]) -> tuple[str, ...]:
        selected = list(messages)[-self.get_config().max_transcript_messages :]
        lines: list[str] = []
        for message in selected:
            role = "assistant" if isinstance(message, ModelResponse) else "user"
            if not isinstance(message, (ModelRequest, ModelResponse)):
                continue
            text_parts: list[str] = []
            for part in message.parts:
                content = getattr(part, "content", None)
                if isinstance(content, str):
                    normalized = content.strip()
                    if normalized:
                        text_parts.append(normalized)
            if text_parts:
                lines.append(f"{role}: {' '.join(text_parts)}")
        return tuple(lines)

    def _cleanup_expired_daily_files(self, workspace_id: str) -> None:
        cutoff = date.today() - timedelta(days=self.get_config().daily_retention_days)
        workspace_dir = self.workspace_manager.locations_for(workspace_id).workspace_dir
        daily_root = workspace_dir / "memory" / "daily"
        if not daily_root.exists():
            return
        for kind_dir in (daily_root / "raw", daily_root / "digest"):
            if not kind_dir.exists():
                continue
            for file_path in kind_dir.glob("*.md"):
                file_date = _parse_memory_date_from_file_name(file_path.name)
                if file_date is None or file_date >= cutoff:
                    continue
                file_path.unlink()

    def _daily_memory_path(
        self,
        *,
        workspace_id: str,
        memory_date: str,
        kind: DailyMemoryKind,
    ) -> Path:
        workspace_dir = self.workspace_manager.locations_for(workspace_id).workspace_dir
        return workspace_dir / "memory" / "daily" / kind.value / f"{memory_date}.md"

    def _long_term_memory_path(self, *, session_id: str, role_id: str) -> Path:
        return (
            self._role_memory_root()
            / self._normalize_path_component(session_id)
            / self._normalize_path_component(role_id)
            / "MEMORY.md"
        )

    def _role_memory_root(self) -> Path:
        return (
            get_project_config_dir(project_root=self.workspace_manager.project_root)
            / "memory"
            / "session_roles"
        )

    def _memory_owner_id(self, *, session_id: str, role_id: str) -> str:
        return f"{session_id}:{role_id}"

    def _normalize_path_component(self, value: str) -> str:
        normalized = "".join(
            character if character.isalnum() or character in {"-", "_"} else "_"
            for character in value.strip()
        )
        return normalized or "unknown"

    def _today_string(self) -> str:
        return date.today().isoformat()


def _render_daily_raw_markdown(document: DailyRawMemoryDocument) -> str:
    lines = [f"# Daily Raw Memory - {document.memory_date}"]
    lines.extend(_render_section("## Session Facts", document.session_facts))
    lines.extend(_render_section("## Observations", document.observations))
    lines.extend(_render_section("## Decisions", document.decisions))
    lines.extend(
        _render_section("## Failures And Recoveries", document.failures_and_recoveries)
    )
    lines.extend(_render_section("## Open Threads", document.open_threads))
    lines.extend(
        _render_section(
            "## Candidate Long-Term Learnings",
            document.candidate_long_term_learnings,
        )
    )
    return "\n".join(lines).strip() + "\n"


def _render_daily_digest_markdown(document: DailyDigestDocument) -> str:
    lines = [f"# Daily Digest - {document.memory_date}"]
    lines.extend(_render_section("## Injection Summary", document.summary_items))
    return "\n".join(lines).strip() + "\n"


def _render_long_term_markdown(document: LongTermMemoryDocument) -> str:
    lines = ["# MEMORY"]
    lines.extend(_render_section("## Role Identity", document.role_identity))
    lines.extend(
        _render_section(
            "## Stable User / Project Preferences",
            document.stable_user_project_preferences,
        )
    )
    lines.extend(_render_section("## Proven Strategies", document.proven_strategies))
    lines.extend(
        _render_section(
            "## Reusable Constraints And Boundaries",
            document.reusable_constraints_and_boundaries,
        )
    )
    lines.extend(
        _render_section(
            "## Important Ongoing Tendencies",
            document.important_ongoing_tendencies,
        )
    )
    return "\n".join(lines).strip() + "\n"


def _render_section(title: str, items: Sequence[str]) -> list[str]:
    lines = ["", title]
    normalized_items = [item.strip() for item in items if item.strip()]
    if not normalized_items:
        lines.append("-")
        return lines
    lines.extend(f"- {item}" for item in normalized_items)
    return lines


def _parse_daily_raw_markdown(
    markdown: str, fallback_date: str
) -> DailyRawMemoryDocument:
    sections = _parse_markdown_sections(markdown)
    return DailyRawMemoryDocument(
        memory_date=_parse_memory_date_from_title(markdown) or fallback_date,
        session_facts=tuple(sections.get("Session Facts", ())),
        observations=tuple(sections.get("Observations", ())),
        decisions=tuple(sections.get("Decisions", ())),
        failures_and_recoveries=tuple(sections.get("Failures And Recoveries", ())),
        open_threads=tuple(sections.get("Open Threads", ())),
        candidate_long_term_learnings=tuple(
            sections.get("Candidate Long-Term Learnings", ())
        ),
    )


def _parse_daily_digest_markdown(
    markdown: str, fallback_date: str
) -> DailyDigestDocument:
    sections = _parse_markdown_sections(markdown)
    return DailyDigestDocument(
        memory_date=_parse_memory_date_from_title(markdown) or fallback_date,
        summary_items=tuple(sections.get("Injection Summary", ())),
    )


def _parse_long_term_markdown(markdown: str) -> LongTermMemoryDocument:
    sections = _parse_markdown_sections(markdown)
    return LongTermMemoryDocument(
        role_identity=tuple(sections.get("Role Identity", ())),
        stable_user_project_preferences=tuple(
            sections.get("Stable User / Project Preferences", ())
        ),
        proven_strategies=tuple(sections.get("Proven Strategies", ())),
        reusable_constraints_and_boundaries=tuple(
            sections.get("Reusable Constraints And Boundaries", ())
        ),
        important_ongoing_tendencies=tuple(
            sections.get("Important Ongoing Tendencies", ())
        ),
    )


def _parse_markdown_sections(markdown: str) -> dict[str, tuple[str, ...]]:
    current: str | None = None
    items_by_section: dict[str, list[str]] = {}
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current = line[3:].strip()
            items_by_section.setdefault(current, [])
            continue
        if current is None or not line.startswith("-"):
            continue
        item = line[1:].strip()
        if item:
            items_by_section[current].append(item)
    return {key: tuple(value) for key, value in items_by_section.items()}


def _parse_memory_date_from_title(markdown: str) -> str | None:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# Daily Raw Memory - "):
            return stripped.removeprefix("# Daily Raw Memory - ").strip()
        if stripped.startswith("# Daily Digest - "):
            return stripped.removeprefix("# Daily Digest - ").strip()
    return None


def _parse_memory_date_from_file_name(file_name: str) -> date | None:
    if not file_name.endswith(".md"):
        return None
    try:
        return date.fromisoformat(file_name[:-3])
    except ValueError:
        return None


def _join_non_empty(parts: Sequence[str]) -> str:
    filtered = [part.strip() for part in parts if part.strip()]
    return "\n\n".join(filtered)


def _strip_code_fence(raw_text: str) -> str:
    stripped = raw_text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) <= 2:
        return stripped
    if lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped
