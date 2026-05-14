# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict, ValidationError

from relay_teams.plugins.marketplace_models import (
    PluginMarketplaceEntry,
    PluginMarketplaceIndex,
    PluginMarketplaceProviderKind,
    PluginMarketplaceVersion,
)

_POLICY_FILE_NAME = "marketplace-policy.json"


class PluginMarketplaceInstallPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_community_plugins: bool = False
    allow_executes_code: bool = False
    require_digest: bool = True
    allow_unclean_scan: bool = False

    def with_overrides(
        self,
        *,
        allow_community_plugins: bool = False,
        allow_executes_code: bool = False,
        allow_missing_digest: bool = False,
        allow_unclean_scan: bool = False,
    ) -> PluginMarketplaceInstallPolicy:
        return self.model_copy(
            update={
                "allow_community_plugins": (
                    self.allow_community_plugins or allow_community_plugins
                ),
                "allow_executes_code": (
                    self.allow_executes_code or allow_executes_code
                ),
                "require_digest": self.require_digest and not allow_missing_digest,
                "allow_unclean_scan": self.allow_unclean_scan or allow_unclean_scan,
            }
        )

    @staticmethod
    def blocked_reasons(
        *,
        provider: PluginMarketplaceProviderKind,
        version: PluginMarketplaceVersion,
    ) -> tuple[str, ...]:
        _ = (provider, version)
        return ()

    @staticmethod
    def blocked_entry_reasons(
        *,
        provider: PluginMarketplaceProviderKind,
        entry: PluginMarketplaceEntry,
    ) -> tuple[str, ...]:
        if provider != PluginMarketplaceProviderKind.CLAWHUB:
            return ()
        if entry.compatibility.value == "direct":
            return ()
        reason = (
            "ClawHub plugin is not directly compatible with Relay Teams"
            f" (compatibility={entry.compatibility.value})"
        )
        if entry.compatibility_reason:
            reason = f"{reason}: {entry.compatibility_reason}"
        return (reason,)

    def require_entry_allowed(
        self,
        *,
        provider: PluginMarketplaceProviderKind,
        entry: PluginMarketplaceEntry,
    ) -> None:
        reasons = self.blocked_entry_reasons(provider=provider, entry=entry)
        if reasons:
            raise ValueError("; ".join(reasons))

    def require_allowed(
        self,
        *,
        provider: PluginMarketplaceProviderKind,
        version: PluginMarketplaceVersion,
    ) -> None:
        reasons = self.blocked_reasons(provider=provider, version=version)
        if reasons:
            raise ValueError("; ".join(reasons))


def load_plugin_marketplace_install_policy(
    app_config_dir: Path,
) -> PluginMarketplaceInstallPolicy:
    policy_file = _policy_file_path(app_config_dir)
    if not policy_file.exists():
        return PluginMarketplaceInstallPolicy()
    try:
        raw = json.loads(policy_file.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid plugin marketplace policy JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("Plugin marketplace policy JSON must be an object")
    try:
        return PluginMarketplaceInstallPolicy.model_validate(
            {str(key): value for key, value in raw.items()}
        )
    except ValidationError as exc:
        raise ValueError(f"Invalid plugin marketplace policy: {exc}") from exc


def save_plugin_marketplace_install_policy(
    *,
    app_config_dir: Path,
    policy: PluginMarketplaceInstallPolicy,
) -> None:
    policy_file = _policy_file_path(app_config_dir)
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text(
        policy.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def apply_install_policy_to_index(
    *,
    index: PluginMarketplaceIndex,
    provider: PluginMarketplaceProviderKind,
    policy: PluginMarketplaceInstallPolicy,
) -> PluginMarketplaceIndex:
    return PluginMarketplaceIndex(
        version=index.version,
        plugins=tuple(
            apply_install_policy_to_entry(
                entry=entry,
                provider=provider,
                policy=policy,
            )
            for entry in index.plugins
        ),
        next_cursor=index.next_cursor,
    )


def apply_install_policy_to_entry(
    *,
    entry: PluginMarketplaceEntry,
    provider: PluginMarketplaceProviderKind,
    policy: PluginMarketplaceInstallPolicy,
) -> PluginMarketplaceEntry:
    return entry.model_copy(
        update={
            "versions": tuple(
                _apply_install_policy_to_version(
                    version=version,
                    provider=provider,
                    policy=policy,
                )
                for version in entry.versions
            )
        }
    )


def _apply_install_policy_to_version(
    *,
    version: PluginMarketplaceVersion,
    provider: PluginMarketplaceProviderKind,
    policy: PluginMarketplaceInstallPolicy,
) -> PluginMarketplaceVersion:
    if version.unsupported_reason:
        return version
    reasons = policy.blocked_reasons(provider=provider, version=version)
    if not reasons:
        return version
    return version.model_copy(update={"unsupported_reason": "; ".join(reasons)})


def _policy_file_path(app_config_dir: Path) -> Path:
    return app_config_dir.expanduser().resolve() / "plugins" / _POLICY_FILE_NAME
