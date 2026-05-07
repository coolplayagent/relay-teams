from __future__ import annotations

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
from relay_teams.hooks.hook_normalization import (
    _normalize_hook_group as _normalize_hook_group_impl,
    _validate_handler_event_compatibility,
    filter_tolerant_hook_groups as _filter_tolerant_hook_groups,
    normalize_hooks_payload,
    parse_tolerant_hooks_payload as _parse_tolerant_hooks_payload,
    validate_hook_event_capabilities,
)
from relay_teams.logger import get_logger
from relay_teams.paths import get_project_root_or_none
from relay_teams.plugins.path_resolution import namespace_plugin_ref
from relay_teams.plugins.plugin_models import PluginComponentSource
from relay_teams.plugins.substitution import substitute_plugin_vars

LOGGER = get_logger(__name__)
_CAPABILITY_WILDCARD = "*"


def parse_tolerant_hooks_payload(payload: object) -> HooksConfig:
    return _parse_tolerant_hooks_payload(payload)


def filter_tolerant_hook_groups(*, config: HooksConfig) -> HooksConfig:
    return _filter_tolerant_hook_groups(config=config)


def _normalize_hook_group(
    raw_group: dict[str, object],
    *,
    raw_event_name: str = "",
) -> list[object]:
    return _normalize_hook_group_impl(raw_group, raw_event_name=raw_event_name)


class _HookRoleEntry(Protocol):
    role_id: str
    skills: tuple[str, ...]
    hooks: HooksConfig
    source_path: Path | None


class _HookRoleRegistry(Protocol):
    @staticmethod
    def get(role_id: str) -> object:
        raise NotImplementedError

    @staticmethod
    def list_roles() -> tuple[_HookRoleEntry, ...]:
        raise NotImplementedError

    @staticmethod
    def list_subagent_roles() -> tuple[_HookRoleEntry, ...]:
        raise NotImplementedError


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
        plugin_hook_sources: tuple[PluginComponentSource, ...] = (),
    ) -> None:
        self._app_config_dir = app_config_dir
        self._project_root = project_root or get_project_root_or_none(Path.cwd())
        self._get_role_registry = get_role_registry
        self._get_skill_registry = get_skill_registry
        self._plugin_hook_sources = plugin_hook_sources

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
            dumps(
                config.model_dump(
                    mode="json",
                    exclude_defaults=True,
                    exclude_none=True,
                ),
                indent=2,
            ),
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
        self._append_plugin_hooks(resolved=resolved, sources=sources)
        self._append_role_hooks(resolved=resolved, sources=sources)
        self._append_skill_hooks(resolved=resolved, sources=sources)
        return HookRuntimeSnapshot(
            sources=tuple(sources),
            hooks={key: tuple(value) for key, value in resolved.items()},
        )

    def validate_plugin_hook_sources(self) -> None:
        for plugin_source in self._plugin_hook_sources:
            _ = self._load_single_file(
                plugin_source.path,
                tolerant=False,
                plugin_source=plugin_source,
            )

    def _load_single_file(
        self,
        path: Path,
        *,
        tolerant: bool,
        plugin_source: PluginComponentSource | None = None,
    ) -> HooksConfig:
        if plugin_source is None and not path.exists():
            return HooksConfig()
        try:
            payload = (
                plugin_source.inline_config
                if plugin_source is not None and plugin_source.inline_config is not None
                else _load_json_object(path)
            )
            plugin_name = "" if plugin_source is None else plugin_source.plugin_name
            if plugin_source is not None:
                payload = substitute_plugin_vars(
                    value=payload,
                    plugin_root=plugin_source.root_dir,
                    plugin_data=plugin_source.data_dir,
                    user_config=plugin_source.user_config,
                    allow_env=True,
                )
            if tolerant:
                normalized_payload = normalize_hooks_payload(payload, tolerant=True)
                if plugin_name:
                    normalized_payload = _namespace_plugin_hooks_payload(
                        normalized_payload,
                        plugin_name=plugin_name,
                    )
                return self._load_tolerant_payload(
                    path=path,
                    payload=normalized_payload,
                )
            normalized_payload = normalize_hooks_payload(payload)
            if plugin_name:
                normalized_payload = _namespace_plugin_hooks_payload(
                    normalized_payload,
                    plugin_name=plugin_name,
                )
            config = HooksConfig.model_validate(normalized_payload)
            validate_hook_event_capabilities(config=config)
            return self._validate_handler_references(config=config, tolerant=False)
        except Exception:
            if not tolerant:
                raise
            LOGGER.warning("Ignoring invalid hook config", extra={"path": str(path)})
            return HooksConfig()

    def _append_plugin_hooks(
        self,
        *,
        resolved: dict[HookEventName, list[ResolvedHookMatcherGroup]],
        sources: list[HookSourceInfo],
    ) -> None:
        for plugin_source in self._plugin_hook_sources:
            path = plugin_source.path
            if plugin_source.inline_config is None and not path.exists():
                continue
            config = self._load_single_file(
                path,
                tolerant=True,
                plugin_source=plugin_source,
            )
            if not config.hooks:
                continue
            source = HookSourceInfo(
                scope=HookSourceScope.PLUGIN,
                path=path,
                plugin_name=plugin_source.plugin_name,
                plugin_root=plugin_source.root_dir,
                plugin_data=plugin_source.data_dir,
            )
            sources.append(source)
            for event_name, groups in config.hooks.items():
                bucket = resolved.setdefault(event_name, [])
                for group in groups:
                    if group.hooks:
                        bucket.append(
                            ResolvedHookMatcherGroup(
                                source=source,
                                event_name=event_name,
                                group=group,
                            )
                        )

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
                    {"hooks": {raw_event_name: [raw_group | {"hooks": [raw_handler]}]}}
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
        agent_hook_role_ids = self._known_agent_hook_role_ids()
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
                        agent_hook_role_ids=agent_hook_role_ids,
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
        agent_hook_role_ids: frozenset[str],
        tolerant: bool,
    ) -> bool:
        if handler.type != HookHandlerType.AGENT:
            return True
        role_id = str(handler.role_id or "").strip()
        if not role_id:
            if not tolerant:
                raise ValueError("Agent hook role_id is required.")
            LOGGER.warning("Ignoring agent hook handler with empty role_id")
            return False
        if role_id in agent_hook_role_ids:
            return True
        if role_id in known_role_ids:
            if not tolerant:
                raise ValueError(
                    f"Agent hook role_id must reference a subagent role: {role_id}"
                )
            LOGGER.warning(
                "Ignoring hook handler with non-subagent agent role",
                extra={"role_id": role_id},
            )
            return False
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

    def _known_agent_hook_role_ids(self) -> frozenset[str]:
        if self._get_role_registry is None:
            return frozenset()
        role_registry = cast(_HookRoleRegistry, self._get_role_registry())
        return frozenset(
            str(role.role_id or "").strip()
            for role in role_registry.list_subagent_roles()
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


def _namespace_plugin_hooks_payload(payload: object, *, plugin_name: str) -> object:
    if not isinstance(payload, dict):
        return payload
    raw_hooks = payload.get("hooks")
    if not isinstance(raw_hooks, dict):
        return payload
    next_hooks: dict[object, object] = {}
    for event_name, raw_groups in raw_hooks.items():
        if not isinstance(raw_groups, list):
            next_hooks[event_name] = raw_groups
            continue
        next_hooks[event_name] = [
            _namespace_plugin_hook_group(group, plugin_name=plugin_name)
            for group in raw_groups
        ]
    return payload | {"hooks": next_hooks}


def _namespace_plugin_hook_group(group: object, *, plugin_name: str) -> object:
    if not isinstance(group, dict):
        return group
    next_group = dict(group)
    raw_role_ids = next_group.get("role_ids")
    if isinstance(raw_role_ids, list):
        next_group["role_ids"] = [
            _namespace_plugin_role_ref(value, plugin_name=plugin_name)
            for value in raw_role_ids
        ]
    elif isinstance(raw_role_ids, tuple):
        next_group["role_ids"] = tuple(
            _namespace_plugin_role_ref(value, plugin_name=plugin_name)
            for value in raw_role_ids
        )
    raw_handlers = next_group.get("hooks")
    if isinstance(raw_handlers, list):
        next_group["hooks"] = [
            _namespace_plugin_hook_handler(handler, plugin_name=plugin_name)
            for handler in raw_handlers
        ]
    return next_group


def _namespace_plugin_hook_handler(handler: object, *, plugin_name: str) -> object:
    if not isinstance(handler, dict):
        return handler
    next_handler = dict(handler)
    if str(next_handler.get("type") or "").strip() != HookHandlerType.AGENT.value:
        return next_handler
    role_id = next_handler.get("role_id")
    if isinstance(role_id, str):
        next_handler["role_id"] = _namespace_plugin_role_ref(
            role_id,
            plugin_name=plugin_name,
        )
    return next_handler


def _namespace_plugin_role_ref(value: object, *, plugin_name: str) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if not normalized or normalized == _CAPABILITY_WILDCARD or ":" in normalized:
        return value
    return namespace_plugin_ref(plugin_name=plugin_name, local_name=normalized)


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
