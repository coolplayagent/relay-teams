from __future__ import annotations

from json import dumps, loads
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from relay_teams.hooks.hook_models import (
    HookEventName,
    HooksConfig,
    HookRuntimeSnapshot,
    HookSourceInfo,
    HookSourceScope,
    ResolvedHookMatcherGroup,
)
from relay_teams.logger import get_logger
from relay_teams.paths import get_project_root_or_none

LOGGER = get_logger(__name__)


class HookLoader:
    def __init__(
        self, *, app_config_dir: Path, project_root: Path | None = None
    ) -> None:
        self._app_config_dir = app_config_dir
        self._project_root = project_root or get_project_root_or_none(Path.cwd())

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
        return HooksConfig.model_validate(payload)

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
        return HookRuntimeSnapshot(
            sources=tuple(sources),
            hooks={key: tuple(value) for key, value in resolved.items()},
        )

    def _load_single_file(self, path: Path, *, tolerant: bool) -> HooksConfig:
        if not path.exists():
            return HooksConfig()
        try:
            payload = _load_json_object(path)
            return HooksConfig.model_validate(payload)
        except Exception:
            if not tolerant:
                raise
            LOGGER.warning("Ignoring invalid hook config", extra={"path": str(path)})
            return HooksConfig()


def _load_json_object(file_path: Path) -> dict[str, JsonValue]:
    raw = cast(object, loads(file_path.read_text(encoding="utf-8")))
    if isinstance(raw, dict):
        return cast(dict[str, JsonValue], raw)
    return {}
