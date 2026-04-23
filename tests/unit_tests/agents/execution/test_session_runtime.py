# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast

import pytest

from .agent_llm_session_test_support import (
    AgentLlmSession,
    APIStatusError,
    BinaryContent,
    LlmRetryConfig,
    McpRegistry,
    MessageRepository,
    ModelCapabilities,
    ModelEndpointConfig,
    ModelModalityMatrix,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
    _FakeMessageRepo,
    _build_request,
    httpx,
)


@pytest.mark.asyncio
async def test_generate_async_deduplicates_against_provider_history() -> None:
    session = object.__new__(AgentLlmSession)

    class _FakeInjectionManager:
        def drain_at_boundary(self, run_id: str, instance_id: str) -> list[object]:
            _ = (run_id, instance_id)
            return []

    class _FakeControlContext:
        def raise_if_cancelled(self) -> None:
            return None

    class _FakeRunControlManager:
        def context(self, *, run_id: str, instance_id: str) -> _FakeControlContext:
            _ = (run_id, instance_id)
            return _FakeControlContext()

    usage = SimpleNamespace(
        input_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
        requests=0,
        tool_calls=0,
        details={},
    )
    echoed_request = ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    "describe this image",
                    BinaryContent(data=b"image-bytes", media_type="image/png"),
                )
            )
        ]
    )
    final_response = ModelResponse(
        parts=[TextPart(content="done")],
        model_name="fake-model",
    )
    provider_history = [echoed_request]
    message_repo = _FakeMessageRepo(history=[])

    class _FakeResult:
        response = final_response

        def new_messages(self) -> list[ModelRequest | ModelResponse]:
            return [echoed_request, final_response]

        def usage(self) -> object:
            return usage

    class _FakeAgentRun:
        def __init__(self) -> None:
            self.result = _FakeResult()
            self._yielded = False

        async def __aenter__(self) -> "_FakeAgentRun":
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> bool | None:
            _ = (exc_type, exc, tb)
            return None

        def __aiter__(self) -> "_FakeAgentRun":
            self._yielded = False
            return self

        async def __anext__(self) -> object:
            if self._yielded:
                raise StopAsyncIteration
            self._yielded = True
            return object()

        def new_messages(self) -> list[ModelRequest | ModelResponse]:
            return [echoed_request, final_response]

        def usage(self) -> object:
            return usage

    class _FakeAgent:
        def iter(
            self,
            prompt: str | None,
            *,
            deps: object,
            message_history: Sequence[ModelRequest | ModelResponse],
            usage_limits: object,
        ) -> _FakeAgentRun:
            _ = (prompt, deps, message_history, usage_limits)
            return _FakeAgentRun()

    session.__dict__["_config"] = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        capabilities=ModelCapabilities(
            input=ModelModalityMatrix(text=True, image=True),
            output=ModelModalityMatrix(text=True),
        ),
    )
    session.__dict__["_profile_name"] = None
    session.__dict__["_retry_config"] = LlmRetryConfig()
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(MessageRepository, message_repo)
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_user_question_repo"] = None
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_run_intent_repo"] = cast(object, None)
    session.__dict__["_background_task_service"] = None
    session.__dict__["_todo_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve": lambda self, **kwargs: cast(object, None)},
    )()
    session.__dict__["_media_asset_service"] = cast(object, None)
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_subagent_reflection_service"] = None
    session.__dict__["_conversation_compaction_service"] = None
    session.__dict__["_conversation_microcompact_service"] = None
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_skill_registry"] = cast(object, None)
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_run_control_manager"] = _FakeRunControlManager()
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_user_question_manager"] = None
    session.__dict__["_tool_approval_policy"] = cast(object, None)
    session.__dict__["_notification_service"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_fallback_middleware"] = cast(object, None)
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_hook_service"] = None
    session.__dict__["_injection_manager"] = _FakeInjectionManager()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_resolve_tool_approval_policy"] = lambda run_id: cast(
        object, None
    )
    session.__dict__["_publish_committed_tool_outcome_events_from_messages"] = (
        lambda **kwargs: None
    )

    async def _build_agent_iteration_context(
        **kwargs: object,
    ) -> tuple[object, Sequence[ModelRequest | ModelResponse], str, object]:
        _ = kwargs
        prepared_prompt = SimpleNamespace(
            history=(),
            system_prompt="System prompt",
            budget=SimpleNamespace(),
            estimated_history_tokens_before_microcompact=0,
            estimated_history_tokens_after_microcompact=0,
            microcompact_compacted_message_count=0,
            microcompact_compacted_part_count=0,
        )
        return prepared_prompt, [], "System prompt", _FakeAgent()

    async def _close_run_scoped_llm_http_client(*, request: object) -> None:
        _ = request
        return None

    observed_histories: list[Sequence[ModelRequest | ModelResponse]] = []

    def _drop_duplicate_leading_request(
        *,
        history: Sequence[ModelRequest | ModelResponse],
        new_messages: list[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        observed_histories.append(history)
        return AgentLlmSession._drop_duplicate_leading_request(
            session,
            history=history,
            new_messages=new_messages,
        )

    session.__dict__["_provider_history_for_model_turn"] = lambda **kwargs: (
        provider_history
    )
    session.__dict__["_drop_duplicate_leading_request"] = (
        _drop_duplicate_leading_request
    )
    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context
    session.__dict__["_close_run_scoped_llm_http_client"] = (
        _close_run_scoped_llm_http_client
    )

    result = await AgentLlmSession._generate_async(
        session,
        _build_request(user_prompt=None),
        skip_initial_user_prompt_persist=True,
    )

    assert result == "done"
    assert observed_histories
    assert all(list(history) == provider_history for history in observed_histories)
    assert len(message_repo.append_calls) == 1
    assert message_repo.append_calls[0] == [final_response]


@pytest.mark.asyncio
async def test_generate_async_passes_retry_after_to_retry_schedule() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=600,
    )
    session.__dict__["_retry_config"] = LlmRetryConfig(
        jitter=False,
        max_retries=2,
        initial_delay_ms=2000,
    )
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(
        MessageRepository, _FakeMessageRepo(history=[])
    )
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve": lambda self, **kwargs: cast(object, None)},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy"] = lambda run_id: cast(
        object, None
    )
    session.__dict__["_build_model_api_error_message"] = lambda error: "rate limited"

    async def _no_recovery(**kwargs: object) -> None:
        _ = kwargs
        return None

    session.__dict__["_maybe_recover_from_tool_args_parse_failure"] = _no_recovery
    session.__dict__["_should_retry_request"] = lambda **kwargs: True

    captured_schedules: list[object] = []

    async def _capture_retry_scheduled(**kwargs: object) -> None:
        captured_schedules.append(kwargs["schedule"])
        raise RuntimeError("stop after scheduling retry")

    session.__dict__["_handle_retry_scheduled"] = _capture_retry_scheduled
    session.__dict__["_persist_user_prompt_if_needed"] = lambda **kwargs: (
        kwargs["history"],
        False,
    )
    session.__dict__["_run_control_manager"] = type(
        "_RunControlManager",
        (),
        {
            "context": lambda self, run_id, instance_id: type(
                "_ControlContext",
                (),
                {"raise_if_cancelled": lambda self: None},
            )()
        },
    )()

    class _FailingAgentContext:
        async def __aenter__(self) -> object:
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            response = httpx.Response(
                429,
                headers={"Retry-After": "7"},
                request=request,
            )
            raise APIStatusError(
                "rate limited",
                response=response,
                body={"error": {"code": "rate_limited", "message": "slow down"}},
            )

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    class _FailingAgent:
        def iter(self, *_args: object, **_kwargs: object) -> _FailingAgentContext:
            return _FailingAgentContext()

    async def _build_agent_iteration_context(
        **kwargs: object,
    ) -> tuple[str, list[object], str, object]:
        _ = kwargs
        return "", [], "System prompt", _FailingAgent()

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    with pytest.raises(RuntimeError, match="stop after scheduling retry"):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    assert len(captured_schedules) == 1
    schedule = captured_schedules[0]
    assert getattr(schedule, "delay_ms") == 7000


@pytest.mark.asyncio
async def test_generate_async_closes_scoped_transport_cache_on_cancellation() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=0)
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(
        MessageRepository, _FakeMessageRepo(history=[])
    )
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve": lambda self, **kwargs: cast(object, None)},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy"] = lambda run_id: cast(
        object, None
    )
    session.__dict__["_persist_user_prompt_if_needed"] = lambda **kwargs: (
        kwargs["history"],
        False,
    )

    class _CancelledControlContext:
        def raise_if_cancelled(self) -> None:
            raise asyncio.CancelledError()

    session.__dict__["_run_control_manager"] = type(
        "_RunControlManager",
        (),
        {
            "context": lambda self, run_id, instance_id: _CancelledControlContext(),
        },
    )()

    class _UnusedAgent:
        def iter(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("cancelled runs should not start agent iteration")

    async def _build_agent_iteration_context(
        **kwargs: object,
    ) -> tuple[str, list[object], str, object]:
        _ = kwargs
        return "", [], "System prompt", _UnusedAgent()

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    closed_run_ids: list[str] = []

    async def _close_run_scoped_llm_http_client(*, request: object) -> None:
        closed_run_ids.append(getattr(request, "run_id"))

    session.__dict__["_close_run_scoped_llm_http_client"] = (
        _close_run_scoped_llm_http_client
    )

    with pytest.raises(asyncio.CancelledError):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    assert closed_run_ids == ["run-1"]


@pytest.mark.asyncio
async def test_generate_async_closes_scoped_transport_cache_on_setup_failure() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=0)
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(
        MessageRepository, _FakeMessageRepo(history=[])
    )
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve": lambda self, **kwargs: cast(object, None)},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy"] = lambda run_id: cast(
        object, None
    )

    async def _build_agent_iteration_context(**kwargs: object) -> object:
        _ = kwargs
        raise RuntimeError("setup failed after creating scoped client")

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    closed_run_ids: list[str] = []

    async def _close_run_scoped_llm_http_client(*, request: object) -> None:
        closed_run_ids.append(getattr(request, "run_id"))

    session.__dict__["_close_run_scoped_llm_http_client"] = (
        _close_run_scoped_llm_http_client
    )

    with pytest.raises(RuntimeError, match="setup failed"):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    assert closed_run_ids == ["run-1"]


@pytest.mark.asyncio
async def test_generate_async_does_not_emit_retry_exhausted_after_fallback_exhausted() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="primary-model",
        base_url="https://example.test/v1",
        api_key="primary-key",
        fallback_policy_id="same_provider_then_other_provider",
    )
    session.__dict__["_profile_name"] = "primary"
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=0)
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_skill_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(
        MessageRepository, _FakeMessageRepo(history=[])
    )
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve": lambda self, **kwargs: cast(object, None)},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy"] = lambda run_id: cast(
        object, None
    )
    session.__dict__["_build_model_api_error_message"] = lambda error: "rate limited"
    session.__dict__["_persist_user_prompt_if_needed"] = lambda **kwargs: (
        kwargs["history"],
        False,
    )
    session.__dict__["_run_control_manager"] = type(
        "_RunControlManager",
        (),
        {
            "context": lambda self, run_id, instance_id: type(
                "_ControlContext",
                (),
                {"raise_if_cancelled": lambda self: None},
            )()
        },
    )()

    async def _no_recovery(**kwargs: object) -> None:
        _ = kwargs
        return None

    session.__dict__["_maybe_recover_from_tool_args_parse_failure"] = _no_recovery
    session.__dict__["_should_retry_request"] = lambda **kwargs: False
    session.__dict__["_fallback_middleware"] = type(
        "_FallbackMiddleware",
        (),
        {
            "has_enabled_policy": lambda self, config: True,
            "select_fallback": lambda self, **kwargs: None,
        },
    )()

    retry_exhausted_calls: list[dict[str, object]] = []
    fallback_exhausted_calls: list[dict[str, object]] = []
    retry_scheduled_calls: list[dict[str, object]] = []

    async def _capture_retry_scheduled(**kwargs: object) -> None:
        retry_scheduled_calls.append(kwargs)

    session.__dict__["_handle_retry_scheduled"] = _capture_retry_scheduled
    session.__dict__["_handle_retry_exhausted"] = lambda **kwargs: (
        retry_exhausted_calls.append(kwargs)
    )
    session.__dict__["_handle_fallback_exhausted"] = lambda **kwargs: (
        fallback_exhausted_calls.append(kwargs)
    )
    session.__dict__["_raise_assistant_run_error"] = lambda **kwargs: (
        _ for _ in ()
    ).throw(RuntimeError("stop after fallback exhaustion"))

    class _FailingAgentContext:
        async def __aenter__(self) -> object:
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            response = httpx.Response(
                429,
                headers={"Retry-After": "1"},
                request=request,
            )
            raise APIStatusError(
                "rate limited",
                response=response,
                body={"error": {"code": "rate_limit_exceeded", "message": "slow down"}},
            )

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    class _FailingAgent:
        def iter(self, *_args: object, **_kwargs: object) -> _FailingAgentContext:
            return _FailingAgentContext()

    async def _build_agent_iteration_context(
        **kwargs: object,
    ) -> tuple[str, list[object], str, object]:
        _ = kwargs
        return "", [], "System prompt", _FailingAgent()

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    with pytest.raises(RuntimeError, match="stop after fallback exhaustion"):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    assert len(fallback_exhausted_calls) == 1
    assert retry_scheduled_calls == []
    assert retry_exhausted_calls == []
