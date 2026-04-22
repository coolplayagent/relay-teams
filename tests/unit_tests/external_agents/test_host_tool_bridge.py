# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from types import SimpleNamespace
from typing import cast

import relay_teams.external_agents.host_tool_bridge as host_tool_bridge_module
from pydantic_ai.messages import BinaryContent, ToolReturn
from relay_teams.providers.model_config import (
    ModelCapabilities,
    ModelEndpointConfig,
    ProviderType,
)
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.roles.role_models import RoleDefinition


class _PublicTool:
    pass


class _PublicToolResult:
    pass


class _LegacyTool:
    pass


class _LegacyToolResult:
    pass


class _MissingRunIntentRepo:
    def get(self, _run_id: str) -> object:
        raise KeyError


class _FakeToolApprovalPolicy:
    def with_yolo(self, _yolo: bool) -> "_FakeToolApprovalPolicy":
        return self


class _FakeWorkspaceManager:
    def resolve(self, **_kwargs: object) -> object:
        return object()


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


def test_build_tool_deps_uses_resolved_model_capabilities() -> None:
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
            "_role_memory_service": None,
            "_media_asset_service": None,
            "_computer_runtime": None,
            "_background_task_service": None,
            "_monitor_service": None,
            "_todo_service": None,
            "_run_intent_repo": _MissingRunIntentRepo(),
            "_get_role_registry": lambda: object(),
            "_get_mcp_registry": lambda: object(),
            "_get_task_service": lambda: object(),
            "_get_task_execution_service": lambda: object(),
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

    deps = bridge._build_tool_deps(request=request)

    assert requested == [(role, request)]
    assert deps.model_capabilities == resolved_config.capabilities


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
        "role_memory_service": None,
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
        "resolve_model_config": resolver,
    }
    bridge_ctor = cast(
        Callable[..., object], host_tool_bridge_module.ExternalAcpHostToolBridge
    )
    bridge = cast(
        host_tool_bridge_module.ExternalAcpHostToolBridge, bridge_ctor(**kwargs)
    )

    assert bridge._resolve_model_config is resolver


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
