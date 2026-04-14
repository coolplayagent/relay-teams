from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from relay_teams.hooks.hook_models import HookEventName, HookMatcherGroup, HooksConfig


class HookConfigLoader:
    def __init__(self, *, config_dir: Path) -> None:
        self._config_path = config_dir / "hooks.json"

    @property
    def config_path(self) -> Path:
        return self._config_path

    def load(self) -> HooksConfig:
        if not self._config_path.exists():
            return HooksConfig()
        raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("hooks.json must contain a top-level object")
        raw_hooks = cast(dict[object, object], raw).get("hooks", {})
        if not isinstance(raw_hooks, dict):
            raise ValueError("hooks.json field 'hooks' must be an object")
        parsed: dict[HookEventName, tuple[HookMatcherGroup, ...]] = {}
        for raw_name, raw_groups in cast(dict[object, object], raw_hooks).items():
            event_name = HookEventName(str(raw_name))
            if not isinstance(raw_groups, list):
                raise ValueError(f"hooks for {event_name.value} must be a list")
            groups = tuple(HookMatcherGroup.model_validate(item) for item in raw_groups)
            parsed[event_name] = groups
        return HooksConfig(hooks=parsed)
