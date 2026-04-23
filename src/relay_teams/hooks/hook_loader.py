from __future__ import annotations

from json import dumps, loads
from pathlib import Path
from collections.abc import Callable
from typing import Protocol, cast

from pydantic import JsonValue

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
        config = HooksConfig.model_validate(payload)
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
            config = HooksConfig.model_validate(payload)
            return self._validate_handler_references(config=config, tolerant=tolerant)
        except Exception:
            if not tolerant:
                raise
            LOGGER.warning("Ignoring invalid hook config", extra={"path": str(path)})
            return HooksConfig()

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
        skill_role_ids: dict[str, list[str]] = {}
        for role in role_registry.list_roles():
            role_id = str(role.role_id or "").strip()
            if not role_id:
                continue
            for skill_ref in role.skills:
                normalized_ref = str(skill_ref or "").strip()
                if not normalized_ref:
                    continue
                skill_role_ids.setdefault(normalized_ref, []).append(role_id)
        return {
            skill_ref: tuple(role_ids) for skill_ref, role_ids in skill_role_ids.items()
        }


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
