# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.providers.model_config import (
    ModelEndpointConfig,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleLoader, RoleRegistry
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.tools.generated_tools import (
    AutoHarnessService,
    GeneratedToolDisableResult,
    GeneratedToolDraft,
    GeneratedToolRecord,
    GeneratedToolStatus,
    GeneratedToolTestCase,
    GeneratedToolUpgradeResult,
)
import relay_teams.tools.auto_harness_tools.disable_tool as disable_tool_module
import relay_teams.tools.auto_harness_tools.upgrade_tool as upgrade_tool_module
import relay_teams.tools.generated_tools.service as generated_service_module
from relay_teams.tools.registry import ToolRegistry
from relay_teams.tools.runtime.context import ToolContext, ToolDeps


SAFE_CODE = """\
def run(tool_input):
    return {"total": int(tool_input["a"]) + int(tool_input["b"])}
"""

FAIL_CODE = """\
def run(tool_input):
    return {"total": -1}
"""


class _DraftAutoHarnessService(AutoHarnessService):
    _draft: GeneratedToolDraft
    _drafts: list[GeneratedToolDraft]

    def set_draft(self, draft: GeneratedToolDraft) -> None:
        self._draft = draft
        self._drafts = [draft]

    def set_drafts(self, drafts: list[GeneratedToolDraft]) -> None:
        self._drafts = list(drafts)
        if drafts:
            self._draft = drafts[0]

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
        extra_prompt: str | None = None,
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
            extra_prompt,
        )
        if self._drafts:
            return self._drafts.pop(0)
        return self._draft

    @staticmethod
    async def _execute_code(
        *,
        code: str,
        tool_input: dict[str, JsonValue],
        expected: JsonValue | None,
        has_expected: bool,
    ) -> JsonValue:
        result = generated_service_module._execute_generated_code_sync(code, tool_input)
        if has_expected and result != expected:
            raise ValueError("Generated tool test case failed")
        return result

    @staticmethod
    async def _run_test_cases(
        code: str,
        test_cases: tuple[GeneratedToolTestCase, ...],
    ) -> tuple[str, ...]:
        failures: list[str] = []
        for test_case in test_cases:
            try:
                result = generated_service_module._execute_generated_code_sync(
                    code, test_case.input
                )
                if test_case.has_expected and result != test_case.expected:
                    failures.append("Generated tool test case failed")
            except Exception as exc:
                failures.append(str(exc))
        return tuple(failures)


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


def _build_service(
    tmp_path: Path,
    *,
    code: str,
    extra_role_ids: tuple[str, ...] = (),
    resolve_role_instance_id: generated_service_module.RoleInstanceResolver
    | None = None,
    reload_observer: Callable[[ToolRegistry], None] | None = None,
    post_reload_observer: Callable[[RoleRegistry], None] | None = None,
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
        if post_reload_observer is not None:
            post_reload_observer(updated)

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


# ---------------------------------------------------------------------------
# FE2-13: Disable Tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_tool_marks_disabled_and_removes_from_role(
    tmp_path: Path,
) -> None:
    service, _role_registry, tool_registry, role = _build_service(
        tmp_path, code=SAFE_CODE
    )
    synthesis = await service.synthesize_tool(
        role=role,
        session_id="s1",
        run_id="r1",
        task_id="t1",
        workspace_id="w1",
        conversation_id="c1",
        instance_id="i1",
        tool_name="sum",
        description="Add two integers",
        input_schema=_schema(),
        behavior="Return a + b as total.",
        test_cases=_test_cases(),
        target_role_id=None,
        thinking=RunThinkingConfig(),
    )
    enabled = await service.enable_tool(
        current_role_id=role.role_id,
        tool_name=synthesis.tool_name,
        code_hash=synthesis.code_hash,
        target_role_id=None,
        run_id="r1",
        instance_id="i1",
    )
    assert enabled.status == GeneratedToolStatus.ENABLED
    assert "generated_sum" in tool_registry.list_names()
    assert "- generated_sum" in (tmp_path / "roles" / "worker.md").read_text(
        encoding="utf-8"
    )

    disabled = await service.disable_tool(
        current_role_id=role.role_id,
        tool_name=synthesis.tool_name,
        code_hash=synthesis.code_hash,
        target_role_id=None,
        run_id="r1",
        instance_id="i1",
    )
    assert isinstance(disabled, GeneratedToolDisableResult)
    assert disabled.tool_name == "generated_sum"
    assert disabled.status == GeneratedToolStatus.DISABLED
    assert disabled.role_updated is True
    assert disabled.target_role_id == "Worker"
    assert "generated_sum" not in tool_registry.list_names()
    assert "- generated_sum" not in (tmp_path / "roles" / "worker.md").read_text(
        encoding="utf-8"
    )

    # Verify record on disk is DISABLED
    record = service._load_record("generated_sum")
    assert record.status == GeneratedToolStatus.DISABLED


@pytest.mark.asyncio
async def test_disable_tool_rejects_not_enabled(tmp_path: Path) -> None:
    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path, code=SAFE_CODE
    )
    synthesis = await service.synthesize_tool(
        role=role,
        session_id="s1",
        run_id="r1",
        task_id="t1",
        workspace_id="w1",
        conversation_id="c1",
        instance_id="i1",
        tool_name="sum",
        description="Add two integers",
        input_schema=_schema(),
        behavior="Return a + b as total.",
        test_cases=_test_cases(),
        target_role_id=None,
        thinking=RunThinkingConfig(),
    )
    with pytest.raises(ValueError, match="not enabled"):
        await service.disable_tool(
            current_role_id=role.role_id,
            tool_name=synthesis.tool_name,
            code_hash=synthesis.code_hash,
        )


@pytest.mark.asyncio
async def test_disable_tool_rejects_wrong_hash(tmp_path: Path) -> None:
    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path, code=SAFE_CODE
    )
    synthesis = await service.synthesize_tool(
        role=role,
        session_id="s1",
        run_id="r1",
        task_id="t1",
        workspace_id="w1",
        conversation_id="c1",
        instance_id="i1",
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
    with pytest.raises(ValueError, match="code_hash does not match"):
        await service.disable_tool(
            current_role_id=role.role_id,
            tool_name=synthesis.tool_name,
            code_hash="wrong",
        )


@pytest.mark.asyncio
async def test_disable_tool_action_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    role = RoleDefinition(
        role_id="Worker",
        name="Worker",
        description="Does work.",
        version="1",
        tools=(),
        system_prompt="Work.",
    )

    class _DisableService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, str | None]] = []

        async def disable_tool(
            self,
            *,
            current_role_id: str,
            tool_name: str,
            code_hash: str,
            target_role_id: str | None = None,
            run_id: str | None = None,
            instance_id: str | None = None,
            session_id: str | None = None,
        ) -> GeneratedToolDisableResult:
            self.calls.append((current_role_id, tool_name, code_hash, target_role_id))
            return GeneratedToolDisableResult(
                tool_name=tool_name,
                code_hash=code_hash,
                target_role_id=target_role_id or current_role_id,
                status=GeneratedToolStatus.DISABLED,
                role_updated=True,
            )

    service = _DisableService()
    ctx = cast(
        ToolContext,
        cast(
            object,
            _ActionContext(_ActionDeps(role=role, auto_harness_service=service)),
        ),
    )
    result = await disable_tool_module._run_disable_action(
        ctx,
        tool_name="generated_sum",
        code_hash="hash-1",
        target_role_id=None,
    )
    assert result["status"] == "disabled"
    assert result["role_updated"] is True
    assert service.calls == [("Worker", "generated_sum", "hash-1", None)]

    request = disable_tool_module._build_disable_approval_request(
        tool_name="generated_sum",
        code_hash="hash-1",
        target_role_id="Worker",
    )
    assert "disable" in request.cache_key
    assert request.risk_level is not None

    with pytest.raises(RuntimeError, match="not configured"):
        await disable_tool_module._run_disable_action(
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
        assert kwargs["tool_name"] == "auto_harness_disable_tool"
        assert kwargs["force_approval"] is True
        return {"ok": True}

    monkeypatch.setattr(
        disable_tool_module, "execute_tool_call", _fake_execute_tool_call
    )
    agent = _ToolCaptureAgent()
    disable_tool_module.register(cast(Agent[ToolDeps, str], cast(object, agent)))
    assert agent.function is not None
    tool_func = cast(
        Callable[..., Awaitable[dict[str, JsonValue]]],
        agent.function,
    )
    assert await tool_func(ctx, "generated_sum", "hash-1", None) == {"ok": True}


# ---------------------------------------------------------------------------
# FE2-14: List Tools API
# ---------------------------------------------------------------------------


def test_list_records_returns_enabled_and_pending(tmp_path: Path) -> None:
    service, _role_registry, _tool_registry, _role = _build_service(
        tmp_path, code=SAFE_CODE
    )
    assert service.list_records() == ()

    record_dir = tmp_path / "config" / "generated_tools" / "generated_sum"
    record_dir.mkdir(parents=True)
    record = GeneratedToolRecord(
        tool_name="generated_sum",
        description="Add two integers",
        input_schema=_schema(),
        test_cases=_test_cases(),
        code_hash=generated_service_module._code_hash(SAFE_CODE),
        status=GeneratedToolStatus.ENABLED,
        target_role_id="Worker",
        created_by_role_id="Worker",
    )
    (record_dir / "tool.json").write_text(
        record.model_dump_json(indent=2), encoding="utf-8"
    )
    (record_dir / "implementation.py").write_text(SAFE_CODE, encoding="utf-8")

    records = service.list_records()
    assert len(records) == 1
    assert records[0].tool_name == "generated_sum"
    assert records[0].status == GeneratedToolStatus.ENABLED
    assert records[0].version == 1


def test_version_field_defaults_to_one() -> None:
    record = GeneratedToolRecord(
        tool_name="generated_test",
        description="Test",
        input_schema={},
        code_hash="abc",
        status=GeneratedToolStatus.PENDING,
        target_role_id="Worker",
        created_by_role_id="Worker",
    )
    assert record.version == 1


# ---------------------------------------------------------------------------
# FE2-15: Synthesis Retry Loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_retries_on_test_failure_and_succeeds(
    tmp_path: Path,
) -> None:
    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path, code=FAIL_CODE
    )
    # First draft fails tests, second draft passes
    service.set_drafts(
        [
            GeneratedToolDraft(code=FAIL_CODE, notes="bad"),
            GeneratedToolDraft(code=SAFE_CODE, notes="good"),
        ]
    )
    result = await service.synthesize_tool(
        role=role,
        session_id="s1",
        run_id="r1",
        task_id="t1",
        workspace_id="w1",
        conversation_id="c1",
        instance_id="i1",
        tool_name="sum",
        description="Add two integers",
        input_schema=_schema(),
        behavior="Return a + b as total.",
        test_cases=_test_cases(),
        target_role_id=None,
        thinking=RunThinkingConfig(),
    )
    assert result.retry_count == 1
    assert len(result.retry_messages) > 0
    assert result.status == GeneratedToolStatus.PENDING
    assert result.tool_name == "generated_sum"


@pytest.mark.asyncio
async def test_synthesize_retries_exhausted_raises(tmp_path: Path) -> None:
    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path, code=FAIL_CODE
    )
    # All drafts return failing code (3 total: original + 2 retries)
    service.set_drafts(
        [
            GeneratedToolDraft(code=FAIL_CODE, notes="bad1"),
            GeneratedToolDraft(code=FAIL_CODE, notes="bad2"),
            GeneratedToolDraft(code=FAIL_CODE, notes="bad3"),
        ]
    )
    with pytest.raises(ValueError, match="failed after 2 retries"):
        await service.synthesize_tool(
            role=role,
            session_id="s1",
            run_id="r1",
            task_id="t1",
            workspace_id="w1",
            conversation_id="c1",
            instance_id="i1",
            tool_name="sum",
            description="Add two integers",
            input_schema=_schema(),
            behavior="Return a + b as total.",
            test_cases=_test_cases(),
            target_role_id=None,
            thinking=RunThinkingConfig(),
        )


@pytest.mark.asyncio
async def test_synthesize_no_retry_when_tests_pass_first_try(
    tmp_path: Path,
) -> None:
    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path, code=SAFE_CODE
    )
    result = await service.synthesize_tool(
        role=role,
        session_id="s1",
        run_id="r1",
        task_id="t1",
        workspace_id="w1",
        conversation_id="c1",
        instance_id="i1",
        tool_name="sum",
        description="Add two integers",
        input_schema=_schema(),
        behavior="Return a + b as total.",
        test_cases=_test_cases(),
        target_role_id=None,
        thinking=RunThinkingConfig(),
    )
    assert result.retry_count == 0
    assert result.retry_messages == ()


@pytest.mark.asyncio
async def test_synthesize_retry_messages_populated(tmp_path: Path) -> None:
    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path, code=FAIL_CODE
    )
    service.set_drafts(
        [
            GeneratedToolDraft(code=FAIL_CODE, notes="bad1"),
            GeneratedToolDraft(code=FAIL_CODE, notes="bad2"),
            GeneratedToolDraft(code=SAFE_CODE, notes="good"),
        ]
    )
    result = await service.synthesize_tool(
        role=role,
        session_id="s1",
        run_id="r1",
        task_id="t1",
        workspace_id="w1",
        conversation_id="c1",
        instance_id="i1",
        tool_name="sum",
        description="Add two integers",
        input_schema=_schema(),
        behavior="Return a + b as total.",
        test_cases=_test_cases(),
        target_role_id=None,
        thinking=RunThinkingConfig(),
    )
    assert result.retry_count == 2
    assert len(result.retry_messages) >= 2


# ---------------------------------------------------------------------------
# FE2-16: Upgrade Tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upgrade_tool_increments_version(tmp_path: Path) -> None:
    service, _role_registry, tool_registry, role = _build_service(
        tmp_path, code=SAFE_CODE
    )
    synthesis = await service.synthesize_tool(
        role=role,
        session_id="s1",
        run_id="r1",
        task_id="t1",
        workspace_id="w1",
        conversation_id="c1",
        instance_id="i1",
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

    upgraded_code = """\
def run(tool_input):
    return {"total": int(tool_input["a"]) + int(tool_input["b"]) + 100}
"""
    upgraded_test = (
        GeneratedToolTestCase(
            input={"a": 2, "b": 3},
            expected={"total": 105},
        ),
    )
    service.set_draft(GeneratedToolDraft(code=upgraded_code, notes="upgraded"))
    result = await service.upgrade_tool(
        role=role,
        session_id="s1",
        run_id="r1",
        task_id="t1",
        workspace_id="w1",
        conversation_id="c1",
        instance_id="i1",
        tool_name="sum",
        description="Add two integers plus 100",
        input_schema=_schema(),
        behavior="Return a + b + 100 as total.",
        test_cases=upgraded_test,
        target_role_id=None,
        thinking=RunThinkingConfig(),
    )
    assert isinstance(result, GeneratedToolUpgradeResult)
    assert result.previous_version == 1
    assert result.new_version == 2
    assert result.tool_name == "generated_sum"
    assert result.status == GeneratedToolStatus.ENABLED
    assert result.test_count == 1

    # Verify upgraded tool executes correctly
    execution_result = await service.execute_generated_tool(
        tool_name="generated_sum",
        tool_input={"a": 1, "b": 2},
    )
    assert execution_result == {"total": 103}

    # Verify the record on disk has new version
    record = service._load_record("generated_sum")
    assert record.version == 2
    assert "generated_sum" in tool_registry.list_names()


@pytest.mark.asyncio
async def test_upgrade_tool_rejects_not_enabled(tmp_path: Path) -> None:
    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path, code=SAFE_CODE
    )
    # Synthesize but don't enable - PENDING status
    synthesis = await service.synthesize_tool(
        role=role,
        session_id="s1",
        run_id="r1",
        task_id="t1",
        workspace_id="w1",
        conversation_id="c1",
        instance_id="i1",
        tool_name="sum",
        description="Add two integers",
        input_schema=_schema(),
        behavior="Return a + b as total.",
        test_cases=_test_cases(),
        target_role_id=None,
        thinking=RunThinkingConfig(),
    )
    service.set_draft(GeneratedToolDraft(code=SAFE_CODE, notes="v2"))
    with pytest.raises(ValueError, match="not enabled"):
        await service.upgrade_tool(
            role=role,
            session_id="s1",
            run_id="r1",
            task_id="t1",
            workspace_id="w1",
            conversation_id="c1",
            instance_id="i1",
            tool_name="sum",
            description="Add two integers",
            input_schema=_schema(),
            behavior="Return a + b as total.",
            test_cases=_test_cases(),
            target_role_id=None,
            thinking=RunThinkingConfig(),
        )
    # Verify the original record is still pending
    record = service._load_record(synthesis.tool_name)
    assert record.status == GeneratedToolStatus.PENDING


@pytest.mark.asyncio
async def test_upgrade_tool_rejects_failing_tests(tmp_path: Path) -> None:
    service, _role_registry, _tool_registry, role = _build_service(
        tmp_path, code=SAFE_CODE
    )
    synthesis = await service.synthesize_tool(
        role=role,
        session_id="s1",
        run_id="r1",
        task_id="t1",
        workspace_id="w1",
        conversation_id="c1",
        instance_id="i1",
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
    service.set_draft(GeneratedToolDraft(code=FAIL_CODE, notes="bad upgrade"))
    with pytest.raises(ValueError, match="failed test cases"):
        await service.upgrade_tool(
            role=role,
            session_id="s1",
            run_id="r1",
            task_id="t1",
            workspace_id="w1",
            conversation_id="c1",
            instance_id="i1",
            tool_name="sum",
            description="Add two integers",
            input_schema=_schema(),
            behavior="Return a + b as total.",
            test_cases=_test_cases(),
            target_role_id=None,
            thinking=RunThinkingConfig(),
        )


@pytest.mark.asyncio
async def test_upgrade_tool_action_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    role = RoleDefinition(
        role_id="Worker",
        name="Worker",
        description="Does work.",
        version="1",
        tools=(),
        system_prompt="Work.",
    )

    class _UpgradeService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int]] = []

        async def upgrade_tool(
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
        ) -> GeneratedToolUpgradeResult:
            self.calls.append((role.role_id, tool_name, len(test_cases)))
            return GeneratedToolUpgradeResult(
                tool_name="generated_" + tool_name,
                code_hash="hash-v2",
                target_role_id=role.role_id,
                status=GeneratedToolStatus.ENABLED,
                previous_version=1,
                new_version=2,
                test_count=len(test_cases),
            )

    service = _UpgradeService()
    resolver = _RuntimeRoleResolver(role)
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
    result = await upgrade_tool_module._run_upgrade_action(
        ctx,
        tool_name="sum",
        description="Add",
        input_schema=_schema(),
        behavior="Add values.",
        test_cases=list(_test_cases()),
        target_role_id=None,
    )
    assert result["new_version"] == 2
    assert result["previous_version"] == 1
    assert resolver.calls == [("run-1", "Worker")]

    with pytest.raises(RuntimeError, match="not configured"):
        await upgrade_tool_module._run_upgrade_action(
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
        assert kwargs["tool_name"] == "auto_harness_upgrade_tool"
        assert kwargs["force_approval"] is True
        return {"ok": True}

    monkeypatch.setattr(
        upgrade_tool_module, "execute_tool_call", _fake_execute_tool_call
    )
    agent = _ToolCaptureAgent()
    upgrade_tool_module.register(cast(Agent[ToolDeps, str], cast(object, agent)))
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
