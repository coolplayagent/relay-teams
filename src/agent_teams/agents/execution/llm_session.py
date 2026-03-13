# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
import logging
from copy import deepcopy
from collections.abc import Sequence
from json import dumps
from typing import TYPE_CHECKING, Protocol, cast

from pydantic_ai._agent_graph import ModelRequestNode
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agent_teams.providers.model_config import ModelEndpointConfig
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.logger import (
    close_model_stream,
    get_logger,
    log_event,
    log_model_output,
    log_model_stream_chunk,
)
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.control import RunControlManager
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.models import RunEvent
from agent_teams.agents.agent_repo import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.agents.execution.message_repo import MessageRepository
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.agents.tasks.task_repo import TaskRepository
from agent_teams.providers.token_usage_repo import TokenUsageRepository
from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.agents.execution.agent_builder import build_coordination_agent
from agent_teams.agents.execution.provider_prompts import (
    PromptSkillInstruction,
    ProviderPromptAugmentInput,
    build_provider_augmented_system_prompt,
)
from agent_teams.agents.tasks.task_status_sanitizer import (
    sanitize_task_status_payload,
)
from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime import (
    ToolApprovalManager,
    ToolApprovalPolicy,
    ToolDeps,
)
from agent_teams.mcp.registry import McpRegistry
from agent_teams.notifications import NotificationService
from agent_teams.providers.contracts import LLMRequest
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.skills.registry import SkillRegistry
from agent_teams.workspace import (
    WorkspaceManager,
    build_conversation_id,
)

if TYPE_CHECKING:
    from agent_teams.agents.orchestration.task_execution_service import (
        TaskExecutionService,
    )
    from agent_teams.roles.registry import RoleRegistry

LOGGER = get_logger(__name__)


class _AgentRunResult(Protocol):
    @property
    def response(self) -> object: ...

    def new_messages(self) -> Sequence[ModelMessage]: ...

    def usage(self) -> object: ...


class AgentLlmSession:
    def __init__(
        self,
        config: ModelEndpointConfig,
        *,
        task_repo: TaskRepository,
        shared_store: SharedStateRepository,
        event_bus: EventLog,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        agent_repo: AgentInstanceRepository,
        approval_ticket_repo: ApprovalTicketRepository,
        run_runtime_repo: RunRuntimeRepository,
        workspace_manager: WorkspaceManager,
        role_memory_service: RoleMemoryService | None,
        tool_registry: ToolRegistry,
        mcp_registry: McpRegistry,
        skill_registry: SkillRegistry,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
        message_repo: MessageRepository,
        role_registry: "RoleRegistry",
        task_execution_service: "TaskExecutionService",
        task_service: TaskOrchestrationService,
        run_control_manager: RunControlManager,
        tool_approval_manager: ToolApprovalManager,
        tool_approval_policy: ToolApprovalPolicy,
        notification_service: NotificationService | None = None,
        token_usage_repo: TokenUsageRepository | None = None,
    ) -> None:
        self._config = config
        self._task_repo = task_repo
        self._shared_store = shared_store
        self._event_bus = event_bus
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._agent_repo = agent_repo
        self._approval_ticket_repo = approval_ticket_repo
        self._run_runtime_repo = run_runtime_repo
        self._workspace_manager = workspace_manager
        self._role_memory_service = role_memory_service
        self._tool_registry = tool_registry
        self._mcp_registry = mcp_registry
        self._skill_registry = skill_registry
        self._allowed_tools = allowed_tools
        self._allowed_mcp_servers = allowed_mcp_servers
        self._allowed_skills = allowed_skills
        self._role_registry = role_registry
        self._task_execution_service = task_execution_service
        self._task_service = task_service
        self._run_control_manager = run_control_manager
        self._message_repo = message_repo
        self._tool_approval_manager = tool_approval_manager
        self._tool_approval_policy = tool_approval_policy
        self._notification_service = notification_service
        self._token_usage_repo = token_usage_repo

    async def run(self, request: LLMRequest) -> str:
        return await self._generate_async(request)

    async def _generate_async(self, request: LLMRequest) -> str:
        resolved_workspace_id = request.workspace_id
        resolved_conversation_id = request.conversation_id or build_conversation_id(
            request.session_id,
            request.role_id,
        )
        skill_instructions = (
            tuple(
                PromptSkillInstruction(
                    name=entry.name,
                    instructions=entry.instructions,
                )
                for entry in self._skill_registry.get_instruction_entries(
                    self._allowed_skills
                )
            )
            if self._allowed_skills
            else ()
        )
        agent_system_prompt = build_provider_augmented_system_prompt(
            ProviderPromptAugmentInput(
                system_prompt=request.system_prompt,
                allowed_tools=self._allowed_tools,
                skill_instructions=skill_instructions,
            )
        )
        log_event(
            LOGGER,
            logging.INFO,
            event="llm.request.started",
            message="LLM request started",
            payload={
                "model": self._config.model,
                "base_url": self._config.base_url,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
                "task_id": request.task_id,
            },
        )
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.MODEL_STEP_STARTED,
                payload_json=dumps(
                    {"role_id": request.role_id, "instance_id": request.instance_id}
                ),
            )
        )
        model_settings: OpenAIChatModelSettings = {
            # Some OpenAI-compatible providers return cumulative usage in each stream chunk.
            # Enabling this flag makes pydantic-ai keep the last chunk usage instead of summing chunks.
            "openai_continuous_usage_stats": True,
            "temperature": self._config.sampling.temperature,
            "top_p": self._config.sampling.top_p,
            "max_tokens": self._config.sampling.max_tokens,
        }
        agent = build_coordination_agent(
            model_name=self._config.model,
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            system_prompt=agent_system_prompt,
            allowed_tools=self._allowed_tools,
            model_settings=model_settings,
            ssl_verify=self._config.ssl_verify,
            connect_timeout_seconds=self._config.connect_timeout_seconds,
            allowed_mcp_servers=self._allowed_mcp_servers,
            allowed_skills=self._allowed_skills,
            tool_registry=self._tool_registry,
            mcp_registry=self._mcp_registry,
            skill_registry=self._skill_registry,
        )
        deps = ToolDeps(
            task_repo=self._task_repo,
            shared_store=self._shared_store,
            event_bus=self._event_bus,
            message_repo=self._message_repo,
            approval_ticket_repo=self._approval_ticket_repo,
            run_runtime_repo=self._run_runtime_repo,
            injection_manager=self._injection_manager,
            run_event_hub=self._run_event_hub,
            agent_repo=self._agent_repo,
            workspace=self._workspace_manager.resolve(
                session_id=request.session_id,
                role_id=request.role_id,
                instance_id=request.instance_id,
                workspace_id=resolved_workspace_id,
                conversation_id=resolved_conversation_id,
            ),
            role_memory=self._role_memory_service,
            run_id=request.run_id,
            trace_id=request.trace_id,
            task_id=request.task_id,
            session_id=request.session_id,
            workspace_id=resolved_workspace_id,
            conversation_id=resolved_conversation_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            role_registry=self._role_registry,
            task_service=self._task_service,
            task_execution_service=self._task_execution_service,
            run_control_manager=self._run_control_manager,
            tool_approval_manager=self._tool_approval_manager,
            tool_approval_policy=self._tool_approval_policy,
            notification_service=self._notification_service,
        )
        control_ctx = self._run_control_manager.context(
            run_id=request.run_id,
            instance_id=request.instance_id,
        )

        printed_any = False
        emitted_text_chunks: list[str] = []
        history: list[ModelRequest | ModelResponse] = (
            self._truncate_history_to_safe_boundary(
                self._filter_model_messages(
                    self._message_repo.get_history_for_conversation(
                        resolved_conversation_id
                    )
                )
            )
        )
        history = self._persist_user_prompt_if_needed(
            request=request,
            history=history,
            content=request.user_prompt,
        )
        seen_count = 0
        buffered_messages: list[ModelRequest | ModelResponse] = []
        restarted = False
        result: _AgentRunResult | None = None
        request_level_input_tokens = 0
        request_level_output_tokens = 0
        request_level_requests = 0
        saw_request_level_usage = False

        try:
            while True:
                control_ctx.raise_if_cancelled()
                restarted = False
                async with agent.iter(
                    None,
                    deps=deps,
                    message_history=history,
                ) as agent_run:
                    async for node in agent_run:
                        control_ctx.raise_if_cancelled()
                        if isinstance(node, ModelRequestNode):
                            usage_before = deepcopy(agent_run.usage())
                            # Stream text chunks from this model response in real-time
                            async with node.stream(agent_run.ctx) as stream:
                                async for text_delta in stream.stream_text(delta=True):
                                    control_ctx.raise_if_cancelled()
                                    if text_delta:
                                        log_model_stream_chunk(
                                            request.role_id, text_delta
                                        )
                                        printed_any = True
                                        emitted_text_chunks.append(text_delta)
                                        self._run_event_hub.publish(
                                            RunEvent(
                                                session_id=request.session_id,
                                                run_id=request.run_id,
                                                trace_id=request.trace_id,
                                                task_id=request.task_id,
                                                instance_id=request.instance_id,
                                                role_id=request.role_id,
                                                event_type=RunEventType.TEXT_DELTA,
                                                payload_json=dumps(
                                                    {
                                                        "text": text_delta,
                                                        "role_id": request.role_id,
                                                        "instance_id": request.instance_id,
                                                    }
                                                ),
                                            )
                                        )
                            usage_after = stream.usage()
                            request_level_input_tokens += self._usage_delta_int(
                                after=usage_after,
                                before=usage_before,
                                field_name="input_tokens",
                            )
                            request_level_output_tokens += self._usage_delta_int(
                                after=usage_after,
                                before=usage_before,
                                field_name="output_tokens",
                            )
                            request_level_requests += self._usage_delta_int(
                                after=usage_after,
                                before=usage_before,
                                field_name="requests",
                            )
                            saw_request_level_usage = True

                        # After each node (ModelRequestNode or others like CallToolsNode),
                        # scan for new messages to emit tool call/result events
                        all_new = agent_run.new_messages()
                        new_to_process = list(all_new)[seen_count:]
                        if new_to_process:
                            self._publish_tool_call_events_from_messages(
                                request=request,
                                messages=new_to_process,
                            )
                            buffered_messages.extend(new_to_process)
                            history, buffered_messages = self._commit_ready_messages(
                                request=request,
                                history=history,
                                pending_messages=buffered_messages,
                            )
                            seen_count += len(new_to_process)

                        # Drain pending user injections at this boundary (already handled in previous version, check if needed here)
                        injections = self._injection_manager.drain_at_boundary(
                            request.run_id, request.instance_id
                        )
                        if injections:
                            extra = [
                                ModelRequest(
                                    parts=[UserPromptPart(content=msg.content)]
                                )
                                for msg in injections
                            ]
                            for msg in injections:
                                self._run_event_hub.publish(
                                    RunEvent(
                                        session_id=request.session_id,
                                        run_id=request.run_id,
                                        trace_id=request.trace_id,
                                        task_id=request.task_id,
                                        instance_id=request.instance_id,
                                        role_id=request.role_id,
                                        event_type=RunEventType.INJECTION_APPLIED,
                                        payload_json=msg.model_dump_json(),
                                    )
                                )
                            self._message_repo.append(
                                session_id=request.session_id,
                                workspace_id=resolved_workspace_id,
                                conversation_id=resolved_conversation_id,
                                agent_role_id=request.role_id,
                                instance_id=request.instance_id,
                                task_id=request.task_id,
                                trace_id=request.trace_id,
                                messages=extra,
                            )
                            # Restart iter() with injected messages appended to committed history
                            history = self._filter_model_messages(
                                self._message_repo.get_history_for_conversation(
                                    resolved_conversation_id
                                )
                            )
                            seen_count = 0
                            buffered_messages = []
                            restarted = True
                            break  # break inner for-loop, restart while

                if not restarted:
                    # Normal completion
                    maybe_result = agent_run.result
                    if maybe_result is None:
                        raise RuntimeError("Model run finished without a result object")
                    result = maybe_result
                    # Flush any remaining messages (e.g. final tool results)
                    all_new = result.new_messages()
                    to_save = list(all_new)[seen_count:]
                    if to_save:
                        self._publish_tool_call_events_from_messages(
                            request=request,
                            messages=to_save,
                        )
                        buffered_messages.extend(to_save)
                    history, buffered_messages = self._commit_all_safe_messages(
                        request=request,
                        history=history,
                        pending_messages=buffered_messages,
                    )
                    # Record and publish token usage
                    usage = result.usage()
                    input_tokens = request_level_input_tokens
                    output_tokens = request_level_output_tokens
                    requests = request_level_requests
                    if not saw_request_level_usage:
                        input_tokens = self._usage_field_int(usage, "input_tokens")
                        output_tokens = self._usage_field_int(usage, "output_tokens")
                        requests = self._usage_field_int(usage, "requests")
                    tool_calls = self._usage_field_int(usage, "tool_calls")
                    if self._token_usage_repo is not None:
                        self._token_usage_repo.record(
                            session_id=request.session_id,
                            run_id=request.run_id,
                            instance_id=request.instance_id,
                            role_id=request.role_id,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            requests=requests,
                            tool_calls=tool_calls,
                        )
                    self._run_event_hub.publish(
                        RunEvent(
                            session_id=request.session_id,
                            run_id=request.run_id,
                            trace_id=request.trace_id,
                            task_id=request.task_id,
                            instance_id=request.instance_id,
                            role_id=request.role_id,
                            event_type=RunEventType.TOKEN_USAGE,
                            payload_json=dumps(
                                {
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
                                    "total_tokens": input_tokens + output_tokens,
                                    "requests": requests,
                                    "tool_calls": tool_calls,
                                    "role_id": request.role_id,
                                    "instance_id": request.instance_id,
                                }
                            ),
                        )
                    )
                    log_event(
                        LOGGER,
                        logging.INFO,
                        event="llm.token_usage.recorded",
                        message="LLM token usage recorded",
                        payload={
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "requests": requests,
                            "tool_calls": tool_calls,
                            "role_id": request.role_id,
                            "instance_id": request.instance_id,
                        },
                    )
                    break  # done
        except ModelAPIError as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                event="llm.request.failed",
                message="LLM provider request failed",
                payload={
                    "model": self._config.model,
                    "base_url": self._config.base_url,
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                },
                exc_info=exc,
            )
            raise ModelAPIError(
                model_name=exc.model_name,
                message=self._build_model_api_error_message(exc),
            ) from exc

        assert result is not None

        if printed_any:
            close_model_stream()

        text = self._extract_text(result.response)
        if not text and emitted_text_chunks:
            text = "".join(emitted_text_chunks)
        elif text and not emitted_text_chunks:
            self._run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.TEXT_DELTA,
                    payload_json=dumps(
                        {
                            "text": text,
                            "role_id": request.role_id,
                            "instance_id": request.instance_id,
                        }
                    ),
                )
            )
        if text and not printed_any:
            log_model_output(request.role_id, text)
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=RunEventType.MODEL_STEP_FINISHED,
                payload_json=dumps(
                    {"role_id": request.role_id, "instance_id": request.instance_id}
                ),
            )
        )
        log_event(
            LOGGER,
            logging.INFO,
            event="llm.request.completed",
            message="LLM request completed",
            payload={
                "model": self._config.model,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
                "task_id": request.task_id,
                "chars": len(text),
            },
        )
        return text

    def _usage_field_int(self, usage_obj: object, field_name: str) -> int:
        value = getattr(usage_obj, field_name, 0)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        return 0

    def _usage_delta_int(
        self, *, after: object, before: object, field_name: str
    ) -> int:
        after_value = self._usage_field_int(after, field_name)
        before_value = self._usage_field_int(before, field_name)
        delta = after_value - before_value
        return delta if delta > 0 else 0

    def _build_model_api_error_message(self, error: ModelAPIError) -> str:
        chain = self._exception_chain(error)
        if self._is_proxy_auth_failure(chain):
            return (
                f"{error.message} Proxy authentication failed (HTTP 407). "
                "Check HTTP_PROXY/HTTPS_PROXY credentials or set NO_PROXY for the model endpoint."
            )
        if self._is_connect_timeout(chain):
            return (
                f"{error.message} Connection to the model endpoint timed out. "
                "Check base_url, proxy/NO_PROXY settings, network reachability, "
                "or increase connect_timeout_seconds in the model profile."
            )

        root_message = self._deepest_distinct_exception_message(
            chain=chain,
            primary_message=error.message,
        )
        if root_message is None:
            return error.message
        return f"{error.message} Root cause: {root_message}"

    def _exception_chain(self, error: BaseException) -> tuple[BaseException, ...]:
        chain: list[BaseException] = []
        seen_ids: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen_ids:
            chain.append(current)
            seen_ids.add(id(current))
            if current.__cause__ is not None:
                current = current.__cause__
                continue
            if current.__suppress_context__:
                break
            current = current.__context__
        return tuple(chain)

    def _is_proxy_auth_failure(
        self,
        chain: Sequence[BaseException],
    ) -> bool:
        for error in chain:
            message = str(error).strip().lower()
            if "407 proxy authentication required" in message:
                return True
        return False

    def _is_connect_timeout(
        self,
        chain: Sequence[BaseException],
    ) -> bool:
        for error in chain:
            if error.__class__.__name__ == "ConnectTimeout":
                return True
        return False

    def _deepest_distinct_exception_message(
        self,
        *,
        chain: Sequence[BaseException],
        primary_message: str,
    ) -> str | None:
        normalized_primary = primary_message.strip()
        for error in reversed(chain):
            message = str(error).strip()
            if not message or message == normalized_primary:
                continue
            return message
        return None

    def _extract_text(self, response: object) -> str:
        parts = getattr(response, "parts", None)
        if isinstance(parts, list):
            texts: list[str] = []
            for part in cast(list[object], parts):
                content = getattr(part, "content", None)
                if isinstance(content, str) and content:
                    texts.append(content)
            if texts:
                return "".join(texts)
        return str(response)

    def _to_json(self, obj: object) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, default=str)
        except Exception:
            return json.dumps({"error": "unserializable", "repr": str(obj)})

    def _filter_model_messages(
        self, messages: Sequence[ModelRequest | ModelResponse]
    ) -> list[ModelRequest | ModelResponse]:
        return list(messages)

    def _collect_pending_tool_calls(
        self, messages: Sequence[ModelRequest | ModelResponse]
    ) -> list[tuple[str, str]]:
        pending_tool_call_ids: dict[str, str] = {}
        for msg in messages:
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if not isinstance(part, ToolCallPart):
                        continue
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if tool_call_id:
                        pending_tool_call_ids[tool_call_id] = str(part.tool_name)
                continue
            for part in msg.parts:
                tool_call_id = str(getattr(part, "tool_call_id", "") or "").strip()
                if not tool_call_id:
                    continue
                if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    pending_tool_call_ids.pop(tool_call_id, None)
        return list(pending_tool_call_ids.items())

    def _truncate_history_to_safe_boundary(
        self,
        history: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        messages = list(history)
        safe_index = self._last_committable_index(messages)
        return messages[:safe_index]

    def _persist_user_prompt_if_needed(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        content: str | None,
    ) -> list[ModelRequest | ModelResponse]:
        prompt = str(content or "").strip()
        if not prompt:
            return history
        if self._history_ends_with_user_prompt(history, prompt):
            return history
        self._message_repo.prune_conversation_history_to_safe_boundary(
            self._conversation_id(request)
        )
        prompt_message = ModelRequest(parts=[UserPromptPart(content=prompt)])
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=self._workspace_id(request),
            conversation_id=self._conversation_id(request),
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=[prompt_message],
        )
        return self._filter_model_messages(
            self._message_repo.get_history_for_conversation(
                self._conversation_id(request)
            )
        )

    def _history_ends_with_user_prompt(
        self,
        history: Sequence[ModelRequest | ModelResponse],
        content: str,
    ) -> bool:
        target = str(content or "").strip()
        if not target or not history:
            return False
        last = history[-1]
        if not isinstance(last, ModelRequest):
            return False
        parts = [part for part in last.parts if isinstance(part, UserPromptPart)]
        if len(parts) != len(last.parts):
            return False
        return (
            "\n".join(str(part.content or "").strip() for part in parts).strip()
            == target
        )

    def _commit_ready_messages(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
    ) -> tuple[list[ModelRequest | ModelResponse], list[ModelRequest | ModelResponse]]:
        safe_index = self._last_committable_index(pending_messages)
        if safe_index <= 0:
            return history, pending_messages
        ready = pending_messages[:safe_index]
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=self._workspace_id(request),
            conversation_id=self._conversation_id(request),
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=ready,
        )
        self._publish_committed_tool_outcome_events_from_messages(
            request=request,
            messages=ready,
        )
        next_history = self._filter_model_messages(
            self._message_repo.get_history_for_conversation(
                self._conversation_id(request)
            )
        )
        return next_history, pending_messages[safe_index:]

    def _commit_all_safe_messages(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
    ) -> tuple[list[ModelRequest | ModelResponse], list[ModelRequest | ModelResponse]]:
        next_history = history
        remaining = list(pending_messages)
        while remaining:
            safe_index = self._last_committable_index(remaining)
            if safe_index <= 0:
                break
            next_history, remaining = self._commit_ready_messages(
                request=request,
                history=next_history,
                pending_messages=remaining,
            )
        return next_history, remaining

    def _last_committable_index(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> int:
        pending: set[str] = set()
        last_safe_index = 0
        for index, msg in enumerate(messages, start=1):
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if not isinstance(part, ToolCallPart):
                        continue
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if tool_call_id:
                        pending.add(tool_call_id)
            else:
                for part in msg.parts:
                    tool_call_id = str(getattr(part, "tool_call_id", "") or "").strip()
                    if not tool_call_id:
                        continue
                    if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                        pending.discard(tool_call_id)
            if not pending:
                last_safe_index = index
        return last_safe_index

    def _publish_tool_call_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
    ) -> None:
        for msg in messages:
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if not isinstance(part, ToolCallPart):
                        continue
                    self._run_event_hub.publish(
                        RunEvent(
                            session_id=request.session_id,
                            run_id=request.run_id,
                            trace_id=request.trace_id,
                            task_id=request.task_id,
                            instance_id=request.instance_id,
                            role_id=request.role_id,
                            event_type=RunEventType.TOOL_CALL,
                            payload_json=self._to_json(
                                {
                                    "tool_name": part.tool_name,
                                    "tool_call_id": part.tool_call_id,
                                    "args": part.args,
                                    "role_id": request.role_id,
                                    "instance_id": request.instance_id,
                                }
                            ),
                        )
                    )

    def _publish_committed_tool_outcome_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
    ) -> None:
        for msg in messages:
            if isinstance(msg, ModelResponse):
                continue
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    result_payload = cast(
                        JsonValue,
                        sanitize_task_status_payload(
                            self._to_json_compatible(cast(object, part.content))
                        ),
                    )
                    is_error = False
                    if isinstance(result_payload, dict):
                        payload_map = cast(dict[str, object], result_payload)
                        is_error = payload_map.get("ok") is False
                    self._run_event_hub.publish(
                        RunEvent(
                            session_id=request.session_id,
                            run_id=request.run_id,
                            trace_id=request.trace_id,
                            task_id=request.task_id,
                            instance_id=request.instance_id,
                            role_id=request.role_id,
                            event_type=RunEventType.TOOL_RESULT,
                            payload_json=self._to_json(
                                {
                                    "tool_name": str(part.tool_name),
                                    "tool_call_id": (
                                        str(part.tool_call_id)
                                        if part.tool_call_id
                                        else ""
                                    ),
                                    "result": result_payload,
                                    "error": is_error,
                                    "role_id": request.role_id,
                                    "instance_id": request.instance_id,
                                }
                            ),
                        )
                    )
                elif isinstance(part, RetryPromptPart) and part.tool_name:
                    self._run_event_hub.publish(
                        RunEvent(
                            session_id=request.session_id,
                            run_id=request.run_id,
                            trace_id=request.trace_id,
                            task_id=request.task_id,
                            instance_id=request.instance_id,
                            role_id=request.role_id,
                            event_type=RunEventType.TOOL_INPUT_VALIDATION_FAILED,
                            payload_json=self._to_json(
                                {
                                    "tool_name": part.tool_name,
                                    "tool_call_id": part.tool_call_id,
                                    "reason": "Input validation failed before tool execution.",
                                    "details": part.content,
                                    "role_id": request.role_id,
                                    "instance_id": request.instance_id,
                                }
                            ),
                        )
                    )

    def _to_json_compatible(self, value: object) -> JsonValue:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            entries = cast(list[object], value)
            return [self._to_json_compatible(entry) for entry in entries]
        if isinstance(value, dict):
            entries = cast(dict[object, object], value)
            return {
                str(key): self._to_json_compatible(entry)
                for key, entry in entries.items()
            }
        return str(value)

    def _workspace_id(self, request: LLMRequest) -> str:
        return request.workspace_id

    def _conversation_id(self, request: LLMRequest) -> str:
        return request.conversation_id or build_conversation_id(
            request.session_id,
            request.role_id,
        )
