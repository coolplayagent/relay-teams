# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import asyncio
import datetime as datetime_module
import hashlib
import json
import logging
import math
import multiprocessing
import re
import statistics
import time
import yaml
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from multiprocessing.context import ForkServerContext, SpawnContext
from pathlib import Path
from typing import NoReturn, Protocol

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai import Agent, ModelRequestNode
from pydantic_ai.settings import ModelSettings

from relay_teams.agents.execution.llm_transport_scope import (
    llm_http_client_cache_scope_for_request,
)
from relay_teams.agents.execution.model_builder import (
    RuntimeChatModel,
    build_base_model_settings,
    build_runtime_chat_model,
    is_anthropic_provider,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.net.llm_client import build_llm_http_client
from relay_teams.providers.llm_retry import run_with_llm_retry
from relay_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.roles.memory_models import default_memory_profile
from relay_teams.roles.role_models import RoleConfigSource, RoleDefinition
from relay_teams.roles.role_registry import RoleLoader, RoleRegistry
from relay_teams.sessions.runs.run_models import RunKind, RunThinkingConfig
from relay_teams.tools.generated_tools.models import (
    GeneratedToolDraft,
    GeneratedToolEnableResult,
    GeneratedToolRecord,
    GeneratedToolStatus,
    GeneratedToolSynthesisResult,
    GeneratedToolTestCase,
)
from relay_teams.tools.registry import ToolRegister, ToolRegistry
from relay_teams.tools.runtime.context import ToolContext, ToolDeps
from relay_teams.tools.runtime.execution import execute_tool_call
from relay_teams.tools.runtime.models import ToolResultProjection

LOGGER = get_logger(__name__)
GENERATED_TOOL_PREFIX = "generated_"
AUTO_HARNESS_SYNTHESIZE_TOOL = "auto_harness_synthesize_tool"
AUTO_HARNESS_ENABLE_TOOL = "auto_harness_enable_tool"
_MANIFEST_NAME = "tool.json"
_IMPLEMENTATION_NAME = "implementation.py"
_GENERATED_TOOLS_DIR = "generated_tools"
_EXECUTION_TIMEOUT_SECONDS = 2.0
_EXECUTION_POLL_INTERVAL_SECONDS = 0.02
_MODEL_OUTPUT_MAX_TOKENS = 2400
_ALLOWED_CALL_NAMES = frozenset(
    {
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "float",
        "int",
        "isinstance",
        "len",
        "list",
        "max",
        "min",
        "round",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "ValueError",
        "TypeError",
    }
)
_ALLOWED_ATTRIBUTE_CALL_NAMES = frozenset(
    {
        "datetime.date.fromisoformat",
        "datetime.datetime.fromisoformat",
        "datetime.time.fromisoformat",
        "json.dumps",
        "json.loads",
        "math.ceil",
        "math.fabs",
        "math.floor",
        "math.isfinite",
        "math.isinf",
        "math.isnan",
        "math.sqrt",
        "math.trunc",
        "re.fullmatch",
        "re.match",
        "re.search",
        "re.split",
        "re.sub",
        "statistics.mean",
        "statistics.median",
        "statistics.pstdev",
        "statistics.pvariance",
        "statistics.stdev",
        "statistics.variance",
        "tool_input.get",
        "tool_input.items",
        "tool_input.keys",
        "tool_input.values",
    }
)
_DENIED_CALL_NAMES = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "dir",
        "eval",
        "exec",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
    }
)
_DENIED_NODES = (
    ast.AsyncFunctionDef,
    ast.Await,
    ast.ClassDef,
    ast.Delete,
    ast.For,
    ast.Global,
    ast.Import,
    ast.ImportFrom,
    ast.Lambda,
    ast.Nonlocal,
    ast.Try,
    ast.While,
    ast.With,
    ast.Yield,
    ast.YieldFrom,
)


ModelConfigResolver = Callable[
    [RoleDefinition, str | None], tuple[ModelEndpointConfig | None, str | None]
]
RoleReloadCallback = Callable[[RoleRegistry], None]
RoleInstanceResolver = Callable[[str, str], str | None]


async def _run_generated_tool_action(
    tool_input: dict[str, JsonValue],
    *,
    service: AutoHarnessService,
    tool_name: str,
) -> ToolResultProjection:
    result = await service.execute_generated_tool(
        tool_name=tool_name,
        tool_input=tool_input,
    )
    return ToolResultProjection(
        visible_data={"result": result},
        internal_data={
            "tool_name": tool_name,
            "result": result,
        },
    )


class AutoHarnessService:
    def __init__(
        self,
        *,
        config_dir: Path,
        roles_dir: Path,
        builtin_roles_dir: Path,
        tool_registry: ToolRegistry,
        get_role_registry: Callable[[], RoleRegistry],
        resolve_model_config: ModelConfigResolver,
        on_roles_reloaded: RoleReloadCallback,
        resolve_role_instance_id: RoleInstanceResolver | None = None,
        retry_config: LlmRetryConfig | None = None,
    ) -> None:
        self._tools_dir = config_dir / _GENERATED_TOOLS_DIR
        self._roles_dir = roles_dir
        self._builtin_roles_dir = builtin_roles_dir
        self._tool_registry = tool_registry
        self._get_role_registry = get_role_registry
        self._resolve_model_config = resolve_model_config
        self._on_roles_reloaded = on_roles_reloaded
        self._resolve_role_instance_id = resolve_role_instance_id
        self._retry_config = retry_config or LlmRetryConfig()
        self._runtime_tools_dirty: dict[tuple[str, str], set[str]] = {}

    def register_enabled_tools(self) -> None:
        for record in self.list_records():
            if record.status != GeneratedToolStatus.ENABLED:
                continue
            try:
                self._validate_record_implementation(record)
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="autoharness.generated_tool_invalid_on_startup",
                    message="Ignoring invalid enabled generated tool",
                    payload={"tool_name": record.tool_name},
                    exc_info=exc,
                )
                continue
            self._register_record(record)

    def list_records(self) -> tuple[GeneratedToolRecord, ...]:
        if not self._tools_dir.exists():
            return ()
        records: list[GeneratedToolRecord] = []
        for manifest_path in sorted(self._tools_dir.glob(f"*/{_MANIFEST_NAME}")):
            try:
                records.append(self._load_record_from_manifest(manifest_path))
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="autoharness.generated_tool_manifest_invalid",
                    message="Ignoring invalid generated tool manifest",
                    payload={"manifest_path": str(manifest_path)},
                    exc_info=exc,
                )
        return tuple(records)

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
        normalized_tool_name = _normalize_generated_tool_name(tool_name)
        _validate_input_schema(input_schema)
        target_role = self._resolve_target_role(
            current_role_id=role.role_id,
            target_role_id=target_role_id,
        )
        unavailable_tool_names = {
            record.name for record in self._tool_registry.list_unavailable_tools()
        }
        if (
            normalized_tool_name in set(self._tool_registry.list_names())
            or normalized_tool_name in unavailable_tool_names
            or self._tool_dir(normalized_tool_name).exists()
        ):
            raise ValueError(f"Tool already exists: {normalized_tool_name}")
        draft = await self._generate_tool_draft(
            role=role,
            session_id=session_id,
            run_id=run_id,
            task_id=task_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            instance_id=instance_id,
            tool_name=normalized_tool_name,
            description=description,
            input_schema=input_schema,
            behavior=behavior,
            test_cases=test_cases,
            thinking=thinking,
        )
        code = _strip_markdown_code_fence(draft.code)
        _validate_generated_code(code)
        for test_case in test_cases:
            await self._execute_code(
                code=code,
                tool_input=test_case.input,
                expected=test_case.expected,
                has_expected=test_case.has_expected,
            )
        code_hash = _code_hash(code)
        record = GeneratedToolRecord(
            tool_name=normalized_tool_name,
            description=description.strip(),
            input_schema=input_schema,
            test_cases=test_cases,
            code_hash=code_hash,
            status=GeneratedToolStatus.PENDING,
            target_role_id=target_role.role_id,
            created_by_role_id=role.role_id,
        )
        self._write_record(record=record, code=code)
        return GeneratedToolSynthesisResult(
            tool_name=record.tool_name,
            code_hash=record.code_hash,
            status=record.status,
            test_count=len(record.test_cases),
            notes=draft.notes.strip(),
        )

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
        normalized_tool_name = _normalize_generated_tool_name(tool_name)
        record = self._load_record(normalized_tool_name)
        if record.status != GeneratedToolStatus.PENDING:
            raise ValueError(f"Generated tool is not pending: {normalized_tool_name}")
        if record.code_hash != code_hash.strip():
            raise ValueError("code_hash does not match the pending generated tool")
        target_role = self._resolve_target_role(
            current_role_id=current_role_id,
            target_role_id=target_role_id or record.target_role_id,
        )
        code = self._load_validated_record_implementation(
            record,
            hash_mismatch_message=(
                "implementation.py does not match the recorded code_hash"
            ),
        )
        for test_case in record.test_cases:
            await self._execute_code(
                code=code,
                tool_input=test_case.input,
                expected=test_case.expected,
                has_expected=test_case.has_expected,
            )
        enabled = record.model_copy(
            update={
                "status": GeneratedToolStatus.ENABLED,
                "target_role_id": target_role.role_id,
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
        self._register_record(enabled)
        try:
            role_updated = self._attach_tool_to_role(
                role=target_role,
                tool_name=record.tool_name,
            )
        except Exception:
            self._tool_registry.unregister_tool(enabled.tool_name)
            raise
        self._write_record(record=enabled, code=code)
        if run_id:
            dirty_instance_ids: list[str] = []
            if target_role.role_id == current_role_id and instance_id:
                dirty_instance_ids.append(instance_id)
            if session_id and self._resolve_role_instance_id is not None:
                target_instance_id = self._resolve_role_instance_id(
                    session_id,
                    target_role.role_id,
                )
                if target_instance_id and target_instance_id not in dirty_instance_ids:
                    dirty_instance_ids.append(target_instance_id)
            for dirty_instance_id in dirty_instance_ids:
                self.mark_runtime_tools_dirty(
                    run_id=run_id,
                    instance_id=dirty_instance_id,
                    tool_names=(enabled.tool_name,),
                )
        log_event(
            LOGGER,
            logging.INFO,
            event="autoharness.generated_tool_enabled",
            message="Generated tool enabled and attached to role",
            payload={
                "tool_name": enabled.tool_name,
                "target_role_id": target_role.role_id,
                "current_role_id": current_role_id,
            },
        )
        return GeneratedToolEnableResult(
            tool_name=enabled.tool_name,
            code_hash=enabled.code_hash,
            target_role_id=target_role.role_id,
            status=enabled.status,
            role_updated=role_updated,
        )

    def mark_runtime_tools_dirty(
        self,
        *,
        run_id: str,
        instance_id: str,
        tool_names: tuple[str, ...],
    ) -> None:
        key = (run_id, instance_id)
        dirty = self._runtime_tools_dirty.setdefault(key, set())
        dirty.update(name for name in tool_names if name.strip())

    def consume_tools_dirty(self, *, run_id: str, instance_id: str) -> tuple[str, ...]:
        dirty = self._runtime_tools_dirty.pop((run_id, instance_id), set())
        return tuple(sorted(dirty))

    async def execute_generated_tool(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, JsonValue],
    ) -> JsonValue:
        record = self._load_record(tool_name)
        if record.status != GeneratedToolStatus.ENABLED:
            raise PermissionError(f"Generated tool is not enabled: {tool_name}")
        _validate_input_schema(record.input_schema)
        code = self._load_validated_record_implementation(
            record,
            hash_mismatch_message=(
                f"Generated tool implementation hash mismatch: {tool_name}"
            ),
        )
        return await self._execute_code(
            code=code,
            tool_input=tool_input,
            expected=None,
            has_expected=False,
        )

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
        config, profile_name = self._resolve_model_config(role, session_id)
        if config is None:
            raise RuntimeError("AutoHarness could not resolve the current role model")
        request = LLMRequest(
            run_id=run_id,
            trace_id=run_id,
            task_id=task_id,
            session_id=session_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            instance_id=instance_id,
            role_id=role.role_id,
            system_prompt="AutoHarness synthesis",
            user_prompt=None,
            session_mode="normal",
            run_kind=RunKind.CONVERSATION,
            thinking=thinking,
        )
        agent = Agent[None, GeneratedToolDraft](
            model=_build_model(
                config,
                cache_scope=llm_http_client_cache_scope_for_request(request),
            ),
            output_type=GeneratedToolDraft,
            instructions=(
                "Generate a safe Python implementation for a Relay Teams generated "
                "tool. Return only the structured output. The code must define "
                "`run(tool_input)` and return JSON-serializable data. Do not use "
                "imports, files, network, subprocesses, eval, exec, dynamic import, "
                "dunder attributes, or global mutable state. Prefer simple "
                "deterministic logic."
            ),
            model_settings=_model_settings(config),
            retries=2,
        )
        prompt = _build_generation_prompt(
            role=role,
            tool_name=tool_name,
            description=description,
            input_schema=input_schema,
            behavior=behavior,
            test_cases=test_cases,
            profile_name=profile_name,
        )
        return await run_with_llm_retry(
            operation=lambda: _run_streaming_generation(
                agent=agent,
                prompt=prompt,
            ),
            config=self._retry_config,
            is_retry_allowed=lambda: True,
            on_retry_scheduled=lambda _schedule: None,
        )

    @staticmethod
    async def _execute_code(
        *,
        code: str,
        tool_input: dict[str, JsonValue],
        expected: JsonValue | None,
        has_expected: bool,
    ) -> JsonValue:
        result = await _execute_generated_code_in_process(code, tool_input)
        if has_expected and result != expected:
            raise ValueError(
                "Generated tool test case failed: "
                f"expected {json.dumps(expected, ensure_ascii=False, sort_keys=True)}, "
                f"got {json.dumps(result, ensure_ascii=False, sort_keys=True)}"
            )
        return result

    def _register_record(self, record: GeneratedToolRecord) -> None:
        self._tool_registry.register_tool(
            record.tool_name,
            self._build_tool_register(record),
        )

    def _validate_record_implementation(self, record: GeneratedToolRecord) -> None:
        self._load_validated_record_implementation(
            record,
            hash_mismatch_message=(
                f"Generated tool implementation hash mismatch: {record.tool_name}"
            ),
        )

    def _load_validated_record_implementation(
        self,
        record: GeneratedToolRecord,
        *,
        hash_mismatch_message: str,
    ) -> str:
        code = self._implementation_path(record.tool_name).read_text(encoding="utf-8")
        _validate_generated_code(code)
        if _code_hash(code) != record.code_hash:
            raise ValueError(hash_mismatch_message)
        return code

    def _build_tool_register(self, record: GeneratedToolRecord) -> ToolRegister:
        service = self
        description = _build_runtime_tool_description(record)

        def register(agent: Agent[ToolDeps, str]) -> None:
            @agent.tool(
                name=record.tool_name,
                description=description,
                timeout=_EXECUTION_TIMEOUT_SECONDS + 1.0,
            )
            async def generated_tool(
                ctx: ToolContext,
                tool_input: dict[str, JsonValue],
            ) -> dict[str, JsonValue]:
                input_keys: list[JsonValue] = [key for key in sorted(tool_input.keys())]

                async def action(
                    tool_args: dict[str, JsonValue],
                ) -> ToolResultProjection:
                    return await _run_generated_tool_action(
                        tool_args,
                        service=service,
                        tool_name=record.tool_name,
                    )

                return await execute_tool_call(
                    ctx,
                    tool_name=record.tool_name,
                    args_summary={
                        "input_keys": input_keys,
                    },
                    action=action,
                    raw_args=tool_input,
                )

        return register

    def _load_record(self, tool_name: str) -> GeneratedToolRecord:
        manifest_path = self._manifest_path(tool_name)
        if not manifest_path.is_file():
            raise KeyError(f"Generated tool not found: {tool_name}")
        return self._load_record_from_manifest(manifest_path)

    @staticmethod
    def _load_record_from_manifest(manifest_path: Path) -> GeneratedToolRecord:
        record = GeneratedToolRecord.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        _validate_record_tool_name(record)
        return record

    def _write_record(self, *, record: GeneratedToolRecord, code: str) -> None:
        tool_dir = self._tool_dir(record.tool_name)
        tool_dir.mkdir(parents=True, exist_ok=True)
        self._implementation_path(record.tool_name).write_text(code, encoding="utf-8")
        self._manifest_path(record.tool_name).write_text(
            record.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _resolve_target_role(
        self,
        *,
        current_role_id: str,
        target_role_id: str | None,
    ) -> RoleDefinition:
        resolved_role_id = (target_role_id or current_role_id).strip()
        if not resolved_role_id:
            raise ValueError("target_role_id must not be empty")
        try:
            return self._get_role_registry().get(resolved_role_id)
        except KeyError as exc:
            if target_role_id is None:
                raise ValueError(
                    "Temporary roles cannot receive generated tool assets; "
                    "pass target_role_id for a persistent role."
                ) from exc
            raise ValueError(f"Unknown target role: {resolved_role_id}") from exc

    def _attach_tool_to_role(self, *, role: RoleDefinition, tool_name: str) -> bool:
        if tool_name in role.tools:
            return False
        role_map = RoleLoader().build_effective_role_map(
            builtin_roles_dir=self._builtin_roles_dir,
            app_roles_dir=self._roles_dir,
        )
        source_record = role_map.get(role.role_id)
        if source_record is None:
            raise ValueError(f"Role not found: {role.role_id}")
        source_path, source = source_record
        content = source_path.read_text(encoding="utf-8")
        front_matter, body = _split_markdown_front_matter(content)
        parsed = yaml.safe_load(front_matter)
        if not isinstance(parsed, dict):
            raise ValueError(f"Invalid role front matter: {source_path.name}")
        raw_tools = parsed.get("tools", [])
        if raw_tools is None:
            raw_tools = []
        if not isinstance(raw_tools, list):
            raise ValueError(f"Role tools must be a list: {source_path.name}")
        tools = [str(item) for item in raw_tools if str(item).strip()]
        if tool_name not in tools:
            tools.append(tool_name)
        parsed["tools"] = tools
        target_path = (
            self._roles_dir / f"{role.role_id}.md"
            if source == RoleConfigSource.BUILTIN
            else source_path
        )
        previous_content = (
            target_path.read_text(encoding="utf-8") if target_path.exists() else None
        )
        current_registry = self._get_role_registry()
        previous_role = current_registry.get(role.role_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            target_path.write_text(
                _render_role_markdown(front_matter=parsed, body=body),
                encoding="utf-8",
            )
            updated_registry = RoleLoader().load_builtin_and_app(
                builtin_roles_dir=self._builtin_roles_dir,
                app_roles_dir=self._roles_dir,
                allow_empty=True,
            )
            current_registry.register(updated_registry.get(role.role_id))
            self._on_roles_reloaded(updated_registry)
        except Exception as exc:
            try:
                if previous_content is None:
                    target_path.unlink(missing_ok=True)
                else:
                    target_path.write_text(previous_content, encoding="utf-8")
                current_registry.register(previous_role)
            except Exception as rollback_exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="autoharness.role_attach_rollback_failed",
                    message="Failed to roll back generated tool role attachment",
                    payload={
                        "role_id": role.role_id,
                        "tool_name": tool_name,
                        "target_path": str(target_path),
                        "error_type": type(exc).__name__,
                    },
                    exc_info=rollback_exc,
                )
            raise
        return True

    def _tool_dir(self, tool_name: str) -> Path:
        return self._tools_dir / tool_name

    def _manifest_path(self, tool_name: str) -> Path:
        return self._tool_dir(tool_name) / _MANIFEST_NAME

    def _implementation_path(self, tool_name: str) -> Path:
        return self._tool_dir(tool_name) / _IMPLEMENTATION_NAME


async def _run_streaming_generation(
    *,
    agent: Agent[None, GeneratedToolDraft],
    prompt: str,
) -> GeneratedToolDraft:
    async with agent.iter(prompt) as agent_run:
        async for node in agent_run:
            if not isinstance(node, ModelRequestNode):
                continue
            async with node.stream(agent_run.ctx) as stream:
                async for _event in stream:
                    pass
        if agent_run.result is None:
            raise RuntimeError("AutoHarness synthesis did not produce a result")
        return agent_run.result.output


def _build_model(
    config: ModelEndpointConfig,
    *,
    cache_scope: str | None,
) -> RuntimeChatModel:
    return build_runtime_chat_model(
        config=config,
        http_client=build_llm_http_client(
            connect_timeout_seconds=config.connect_timeout_seconds,
            ssl_verify=config.ssl_verify,
            cache_scope=cache_scope,
        ),
    )


def _model_settings(config: ModelEndpointConfig) -> ModelSettings:
    configured_max_tokens = config.sampling.max_tokens
    max_tokens = (
        _MODEL_OUTPUT_MAX_TOKENS
        if configured_max_tokens is None
        else min(configured_max_tokens, _MODEL_OUTPUT_MAX_TOKENS)
    )
    capped_config = config.model_copy(
        update={
            "sampling": config.sampling.model_copy(
                update={
                    "temperature": min(config.sampling.temperature, 0.2),
                    "max_tokens": max_tokens,
                }
            )
        }
    )
    settings = build_base_model_settings(capped_config)
    if not is_anthropic_provider(config.provider):
        settings["extra_body"] = {"response_format": {"type": "json_object"}}
    return settings


def _build_generation_prompt(
    *,
    role: RoleDefinition,
    tool_name: str,
    description: str,
    input_schema: dict[str, JsonValue],
    behavior: str,
    test_cases: tuple[GeneratedToolTestCase, ...],
    profile_name: str | None,
) -> str:
    return "\n".join(
        (
            f"Role: {role.role_id}",
            f"Model profile: {profile_name or role.model_profile}",
            f"Tool name: {tool_name}",
            f"Description: {description.strip()}",
            "Input schema:",
            json.dumps(input_schema, ensure_ascii=False, sort_keys=True),
            "Behavior:",
            behavior.strip(),
            "Test cases:",
            json.dumps(
                [case.model_dump(mode="json") for case in test_cases],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "Return code that defines exactly one public function: "
            "run(tool_input). Do not include markdown fences.",
        )
    )


def _normalize_generated_tool_name(tool_name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", tool_name.strip()).strip("_").lower()
    if not normalized:
        raise ValueError("tool_name must not be empty")
    if not normalized.startswith(GENERATED_TOOL_PREFIX):
        normalized = GENERATED_TOOL_PREFIX + normalized
    if normalized == GENERATED_TOOL_PREFIX:
        raise ValueError("tool_name must include a name after generated_")
    return normalized


def _validate_input_schema(input_schema: dict[str, JsonValue]) -> None:
    schema_type = input_schema.get("type")
    if schema_type is not None and schema_type != "object":
        raise ValueError("Generated tool input_schema must be an object schema")
    properties = input_schema.get("properties")
    if properties is not None and not isinstance(properties, dict):
        raise ValueError("Generated tool input_schema.properties must be an object")


def _validate_record_tool_name(record: GeneratedToolRecord) -> None:
    if record.tool_name != _normalize_generated_tool_name(record.tool_name):
        raise ValueError(
            "Generated tool manifest tool_name must use the generated_ namespace"
        )


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _strip_markdown_code_fence(code: str) -> str:
    stripped = code.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _validate_generated_code(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"Generated tool code has invalid syntax: {exc}") from exc
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1 or functions[0].name != "run":
        raise ValueError("Generated tool code must define exactly one run function")
    _validate_run_function_signature(functions[0])
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.FunctionDef):
            continue
        raise ValueError(
            "Generated tool code may only contain a module docstring and run"
        )
    _GeneratedCodeSafetyVisitor().visit(tree)


def _validate_run_function_signature(function: ast.FunctionDef) -> None:
    arguments = function.args
    if (
        arguments.posonlyargs
        or len(arguments.args) != 1
        or arguments.args[0].arg != "tool_input"
        or arguments.vararg is not None
        or arguments.kwonlyargs
        or arguments.kwarg is not None
        or arguments.defaults
        or arguments.kw_defaults
    ):
        raise ValueError(
            "Generated tool run function must accept exactly one required "
            "tool_input argument"
        )


def _attribute_call_name(node: ast.Attribute) -> str | None:
    parts = [node.attr]
    current = node.value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


class _GeneratedCodeSafetyVisitor(ast.NodeVisitor):
    def visit(self, node: ast.AST) -> None:
        if isinstance(node, _DENIED_NODES):
            raise ValueError(
                f"Generated tool code uses forbidden syntax: {type(node).__name__}"
            )
        return super().visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise ValueError("Generated tool code must not access private attributes")
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            raise ValueError("Generated tool code must not mutate attributes")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in _DENIED_CALL_NAMES:
                raise ValueError(
                    f"Generated tool code calls forbidden function: {name}"
                )
            if name not in _ALLOWED_CALL_NAMES:
                raise ValueError(
                    f"Generated tool code calls unsupported function: {name}"
                )
        elif isinstance(node.func, ast.Attribute):
            self.generic_visit(node)
            name = _attribute_call_name(node.func)
            if name is None or name not in _ALLOWED_ATTRIBUTE_CALL_NAMES:
                target = name or node.func.attr
                raise ValueError(
                    "Generated tool code calls unsupported attribute function: "
                    f"{target}"
                )
            return
        else:
            raise ValueError(
                "Generated tool code calls unsupported callable expression"
            )
        self.generic_visit(node)


class _GeneratedCodeProcessMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    result: JsonValue | None = None
    error_type: str = ""
    message: str = ""


class _GeneratedCodeOutputConnection(Protocol):
    def send(self, obj: object) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def _generated_code_process_context() -> ForkServerContext | SpawnContext:
    if "forkserver" in multiprocessing.get_all_start_methods():
        return multiprocessing.get_context("forkserver")
    return multiprocessing.get_context("spawn")


async def _execute_generated_code_in_process(
    code: str,
    tool_input: dict[str, JsonValue],
) -> JsonValue:
    _validate_generated_code(code)
    process_context = _generated_code_process_context()
    parent_connection, child_connection = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=_execute_generated_code_process_worker,
        args=(code, dict(tool_input), child_connection),
    )
    process.start()
    child_connection.close()
    try:
        deadline = time.monotonic() + _EXECUTION_TIMEOUT_SECONDS
        raw_message: object | None = None
        while raw_message is None:
            if parent_connection.poll():
                raw_message = await asyncio.to_thread(parent_connection.recv)
                break
            if not process.is_alive():
                await asyncio.to_thread(process.join, 0.0)
                if parent_connection.poll():
                    raw_message = await asyncio.to_thread(parent_connection.recv)
                    break
                raise RuntimeError("Generated tool process exited without a result")
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                process.terminate()
                await asyncio.to_thread(process.join, 1.0)
                if process.is_alive():
                    process.kill()
                    await asyncio.to_thread(process.join, 1.0)
                raise TimeoutError("Generated tool execution timed out")
            await asyncio.sleep(
                min(_EXECUTION_POLL_INTERVAL_SECONDS, max(remaining, 0.0))
            )
        await asyncio.to_thread(
            process.join,
            max(deadline - time.monotonic(), 0.0),
        )
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 1.0)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 1.0)
            raise TimeoutError("Generated tool execution timed out")
        if raw_message is None:
            raise RuntimeError("Generated tool process exited without a result")
        message = _GeneratedCodeProcessMessage.model_validate(raw_message)
        if message.ok:
            return message.result
        return _raise_generated_code_process_error(message)
    finally:
        parent_connection.close()


def _execute_generated_code_process_worker(
    code: str,
    tool_input: dict[str, JsonValue],
    output_connection: _GeneratedCodeOutputConnection,
) -> None:
    try:
        try:
            result = _execute_generated_code_sync(code, tool_input)
        except Exception as exc:
            output_connection.send(
                _GeneratedCodeProcessMessage(
                    ok=False,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )
            return
        output_connection.send(_GeneratedCodeProcessMessage(ok=True, result=result))
    finally:
        output_connection.close()


def _raise_generated_code_process_error(
    message: _GeneratedCodeProcessMessage,
) -> NoReturn:
    if message.error_type == "ValueError":
        raise ValueError(message.message)
    if message.error_type == "TypeError":
        raise TypeError(message.message)
    raise RuntimeError(
        "Generated tool process failed"
        + (f": {message.message}" if message.message else "")
    )


class _SafeModuleFacade:
    __slots__ = ("_attrs",)

    _attrs: dict[str, object]

    def __init__(self, attrs: Mapping[str, object]) -> None:
        object.__setattr__(self, "_attrs", dict(attrs))

    def __getattr__(self, name: str) -> object:
        try:
            return self._attrs[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: object) -> NoReturn:
        _ = (name, value)
        raise AttributeError("Generated tool globals are read-only")

    def __delattr__(self, name: str) -> NoReturn:
        _ = name
        raise AttributeError("Generated tool globals are read-only")


def _build_generated_code_globals() -> dict[str, object]:
    return {
        "__builtins__": {
            "ValueError": ValueError,
            "TypeError": TypeError,
            "abs": abs,
            "all": all,
            "any": any,
            "bool": bool,
            "dict": dict,
            "enumerate": enumerate,
            "float": float,
            "int": int,
            "isinstance": isinstance,
            "len": len,
            "list": list,
            "max": max,
            "min": min,
            "round": round,
            "set": set,
            "sorted": sorted,
            "str": str,
            "sum": sum,
            "tuple": tuple,
        },
        "json": _SafeModuleFacade(
            {
                "dumps": json.dumps,
                "loads": json.loads,
            }
        ),
        "math": _SafeModuleFacade(
            {
                "ceil": math.ceil,
                "e": math.e,
                "fabs": math.fabs,
                "floor": math.floor,
                "isfinite": math.isfinite,
                "isinf": math.isinf,
                "isnan": math.isnan,
                "pi": math.pi,
                "sqrt": math.sqrt,
                "tau": math.tau,
                "trunc": math.trunc,
            }
        ),
        "re": _SafeModuleFacade(
            {
                "ASCII": re.ASCII,
                "DOTALL": re.DOTALL,
                "IGNORECASE": re.IGNORECASE,
                "MULTILINE": re.MULTILINE,
                "fullmatch": re.fullmatch,
                "match": re.match,
                "search": re.search,
                "split": re.split,
                "sub": re.sub,
            }
        ),
        "datetime": _SafeModuleFacade(
            {
                "date": datetime_module.date,
                "datetime": datetime_module.datetime,
                "time": datetime_module.time,
            }
        ),
        "statistics": _SafeModuleFacade(
            {
                "mean": statistics.mean,
                "median": statistics.median,
                "pstdev": statistics.pstdev,
                "pvariance": statistics.pvariance,
                "stdev": statistics.stdev,
                "variance": statistics.variance,
            }
        ),
    }


def _execute_generated_code_sync(
    code: str,
    tool_input: dict[str, JsonValue],
) -> JsonValue:
    _validate_generated_code(code)
    globals_map = _build_generated_code_globals()
    exec(compile(code, "<generated_tool>", "exec"), globals_map, globals_map)
    run_callable = globals_map.get("run")
    if not callable(run_callable):
        raise ValueError("Generated tool code did not define callable run")
    result = run_callable(dict(tool_input))
    return _to_json_value(result)


def _to_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, tuple):
        return [_to_json_value(item) for item in value]
    if isinstance(value, list):
        return [_to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    raise TypeError(f"Generated tool returned non-JSON value: {type(value).__name__}")


def _build_runtime_tool_description(record: GeneratedToolRecord) -> str:
    schema_json = json.dumps(record.input_schema, ensure_ascii=False, sort_keys=True)
    return (
        f"{record.description.strip()}\n\n"
        "Generated AutoHarness tool. Pass one JSON object as `tool_input`.\n\n"
        f"Input schema: {schema_json}"
    )


def _split_markdown_front_matter(content: str) -> tuple[str, str]:
    lines = content.lstrip("\ufeff").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("Role markdown must start with YAML front matter")
    end_index: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_index = idx
            break
    if end_index is None:
        raise ValueError("Invalid YAML front matter delimiters")
    return "".join(lines[1:end_index]), "".join(lines[end_index + 1 :]).strip()


def _render_role_markdown(*, front_matter: dict[object, object], body: str) -> str:
    if "memory_profile" in front_matter:
        memory_profile = front_matter.get("memory_profile")
        if isinstance(
            memory_profile, dict
        ) and memory_profile == default_memory_profile().model_dump(mode="json"):
            front_matter.pop("memory_profile", None)
    serialized = yaml.safe_dump(
        front_matter,
        sort_keys=False,
        allow_unicode=False,
    ).strip()
    return f"---\n{serialized}\n---\n\n{body.strip()}\n"
