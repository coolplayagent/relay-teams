from __future__ import annotations

from collections.abc import Callable
from json import dumps, loads
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from relay_teams.hooks.hook_models import (
    event_allows_handler_type,
    HookEventName,
    HookHandlerType,
    HookMatcherGroup,
    HookRuntimeSnapshot,
    HookSourceInfo,
    HookSourceScope,
    HooksConfig,
    ResolvedHookMatcherGroup,
)
from relay_teams.logger import get_logger
from relay_teams.paths import get_project_root_or_none

if TYPE_CHECKING:
    from relay_teams.roles.role_registry import RoleRegistry
    from relay_teams.skills.skill_registry import SkillRegistry

LOGGER = get_logger(__name__)


class HookLoader:
    def __init__(
        self,
        *,
        app_config_dir: Path,
        project_root: Path | None = None,
        get_role_registry: Callable[[], RoleRegistry] | None = None,
        get_skill_registry: Callable[[], SkillRegistry] | None = None,
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
        config = HooksConfig.model_validate(payload)
        self._validate_supported_handlers(config)
        self._validate_supported_runtime_options(config)
        self._validate_known_agent_roles(config)
        return config

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
            source = HookSourceInfo(scope=scope, path=path)
            self._append_config(
                resolved=resolved,
                sources=sources,
                source=source,
                config=config,
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
            config = HooksConfig.model_validate(payload)
            return self._sanitize_runtime_config(config, path=path)
        except Exception:
            if not tolerant:
                raise
            LOGGER.warning("Ignoring invalid hook config", extra={"path": str(path)})
            return HooksConfig()

    def _append_config(
        self,
        *,
        resolved: dict[HookEventName, list[ResolvedHookMatcherGroup]],
        sources: list[HookSourceInfo],
        source: HookSourceInfo,
        config: HooksConfig,
    ) -> None:
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

    def _append_role_hooks(
        self,
        *,
        resolved: dict[HookEventName, list[ResolvedHookMatcherGroup]],
        sources: list[HookSourceInfo],
    ) -> None:
        role_registry = self._get_role_registry() if self._get_role_registry else None
        if role_registry is None:
            return
        for role in role_registry.list_roles():
            raw_hooks = getattr(role, "hooks", {})
            if not raw_hooks:
                continue
            source_path = getattr(role, "source_path", None)
            source = HookSourceInfo(
                scope=HookSourceScope.ROLE,
                path=source_path or Path(f"role_{role.role_id}.md"),
            )
            config = self._validate_embedded_hooks(raw_hooks, path=source.path)
            if config is None:
                continue
            sources.append(source)
            for event_name, groups in config.hooks.items():
                bucket = resolved.setdefault(event_name, [])
                for group in groups:
                    augmented_group = self._augment_group_role_ids(
                        group,
                        extra_role_ids=(role.role_id,),
                    )
                    bucket.append(
                        ResolvedHookMatcherGroup(
                            source=source,
                            event_name=event_name,
                            group=augmented_group,
                        )
                    )

    def _append_skill_hooks(
        self,
        *,
        resolved: dict[HookEventName, list[ResolvedHookMatcherGroup]],
        sources: list[HookSourceInfo],
    ) -> None:
        skill_registry = (
            self._get_skill_registry() if self._get_skill_registry else None
        )
        role_registry = self._get_role_registry() if self._get_role_registry else None
        if skill_registry is None:
            return
        skill_role_map: dict[str, tuple[str, ...]] = {}
        if role_registry is not None:
            for role in role_registry.list_roles():
                for skill_ref in skill_registry.resolve_known(
                    role.skills,
                    strict=False,
                    consumer="hooks.loader.skill_role_map",
                ):
                    existing = set(skill_role_map.get(skill_ref, ()))
                    existing.add(role.role_id)
                    skill_role_map[skill_ref] = tuple(sorted(existing))
        for skill in skill_registry.list_skill_definitions():
            raw_hooks = getattr(skill.metadata, "hooks", {})
            if not raw_hooks:
                continue
            attached_role_ids = skill_role_map.get(skill.ref, ())
            if not attached_role_ids:
                continue
            source = HookSourceInfo(
                scope=HookSourceScope.SKILL,
                path=(skill.directory / "SKILL.md"),
            )
            config = self._validate_embedded_hooks(raw_hooks, path=source.path)
            if config is None:
                continue
            sources.append(source)
            for event_name, groups in config.hooks.items():
                bucket = resolved.setdefault(event_name, [])
                for group in groups:
                    constrained_group = self._constrain_skill_group_role_ids(
                        group,
                        attached_role_ids=attached_role_ids,
                    )
                    if constrained_group is None:
                        continue
                    bucket.append(
                        ResolvedHookMatcherGroup(
                            source=source,
                            event_name=event_name,
                            group=constrained_group,
                        )
                    )

    def _augment_group_role_ids(
        self,
        group: HookMatcherGroup,
        *,
        extra_role_ids: tuple[str, ...],
    ) -> HookMatcherGroup:
        if not extra_role_ids:
            return group
        merged_role_ids = tuple(
            dict.fromkeys([*group.role_ids, *extra_role_ids]).keys()
        )
        return group.model_copy(update={"role_ids": merged_role_ids})

    def _constrain_skill_group_role_ids(
        self,
        group: HookMatcherGroup,
        *,
        attached_role_ids: tuple[str, ...],
    ) -> HookMatcherGroup | None:
        if not attached_role_ids:
            return None
        if not group.role_ids:
            return group.model_copy(update={"role_ids": attached_role_ids})
        attached_role_id_set = set(attached_role_ids)
        constrained_role_ids = tuple(
            role_id for role_id in group.role_ids if role_id in attached_role_id_set
        )
        if not constrained_role_ids:
            return None
        return group.model_copy(update={"role_ids": constrained_role_ids})

    def _validate_embedded_hooks(
        self, raw_hooks: object, *, path: Path
    ) -> HooksConfig | None:
        try:
            config = HooksConfig.model_validate({"hooks": raw_hooks})
        except Exception:
            LOGGER.warning(
                "Ignoring invalid embedded hook config",
                extra={"path": str(path)},
            )
            return None
        return self._sanitize_runtime_config(config, path=path)

    def _sanitize_runtime_config(
        self, config: HooksConfig, *, path: Path
    ) -> HooksConfig:
        role_registry = self._get_role_registry() if self._get_role_registry else None
        sanitized_hooks: dict[HookEventName, tuple[HookMatcherGroup, ...]] = {}
        for event_name, groups in config.hooks.items():
            next_groups: list[HookMatcherGroup] = []
            for group in groups:
                next_handlers = []
                for handler in group.hooks:
                    if not self._runtime_handler_is_supported(
                        event_name=event_name,
                        handler=handler,
                        path=path,
                    ):
                        continue
                    if role_registry is not None and (
                        handler.type == HookHandlerType.AGENT
                        and str(handler.role_id or "").strip()
                    ):
                        try:
                            role_registry.get(str(handler.role_id).strip())
                        except KeyError:
                            LOGGER.warning(
                                "Ignoring hook handler with unknown role reference",
                                extra={
                                    "path": str(path),
                                    "event_name": event_name.value,
                                    "role_id": str(handler.role_id),
                                },
                            )
                            continue
                    next_handlers.append(handler)
                if next_handlers:
                    next_groups.append(
                        group.model_copy(update={"hooks": tuple(next_handlers)})
                    )
            if next_groups:
                sanitized_hooks[event_name] = tuple(next_groups)
        return HooksConfig(hooks=sanitized_hooks)

    def _validate_known_agent_roles(self, config: HooksConfig) -> None:
        role_registry = self._get_role_registry() if self._get_role_registry else None
        if role_registry is None:
            return
        for event_name, groups in config.hooks.items():
            for group in groups:
                for handler in group.hooks:
                    if handler.type != HookHandlerType.AGENT:
                        continue
                    role_id = str(handler.role_id or "").strip()
                    if not role_id:
                        continue
                    try:
                        role_registry.get(role_id)
                    except KeyError as exc:
                        raise ValueError(
                            f"Unknown role reference for {event_name.value} agent hook: {role_id}"
                        ) from exc

    def _validate_supported_handlers(self, config: HooksConfig) -> None:
        for event_name, groups in config.hooks.items():
            for group in groups:
                for handler in group.hooks:
                    if not event_allows_handler_type(event_name, handler.type):
                        raise ValueError(
                            f"{event_name.value} does not support {handler.type.value} hooks"
                        )

    def _validate_supported_runtime_options(self, config: HooksConfig) -> None:
        for event_name, groups in config.hooks.items():
            for group in groups:
                for handler in group.hooks:
                    if handler.run_async:
                        raise ValueError(
                            f"{event_name.value} hook {handler.type.value} does not support async execution yet"
                        )
                    if handler.on_error != "ignore":
                        raise ValueError(
                            f"{event_name.value} hook {handler.type.value} does not support on_error={handler.on_error!r} yet"
                        )

    def _runtime_handler_is_supported(
        self,
        *,
        event_name: HookEventName,
        handler: object,
        path: Path,
    ) -> bool:
        typed_handler = cast(HookHandlerType, getattr(handler, "type"))
        if not event_allows_handler_type(event_name, typed_handler):
            LOGGER.warning(
                "Ignoring unsupported hook handler for event",
                extra={
                    "path": str(path),
                    "event_name": event_name.value,
                    "handler_type": typed_handler.value,
                },
            )
            return False
        run_async = bool(getattr(handler, "run_async"))
        if run_async:
            LOGGER.warning(
                "Ignoring unsupported async hook handler",
                extra={
                    "path": str(path),
                    "event_name": event_name.value,
                    "handler_type": typed_handler.value,
                },
            )
            return False
        on_error = str(getattr(handler, "on_error"))
        if on_error != "ignore":
            LOGGER.warning(
                "Ignoring unsupported hook on_error policy",
                extra={
                    "path": str(path),
                    "event_name": event_name.value,
                    "handler_type": typed_handler.value,
                    "on_error": on_error,
                },
            )
            return False
        return True


def _load_json_object(file_path: Path) -> dict[str, JsonValue]:
    raw = cast(object, loads(file_path.read_text(encoding="utf-8-sig")))
    if isinstance(raw, dict):
        return cast(dict[str, JsonValue], raw)
    return {}
