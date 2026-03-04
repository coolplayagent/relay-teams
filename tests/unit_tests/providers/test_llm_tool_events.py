import json
from pathlib import Path
from typing import cast

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from agent_teams.core.enums import RunEventType
from agent_teams.core.models import ModelEndpointConfig
from agent_teams.providers.llm import LLMRequest, OpenAICompatibleProvider
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_control_manager import RunControlManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.runtime.tool_approval_manager import ToolApprovalManager
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.message_repo import MessageRepository
from agent_teams.tools.policy import ToolApprovalPolicy
from agent_teams.tools.registry import ToolRegistry
from agent_teams.mcp.registry import McpRegistry
from agent_teams.roles.registry import RoleRegistry
from agent_teams.skills.registry import SkillRegistry
from agent_teams.coordination.task_execution_service import TaskExecutionService


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


class _FakeRunControlManager:
    def is_run_stop_requested(self, run_id: str) -> bool:
        return False

    def is_subagent_stop_requested(self, *, run_id: str, instance_id: str) -> bool:
        return False


def _provider_with_hub(hub: _FakeRunEventHub) -> OpenAICompatibleProvider:
    config = ModelEndpointConfig(
        model='gpt-test',
        base_url='http://localhost',
        api_key='test-key',
    )
    return OpenAICompatibleProvider(
        config,
        task_repo=None,
        instance_pool=None,
        shared_store=None,
        event_bus=None,
        injection_manager=cast(RunInjectionManager, object()),
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        agent_repo=cast(AgentInstanceRepository, object()),
        workspace_root=Path("."),
        tool_registry=cast(ToolRegistry, object()),
        mcp_registry=cast(McpRegistry, object()),
        skill_registry=cast(SkillRegistry, object()),
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
        message_repo=cast(MessageRepository, object()),
        role_registry=cast(RoleRegistry, object()),
        task_execution_service=cast(TaskExecutionService, object()),
        run_control_manager=cast(RunControlManager, cast(object, _FakeRunControlManager())),
        tool_approval_manager=cast(ToolApprovalManager, object()),
        tool_approval_policy=cast(ToolApprovalPolicy, object()),
    )


def _request() -> LLMRequest:
    return LLMRequest(
        run_id='run-1',
        trace_id='trace-1',
        task_id='task-1',
        session_id='session-1',
        instance_id='inst-1',
        role_id='coordinator_agent',
        system_prompt='sys',
        user_prompt='user',
    )


def test_publish_tool_events_emits_call_validation_failure_and_result() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)

    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name='create_workflow_graph',
                    args={'objective': 'x'},
                    tool_call_id='call-1',
                )
            ]
        ),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    content='Invalid arguments for tool create_workflow_graph',
                    tool_name='create_workflow_graph',
                    tool_call_id='call-1',
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name='create_workflow_graph',
                    content={'ok': True},
                    tool_call_id='call-2',
                )
            ]
        ),
    ]

    provider._publish_tool_events_from_messages(
        request=_request(),
        messages=messages,
    )

    event_types = [event.event_type for event in hub.events]
    assert event_types == [
        RunEventType.TOOL_CALL,
        RunEventType.TOOL_INPUT_VALIDATION_FAILED,
        RunEventType.TOOL_RESULT,
    ]

    tool_call_payload = json.loads(hub.events[0].payload_json)
    assert tool_call_payload['tool_name'] == 'create_workflow_graph'
    assert tool_call_payload['tool_call_id'] == 'call-1'

    validation_payload = json.loads(hub.events[1].payload_json)
    assert validation_payload['tool_name'] == 'create_workflow_graph'
    assert validation_payload['tool_call_id'] == 'call-1'
    assert validation_payload['reason'] == 'Input validation failed before tool execution.'
    assert validation_payload['details'] == 'Invalid arguments for tool create_workflow_graph'

    tool_result_payload = json.loads(hub.events[2].payload_json)
    assert tool_result_payload['tool_name'] == 'create_workflow_graph'
    assert tool_result_payload['tool_call_id'] == 'call-2'
    assert tool_result_payload['error'] is False


def test_publish_tool_events_skips_retry_without_tool_name() -> None:
    hub = _FakeRunEventHub()
    provider = _provider_with_hub(hub)

    provider._publish_tool_events_from_messages(
        request=_request(),
        messages=[ModelRequest(parts=[RetryPromptPart(content='retry output')])],
    )

    assert hub.events == []
