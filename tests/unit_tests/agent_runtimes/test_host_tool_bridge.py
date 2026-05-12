# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from types import SimpleNamespace
from typing import cast

import pytest
import relay_teams.agent_runtimes.host_tool_bridge as host_tool_bridge_module
from pydantic import JsonValue
from pydantic_ai.messages import BinaryContent, ToolReturn
from relay_teams.providers.model_config import (
    ModelCapabilities,
    ModelEndpointConfig,
    ProviderType,
)
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.reminders import render_system_reminder
from relay_teams.reminders.delivery import SystemReminderDeliveryMode
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.sessions.runs.enums import InjectionSource
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.tools.runtime.policy import ToolApprovalPolicy


class _PublicTool:
    pass


class _PublicToolResult:
    pass


class _LegacyTool:
    pass


class _LegacyToolResult:
    pass


class _MissingRunIntentRepo:
    async def get_async(self, _run_id: str) -> object:
        raise KeyError


class _FakeToolApprovalPolicy:
    def with_yolo(self, _yolo: bool) -> "_FakeToolApprovalPolicy":
        return self

    def with_runtime_overrides(
        self,
        *,
        yolo: bool | None = None,
        shell_safety_policy_enabled: bool | None = None,
    ) -> "_FakeToolApprovalPolicy":
        _ = (yolo, shell_safety_policy_enabled)
        return self


class _RunIntentRepo:
    def __init__(self, *, yolo: bool, shell_safety_policy_enabled: bool) -> None:
        self._intent = SimpleNamespace(
            yolo=yolo,
            shell_safety_policy_enabled=shell_safety_policy_enabled,
        )

    async def get_async(self, _run_id: str) -> object:
        return self._intent


class _CapturingRunEventHub:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> int:
        self.events.append(event)
        return 0


class _FakeMessageRepo:
    def __init__(self) -> None:
        self.appended_user_prompts: list[dict[str, object]] = []

    async def append_user_prompt_if_missing_async(
        self,
        **kwargs: object,
    ) -> bool:
        self.appended_user_prompts.append(kwargs)
        return True


class _FakeWorkspaceManager:
    async def resolve_async(self, **_kwargs: object) -> object:
        return object()


class _FakeArgumentValidator:
    def validate_python(self, value: object) -> dict[str, object]:
        return dict(cast(dict[str, object], value))


class _FakeFunctionSchema:
    takes_ctx = True
    positional_fields = ("value",)
    var_positional_field = "items"

    def __init__(self) -> None:
        self.validator = _FakeArgumentValidator()
        self.calls: list[tuple[dict[str, object], object]] = []

    async def call(self, args_dict: dict[str, object], ctx: object) -> object:
        self.calls.append((args_dict, ctx))
        return {"called": args_dict["value"]}


class _FakeRuntimeTool:
    def __init__(self, function_schema: _FakeFunctionSchema) -> None:
        self.function_schema = function_schema
        self.args_validator: Callable[..., object] | None = None


class _FakeLifespanManager:
    def __init__(self) -> None:
        self.entered = False

    async def __aenter__(self) -> None:
        self.entered = True

    async def __aexit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        self.entered = False


class _FakeFastMcpServer:
    def __init__(self, lifespan_manager: _FakeLifespanManager) -> None:
        self._mcp_server = object()
        self._lifespan_manager = lambda: lifespan_manager


class _FakeWriteStream:
    def __init__(self, connection: object) -> None:
        self._connection = connection

    async def send(self, _message: object) -> None:
        connection = cast(
            host_tool_bridge_module._HostedMcpConnection, self._connection
        )
        pending = tuple(connection._pending.values())
        assert len(pending) == 1
        pending[0].set_result({"ok": True})


def test_load_fastmcp_tool_types_prefers_public_exports(monkeypatch) -> None:
    public_module = ModuleType("fastmcp.tools")
    setattr(public_module, "Tool", _PublicTool)
    setattr(public_module, "ToolResult", _PublicToolResult)

    def fake_import_module(name: str) -> ModuleType:
        assert name == "fastmcp.tools"
        return public_module

    monkeypatch.setattr(
        host_tool_bridge_module.importlib, "import_module", fake_import_module
    )

    tool_cls, tool_result_cls = host_tool_bridge_module._load_fastmcp_tool_types()

    assert tool_cls is _PublicTool
    assert tool_result_cls is _PublicToolResult


def test_load_fastmcp_tool_types_falls_back_to_legacy_module(monkeypatch) -> None:
    public_module = ModuleType("fastmcp.tools")
    legacy_module = ModuleType("fastmcp.tools.tool")
    setattr(legacy_module, "Tool", _LegacyTool)
    setattr(legacy_module, "ToolResult", _LegacyToolResult)
    requested_modules: list[str] = []

    def fake_import_module(name: str) -> ModuleType:
        requested_modules.append(name)
        if name == "fastmcp.tools":
            return public_module
        if name == "fastmcp.tools.tool":
            return legacy_module
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr(
        host_tool_bridge_module.importlib, "import_module", fake_import_module
    )

    tool_cls, tool_result_cls = host_tool_bridge_module._load_fastmcp_tool_types()

    assert requested_modules == ["fastmcp.tools", "fastmcp.tools.tool"]
    assert tool_cls is _LegacyTool
    assert tool_result_cls is _LegacyToolResult


def test_tool_return_to_fastmcp_result_preserves_envelope_and_image_content() -> None:
    tool_result = host_tool_bridge_module._tool_return_to_fastmcp_result(
        ToolReturn(
            return_value={"ok": True, "data": {"path": "docs/diagram.png"}},
            content=(
                "Image attached for model inspection.",
                BinaryContent(data=b"png-bytes", media_type="image/png"),
            ),
        )
    )

    assert tool_result.structured_content == {
        "ok": True,
        "data": {"path": "docs/diagram.png"},
    }
    assert len(tool_result.content) == 2
    assert tool_result.content[0].type == "text"
    assert tool_result.content[1].type == "image"
    assert tool_result.content[1].data == "cG5nLWJ5dGVz"
    assert tool_result.content[1].mimeType == "image/png"


def test_tool_return_to_fastmcp_result_preserves_scalar_value_with_media_content() -> (
    None
):
    tool_result = host_tool_bridge_module._tool_return_to_fastmcp_result(
        ToolReturn(
            return_value="primary result",
            content=(BinaryContent(data=b"png-bytes", media_type="image/png"),),
        )
    )

    assert tool_result.structured_content is None
    assert len(tool_result.content) == 2
    assert tool_result.content[0].type == "text"
    assert tool_result.content[0].text == "primary result"
    assert tool_result.content[1].type == "image"
    assert tool_result.content[1].data == "cG5nLWJ5dGVz"


@pytest.mark.asyncio
async def test_build_tool_deps_uses_resolved_model_capabilities() -> None:
    resolved_config = ModelEndpointConfig(
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="qwen3-vl",
        base_url="https://example.com/v1",
        api_key="test-key",
        capabilities=ModelCapabilities.model_validate(
            {
                "input": {
                    "text": True,
                    "image": True,
                },
                "output": {
                    "text": True,
                },
            }
        ),
    )
    requested: list[tuple[RoleDefinition, LLMRequest]] = []

    def resolve_model_config(
        role: RoleDefinition,
        request: LLMRequest,
    ) -> ModelEndpointConfig | None:
        requested.append((role, request))
        return resolved_config

    bridge = object.__new__(host_tool_bridge_module.ExternalAcpHostToolBridge)
    runtime_role_resolver = object()
    role = RoleDefinition(
        role_id="main-agent",
        name="Main Agent",
        description="Handles ACP prompts",
        version="1.0.0",
        system_prompt="You are a helpful assistant.",
    )
    request = LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        role_id=role.role_id,
        system_prompt="system",
        user_prompt="hello",
    )
    bridge.__dict__.update(
        {
            "_task_repo": object(),
            "_shared_store": object(),
            "_event_bus": object(),
            "_message_repo": object(),
            "_approval_ticket_repo": object(),
            "_user_question_repo": None,
            "_run_runtime_repo": object(),
            "_injection_manager": object(),
            "_run_event_hub": object(),
            "_agent_repo": object(),
            "_workspace_manager": _FakeWorkspaceManager(),
            "_media_asset_service": None,
            "_computer_runtime": None,
            "_background_task_service": None,
            "_monitor_service": None,
            "_todo_service": None,
            "_run_intent_repo": _MissingRunIntentRepo(),
            "_get_role_registry": object,
            "_get_skill_registry": object,
            "_get_mcp_registry": object,
            "_get_task_service": object,
            "_get_task_execution_service": object,
            "_runtime_role_resolver": runtime_role_resolver,
            "_run_control_manager": object(),
            "_tool_approval_manager": object(),
            "_user_question_manager": None,
            "_tool_approval_policy": _FakeToolApprovalPolicy(),
            "_shell_approval_repo": None,
            "_metric_recorder": None,
            "_get_notification_service": lambda: None,
            "_im_tool_service": None,
            "_resolve_model_config": resolve_model_config,
            "_role": role,
        }
    )

    deps = await bridge._build_tool_deps_async(request=request)

    assert requested == [(role, request)]
    assert deps.model_capabilities == resolved_config.capabilities
    assert deps.runtime_role_resolver is runtime_role_resolver


@pytest.mark.asyncio
async def test_build_tool_deps_applies_shell_safety_policy_from_run_intent() -> None:
    bridge = object.__new__(host_tool_bridge_module.ExternalAcpHostToolBridge)
    request = LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        role_id="main-agent",
        system_prompt="system",
        user_prompt="hello",
    )
    bridge.__dict__.update(
        {
            "_task_repo": object(),
            "_shared_store": object(),
            "_event_bus": object(),
            "_message_repo": object(),
            "_approval_ticket_repo": object(),
            "_user_question_repo": None,
            "_run_runtime_repo": object(),
            "_injection_manager": object(),
            "_run_event_hub": object(),
            "_agent_repo": object(),
            "_workspace_manager": _FakeWorkspaceManager(),
            "_media_asset_service": None,
            "_computer_runtime": None,
            "_background_task_service": None,
            "_monitor_service": None,
            "_todo_service": None,
            "_run_intent_repo": _RunIntentRepo(
                yolo=True,
                shell_safety_policy_enabled=False,
            ),
            "_get_role_registry": lambda: object(),
            "_get_skill_registry": lambda: object(),
            "_get_mcp_registry": lambda: object(),
            "_get_task_service": lambda: object(),
            "_get_task_execution_service": lambda: object(),
            "_runtime_role_resolver": object(),
            "_run_control_manager": object(),
            "_tool_approval_manager": object(),
            "_user_question_manager": None,
            "_tool_approval_policy": ToolApprovalPolicy(),
            "_shell_approval_repo": None,
            "_metric_recorder": None,
            "_get_notification_service": lambda: None,
            "_im_tool_service": None,
            "_resolve_model_config": None,
            "_role": None,
        }
    )

    deps = await bridge._build_tool_deps_async(request=request)

    assert deps.tool_approval_policy.yolo is True
    assert deps.tool_approval_policy.shell_safety_policy_enabled is False


def test_host_tool_bridge_init_stores_model_resolver(
    monkeypatch,
) -> None:
    def resolver(role, request):
        _ = (role, request)
        return None

    monkeypatch.setattr(
        host_tool_bridge_module,
        "build_coordination_agent",
        lambda **kwargs: SimpleNamespace(model=object()),
    )
    kwargs: dict[str, object] = {
        "task_repo": cast(object, object()),
        "shared_store": cast(object, object()),
        "event_bus": cast(object, object()),
        "injection_manager": cast(object, object()),
        "run_event_hub": cast(object, object()),
        "agent_repo": cast(object, object()),
        "approval_ticket_repo": cast(object, object()),
        "user_question_repo": None,
        "run_runtime_repo": cast(object, object()),
        "run_intent_repo": cast(object, object()),
        "background_task_service": None,
        "todo_service": None,
        "monitor_service": None,
        "workspace_manager": cast(object, object()),
        "tool_registry": cast(object, object()),
        "message_repo": cast(object, object()),
        "get_mcp_registry": lambda: cast(object, object()),
        "get_skill_registry": lambda: cast(object, object()),
        "get_role_registry": lambda: cast(object, object()),
        "get_task_execution_service": lambda: cast(object, object()),
        "get_task_service": lambda: cast(object, object()),
        "run_control_manager": cast(object, object()),
        "tool_approval_manager": cast(object, object()),
        "user_question_manager": None,
        "tool_approval_policy": _FakeToolApprovalPolicy(),
        "get_notification_service": lambda: None,
        "runtime_role_resolver": object(),
        "resolve_model_config": resolver,
    }
    bridge_ctor = cast(
        Callable[..., object], host_tool_bridge_module.ExternalAcpHostToolBridge
    )
    bridge = cast(
        host_tool_bridge_module.ExternalAcpHostToolBridge, bridge_ctor(**kwargs)
    )

    assert bridge._resolve_model_config is resolver
    assert bridge._runtime_role_resolver is kwargs["runtime_role_resolver"]


def test_resolve_request_model_capabilities_defaults_when_resolution_is_unavailable() -> (
    None
):
    request = LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        role_id="main-agent",
        system_prompt="system",
        user_prompt="hello",
    )
    bridge = object.__new__(host_tool_bridge_module.ExternalAcpHostToolBridge)

    bridge.__dict__["_role"] = None
    bridge.__dict__["_resolve_model_config"] = None
    assert (
        bridge._resolve_request_model_capabilities(request=request)
        == ModelCapabilities()
    )

    role = RoleDefinition(
        role_id="main-agent",
        name="Main Agent",
        description="Handles ACP prompts",
        version="1.0.0",
        system_prompt="You are a helpful assistant.",
    )
    bridge.__dict__["_role"] = role
    bridge.__dict__["_resolve_model_config"] = lambda _role, _request: None

    assert (
        bridge._resolve_request_model_capabilities(request=request)
        == ModelCapabilities()
    )


def test_json_object_filters_non_json_values() -> None:
    assert host_tool_bridge_module._json_object(
        {
            "valid": [1, {"nested": None}],
            "invalid": object(),
            3: "skipped",
        }
    ) == {"valid": [1, {"nested": None}]}
    assert host_tool_bridge_module._json_object(object()) == {}
    assert host_tool_bridge_module._is_json_value({"invalid": object()}) is False


def test_function_tools_and_call_args_extract_host_tool_invocation() -> None:
    function_schema = _FakeFunctionSchema()
    tool = cast(
        host_tool_bridge_module.PydanticTool[host_tool_bridge_module.ToolDeps],
        _FakeRuntimeTool(function_schema),
    )
    toolset = host_tool_bridge_module.FunctionToolset()
    toolset.tools["sample"] = tool

    assert host_tool_bridge_module._function_tools((object(), toolset)) == (tool,)

    ctx = cast(
        host_tool_bridge_module.RunContext[host_tool_bridge_module.ToolDeps],
        object(),
    )
    args, kwargs = host_tool_bridge_module._tool_call_args(
        tool=tool,
        args_dict={"value": "input", "items": ["one", "two"], "flag": True},
        ctx=ctx,
    )

    assert args == [ctx, "input", "one", "two"]
    assert kwargs == {"flag": True}


@pytest.mark.asyncio
async def test_run_hosted_tool_invokes_async_args_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        role_id="main-agent",
        system_prompt="system",
        user_prompt="hello",
    )
    role = RoleDefinition(
        role_id="main-agent",
        name="Main Agent",
        description="Handles ACP prompts",
        version="1.0.0",
        system_prompt="You are a helpful assistant.",
    )
    seen_validator_args: list[tuple[object, object, tuple[object, ...], object]] = []
    function_schema = _FakeFunctionSchema()
    raw_tool = _FakeRuntimeTool(function_schema)

    async def args_validator(
        ctx: object,
        value: object,
        *items: object,
        flag: object,
    ) -> None:
        seen_validator_args.append((ctx, value, items, flag))

    raw_tool.args_validator = args_validator
    tool = cast(
        host_tool_bridge_module.PydanticTool[host_tool_bridge_module.ToolDeps],
        raw_tool,
    )
    definition = host_tool_bridge_module.HostedToolDefinition(
        source="builtin",
        exposed_name="agent_teams_builtin_sample",
        raw_name="sample",
        description="Sample tool",
        input_schema={},
        tool=tool,
    )
    bridge = object.__new__(host_tool_bridge_module.ExternalAcpHostToolBridge)
    bridge.__dict__.update(
        {
            "_active_request": request,
            "_role": role,
            "_context_model": cast(host_tool_bridge_module.Model, object()),
        }
    )

    async def fake_build_tool_deps_async(
        *,
        request: LLMRequest,
    ) -> host_tool_bridge_module.ToolDeps:
        _ = request
        return cast(host_tool_bridge_module.ToolDeps, object())

    monkeypatch.setattr(
        bridge,
        "_build_tool_deps_async",
        fake_build_tool_deps_async,
    )

    result = await bridge.run_hosted_tool(
        definition=definition,
        arguments={"value": "input", "items": ["one"], "flag": True},
    )

    assert result == {"called": "input"}
    assert len(seen_validator_args) == 1
    assert seen_validator_args[0][1:] == ("input", ("one",), True)
    assert function_schema.calls[0][0] == {
        "value": "input",
        "items": ["one"],
        "flag": True,
    }


@pytest.mark.asyncio
async def test_run_hosted_tool_appends_boundary_system_reminder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        role_id="main-agent",
        system_prompt="system",
        user_prompt="hello",
    )
    role = RoleDefinition(
        role_id="main-agent",
        name="Main Agent",
        description="Handles ACP prompts",
        version="1.0.0",
        system_prompt="You are a helpful assistant.",
    )
    function_schema = _FakeFunctionSchema()
    tool = cast(
        host_tool_bridge_module.PydanticTool[host_tool_bridge_module.ToolDeps],
        _FakeRuntimeTool(function_schema),
    )
    definition = host_tool_bridge_module.HostedToolDefinition(
        source="builtin",
        exposed_name="agent_teams_builtin_sample",
        raw_name="sample",
        description="Sample tool",
        input_schema={},
        tool=tool,
    )
    injection_manager = RunInjectionManager()
    injection_manager.activate("run-1")
    reminder = render_system_reminder("Inspect the tool failure before continuing.")
    _ = injection_manager.enqueue(
        "run-1",
        "instance-1",
        InjectionSource.SYSTEM,
        reminder,
        visibility="internal",
        internal_kind="tool_failure",
        internal_delivery_mode=SystemReminderDeliveryMode.GUIDANCE.value,
        internal_issue_key="tool_failure:sample:tool_error",
    )
    event_hub = _CapturingRunEventHub()
    message_repo = _FakeMessageRepo()
    bridge = object.__new__(host_tool_bridge_module.ExternalAcpHostToolBridge)
    bridge.__dict__.update(
        {
            "_active_request": request,
            "_role": role,
            "_context_model": cast(host_tool_bridge_module.Model, object()),
            "_injection_manager": injection_manager,
            "_run_event_hub": cast(RunEventHub, event_hub),
            "_message_repo": message_repo,
        }
    )

    async def fake_build_tool_deps_async(
        *,
        request: LLMRequest,
    ) -> host_tool_bridge_module.ToolDeps:
        _ = request
        return cast(host_tool_bridge_module.ToolDeps, object())

    monkeypatch.setattr(
        bridge,
        "_build_tool_deps_async",
        fake_build_tool_deps_async,
    )

    result = await bridge.run_hosted_tool(
        definition=definition,
        arguments={"value": "input"},
    )

    assert isinstance(result, ToolReturn)
    assert result.return_value == {"called": "input"}
    assert result.content == reminder
    assert message_repo.appended_user_prompts[0]["content"] == reminder
    assert "Inspect the tool failure" not in event_hub.events[0].payload_json
    assert (
        injection_manager.drain_system_reminders_at_boundary("run-1", "instance-1")
        == ()
    )


def test_append_system_reminder_content_preserves_existing_tool_return() -> None:
    result = ToolReturn(
        return_value={"called": "input"},
        content="existing output",
        metadata={"source": "tool"},
    )

    merged = host_tool_bridge_module._append_system_reminder_content(
        result,
        ("Inspect the tool failure before continuing.",),
    )

    assert isinstance(merged, ToolReturn)
    assert merged.return_value == {"called": "input"}
    assert merged.content == (
        "existing output\n\nInspect the tool failure before continuing."
    )
    assert merged.metadata == {"source": "tool"}


def test_append_system_reminder_content_handles_empty_tool_return_content() -> None:
    result = ToolReturn(return_value={"called": "input"}, content="")

    merged = host_tool_bridge_module._append_system_reminder_content(
        result,
        ("Inspect the tool failure before continuing.",),
    )

    assert isinstance(merged, ToolReturn)
    assert merged.return_value == {"called": "input"}
    assert merged.content == "Inspect the tool failure before continuing."


def test_append_system_reminder_content_handles_missing_tool_return_content() -> None:
    result = ToolReturn(return_value={"called": "input"}, content=None)

    merged = host_tool_bridge_module._append_system_reminder_content(
        result,
        ("Inspect the tool failure before continuing.",),
    )

    assert isinstance(merged, ToolReturn)
    assert merged.return_value == {"called": "input"}
    assert merged.content == "Inspect the tool failure before continuing."


def test_append_system_reminder_content_handles_sequence_tool_return_content() -> None:
    result = ToolReturn(return_value={"called": "input"}, content=("existing output",))

    merged = host_tool_bridge_module._append_system_reminder_content(
        result,
        ("Inspect the tool failure before continuing.",),
    )

    assert isinstance(merged, ToolReturn)
    assert merged.return_value == {"called": "input"}
    assert merged.content == (
        "existing output",
        "Inspect the tool failure before continuing.",
    )


def test_append_system_reminder_content_returns_original_without_reminder() -> None:
    result = {"called": "input"}

    merged = host_tool_bridge_module._append_system_reminder_content(
        result,
        ("   ",),
    )

    assert merged is result


@pytest.mark.asyncio
async def test_open_connection_requires_transport_callbacks() -> None:
    bridge = object.__new__(host_tool_bridge_module.ExternalAcpHostToolBridge)
    bridge.__dict__.update(
        {
            "_catalog_by_name": {"tool": object()},
            "_server": cast(host_tool_bridge_module.FastMCP, object()),
            "_send_request": None,
            "_send_notification": None,
        }
    )

    with pytest.raises(RuntimeError, match="transport callbacks"):
        await bridge.open_connection(
            server_id=host_tool_bridge_module.HOST_TOOL_SERVER_ID
        )


@pytest.mark.asyncio
async def test_hosted_fastmcp_tool_returns_structured_dict() -> None:
    class _FakeBridge:
        async def run_hosted_tool(
            self,
            *,
            definition: host_tool_bridge_module.HostedToolDefinition,
            arguments: dict[str, object],
        ) -> dict[str, JsonValue]:
            _ = (definition, arguments)
            return {"ok": True}

    definition = host_tool_bridge_module.HostedToolDefinition(
        source="builtin",
        exposed_name="agent_teams_builtin_sample",
        raw_name="sample",
        description="Sample tool",
        input_schema={},
        tool=cast(
            host_tool_bridge_module.PydanticTool[host_tool_bridge_module.ToolDeps],
            object(),
        ),
    )
    fastmcp_tool = host_tool_bridge_module._HostedFastMcpTool(
        bridge=cast(host_tool_bridge_module.ExternalAcpHostToolBridge, _FakeBridge()),
        definition=definition,
    )

    result = await fastmcp_tool.run({"value": "input"})

    assert result.structured_content == {"ok": True}


@pytest.mark.asyncio
async def test_hosted_mcp_connection_cleans_pending_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def send_request(
        _method: str,
        _params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return {}

    async def send_notification(
        _method: str,
        _params: dict[str, JsonValue],
    ) -> None:
        return None

    connection = host_tool_bridge_module._HostedMcpConnection(
        connection_id="conn-1",
        external_session_id="external-1",
        server=cast(host_tool_bridge_module.FastMCP, object()),
        send_request=send_request,
        send_notification=send_notification,
    )

    async def fake_start() -> None:
        return None

    monkeypatch.setattr(connection, "start", fake_start)
    monkeypatch.setattr(connection, "_write_stream", _FakeWriteStream(connection))

    result = await connection.handle_message(
        method="tools/list",
        params={"cursor": "next"},
        message_id="external-message-1",
    )

    assert result == {"ok": True}
    assert connection._pending == {}


def test_fastmcp_internal_accessors_use_explicit_private_attributes() -> None:
    lifespan_manager = _FakeLifespanManager()
    fake_server = _FakeFastMcpServer(lifespan_manager)
    server = cast(host_tool_bridge_module.FastMCP, fake_server)

    manager = host_tool_bridge_module._fastmcp_lifespan_manager(server)
    assert manager is lifespan_manager
    assert host_tool_bridge_module._memory_mcp_server(server) is fake_server._mcp_server


def test_fastmcp_lifespan_manager_rejects_missing_callable() -> None:
    server = SimpleNamespace(_lifespan_manager="not-callable")

    with pytest.raises(RuntimeError, match="lifespan manager"):
        host_tool_bridge_module._fastmcp_lifespan_manager(
            cast(host_tool_bridge_module.FastMCP, server)
        )
