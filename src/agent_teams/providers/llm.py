from __future__ import annotations

import json
from copy import deepcopy
import logging
from json import dumps
from pathlib import Path
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, cast, final, override

from pydantic import BaseModel, ConfigDict
from pydantic_ai._agent_graph import ModelRequestNode
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agent_teams.core.enums import RunEventType
from agent_teams.core.models import ModelEndpointConfig, RunEvent
from agent_teams.core.types import JsonValue
from agent_teams.state.event_log import EventLog
from agent_teams.logger import (
    close_model_stream,
    get_logger,
    log_event,
    log_model_output,
    log_model_stream_chunk,
)
from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_control_manager import RunControlManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.runtime.tool_approval_manager import ToolApprovalManager
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.token_usage_repo import TokenUsageRepository
from agent_teams.agents.builders.collaboration_agent import build_collaboration_agent
from agent_teams.tools.policy import ToolApprovalPolicy
from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime import ToolDeps
from agent_teams.mcp.registry import McpRegistry
from agent_teams.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from agent_teams.coordination.task_execution_service import TaskExecutionService
    from agent_teams.roles.registry import RoleRegistry

LOGGER = get_logger(__name__)


class _AgentRunResult(Protocol):
    @property
    def response(self) -> object: ...

    def new_messages(self) -> Sequence[ModelMessage]: ...

    def usage(self) -> object: ...


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    trace_id: str
    task_id: str
    session_id: str
    instance_id: str
    role_id: str
    system_prompt: str
    user_prompt: str


class LLMProvider:
    async def generate(self, _request: LLMRequest) -> str:
        raise NotImplementedError


class EchoProvider(LLMProvider):
    @override
    async def generate(self, request: LLMRequest) -> str:
        return f"ECHO: {request.user_prompt}"


@final
class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        config: ModelEndpointConfig,
        *,
        task_repo: TaskRepository,
        instance_pool: InstancePool,
        shared_store: SharedStore,
        event_bus: EventLog,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        agent_repo: AgentInstanceRepository,
        workspace_root: Path,
        tool_registry: ToolRegistry,
        mcp_registry: McpRegistry,
        skill_registry: SkillRegistry,
        allowed_tools: tuple[str, ...],
        allowed_mcp_servers: tuple[str, ...],
        allowed_skills: tuple[str, ...],
        message_repo: MessageRepository,
        role_registry: "RoleRegistry",
        task_execution_service: "TaskExecutionService",
        run_control_manager: RunControlManager,
        tool_approval_manager: ToolApprovalManager,
        tool_approval_policy: ToolApprovalPolicy,
        token_usage_repo: TokenUsageRepository | None = None,
    ) -> None:
        self._config = config
        self._task_repo = task_repo
        self._instance_pool = instance_pool
        self._shared_store = shared_store
        self._event_bus = event_bus
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._agent_repo = agent_repo
        self._workspace_root = workspace_root
        self._tool_registry = tool_registry
        self._mcp_registry = mcp_registry
        self._skill_registry = skill_registry
        self._allowed_tools = allowed_tools
        self._allowed_mcp_servers = allowed_mcp_servers
        self._allowed_skills = allowed_skills
        self._role_registry = role_registry
        self._task_execution_service = task_execution_service
        self._run_control_manager = run_control_manager
        self._message_repo = message_repo
        self._tool_approval_manager = tool_approval_manager
        self._tool_approval_policy = tool_approval_policy
        self._token_usage_repo = token_usage_repo

    @override
    async def generate(self, request: LLMRequest) -> str:
        return await self._generate_async(request)

    async def _generate_async(self, request: LLMRequest) -> str:
        tool_rules = f"Available tools: {', '.join(self._allowed_tools)}."
        log_event(
            LOGGER,
            logging.DEBUG,
            event="runtime.debug",
            message=(
                f"[llm:start] role={request.role_id} run={request.run_id} "
                f"task={request.task_id} instance={request.instance_id}"
            ),
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
        model_settings: dict[str, object] = {
            # Some OpenAI-compatible providers return cumulative usage in each stream chunk.
            # Enabling this flag makes pydantic-ai keep the last chunk usage instead of summing chunks.
            "openai_continuous_usage_stats": True,
        }
        agent = build_collaboration_agent(
            model_name=self._config.model,
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            system_prompt=f"{request.system_prompt}\n\n{tool_rules}",
            allowed_tools=self._allowed_tools,
            model_settings=model_settings,
            allowed_mcp_servers=self._allowed_mcp_servers,
            allowed_skills=self._allowed_skills,
            tool_registry=self._tool_registry,
            mcp_registry=self._mcp_registry,
            skill_registry=self._skill_registry,
        )
        deps = ToolDeps(
            task_repo=self._task_repo,
            instance_pool=self._instance_pool,
            shared_store=self._shared_store,
            event_bus=self._event_bus,
            injection_manager=self._injection_manager,
            run_event_hub=self._run_event_hub,
            agent_repo=self._agent_repo,
            workspace_root=self._workspace_root,
            run_id=request.run_id,
            trace_id=request.trace_id,
            task_id=request.task_id,
            session_id=request.session_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            role_registry=self._role_registry,
            task_execution_service=self._task_execution_service,
            run_control_manager=self._run_control_manager,
            tool_approval_manager=self._tool_approval_manager,
            tool_approval_policy=self._tool_approval_policy,
        )
        control_ctx = self._run_control_manager.context(
            run_id=request.run_id,
            instance_id=request.instance_id,
        )

        printed_any = False
        emitted_text_chunks: list[str] = []
        history: list[ModelRequest | ModelResponse] = self._filter_model_messages(
            self._message_repo.get_history(request.instance_id)
        )
        saved_count = 0
        restarted = False
        result: _AgentRunResult | None = None
        is_first_iteration = True
        request_level_input_tokens = 0
        request_level_output_tokens = 0
        request_level_requests = 0
        saw_request_level_usage = False

        while True:
            control_ctx.raise_if_cancelled()
            restarted = False
            prompt_for_iteration = request.user_prompt if is_first_iteration else None
            is_first_iteration = False
            async with agent.iter(
                prompt_for_iteration,
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
                                    log_model_stream_chunk(request.role_id, text_delta)
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
                    new_to_process = list(all_new)[saved_count:]
                    if new_to_process:
                        self._publish_tool_events_from_messages(
                            request=request,
                            messages=new_to_process,
                        )

                        # Persist to repo
                        self._message_repo.append(
                            session_id=request.session_id,
                            instance_id=request.instance_id,
                            task_id=request.task_id,
                            trace_id=request.trace_id,
                            messages=new_to_process,
                        )
                        saved_count += len(new_to_process)

                    # Drain pending user injections at this boundary (already handled in previous version, check if needed here)
                    injections = self._injection_manager.drain_at_boundary(
                        request.run_id, request.instance_id
                    )
                    if injections:
                        extra = [
                            ModelRequest(parts=[UserPromptPart(content=msg.content)])
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
                        # Restart iter() with injected messages appended to history
                        history = self._filter_model_messages(
                            list(agent_run.new_messages()) + extra
                        )
                        saved_count = len(history) - len(extra)
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
                to_save = list(all_new)[saved_count:]
                if to_save:
                    self._message_repo.append(
                        session_id=request.session_id,
                        instance_id=request.instance_id,
                        task_id=request.task_id,
                        trace_id=request.trace_id,
                        messages=to_save,
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
                break  # done

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
            logging.DEBUG,
            event="runtime.debug",
            message=(
                f"[llm:done] role={request.role_id} run={request.run_id} "
                f"task={request.task_id} chars={len(text)}"
            ),
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

    def _publish_tool_events_from_messages(
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
            else:
                for part in msg.parts:
                    if isinstance(part, ToolReturnPart):
                        result_payload = self._to_json_compatible(
                            cast(object, part.content)
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
