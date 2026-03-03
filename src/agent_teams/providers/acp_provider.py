from __future__ import annotations

from dataclasses import dataclass
from json import dumps

from agent_teams.acp.session_client import AgentSessionClient, SessionInitSpec, TurnInput
from agent_teams.acp.session_pool import AcpSessionPool, SessionBinding
from agent_teams.core.enums import RunEventType
from agent_teams.core.models import RunEvent
from agent_teams.providers.llm import LLMProvider, LLMRequest
from agent_teams.runtime.run_event_hub import RunEventHub


@dataclass
class AcpSessionProvider(LLMProvider):
    session_client: AgentSessionClient
    session_pool: AcpSessionPool
    client_id: str
    tools: tuple[dict[str, object], ...]
    skills: tuple[dict[str, object], ...]
    mcp_servers: tuple[dict[str, object], ...]
    run_event_hub: RunEventHub

    async def generate(self, request: LLMRequest) -> str:
        binding = self.session_pool.get(request.instance_id)
        if binding is None or binding.client_id != self.client_id:
            if binding is not None:
                self.session_pool.pop(request.instance_id)
            handle = await self.session_client.open(
                SessionInitSpec(
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    session_id=request.session_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    system_prompt=request.system_prompt,
                    tools=self.tools,
                    skills=self.skills,
                    mcp_servers=self.mcp_servers,
                )
            )
            binding = SessionBinding(client_id=self.client_id, handle=handle)
            self.session_pool.set(request.instance_id, binding)

        self.run_event_hub.publish(
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

        try:
            result = await self.session_client.run_turn(
                binding.handle, TurnInput(user_prompt=request.user_prompt)
            )
        except Exception:
            self.session_pool.pop(request.instance_id)
            raise

        if result.text:
            self.run_event_hub.publish(
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
                            "text": result.text,
                            "role_id": request.role_id,
                            "instance_id": request.instance_id,
                        },
                        ensure_ascii=False,
                    ),
                )
            )

        for tool_call in result.tool_calls:
            self.run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.TOOL_CALL,
                    payload_json=dumps(tool_call, ensure_ascii=False, default=str),
                )
            )
        for tool_result in result.tool_results:
            self.run_event_hub.publish(
                RunEvent(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    trace_id=request.trace_id,
                    task_id=request.task_id,
                    instance_id=request.instance_id,
                    role_id=request.role_id,
                    event_type=RunEventType.TOOL_RESULT,
                    payload_json=dumps(tool_result, ensure_ascii=False, default=str),
                )
            )

        self.run_event_hub.publish(
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
        return result.text

    async def close_instance(self, instance_id: str) -> None:
        binding = self.session_pool.pop(instance_id)
        if binding is None:
            return
        if binding.client_id != self.client_id:
            return
        await self.session_client.close(binding.handle)
