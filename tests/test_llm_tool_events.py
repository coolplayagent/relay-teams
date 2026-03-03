import json

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


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


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
        injection_manager=None,
        run_event_hub=hub,
        agent_repo=None,
        workspace_root=None,
        tool_registry=None,
        mcp_registry=None,
        skill_registry=None,
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
        message_repo=None,
        role_registry=None,
        task_execution_service=None,
        tool_approval_manager=None,
        tool_approval_policy=None,
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
