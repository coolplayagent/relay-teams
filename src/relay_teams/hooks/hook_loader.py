from __future__ import annotations

import fnmatch
from json import dumps, loads
from pathlib import Path
from collections.abc import Callable
from typing import Protocol, cast

from pydantic import JsonValue, ValidationError

from relay_teams.hooks.hook_models import (
    HookEventName,
    HookHandlerType,
    HooksConfig,
    HookRuntimeSnapshot,
    HookHandlerConfig,
    HookSourceInfo,
    HookSourceScope,
    HookMatcherGroup,
    ResolvedHookMatcherGroup,
)
from relay_teams.logger import get_logger
from relay_teams.paths import get_project_root_or_none

LOGGER = get_logger(__name__)
_CAPABILITY_WILDCARD = "*"

MATCHER_UNSUPPORTED_EVENTS = frozenset(
    {
        HookEventName.USER_PROMPT_SUBMIT,
        HookEventName.STOP,
        HookEventName.TASK_CREATED,
        HookEventName.TASK_COMPLETED,
    }
)
TOOL_EVENTS = frozenset(
    {
        HookEventName.PRE_TOOL_USE,
        HookEventName.PERMISSION_REQUEST,
        HookEventName.POST_TOOL_USE,
        HookEventName.POST_TOOL_USE_FAILURE,
    }
)
_EMPTY_GROUP_ERROR = "hook matcher group must contain at least one handler"
COMMAND_ONLY_EVENTS = frozenset({HookEventName.SESSION_START})
COMMAND_HTTP_ONLY_EVENTS = frozenset(
    {
        HookEventName.SESSION_END,
        HookEventName.STOP_FAILURE,
        HookEventName.SUBAGENT_START,
        HookEventName.PRE_COMPACT,
        HookEventName.POST_COMPACT,
    }
)


class _HookRoleEntry(Protocol):
    role_id: str
    skills: tuple[str, ...]
    hooks: HooksConfig
    source_path: Path | None


class _HookRoleRegistry(Protocol):
    def get(self, role_id: str) -> object: ...
    def list_roles(self) -> tuple[_HookRoleEntry, ...]: ...


class _HookSkillMetadata(Protocol):
    hooks: HooksConfig


class _HookSkillEntry(Protocol):
    ref: str
    metadata: _HookSkillMetadata
    directory: Path


class _HookSkillRegistry(Protocol):
    def list_skill_definitions(self) -> tuple[_HookSkillEntry, ...]: ...


class HookLoader:
    def __init__(
        self,
        *,
        app_config_dir: Path,
        project_root: Path | None = None,
        get_role_registry: Callable[[], object] | None = None,
        get_skill_registry: Callable[[], object] | None = None,
    ) -> None:
        self._app_config_dir = app_config_dir
        self._project_root = project_root or get_project_root_or_none(Path.cwd())
        self._get_role_registry = get_role_registry
        self._get_skill_registry = get_skill_registry

    @property
    def user_config_path(self) -> Path:
        return self._app_config_dir / "hooks.json"

    def project_config_paths(self) -> tuple[Path, ...]:
        if self._project_root is None:
            return ()
        base_dir = self._project_root / ".relay-teams"
        return (base_dir / "hooks.json", base_dir / "hooks.local.json")

    def get_user_config(self) -> HooksConfig:
        return self._load_single_file(self.user_config_path, tolerant=True)

    def save_user_config(self, config: HooksConfig) -> None:
        _ = self.user_config_path.write_text(
            dumps(config.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def validate_payload(self, payload: object) -> HooksConfig:
        normalized_payload = normalize_hooks_payload(payload)
        try:
            config = HooksConfig.model_validate(normalized_payload)
        except ValidationError as exc:
            raise ValueError(_format_validation_error(exc)) from exc
        validate_hook_event_capabilities(config=config)
        return self._validate_handler_references(config=config, tolerant=False)

    def load_snapshot(self) -> HookRuntimeSnapshot:
        resolved: dict[HookEventName, list[ResolvedHookMatcherGroup]] = {}
        sources: list[HookSourceInfo] = []
        project_paths = self.project_config_paths()
        ordered_paths = (
            (HookSourceScope.PROJECT_LOCAL, project_paths[-1])
            if len(project_paths) == 2
            else None,
            (HookSourceScope.PROJECT, project_paths[0]) if project_paths else None,
            (HookSourceScope.USER, self.user_config_path),
        )
        for item in ordered_paths:
            if item is None:
                continue
            scope, path = item
            if not path.exists():
                continue
            config = self._load_single_file(path, tolerant=True)
            if not config.hooks:
                continue
            source = HookSourceInfo(scope=scope, path=path)
            sources.append(source)
            for event_name, groups in config.hooks.items():
                bucket = resolved.setdefault(event_name, [])
                for group in groups:
                    bucket.append(
                        ResolvedHookMatcherGroup(
                            source=source,
                            event_name=event_name,
                            group=group,
                        )
                    )
        self._append_role_hooks(resolved=resolved, sources=sources)
        self._append_skill_hooks(resolved=resolved, sources=sources)
        return HookRuntimeSnapshot(
            sources=tuple(sources),
            hooks={key: tuple(value) for key, value in resolved.items()},
        )

    def _load_single_file(self, path: Path, *, tolerant: bool) -> HooksConfig:
        if not path.exists():
            return HooksConfig()
        try:
            payload = _load_json_object(path)
            normalized_payload = normalize_hooks_payload(payload)
            if tolerant:
                return self._load_tolerant_payload(
                    path=path,
                    payload=normalized_payload,
                )
            config = HooksConfig.model_validate(normalized_payload)
            validate_hook_event_capabilities(config=config)
            return self._validate_handler_references(config=config, tolerant=False)
        except Exception:
            if not tolerant:
                raise
            LOGGER.warning("Ignoring invalid hook config", extra={"path": str(path)})
            return HooksConfig()

    def _load_tolerant_payload(self, *, path: Path, payload: object) -> HooksConfig:
        if not isinstance(payload, dict):
            LOGGER.warning("Ignoring invalid hook config", extra={"path": str(path)})
            return HooksConfig()
        raw_hooks = payload.get("hooks")
        if not isinstance(raw_hooks, dict):
            LOGGER.warning("Ignoring invalid hook config", extra={"path": str(path)})
            return HooksConfig()
        next_hooks: dict[HookEventName, list[HookMatcherGroup]] = {}
        for raw_event_name, raw_groups in raw_hooks.items():
            if not isinstance(raw_event_name, str) or not isinstance(raw_groups, list):
                LOGGER.warning(
                    "Ignoring invalid hook event groups",
                    extra={"path": str(path), "event_name": str(raw_event_name)},
                )
                continue
            for index, raw_group in enumerate(raw_groups):
                try:
                    config = HooksConfig.model_validate(
                        {"hooks": {raw_event_name: [raw_group]}}
                    )
                    validate_hook_event_capabilities(config=config)
                    filtered_config = self._validate_handler_references(
                        config=config,
                        tolerant=True,
                    )
                    _append_hook_groups(
                        destination=next_hooks,
                        config=filtered_config,
                    )
                except Exception:
                    if not self._salvage_tolerant_group_handlers(
                        destination=next_hooks,
                        path=path,
                        raw_event_name=raw_event_name,
                        raw_group=raw_group,
                        group_index=index,
                    ):
                        LOGGER.warning(
                            "Ignoring invalid hook group",
                            extra={
                                "path": str(path),
                                "event_name": raw_event_name,
                                "group_index": index,
                            },
                        )
        return HooksConfig(
            hooks={
                event_name: tuple(groups)
                for event_name, groups in next_hooks.items()
                if groups
            }
        )

    def _salvage_tolerant_group_handlers(
        self,
        *,
        destination: dict[HookEventName, list[HookMatcherGroup]],
        path: Path,
        raw_event_name: str,
        raw_group: object,
        group_index: int,
    ) -> bool:
        if not isinstance(raw_group, dict):
            return False
        raw_handlers = raw_group.get("hooks")
        if not isinstance(raw_handlers, list):
            return False
        salvaged = False
        salvaged_hooks: dict[HookEventName, list[HookMatcherGroup]] = {}
        for handler_index, raw_handler in enumerate(raw_handlers):
            try:
                config = HooksConfig.model_validate(
                    {
                        "hooks": {
                            raw_event_name: [dict(raw_group) | {"hooks": [raw_handler]}]
                        }
                    }
                )
                validate_hook_event_capabilities(config=config)
                filtered_config = self._validate_handler_references(
                    config=config,
                    tolerant=True,
                )
                _append_merged_hook_groups(
                    destination=salvaged_hooks,
                    config=filtered_config,
                )
                salvaged = True
            except Exception:
                LOGGER.warning(
                    "Ignoring invalid hook handler",
                    extra={
                        "path": str(path),
                        "event_name": raw_event_name,
                        "group_index": group_index,
                        "handler_index": handler_index,
                    },
                )
        _append_hook_groups(
            destination=destination,
            config=HooksConfig(
                hooks={
                    event_name: tuple(groups)
                    for event_name, groups in salvaged_hooks.items()
                    if groups
                }
            ),
        )
        return salvaged

    def _validate_handler_references(
        self,
        *,
        config: HooksConfig,
        tolerant: bool,
    ) -> HooksConfig:
        known_role_ids = self._known_role_ids()
        if not known_role_ids:
            return config
        next_hooks: dict[HookEventName, tuple[HookMatcherGroup, ...]] = {}
        for event_name, groups in config.hooks.items():
            next_groups: list[HookMatcherGroup] = []
            for group in groups:
                next_handlers: list[HookHandlerConfig] = []
                for handler in group.hooks:
                    if self._handler_role_is_valid(
                        handler=handler,
                        known_role_ids=known_role_ids,
                        tolerant=tolerant,
                    ):
                        next_handlers.append(handler)
                if next_handlers:
                    next_groups.append(
                        group.model_copy(update={"hooks": tuple(next_handlers)})
                    )
            if next_groups:
                next_hooks[event_name] = tuple(next_groups)
        return HooksConfig(hooks=next_hooks)

    @staticmethod
    def _validate_event_capabilities(*, config: HooksConfig) -> None:
        validate_hook_event_capabilities(config=config)

    @staticmethod
    def _validate_handler_event_compatibility(
        *,
        event_name: HookEventName,
        handler: HookHandlerConfig,
    ) -> None:
        _validate_handler_event_compatibility(
            event_name=event_name,
            handler=handler,
        )

    def _handler_role_is_valid(
        self,
        *,
        handler: HookHandlerConfig,
        known_role_ids: frozenset[str],
        tolerant: bool,
    ) -> bool:
        if handler.type != HookHandlerType.AGENT:
            return True
        role_id = str(handler.role_id or "").strip()
        if role_id in known_role_ids:
            return True
        if not tolerant:
            raise ValueError(f"Unknown agent hook role_id: {role_id or '<empty>'}")
        LOGGER.warning(
            "Ignoring hook handler with unknown agent role",
            extra={"role_id": role_id},
        )
        return False

    def _known_role_ids(self) -> frozenset[str]:
        if self._get_role_registry is None:
            return frozenset()
        role_registry = cast(_HookRoleRegistry, self._get_role_registry())
        return frozenset(
            str(role.role_id or "").strip()
            for role in role_registry.list_roles()
            if str(role.role_id or "").strip()
        )

    def _append_role_hooks(
        self,
        *,
        resolved: dict[HookEventName, list[ResolvedHookMatcherGroup]],
        sources: list[HookSourceInfo],
    ) -> None:
        if self._get_role_registry is None:
            return
        role_registry = cast(_HookRoleRegistry, self._get_role_registry())
        for role in role_registry.list_roles():
            role_id = str(role.role_id or "").strip()
            if not role.hooks.hooks or role.source_path is None or not role_id:
                continue
            source = HookSourceInfo(scope=HookSourceScope.ROLE, path=role.source_path)
            sources.append(source)
            for event_name, groups in role.hooks.hooks.items():
                bucket = resolved.setdefault(event_name, [])
                for group in groups:
                    if not group.hooks:
                        continue
                    bucket.append(
                        ResolvedHookMatcherGroup(
                            source=source,
                            event_name=event_name,
                            group=_merge_role_ids(group=group, role_ids=(role_id,)),
                        )
                    )

    def _append_skill_hooks(
        self,
        *,
        resolved: dict[HookEventName, list[ResolvedHookMatcherGroup]],
        sources: list[HookSourceInfo],
    ) -> None:
        if self._get_skill_registry is None:
            return
        skill_registry = cast(_HookSkillRegistry, self._get_skill_registry())
        skill_role_ids = self._build_skill_role_ids()
        for skill in skill_registry.list_skill_definitions():
            if not skill.metadata.hooks.hooks:
                continue
            role_ids = skill_role_ids.get(str(skill.ref or "").strip(), ())
            if not role_ids:
                continue
            source = HookSourceInfo(
                scope=HookSourceScope.SKILL,
                path=Path(skill.directory) / "SKILL.md",
            )
            sources.append(source)
            for event_name, groups in skill.metadata.hooks.hooks.items():
                bucket = resolved.setdefault(event_name, [])
                for group in groups:
                    if not group.hooks:
                        continue
                    bucket.append(
                        ResolvedHookMatcherGroup(
                            source=source,
                            event_name=event_name,
                            group=_merge_role_ids(group=group, role_ids=role_ids),
                        )
                    )

    def _build_skill_role_ids(self) -> dict[str, tuple[str, ...]]:
        if self._get_role_registry is None:
            return {}
        role_registry = cast(_HookRoleRegistry, self._get_role_registry())
        wildcard_skill_refs = self._list_wildcard_skill_refs()
        skill_role_ids: dict[str, list[str]] = {}
        for role in role_registry.list_roles():
            role_id = str(role.role_id or "").strip()
            if not role_id:
                continue
            for skill_ref in role.skills:
                normalized_ref = str(skill_ref or "").strip()
                if not normalized_ref:
                    continue
                if normalized_ref == _CAPABILITY_WILDCARD:
                    for wildcard_skill_ref in wildcard_skill_refs:
                        _append_skill_role_id(
                            skill_role_ids,
                            skill_ref=wildcard_skill_ref,
                            role_id=role_id,
                        )
                    continue
                _append_skill_role_id(
                    skill_role_ids,
                    skill_ref=normalized_ref,
                    role_id=role_id,
                )
        return {
            skill_ref: tuple(role_ids) for skill_ref, role_ids in skill_role_ids.items()
        }

    def _list_wildcard_skill_refs(self) -> tuple[str, ...]:
        if self._get_skill_registry is None:
            return ()
        skill_registry = cast(_HookSkillRegistry, self._get_skill_registry())
        skill_refs: list[str] = []
        for skill in skill_registry.list_skill_definitions():
            skill_ref = str(skill.ref or "").strip()
            if skill_ref:
                skill_refs.append(skill_ref)
        return tuple(skill_refs)


def _append_skill_role_id(
    skill_role_ids: dict[str, list[str]],
    *,
    skill_ref: str,
    role_id: str,
) -> None:
    role_ids = skill_role_ids.setdefault(skill_ref, [])
    if role_id not in role_ids:
        role_ids.append(role_id)


def _append_hook_groups(
    *,
    destination: dict[HookEventName, list[HookMatcherGroup]],
    config: HooksConfig,
) -> None:
    for event_name, groups in config.hooks.items():
        bucket = destination.setdefault(event_name, [])
        bucket.extend(groups)


def _append_merged_hook_groups(
    *,
    destination: dict[HookEventName, list[HookMatcherGroup]],
    config: HooksConfig,
) -> None:
    for event_name, groups in config.hooks.items():
        bucket = destination.setdefault(event_name, [])
        for group in groups:
            for index, existing_group in enumerate(bucket):
                if (
                    existing_group.matcher == group.matcher
                    and existing_group.role_ids == group.role_ids
                    and existing_group.session_modes == group.session_modes
                    and existing_group.run_kinds == group.run_kinds
                ):
                    bucket[index] = existing_group.model_copy(
                        update={"hooks": (*existing_group.hooks, *group.hooks)}
                    )
                    break
            else:
                bucket.append(group)


def _merge_role_ids(
    *,
    group: HookMatcherGroup,
    role_ids: tuple[str, ...],
) -> HookMatcherGroup:
    merged_role_ids = tuple(dict.fromkeys((*group.role_ids, *role_ids)))
    return group.model_copy(update={"role_ids": merged_role_ids})


def _load_json_object(file_path: Path) -> dict[str, JsonValue]:
    raw = cast(object, loads(file_path.read_text(encoding="utf-8")))
    if isinstance(raw, dict):
        return cast(dict[str, JsonValue], raw)
    return {}


def normalize_hooks_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    next_payload = dict(payload)
    raw_hooks = payload.get("hooks")
    if not isinstance(raw_hooks, dict):
        return next_payload
    normalized_hooks: dict[object, object] = {}
    for event_name, raw_groups in raw_hooks.items():
        if not isinstance(event_name, str) or not isinstance(raw_groups, list):
            normalized_hooks[event_name] = raw_groups
            continue
        normalized_groups: list[object] = []
        for raw_group in raw_groups:
            if not isinstance(raw_group, dict):
                normalized_groups.append(raw_group)
                continue
            normalized_groups.extend(_normalize_hook_group(raw_group))
        normalized_hooks[event_name] = normalized_groups
    next_payload["hooks"] = normalized_hooks
    return next_payload


def parse_tolerant_hooks_payload(payload: object) -> HooksConfig:
    normalized_payload = normalize_hooks_payload(payload)
    if not isinstance(normalized_payload, dict):
        return HooksConfig()
    raw_hooks = normalized_payload.get("hooks")
    if not isinstance(raw_hooks, dict):
        return HooksConfig()
    next_hooks: dict[HookEventName, tuple[HookMatcherGroup, ...]] = {}
    for raw_event_name, raw_groups in raw_hooks.items():
        if not isinstance(raw_event_name, str) or not isinstance(raw_groups, list):
            continue
        for raw_group in raw_groups:
            try:
                config = HooksConfig.model_validate(
                    {"hooks": {raw_event_name: [raw_group]}}
                )
                validate_hook_event_capabilities(config=config)
            except ValueError as exc:
                if str(exc) != _EMPTY_GROUP_ERROR:
                    continue
                config = HooksConfig.model_validate(
                    {"hooks": {raw_event_name: [raw_group]}}
                )
            except Exception:
                continue
            for event_name, groups in config.hooks.items():
                existing_groups = next_hooks.get(event_name, ())
                next_hooks[event_name] = (*existing_groups, *groups)
    return HooksConfig(hooks=next_hooks)


def _normalize_hook_group(raw_group: dict[str, object]) -> list[object]:
    group = dict(raw_group)
    raw_handlers = group.get("hooks")
    handlers = raw_handlers
    if isinstance(raw_handlers, list):
        next_handlers: list[object] = []
        for raw_handler in raw_handlers:
            if isinstance(raw_handler, dict):
                next_handlers.append(dict(raw_handler))
            else:
                next_handlers.append(raw_handler)
        handlers = next_handlers
    legacy_if = str(group.get("if_condition") or "").strip()
    if (
        legacy_if
        and isinstance(handlers, list)
        and handlers
        and all(
            isinstance(handler, dict)
            and "if" not in handler
            and "if_rule" not in handler
            for handler in handlers
        )
    ):
        for handler in handlers:
            if isinstance(handler, dict):
                handler["if"] = legacy_if
        group.pop("if_condition", None)
    elif not legacy_if:
        group.pop("if_condition", None)
    group["hooks"] = handlers
    raw_tool_names = group.get("tool_names", ())
    matcher = str(group.get("matcher") or "").strip()
    tool_name_values = (
        raw_tool_names if isinstance(raw_tool_names, (list, tuple)) else ()
    )
    tool_names = tuple(
        dict.fromkeys(
            str(value).strip() for value in tool_name_values if str(value).strip()
        )
    )
    if not tool_names:
        group.pop("tool_names", None)
        return [group]
    if matcher and matcher != "*":
        matching_tool_names = [
            tool_name
            for tool_name in tool_names
            if fnmatch.fnmatchcase(tool_name, matcher)
        ]
        if not matching_tool_names:
            return [group]
        group.pop("tool_names", None)
        return [group | {"matcher": tool_name} for tool_name in matching_tool_names]
    group.pop("tool_names", None)
    return [group | {"matcher": tool_name} for tool_name in tool_names]


def _validate_handler_event_compatibility(
    *,
    event_name: HookEventName,
    handler: HookHandlerConfig,
) -> None:
    if handler.if_rule and event_name not in TOOL_EVENTS:
        raise ValueError(
            f"Hook handler 'if' is only supported for tool events, not {event_name.value}"
        )
    if event_name in COMMAND_ONLY_EVENTS and handler.type != HookHandlerType.COMMAND:
        raise ValueError(f"{event_name.value} only supports command hook handlers")
    if event_name in COMMAND_HTTP_ONLY_EVENTS and handler.type not in {
        HookHandlerType.COMMAND,
        HookHandlerType.HTTP,
    }:
        raise ValueError(
            f"{event_name.value} only supports command and http hook handlers"
        )


def validate_hook_event_capabilities(*, config: HooksConfig) -> None:
    for event_name, groups in config.hooks.items():
        for group in groups:
            if not group.hooks:
                raise ValueError("hook matcher group must contain at least one handler")
            matcher = group.matcher.strip() or "*"
            if event_name in MATCHER_UNSUPPORTED_EVENTS and matcher != "*":
                raise ValueError(
                    f"Matcher is not supported for {event_name.value} hooks"
                )
            for handler in group.hooks:
                _validate_handler_event_compatibility(
                    event_name=event_name,
                    handler=handler,
                )


def filter_tolerant_hook_groups(*, config: HooksConfig) -> HooksConfig:
    next_hooks: dict[HookEventName, tuple[HookMatcherGroup, ...]] = {}
    for event_name, groups in config.hooks.items():
        next_groups: list[HookMatcherGroup] = []
        for group in groups:
            try:
                validate_hook_event_capabilities(
                    config=HooksConfig(hooks={event_name: (group,)})
                )
            except ValueError:
                continue
            next_groups.append(group)
        if next_groups:
            next_hooks[event_name] = tuple(next_groups)
    return HooksConfig(hooks=next_hooks)


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(
            str(part).strip() for part in error.get("loc", ()) if str(part).strip()
        )
        message = str(error.get("msg", "")).strip()
        if location and message:
            parts.append(f"{location}: {message}")
        elif message:
            parts.append(message)
    return "; ".join(parts) or str(exc)
