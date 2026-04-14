from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.hooks.config_loader import HookConfigLoader
from relay_teams.hooks.hook_models import HooksConfig


class HookConfigSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_count: int = 0
    matcher_group_count: int = 0
    handler_count: int = 0


class HookConfigView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_path: str
    exists: bool
    config: dict[str, JsonValue] = Field(default_factory=dict)
    summary: HookConfigSummary


class HookConfigValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    config_path: str
    exists: bool
    summary: HookConfigSummary | None = None
    error: str | None = None


class HookConfigService:
    def __init__(self, *, loader: HookConfigLoader) -> None:
        self._loader = loader

    def get_hook_config(self) -> HookConfigView:
        config = self._loader.load()
        return HookConfigView(
            config_path=str(self._loader.config_path),
            exists=self._loader.config_path.exists(),
            config=config.model_dump(mode="json"),
            summary=_summarize_config(config),
        )

    def validate_hook_config(self) -> HookConfigValidationResult:
        try:
            config = self._loader.load()
        except Exception as exc:
            return HookConfigValidationResult(
                valid=False,
                config_path=str(self._loader.config_path),
                exists=self._loader.config_path.exists(),
                error=str(exc),
            )
        return HookConfigValidationResult(
            valid=True,
            config_path=str(self._loader.config_path),
            exists=self._loader.config_path.exists(),
            summary=_summarize_config(config),
        )


def _summarize_config(config: HooksConfig) -> HookConfigSummary:
    matcher_group_count = 0
    handler_count = 0
    for groups in config.hooks.values():
        matcher_group_count += len(groups)
        for group in groups:
            handler_count += len(group.hooks)
    return HookConfigSummary(
        event_count=len(config.hooks),
        matcher_group_count=matcher_group_count,
        handler_count=handler_count,
    )
