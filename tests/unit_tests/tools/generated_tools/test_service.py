# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel

from relay_teams.net.llm_client import clear_llm_http_client_cache
from relay_teams.providers.model_config import (
    ModelEndpointConfig,
    ProviderType,
    SamplingConfig,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleLoader, RoleRegistry
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.tools.generated_tools import (
    AutoHarnessService,
    GeneratedToolDraft,
    GeneratedToolEnableResult,
    GeneratedToolRecord,
    GeneratedToolStatus,
    GeneratedToolSynthesisResult,
    GeneratedToolTestCase,
)
import relay_teams.tools.auto_harness_tools.enable_tool as enable_tool_module
import relay_teams.tools.auto_harness_tools.synthesize_tool as synthesize_tool_module
import relay_teams.tools.generated_tools.service as generated_service_module
from relay_teams.tools.registry import ToolRegistry
from relay_teams.tools.runtime.context import ToolContext, ToolDeps
from relay_teams.tools.runtime.models import ToolResultProjection


SAFE_CODE = """\
def run(tool_input):
    return {"total": int(tool_input["a"]) + int(tool_input["b"])}
"""

UNSAFE_CODE = """\
def run(tool_input):
    return open(tool_input["path"]).read()
"""


class _DraftAutoHarnessService(AutoHarnessService):
    _draft: GeneratedToolDraft

    def set_draft(self, draft: GeneratedToolDraft) -> None:
        self._draft = draft

    async def _generate_tool_draft(
        self,
        *,
        role: RoleDefinition,
        session_id: str,
        run_id: str,
        task_id: str,
        workspace_id: str,
        conversation_id: str,
        instance_id: str,
        tool_name: str,
        description: str,
        input_schema: dict[str, JsonValue],
        behavior: str,
        test_cases: tuple[GeneratedToolTestCase, ...],
        thinking: RunThinkingConfig,
    ) -> GeneratedToolDraft:
        _ = (
            role,
            session_id,
            run_id,
            task_id,
            workspace_id,
            conversation_id,
            instance_id,
            tool_name,
            description,
            input_schema,
            behavior,
            test_cases,
            thinking,
        )
        return self._draft


class _ToolCaptureAgent:
    def __init__(self) -> None:
        self.function: Callable[..., object] | None = None
        self.tool_kwargs: dict[str, object] = {}

    def tool(self, **kwargs: object) -> Callable[[Callable[..., object]], object]:
        self.tool_kwargs = dict(kwargs)

        def _decorator(func: Callable[..., object]) -> object:
            self.function = func
            return func

        return _decorator


class _ActionDeps:
    def __init__(
        self,
        *,
        role: RoleDefinition,
        auto_harness_service: object | None,
        runtime_role_resolver: object | None = None,
    ) -> None:
        self.auto_harness_service = auto_harness_service
        self.role_registry = RoleRegistry()
        self.role_registry.register(role)
        self.runtime_role_resolver = runtime_role_resolver
        self.role_id = role.role_id
        self.session_id = "session-1"
        self.run_id = "run-1"
        self.task_id = "task-1"
        self.workspace_id = "workspace-1"
        self.conversation_id = "conversation-1"
        self.instance_id = "instance-1"


class _ActionContext:
    def __init__(self, deps: _ActionDeps) -> None:
        self.deps = deps


class _RuntimeRoleResolver:
    def __init__(self, role: RoleDefinition) -> None:
        self.role = role
        self.calls: list[tuple[str, str]] = []

    async def get_effective_role_async(
        self,
        *,
        run_id: str,
        role_id: str,
    ) -> RoleDefinition:
        self.calls.append((run_id, role_id))
        return self.role


class _EnableService:
    def __init__(self) -> None:
        self.calls: list[
            tuple[str, str, str, str | None, str | None, str | None, str | None]
        ] = []

    async def enable_tool(
        self,
        *,
        current_role_id: str,
        tool_name: str,
        code_hash: str,
        target_role_id: str | None,
        run_id: str | None = None,
        instance_id: str | None = None,
        session_id: str | None = None,
    ) -> GeneratedToolEnableResult:
        self.calls.append(
            (
                current_role_id,
                tool_name,
                code_hash,
                target_role_id,
                run_id,
                instance_id,
                session_id,
            )
        )
        return GeneratedToolEnableResult(
            tool_name=tool_name,
            code_hash=code_hash,
            target_role_id=target_role_id or current_role_id,
            status=GeneratedToolStatus.ENABLED,
            role_updated=True,
        )


class _SynthesisService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str | None, int]] = []

    async def synthesize_tool(
        self,
        *,
        role: RoleDefinition,
        session_id: str,
        run_id: str,
        task_id: str,
        workspace_id: str,
        conversation_id: str,
        instance_id: str,
        tool_name: str,
        description: str,
        input_schema: dict[str, JsonValue],
        behavior: str,
        test_cases: tuple[GeneratedToolTestCase, ...],
        target_role_id: str | None,
        thinking: RunThinkingConfig,
    ) -> GeneratedToolSynthesisResult:
        _ = (
            session_id,
            run_id,
            task_id,
            workspace_id,
            conversation_id,
            instance_id,
            input_schema,
            behavior,
            thinking,
        )
        self.calls.append(
            (role.role_id, tool_name, description, target_role_id, len(test_cases))
        )
        return GeneratedToolSynthesisResult(
            tool_name="generated_" + tool_name,
            code_hash="hash-1",
            status=GeneratedToolStatus.PENDING,
            test_count=len(test_cases),
            notes="ready",
        )


class _FakeParentConnection:
    def __init__(
        self,
        *,
        message: generated_service_module._GeneratedCodeProcessMessage | None,
    ) -> None:
        self._message = message
        self.closed = False

    def poll(self) -> bool:
        return self._message is not None

    def recv(self) -> object:
        if self._message is None:
            raise RuntimeError("No fake process message")
        return self._message

    def send(self, obj: object) -> None:
        self._message = (
            generated_service_module._GeneratedCodeProcessMessage.model_validate(obj)
        )

    def close(self) -> None:
        self.closed = True


class _FakeChildConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self, *, alive_results: tuple[bool, ...] = (False,)) -> None:
        self._alive_results = list(alive_results)
        self.started = False
        self.join_timeouts: list[float | None] = []
        self.terminated = False
        self.killed = False

    def start(self) -> None:
        self.started = True

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(timeout)

    def is_alive(self) -> bool:
        if self._alive_results:
            return self._alive_results.pop(0)
        return False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def _write_role(
    path: Path, *, role_id: str = "Worker", tools: str = "  - read"
) -> None:
    path.write_text(
        "\n".join(
            (
                "---",
                f"role_id: {role_id}",
                f"name: {role_id}",
                "description: Test role",
                "version: 1.0.0",
                "tools:",
                tools,
                "---",
                "",
                "Test system prompt.",
                "",
            )
        ),
        encoding="utf-8",
    )


def _build_service(
    tmp_path: Path,
    *,
    code: str,
    extra_role_ids: tuple[str, ...] = (),
    resolve_role_instance_id: generated_service_module.RoleInstanceResolver
    | None = None,
    reload_observer: Callable[[ToolRegistry], None] | None = None,
) -> tuple[_DraftAutoHarnessService, RoleRegistry, ToolRegistry, RoleDefinition]:
    config_dir = tmp_path / "config"
    roles_dir = tmp_path / "roles"
    builtin_roles_dir = tmp_path / "builtin_roles"
    roles_dir.mkdir()
    builtin_roles_dir.mkdir()
    _write_role(roles_dir / "worker.md")
    for role_id in extra_role_ids:
        _write_role(roles_dir / f"{role_id.lower()}.md", role_id=role_id)
    role_registry = RoleLoader().load_builtin_and_app(
        builtin_roles_dir=builtin_roles_dir,
        app_roles_dir=roles_dir,
        allow_empty=True,
    )
    tool_registry = ToolRegistry({})
    latest_registry = role_registry

    def _on_roles_reloaded(updated: RoleRegistry) -> None:
        nonlocal latest_registry
        if reload_observer is not None:
            reload_observer(tool_registry)
        latest_registry = updated

    service = _DraftAutoHarnessService(
        config_dir=config_dir,
        roles_dir=roles_dir,
        builtin_roles_dir=builtin_roles_dir,
        tool_registry=tool_registry,
        get_role_registry=lambda: latest_registry,
        resolve_model_config=lambda _role, _session: (
            cast(ModelEndpointConfig | None, None),
            None,
        ),
        on_roles_reloaded=_on_roles_reloaded,
        resolve_role_instance_id=resolve_role_instance_id,
    )
    service.set_draft(GeneratedToolDraft(code=code, notes="generated"))
    return service, role_registry, tool_registry, role_registry.get("Worker")


def _record_for_safe_tool(*, status: GeneratedToolStatus) -> GeneratedToolRecord:
    return GeneratedToolRecord(
        tool_name="generated_sum",
        description="Add two integers",
        input_schema=_schema(),
        test_cases=_test_cases(),
        code_hash=generated_service_module._code_hash(SAFE_CODE),
        status=status,
        target_role_id="Worker",
        created_by_role_id="Worker",
    )


def _schema() -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        },
    )


def _test_cases() -> tuple[GeneratedToolTestCase, ...]:
    return (
        GeneratedToolTestCase(
            input={"a": 2, "b": 3},
            expected={"total": 5},
        ),
    )


@pytest.mark.asyncio
async def test_synthesize_tool_rejects_unsafe_generated_code(tmp_path: Path) -> None:
    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path,
        code=UNSAFE_CODE,
    )

    with pytest.raises(ValueError, match="forbidden function: open"):
        await service.synthesize_tool(
            role=role,
            session_id="session-1",
            run_id="run-1",
            task_id="task-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            instance_id="instance-1",
            tool_name="sum",
            description="Add two integers",
            input_schema=_schema(),
            behavior="Return a + b as total.",
            test_cases=_test_cases(),
            target_role_id=None,
            thinking=RunThinkingConfig(),
        )

    assert not (tmp_path / "config" / "generated_tools").exists()


@pytest.mark.asyncio
async def test_synthesize_and_enable_generated_tool_updates_role_asset(
    tmp_path: Path,
) -> None:
    service, _role_registry, tool_registry, role = _build_service(
        tmp_path,
        code=SAFE_CODE,
    )

    synthesis = await service.synthesize_tool(
        role=role,
        session_id="session-1",
        run_id="run-1",
        task_id="task-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        tool_name="sum",
        description="Add two integers",
        input_schema=_schema(),
        behavior="Return a + b as total.",
        test_cases=_test_cases(),
        target_role_id=None,
        thinking=RunThinkingConfig(),
    )

    assert synthesis.tool_name == "generated_sum"
    assert synthesis.status == GeneratedToolStatus.PENDING
    assert tool_registry.list_names() == ()

    enabled = await service.enable_tool(
        current_role_id=role.role_id,
        tool_name=synthesis.tool_name,
        code_hash=synthesis.code_hash,
        target_role_id=None,
        run_id="run-1",
        instance_id="instance-1",
    )

    assert enabled.status == GeneratedToolStatus.ENABLED
    assert enabled.role_updated is True
    assert "generated_sum" in tool_registry.list_names()
    assert service.consume_tools_dirty(
        run_id="run-1",
        instance_id="instance-1",
    ) == ("generated_sum",)
    result = await service.execute_generated_tool(
        tool_name="generated_sum",
        tool_input={"a": 4, "b": 7},
    )
    assert result == {"total": 11}
    assert "- generated_sum" in (tmp_path / "roles" / "worker.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_enable_tool_marks_target_role_instance_dirty(tmp_path: Path) -> None:
    resolver_calls: list[tuple[str, str]] = []

    def _resolve_role_instance_id(session_id: str, role_id: str) -> str | None:
        resolver_calls.append((session_id, role_id))
        if role_id == "Reviewer":
            return "reviewer-instance"
        return None

    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path,
        code=SAFE_CODE,
        extra_role_ids=("Reviewer",),
        resolve_role_instance_id=_resolve_role_instance_id,
    )
    synthesis = await service.synthesize_tool(
        role=role,
        session_id="session-1",
        run_id="run-1",
        task_id="task-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="worker-instance",
        tool_name="sum",
        description="Add two integers",
        input_schema=_schema(),
        behavior="Return a + b as total.",
        test_cases=_test_cases(),
        target_role_id="Reviewer",
        thinking=RunThinkingConfig(),
    )

    await service.enable_tool(
        current_role_id=role.role_id,
        tool_name=synthesis.tool_name,
        code_hash=synthesis.code_hash,
        target_role_id="Reviewer",
        run_id="run-1",
        instance_id="worker-instance",
        session_id="session-1",
    )

    assert resolver_calls == [("session-1", "Reviewer")]
    assert service.consume_tools_dirty(
        run_id="run-1",
        instance_id="reviewer-instance",
    ) == ("generated_sum",)
    assert (
        service.consume_tools_dirty(
            run_id="run-1",
            instance_id="worker-instance",
        )
        == ()
    )


@pytest.mark.asyncio
async def test_enable_tool_registers_tool_before_role_reload(
    tmp_path: Path,
) -> None:
    observed_tool_names: list[tuple[str, ...]] = []
    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path,
        code=SAFE_CODE,
        reload_observer=lambda registry: observed_tool_names.append(
            registry.list_names()
        ),
    )
    synthesis = await service.synthesize_tool(
        role=role,
        session_id="session-1",
        run_id="run-1",
        task_id="task-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        tool_name="sum",
        description="Add two integers",
        input_schema=_schema(),
        behavior="Return a + b as total.",
        test_cases=_test_cases(),
        target_role_id=None,
        thinking=RunThinkingConfig(),
    )

    await service.enable_tool(
        current_role_id=role.role_id,
        tool_name=synthesis.tool_name,
        code_hash=synthesis.code_hash,
        target_role_id=None,
    )

    assert observed_tool_names == [("generated_sum",)]


@pytest.mark.asyncio
async def test_generated_tool_error_paths_are_validated(tmp_path: Path) -> None:
    service, _role_registry, tool_registry, role = _build_service(
        tmp_path,
        code=SAFE_CODE,
    )
    synthesis = await service.synthesize_tool(
        role=role,
        session_id="session-1",
        run_id="run-1",
        task_id="task-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        tool_name="sum",
        description="Add two integers",
        input_schema=_schema(),
        behavior="Return a + b as total.",
        test_cases=_test_cases(),
        target_role_id=None,
        thinking=RunThinkingConfig(),
    )

    with pytest.raises(PermissionError, match="not enabled"):
        await service.execute_generated_tool(
            tool_name=synthesis.tool_name,
            tool_input={"a": 1, "b": 2},
        )
    with pytest.raises(ValueError, match="code_hash does not match"):
        await service.enable_tool(
            current_role_id=role.role_id,
            tool_name=synthesis.tool_name,
            code_hash="wrong",
            target_role_id=None,
        )
    with pytest.raises(ValueError, match="Tool already exists"):
        await service.synthesize_tool(
            role=role,
            session_id="session-1",
            run_id="run-1",
            task_id="task-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            instance_id="instance-1",
            tool_name="sum",
            description="Add two integers",
            input_schema=_schema(),
            behavior="Return a + b as total.",
            test_cases=_test_cases(),
            target_role_id=None,
            thinking=RunThinkingConfig(),
        )

    implementation_path = (
        tmp_path
        / "config"
        / "generated_tools"
        / synthesis.tool_name
        / "implementation.py"
    )
    implementation_path.write_text(
        "def run(tool_input):\n    return {'total': 0}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="recorded code_hash"):
        await service.enable_tool(
            current_role_id=role.role_id,
            tool_name=synthesis.tool_name,
            code_hash=synthesis.code_hash,
            target_role_id=None,
        )

    implementation_path.write_text(SAFE_CODE.strip(), encoding="utf-8")
    await service.enable_tool(
        current_role_id=role.role_id,
        tool_name=synthesis.tool_name,
        code_hash=synthesis.code_hash,
        target_role_id=None,
    )
    implementation_path.write_text(
        "def run(tool_input):\n    return {'total': 999}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="implementation hash mismatch"):
        await service.execute_generated_tool(
            tool_name=synthesis.tool_name,
            tool_input={"a": 1, "b": 2},
        )
    implementation_path.write_text(SAFE_CODE.strip(), encoding="utf-8")
    with pytest.raises(ValueError, match="not pending"):
        await service.enable_tool(
            current_role_id=role.role_id,
            tool_name=synthesis.tool_name,
            code_hash=synthesis.code_hash,
            target_role_id=None,
        )
    with pytest.raises(ValueError, match="Tool already exists"):
        await service.synthesize_tool(
            role=role,
            session_id="session-1",
            run_id="run-1",
            task_id="task-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            instance_id="instance-1",
            tool_name="sum",
            description="Add two integers",
            input_schema=_schema(),
            behavior="Return a + b as total.",
            test_cases=_test_cases(),
            target_role_id=None,
            thinking=RunThinkingConfig(),
        )
    assert "generated_sum" in tool_registry.list_names()


@pytest.mark.asyncio
async def test_enable_tool_does_not_persist_enabled_state_when_role_update_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _role_registry, tool_registry, role = _build_service(
        tmp_path,
        code=SAFE_CODE,
    )
    synthesis = await service.synthesize_tool(
        role=role,
        session_id="session-1",
        run_id="run-1",
        task_id="task-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        instance_id="instance-1",
        tool_name="sum",
        description="Add two integers",
        input_schema=_schema(),
        behavior="Return a + b as total.",
        test_cases=_test_cases(),
        target_role_id=None,
        thinking=RunThinkingConfig(),
    )

    def _fail_attach(*, role: RoleDefinition, tool_name: str) -> bool:
        _ = (role, tool_name)
        raise ValueError("role front matter is broken")

    monkeypatch.setattr(service, "_attach_tool_to_role", _fail_attach)
    with pytest.raises(ValueError, match="front matter"):
        await service.enable_tool(
            current_role_id=role.role_id,
            tool_name=synthesis.tool_name,
            code_hash=synthesis.code_hash,
            target_role_id=None,
        )

    assert (
        service._load_record(synthesis.tool_name).status == GeneratedToolStatus.PENDING
    )
    assert tool_registry.list_names() == ()


def test_generated_tool_record_listing_and_startup_skip_invalid_records(
    tmp_path: Path,
) -> None:
    service, _role_registry, tool_registry, _role = _build_service(
        tmp_path,
        code=SAFE_CODE,
    )
    invalid_dir = tmp_path / "config" / "generated_tools" / "invalid"
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "tool.json").write_text("{not json", encoding="utf-8")

    enabled_dir = tmp_path / "config" / "generated_tools" / "generated_sum"
    enabled_dir.mkdir()
    record = _record_for_safe_tool(status=GeneratedToolStatus.ENABLED)
    (enabled_dir / "tool.json").write_text(
        record.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (enabled_dir / "implementation.py").write_text(
        "def run(tool_input):\n    return open('x')\n",
        encoding="utf-8",
    )
    tampered_dir = tmp_path / "config" / "generated_tools" / "shell"
    tampered_dir.mkdir()
    tampered_record = record.model_copy(update={"tool_name": "shell"})
    (tampered_dir / "tool.json").write_text(
        tampered_record.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (tampered_dir / "implementation.py").write_text(SAFE_CODE, encoding="utf-8")

    assert service.list_records() == (record,)
    service.register_enabled_tools()
    assert tool_registry.list_names() == ()


@pytest.mark.asyncio
async def test_generated_tool_runtime_register_invokes_shared_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _role_registry, _tool_registry, _role = _build_service(
        tmp_path,
        code=SAFE_CODE,
    )
    record = _record_for_safe_tool(status=GeneratedToolStatus.ENABLED)
    service._write_record(record=record, code=SAFE_CODE)
    agent = _ToolCaptureAgent()
    register = service._build_tool_register(record)
    register(cast(Agent[ToolDeps, str], cast(object, agent)))
    assert agent.tool_kwargs["name"] == "generated_sum"
    assert agent.function is not None

    async def _fake_execute_tool_call(
        ctx: ToolContext,
        *,
        tool_name: str,
        args_summary: dict[str, JsonValue],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: Mapping[str, object],
    ) -> dict[str, JsonValue]:
        _ = ctx
        assert tool_name == "generated_sum"
        assert args_summary == {"input_keys": ["a", "b"]}
        projection = await action(
            tool_input=cast(dict[str, JsonValue], raw_args["tool_input"])
        )
        return {"ok": True, "data": projection.visible_data}

    monkeypatch.setattr(
        generated_service_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )
    tool_func = cast(
        Callable[..., Awaitable[dict[str, JsonValue]]],
        agent.function,
    )
    result = await tool_func(
        cast(
            ToolContext,
            cast(
                object,
                _ActionContext(_ActionDeps(role=_role, auto_harness_service=None)),
            ),
        ),
        {"b": 4, "a": 6},
    )
    assert result == {"ok": True, "data": {"result": {"total": 10}}}


@pytest.mark.asyncio
async def test_generated_tool_action_helper_returns_projection(tmp_path: Path) -> None:
    service, _role_registry, _tool_registry, _role = _build_service(
        tmp_path,
        code=SAFE_CODE,
    )
    record = _record_for_safe_tool(status=GeneratedToolStatus.ENABLED)
    service._write_record(record=record, code=SAFE_CODE)

    projection = await generated_service_module._run_generated_tool_action(
        service=service,
        tool_name="generated_sum",
        tool_input={"a": 8, "b": 9},
    )

    assert projection.visible_data == {"result": {"total": 17}}
    assert projection.internal_data == {
        "tool_name": "generated_sum",
        "result": {"total": 17},
    }


def test_generated_tool_target_role_and_role_asset_branches(tmp_path: Path) -> None:
    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path,
        code=SAFE_CODE,
    )

    with pytest.raises(ValueError, match="target_role_id must not be empty"):
        service._resolve_target_role(current_role_id=role.role_id, target_role_id=" ")
    with pytest.raises(ValueError, match="Temporary roles cannot receive"):
        service._resolve_target_role(current_role_id="Temporary", target_role_id=None)
    with pytest.raises(ValueError, match="Unknown target role"):
        service._resolve_target_role(
            current_role_id=role.role_id, target_role_id="Missing"
        )

    role_with_tool = role.model_copy(update={"tools": ("generated_sum",)})
    assert (
        service._attach_tool_to_role(role=role_with_tool, tool_name="generated_sum")
        is False
    )


def test_generated_tool_attach_builtin_role_writes_app_override(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    roles_dir = tmp_path / "roles"
    builtin_roles_dir = tmp_path / "builtin_roles"
    roles_dir.mkdir()
    builtin_roles_dir.mkdir()
    _write_role(builtin_roles_dir / "builtin.md", role_id="Builtin")
    role_registry = RoleLoader().load_builtin_and_app(
        builtin_roles_dir=builtin_roles_dir,
        app_roles_dir=roles_dir,
        allow_empty=True,
    )
    latest_registry = role_registry

    def _on_roles_reloaded(updated: RoleRegistry) -> None:
        nonlocal latest_registry
        latest_registry = updated

    service = AutoHarnessService(
        config_dir=config_dir,
        roles_dir=roles_dir,
        builtin_roles_dir=builtin_roles_dir,
        tool_registry=ToolRegistry({}),
        get_role_registry=lambda: latest_registry,
        resolve_model_config=lambda _role, _session: (
            cast(ModelEndpointConfig | None, None),
            None,
        ),
        on_roles_reloaded=_on_roles_reloaded,
    )

    assert (
        service._attach_tool_to_role(
            role=role_registry.get("Builtin"),
            tool_name="generated_sum",
        )
        is True
    )
    assert "- generated_sum" in (roles_dir / "Builtin.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_autoharness_enable_action_and_register_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = RoleDefinition(
        role_id="Worker",
        name="Worker",
        description="Does work.",
        version="1",
        tools=(),
        system_prompt="Work.",
    )
    service = _EnableService()
    ctx = cast(
        ToolContext,
        cast(
            object, _ActionContext(_ActionDeps(role=role, auto_harness_service=service))
        ),
    )

    request = enable_tool_module._build_enable_approval_request(
        tool_name="generated_sum",
        code_hash=" hash-1 ",
        target_role_id=None,
    )
    assert request.cache_key == "auto_harness:generated_sum:hash-1:"
    assert request.metadata["target_role_id"] == ""
    targeted_request = enable_tool_module._build_enable_approval_request(
        tool_name="generated_sum",
        code_hash="hash-1",
        target_role_id="Worker",
    )
    assert targeted_request.cache_key == "auto_harness:generated_sum:hash-1:Worker"

    result = await enable_tool_module._run_enable_action(
        ctx,
        tool_name="generated_sum",
        code_hash="hash-1",
        target_role_id="Worker",
    )
    assert result["role_updated"] is True
    assert service.calls == [
        (
            "Worker",
            "generated_sum",
            "hash-1",
            "Worker",
            "run-1",
            "instance-1",
            "session-1",
        )
    ]

    with pytest.raises(RuntimeError, match="not configured"):
        await enable_tool_module._run_enable_action(
            cast(
                ToolContext,
                cast(
                    object,
                    _ActionContext(_ActionDeps(role=role, auto_harness_service=None)),
                ),
            ),
            tool_name="generated_sum",
            code_hash="hash-1",
        )

    async def _fake_execute_tool_call(
        ctx: ToolContext,
        **kwargs: object,
    ) -> dict[str, JsonValue]:
        _ = ctx
        assert kwargs["tool_name"] == "auto_harness_enable_tool"
        assert kwargs["force_approval"] is True
        return {"ok": True}

    monkeypatch.setattr(
        enable_tool_module, "execute_tool_call", _fake_execute_tool_call
    )
    agent = _ToolCaptureAgent()
    enable_tool_module.register(cast(Agent[ToolDeps, str], cast(object, agent)))
    assert agent.function is not None
    tool_func = cast(
        Callable[..., Awaitable[dict[str, JsonValue]]],
        agent.function,
    )
    assert await tool_func(ctx, "generated_sum", "hash-1", None) == {"ok": True}


@pytest.mark.asyncio
async def test_autoharness_synthesize_action_and_register_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = RoleDefinition(
        role_id="Worker",
        name="Worker",
        description="Does work.",
        version="1",
        tools=(),
        system_prompt="Work.",
    )
    resolver = _RuntimeRoleResolver(role)
    service = _SynthesisService()
    ctx = cast(
        ToolContext,
        cast(
            object,
            _ActionContext(
                _ActionDeps(
                    role=role,
                    auto_harness_service=service,
                    runtime_role_resolver=resolver,
                )
            ),
        ),
    )

    result = await synthesize_tool_module._run_synthesize_action(
        ctx,
        tool_name="sum",
        description="Add",
        input_schema=_schema(),
        behavior="Add values.",
        test_cases=list(_test_cases()),
    )
    assert result["tool_name"] == "generated_sum"
    assert resolver.calls == [("run-1", "Worker")]
    assert service.calls == [("Worker", "sum", "Add", None, 1)]

    with pytest.raises(RuntimeError, match="not configured"):
        await synthesize_tool_module._run_synthesize_action(
            cast(
                ToolContext,
                cast(
                    object,
                    _ActionContext(_ActionDeps(role=role, auto_harness_service=None)),
                ),
            ),
            tool_name="sum",
            description="Add",
            input_schema=_schema(),
            behavior="Add values.",
            test_cases=list(_test_cases()),
        )

    async def _fake_execute_tool_call(
        ctx: ToolContext,
        **kwargs: object,
    ) -> dict[str, JsonValue]:
        _ = ctx
        assert kwargs["tool_name"] == "auto_harness_synthesize_tool"
        return {"ok": True}

    monkeypatch.setattr(
        synthesize_tool_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )
    agent = _ToolCaptureAgent()
    synthesize_tool_module.register(cast(Agent[ToolDeps, str], cast(object, agent)))
    assert agent.function is not None
    tool_func = cast(
        Callable[..., Awaitable[dict[str, JsonValue]]],
        agent.function,
    )
    assert await tool_func(
        ctx,
        "sum",
        "Add",
        _schema(),
        "Add values.",
        list(_test_cases()),
        None,
    ) == {"ok": True}


def test_generated_tool_model_and_helper_validation_branches() -> None:
    assert GeneratedToolTestCase.model_validate({"input": None}).input == {}
    with pytest.raises(ValidationError):
        GeneratedToolTestCase.model_validate("raw")
    assert (
        generated_service_module._normalize_generated_tool_name("Report Total")
        == "generated_report_total"
    )
    with pytest.raises(ValueError, match="must not be empty"):
        generated_service_module._normalize_generated_tool_name(" !!! ")
    with pytest.raises(ValueError, match="object schema"):
        generated_service_module._validate_input_schema({"type": "array"})
    with pytest.raises(ValueError, match="properties"):
        generated_service_module._validate_input_schema({"properties": []})
    assert (
        generated_service_module._strip_markdown_code_fence(
            "```python\ndef run(tool_input):\n    return {'ok': True}\n```"
        )
        == "def run(tool_input):\n    return {'ok': True}"
    )


def test_generated_tool_model_builder_uses_configured_provider() -> None:
    config = ModelEndpointConfig(
        provider=ProviderType.ANTHROPIC,
        model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com",
        api_key="anthropic-key",
        sampling=SamplingConfig(
            temperature=0.9,
            top_p=0.7,
            max_tokens=3600,
        ),
    )

    try:
        model = generated_service_module._build_model(
            config,
            cache_scope="test-generated-tool-model-builder",
        )
    finally:
        clear_llm_http_client_cache()

    assert isinstance(model, AnthropicModel)
    settings = generated_service_module._model_settings(config)
    assert (
        settings.get("max_tokens") == generated_service_module._MODEL_OUTPUT_MAX_TOKENS
    )
    assert "temperature" not in settings
    assert "top_p" not in settings
    assert "extra_body" not in settings


def test_generated_code_safety_and_json_conversion_branches() -> None:
    with pytest.raises(ValueError, match="invalid syntax"):
        generated_service_module._validate_generated_code("def run(:")
    with pytest.raises(ValueError, match="exactly one run"):
        generated_service_module._validate_generated_code(
            "def other(tool_input):\n    return 1\n"
        )
    with pytest.raises(ValueError, match="module docstring and run"):
        generated_service_module._validate_generated_code(
            "VALUE = 1\ndef run(tool_input):\n    return VALUE\n"
        )
    with pytest.raises(ValueError, match="forbidden syntax"):
        generated_service_module._validate_generated_code(
            "def run(tool_input):\n    while True:\n        return 1\n"
        )
    with pytest.raises(ValueError, match="forbidden syntax"):
        generated_service_module._validate_generated_code(
            "def run(tool_input):\n"
            "    for value in []:\n"
            "        return value\n"
            "    return None\n"
        )
    with pytest.raises(ValueError, match="private attributes"):
        generated_service_module._validate_generated_code(
            "def run(tool_input):\n    return tool_input.__class__\n"
        )
    with pytest.raises(ValueError, match="mutate attributes"):
        generated_service_module._validate_generated_code(
            "def run(tool_input):\n    json.loads = str\n    return {'ok': True}\n"
        )
    with pytest.raises(ValueError, match="unsupported function"):
        generated_service_module._validate_generated_code(
            "def run(tool_input):\n    return bytes(tool_input)\n"
        )
    with pytest.raises(ValueError, match="unsupported function"):
        generated_service_module._validate_generated_code(
            "def run(tool_input):\n    return range(3)\n"
        )
    with pytest.raises(ValueError, match="unsupported attribute function"):
        generated_service_module._validate_generated_code(
            "def run(tool_input):\n    return datetime.datetime.now()\n"
        )
    with pytest.raises(ValueError, match="forbidden function"):
        generated_service_module._validate_generated_code(
            "def run(tool_input):\n    return eval('1')\n"
        )
    assert generated_service_module._execute_generated_code_sync(
        "def run(tool_input):\n    return {'root': math.sqrt(9)}\n",
        {},
    ) == {"root": 3.0}
    assert generated_service_module._execute_generated_code_sync(
        "def run(tool_input):\n    return {'values': (1, [2, {'x': 3}])}\n",
        {},
    ) == {"values": [1, [2, {"x": 3}]]}
    with pytest.raises(TypeError, match="non-JSON"):
        generated_service_module._to_json_value(object())


@pytest.mark.asyncio
async def test_generated_code_execution_and_prompt_helpers_raise_expected_errors(
    tmp_path: Path,
) -> None:
    service, role_registry, _tool_registry, role = _build_service(
        tmp_path,
        code=SAFE_CODE,
    )
    with pytest.raises(ValueError, match="test case failed"):
        await service._execute_code(
            code=SAFE_CODE,
            tool_input={"a": 1, "b": 2},
            expected={"total": 4},
            has_expected=True,
        )
    assert await service._execute_code(
        code="def run(tool_input):\n    return {'root': math.sqrt(16)}\n",
        tool_input={},
        expected=None,
        has_expected=False,
    ) == {"root": 4.0}
    with pytest.raises(TypeError, match="non-JSON"):
        await service._execute_code(
            code="def run(tool_input):\n    return ValueError\n",
            tool_input={},
            expected=None,
            has_expected=False,
        )
    base_service = AutoHarnessService(
        config_dir=tmp_path / "base_config",
        roles_dir=tmp_path / "roles",
        builtin_roles_dir=tmp_path / "builtin_roles",
        tool_registry=ToolRegistry({}),
        get_role_registry=lambda: role_registry,
        resolve_model_config=lambda _role, _session: (
            cast(ModelEndpointConfig | None, None),
            None,
        ),
        on_roles_reloaded=lambda _updated: None,
    )
    with pytest.raises(RuntimeError, match="could not resolve"):
        await base_service._generate_tool_draft(
            role=role,
            session_id="session-1",
            run_id="run-1",
            task_id="task-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            instance_id="instance-1",
            tool_name="generated_sum",
            description="Add",
            input_schema=_schema(),
            behavior="Add values.",
            test_cases=_test_cases(),
            thinking=RunThinkingConfig(),
        )
    prompt = generated_service_module._build_generation_prompt(
        role=role,
        tool_name="generated_sum",
        description=" Add ",
        input_schema=_schema(),
        behavior=" Add values. ",
        test_cases=_test_cases(),
        profile_name=None,
    )
    assert "Model profile: default" in prompt
    assert "Tool name: generated_sum" in prompt


@pytest.mark.asyncio
async def test_generated_code_process_timeout_terminates_and_kills_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_connection = _FakeParentConnection(message=None)
    child_connection = _FakeChildConnection()
    process = _FakeProcess(alive_results=(True, True))

    def _fake_pipe(
        *, duplex: bool
    ) -> tuple[_FakeParentConnection, _FakeChildConnection]:
        assert duplex is False
        return parent_connection, child_connection

    def _fake_process(
        *,
        target: Callable[[str, dict[str, JsonValue], object], None],
        args: tuple[object, ...],
    ) -> _FakeProcess:
        _ = (target, args)
        return process

    monkeypatch.setattr(generated_service_module.multiprocessing, "Pipe", _fake_pipe)
    monkeypatch.setattr(
        generated_service_module.multiprocessing,
        "Process",
        _fake_process,
    )
    monkeypatch.setattr(generated_service_module, "_EXECUTION_TIMEOUT_SECONDS", 0.0)

    with pytest.raises(TimeoutError, match="timed out"):
        await generated_service_module._execute_generated_code_in_process(
            SAFE_CODE,
            {"a": 1, "b": 2},
        )

    assert process.started is True
    assert process.terminated is True
    assert process.killed is True
    assert process.join_timeouts == [1.0, 1.0]
    assert child_connection.closed is True
    assert parent_connection.closed is True


@pytest.mark.asyncio
async def test_generated_code_process_exit_without_result_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_connection = _FakeParentConnection(message=None)
    child_connection = _FakeChildConnection()
    process = _FakeProcess()

    def _fake_pipe(
        *, duplex: bool
    ) -> tuple[_FakeParentConnection, _FakeChildConnection]:
        assert duplex is False
        return parent_connection, child_connection

    def _fake_process(
        *,
        target: Callable[[str, dict[str, JsonValue], object], None],
        args: tuple[object, ...],
    ) -> _FakeProcess:
        _ = (target, args)
        return process

    monkeypatch.setattr(generated_service_module.multiprocessing, "Pipe", _fake_pipe)
    monkeypatch.setattr(
        generated_service_module.multiprocessing,
        "Process",
        _fake_process,
    )

    with pytest.raises(RuntimeError, match="without a result"):
        await generated_service_module._execute_generated_code_in_process(
            SAFE_CODE,
            {"a": 1, "b": 2},
        )

    assert process.started is True
    assert process.terminated is False
    assert process.killed is False
    assert process.join_timeouts == [0.0]
    assert child_connection.closed is True
    assert parent_connection.closed is True


def test_generated_code_process_worker_and_error_mapping_branches() -> None:
    success_connection = _FakeParentConnection(message=None)
    generated_service_module._execute_generated_code_process_worker(
        SAFE_CODE,
        {"a": 3, "b": 4},
        success_connection,
    )
    assert success_connection.closed is True
    assert success_connection.poll() is True
    assert (
        success_connection.recv()
        == generated_service_module._GeneratedCodeProcessMessage(
            ok=True,
            result={"total": 7},
        )
    )

    error_connection = _FakeParentConnection(message=None)
    generated_service_module._execute_generated_code_process_worker(
        "def run(tool_input):\n    return object()\n",
        {},
        error_connection,
    )
    assert error_connection.closed is True
    with pytest.raises(RuntimeError, match="Generated tool process failed: boom"):
        generated_service_module._raise_generated_code_process_error(
            generated_service_module._GeneratedCodeProcessMessage(
                ok=False,
                error_type="CustomError",
                message="boom",
            )
        )


def test_generated_tool_markdown_helpers_validate_role_content() -> None:
    with pytest.raises(ValueError, match="must start"):
        generated_service_module._split_markdown_front_matter("No front matter")
    with pytest.raises(ValueError, match="delimiters"):
        generated_service_module._split_markdown_front_matter("---\nrole_id: x\n")

    rendered = generated_service_module._render_role_markdown(
        front_matter={
            "role_id": "Worker",
            "name": "Worker",
            "description": "Does work.",
            "version": "1",
            "tools": [],
            "memory_profile": generated_service_module.default_memory_profile().model_dump(
                mode="json"
            ),
        },
        body="Body",
    )
    assert "memory_profile" not in rendered
    assert rendered.startswith("---\nrole_id: Worker")
