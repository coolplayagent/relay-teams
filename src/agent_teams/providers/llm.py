from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable  # noqa: F401  kept for type annotations if needed
from dataclasses import dataclass
from json import dumps
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai._agent_graph import ModelRequestNode
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent
)
from pydantic_ai._utils import get_event_loop

from agent_teams.core.enums import RunEventType
from agent_teams.core.models import ModelEndpointConfig, RunEvent
from agent_teams.runtime.console import close_model_stream, is_debug, log_debug, log_model_output, log_model_stream_chunk
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.message_repo import MessageRepository
from agent_teams.agents.builders.collaboration_agent import build_collaboration_agent
from agent_teams.tools.registry import ToolRegistry
from agent_teams.tools.runtime import ToolDeps
from agent_teams.mcp.registry import McpRegistry
from agent_teams.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from agent_teams.coordination.task_execution_service import TaskExecutionService
    from agent_teams.roles.registry import RoleRegistry


@dataclass(frozen=True)
class LLMRequest:
    run_id: str
    trace_id: str
    task_id: str
    session_id: str
    instance_id: str
    role_id: str
    system_prompt: str
    user_prompt: str


class LLMProvider:
    async def generate(self, request: LLMRequest) -> str:
        raise NotImplementedError


class EchoProvider(LLMProvider):
    async def generate(self, request: LLMRequest) -> str:
        return f'ECHO: {request.user_prompt}'


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        config: ModelEndpointConfig,
        *,
        task_repo,
        instance_pool,
        shared_store,
        event_bus,
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
        role_registry: 'RoleRegistry',
        task_execution_service: 'TaskExecutionService',
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
        self._message_repo = message_repo

    async def generate(self, request: LLMRequest) -> str:
        return await self._generate_async(request)

    async def _generate_async(self, request: LLMRequest) -> str:
        tool_rules = f'Available tools: {", ".join(self._allowed_tools)}.'
        if is_debug():
            log_debug(
                f'[llm:start] role={request.role_id} run={request.run_id} '
                f'task={request.task_id} instance={request.instance_id}'
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
                payload_json=dumps({'role_id': request.role_id, 'instance_id': request.instance_id}),
            )
        )
        agent = build_collaboration_agent(
            model_name=self._config.model,
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            system_prompt=f'{request.system_prompt}\n\n{tool_rules}',
            allowed_tools=self._allowed_tools,
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
        )

        printed_any = False
        emitted_text_chunks: list[str] = []
        history = self._message_repo.get_history(request.instance_id)
        saved_count = 0
        restarted = False

        while True:
            restarted = False
            async with agent.iter(
                request.user_prompt if not history else None,
                deps=deps,
                message_history=history,
            ) as agent_run:
                async for node in agent_run:
                    if isinstance(node, ModelRequestNode):
                        # Stream text chunks from this model response in real-time
                        async with node.stream(agent_run.ctx) as stream:
                            async for text_delta in stream.stream_text(delta=True):
                                if text_delta:
                                    if is_debug():
                                        print(text_delta, end='', flush=True)
                                    else:
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
                                            payload_json=dumps({
                                                'text': text_delta,
                                                'role_id': request.role_id,
                                                'instance_id': request.instance_id
                                            }),
                                        )
                                    )

                    # After each node (ModelRequestNode or others like CallToolsNode),
                    # scan for new messages to emit tool call/result events
                    all_new = agent_run.new_messages()
                    new_to_process = list(all_new)[saved_count:]
                    if new_to_process:
                        from pydantic_ai.messages import ModelResponse, ModelRequest, ToolCallPart, ToolReturnPart
                        
                        for msg in new_to_process:
                            if isinstance(msg, ModelResponse):
                                for part in msg.parts:
                                    if isinstance(part, ToolCallPart):
                                        self._run_event_hub.publish(
                                            RunEvent(
                                                session_id=request.session_id,
                                                run_id=request.run_id,
                                                trace_id=request.trace_id,
                                                task_id=request.task_id,
                                                instance_id=request.instance_id,
                                                role_id=request.role_id,
                                                event_type=RunEventType.TOOL_CALL,
                                                payload_json=self._to_json({
                                                    "tool_name": part.tool_name,
                                                    "args": part.args,
                                                    "role_id": request.role_id,
                                                    "instance_id": request.instance_id,
                                                }),
                                            )
                                        )
                            elif isinstance(msg, ModelRequest):
                                for part in msg.parts:
                                    if isinstance(part, ToolReturnPart):
                                        self._run_event_hub.publish(
                                            RunEvent(
                                                session_id=request.session_id,
                                                run_id=request.run_id,
                                                trace_id=request.trace_id,
                                                task_id=request.task_id,
                                                instance_id=request.instance_id,
                                                role_id=request.role_id,
                                                event_type=RunEventType.TOOL_RESULT,
                                                payload_json=self._to_json({
                                                    "tool_name": part.tool_name,
                                                    "result": part.content,
                                                    "error": False, # Basic assumption for now
                                                    "role_id": request.role_id,
                                                    "instance_id": request.instance_id,
                                                }),
                                            )
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
                        from pydantic_ai.messages import ModelRequest, UserPromptPart
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
                        history = list(agent_run.new_messages()) + extra
                        saved_count = len(history) - len(extra)
                        restarted = True
                        break  # break inner for-loop, restart while

            if not restarted:
                # Normal completion
                result = agent_run.result
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
                break  # done


        if printed_any and is_debug():
            print()
        if printed_any and not is_debug():
            close_model_stream()

        text = self._extract_text(result.response)
        if not text and emitted_text_chunks:
            text = ''.join(emitted_text_chunks)
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
                    payload_json=dumps({
                        'text': text,
                        'role_id': request.role_id,
                        'instance_id': request.instance_id
                    }),
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
                payload_json=dumps({'role_id': request.role_id, 'instance_id': request.instance_id}),
            )
        )
        if is_debug():
            log_debug(
                f'[llm:done] role={request.role_id} run={request.run_id} '
                f'task={request.task_id} chars={len(text)}'
            )
        return text

    def _extract_text(self, response: object) -> str:
        parts = getattr(response, 'parts', None)
        if isinstance(parts, list):
            texts: list[str] = []
            for part in parts:
                content = getattr(part, 'content', None)
                if isinstance(content, str) and content:
                    texts.append(content)
            if texts:
                return ''.join(texts)
        return str(response)

    def _to_json(self, obj: Any) -> str:
        import json
        try:
            return json.dumps(obj, ensure_ascii=False, default=str)
        except Exception:
            return json.dumps({"error": "unserializable", "repr": str(obj)})
