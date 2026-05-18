# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from urllib.parse import quote, urlencode
from urllib.request import OpenerDirector, ProxyHandler, Request, build_opener

from pydantic import JsonValue

from relay_teams.env.proxy_env import load_proxy_env_config
from relay_teams.logger import get_logger
from relay_teams.plugins.marketplace_models import (
    PluginMarketplaceCompatibility,
    PluginMarketplaceEntry,
    PluginMarketplaceIndex,
    PluginMarketplaceSource,
    PluginMarketplaceVersion,
)
from relay_teams.plugins.plugin_models import (
    PluginInstallSource,
    PluginInstallSourceKind,
)

_DEFAULT_CLAWHUB_BASE_URL = "https://clawhub.ai"
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 100
_CLAWHUB_FAMILIES = ("code-plugin", "bundle-plugin")
_HTTP_TIMEOUT_SECONDS = 30.0
_SRI_RE = re.compile(r"^(sha(?:256|384|512))-(.+)$")
_SUPPORTED_BUNDLE_FORMATS = frozenset({"claude", "generic"})
_KNOWN_UNMAPPABLE_PACKAGES = frozenset(
    {
        "claoow",
        "clawhub-github-publish-iylrms",
        "cn-creator-pack",
        "gog-extended",
        "kdp-author-engine-bundle",
        "me-skills",
        "topny-backlink-tool",
        "topny-clickable-plugin",
    }
)
LOGGER = get_logger(__name__)


class ClawHubMarketplaceProvider:
    def load_index(
        self,
        *,
        source: PluginMarketplaceSource,
        limit: int = _DEFAULT_LIMIT,
        cursor: str = "",
        fetch_all: bool = True,
        include_versions: bool = False,
    ) -> PluginMarketplaceIndex:
        base_url = _normalized_base_url(source.value)
        safe_limit = _safe_limit(limit)
        items: list[Mapping[str, object]] = []
        next_cursors: dict[str, str] = {}
        input_cursors = _decode_family_cursors(cursor)
        remaining = safe_limit
        families = _CLAWHUB_FAMILIES
        if cursor.strip() and input_cursors:
            pending_families = tuple(
                family
                for family in _CLAWHUB_FAMILIES
                if family in input_cursors and not input_cursors[family]
            )
            cursor_families = tuple(
                family
                for family in _CLAWHUB_FAMILIES
                if family in input_cursors and input_cursors[family]
            )
            families = pending_families + cursor_families
        for family in families:
            if not fetch_all and remaining <= 0:
                next_cursors[family] = input_cursors.get(family, "")
                continue
            family_cursor = input_cursors.get(family, "")
            while True:
                family_limit = safe_limit if fetch_all else remaining
                query = {
                    "family": family,
                    "limit": str(family_limit),
                }
                if family_cursor:
                    query["cursor"] = family_cursor
                raw = _get_json(f"{base_url}/api/v1/packages?{urlencode(query)}")
                raw_items = _object_list_field(raw, "items")
                if fetch_all:
                    items.extend(raw_items)
                else:
                    items.extend(raw_items[:remaining])
                    remaining -= min(remaining, len(raw_items))
                family_cursor = _optional_string(raw, "nextCursor")
                if not fetch_all or not family_cursor:
                    break
            if family_cursor:
                next_cursors[family] = family_cursor
        entries = self._entries_from_raw_packages(
            raw_packages=items,
            base_url=base_url,
            include_versions=include_versions,
        )
        return PluginMarketplaceIndex(
            version="1",
            plugins=entries,
            next_cursor="" if fetch_all else _encode_family_cursors(next_cursors),
        )

    def load_entry(
        self,
        *,
        source: PluginMarketplaceSource,
        name: str,
    ) -> PluginMarketplaceEntry:
        base_url = _normalized_base_url(source.value)
        raw_package = self._raw_package_for_name(base_url=base_url, name=name)
        return self._entry_from_raw_package(
            raw_package=raw_package,
            base_url=base_url,
            include_versions=True,
            include_version_history=True,
            fallback_name=name,
        )

    def load_entry_detail(
        self,
        *,
        source: PluginMarketplaceSource,
        name: str,
        fallback_entry: PluginMarketplaceEntry | None = None,
    ) -> PluginMarketplaceEntry:
        base_url = _normalized_base_url(source.value)
        raw_package = _get_json(f"{base_url}/api/v1/packages/{_quote_path(name)}")
        if fallback_entry is not None:
            raw_package = _merged_package_detail(
                _raw_package_from_entry(fallback_entry),
                raw_package,
            )
        return self._entry_from_raw_package(
            raw_package=raw_package,
            base_url=base_url,
            include_versions=True,
            include_version_history=True,
            fallback_name=name,
        )

    def search_index(
        self,
        *,
        source: PluginMarketplaceSource,
        query: str,
        include_versions: bool = False,
    ) -> PluginMarketplaceIndex:
        base_url = _normalized_base_url(source.value)
        normalized_query = query.strip()
        if not normalized_query:
            return self.load_index(
                source=source,
                fetch_all=True,
                include_versions=include_versions,
            )
        raw = _get_json(
            f"{base_url}/api/v1/packages/search?{urlencode({'q': normalized_query})}"
        )
        entries = self._entries_from_raw_packages(
            raw_packages=_object_list_field(raw, "items"),
            base_url=base_url,
            include_versions=include_versions,
        )
        return PluginMarketplaceIndex(version="1", plugins=entries)

    def _entries_from_raw_packages(
        self,
        *,
        raw_packages: tuple[Mapping[str, object], ...] | list[Mapping[str, object]],
        base_url: str,
        include_versions: bool = False,
    ) -> tuple[PluginMarketplaceEntry, ...]:
        entries: list[PluginMarketplaceEntry] = []
        for raw_package in raw_packages:
            if not _package_version_or_empty(raw_package):
                LOGGER.warning(
                    "Skipping ClawHub marketplace package without version: %s",
                    _package_log_name(raw_package),
                )
                continue
            entries.append(
                self._entry_from_raw_package(
                    raw_package=raw_package,
                    base_url=base_url,
                    include_versions=include_versions,
                )
            )
        return tuple(entries)

    def _entry_from_raw_package(
        self,
        *,
        raw_package: Mapping[str, object],
        base_url: str,
        include_versions: bool = False,
        include_version_history: bool = False,
        fallback_name: str = "",
    ) -> PluginMarketplaceEntry:
        name = _package_name_or_fallback(raw_package, fallback_name=fallback_name)
        version = _package_version(raw_package)
        entry_package = raw_package
        if include_versions and include_version_history:
            versions = self._versions_for_package(
                base_url=base_url,
                name=name,
                fallback_package=raw_package,
                fallback_version=version,
            )
        elif include_versions:
            latest_detail = self._version_detail_or_fallback(
                base_url=base_url,
                name=name,
                version=version,
                fallback=raw_package,
            )
            entry_package = _merged_package_detail(raw_package, latest_detail)
            versions = (
                self._version_from_raw_package(
                    base_url=base_url,
                    name=name,
                    version=version,
                    raw_version=entry_package,
                ),
            )
        else:
            versions = (
                self._version_from_raw_package(
                    base_url=base_url,
                    name=name,
                    version=version,
                    raw_version=raw_package,
                ),
            )
        family = _package_family(entry_package)
        compatibility, compatibility_reason = _compatibility_for_package(entry_package)
        return PluginMarketplaceEntry(
            name=_entry_name(name),
            description=_package_description(entry_package),
            latest=version,
            versions=versions,
            provider_family=family,
            compatibility=compatibility,
            compatibility_reason=compatibility_reason,
        )

    @staticmethod
    def _raw_package_for_name(
        *,
        base_url: str,
        name: str,
    ) -> Mapping[str, object]:
        detail_package: Mapping[str, object] | None = None
        try:
            detail_package = _get_json(
                f"{base_url}/api/v1/packages/{_quote_path(name)}"
            )
        except ValueError:
            # ClawHub can omit direct detail records; fall back to family listings.
            pass
        if detail_package is not None and _detail_package_has_complete_metadata(
            detail_package
        ):
            return detail_package
        for family in _CLAWHUB_FAMILIES:
            cursor = ""
            while True:
                query = {
                    "family": family,
                    "limit": str(_DEFAULT_LIMIT),
                }
                if cursor:
                    query["cursor"] = cursor
                try:
                    raw = _get_json(f"{base_url}/api/v1/packages?{urlencode(query)}")
                except ValueError:
                    if detail_package is not None and _package_version_or_empty(
                        detail_package
                    ):
                        return detail_package
                    raise
                for raw_package in _object_list_field(raw, "items"):
                    raw_name = _required_string(raw_package, "name")
                    if raw_name == name:
                        if detail_package is not None:
                            return _merged_package_detail(raw_package, detail_package)
                        return raw_package
                cursor = _optional_string(raw, "nextCursor")
                if not cursor:
                    break
        if detail_package is not None and _package_version_or_empty(detail_package):
            return detail_package
        raise ValueError(f"ClawHub marketplace plugin not found: {name}")

    def _versions_for_package(
        self,
        *,
        base_url: str,
        name: str,
        fallback_package: Mapping[str, object],
        fallback_version: str,
    ) -> tuple[PluginMarketplaceVersion, ...]:
        try:
            raw = _get_json(f"{base_url}/api/v1/packages/{_quote_path(name)}/versions")
            raw_versions = _object_list_field(raw, "items")
        except ValueError:
            raw_versions = ()
        if not raw_versions:
            return (
                self._version_from_raw_package(
                    base_url=base_url,
                    name=name,
                    version=fallback_version,
                    raw_version=fallback_package,
                ),
            )
        versions: list[PluginMarketplaceVersion] = []
        for raw_version in raw_versions:
            version = _package_version(raw_version)
            try:
                raw_detail = _get_json(
                    f"{base_url}/api/v1/packages/{_quote_path(name)}/versions/"
                    f"{_quote_path(version)}"
                )
                version_detail = _version_detail_with_fallback(
                    fallback_package=fallback_package,
                    raw_version=raw_version,
                    raw_detail=raw_detail,
                )
            except ValueError:
                version_detail = _version_detail_after_failed_lookup(
                    fallback_package=fallback_package,
                    raw_version=raw_version,
                )
            versions.append(
                self._version_from_raw_package(
                    base_url=base_url,
                    name=name,
                    version=version,
                    raw_version=version_detail,
                )
            )
        return tuple(versions)

    @staticmethod
    def _version_detail_or_fallback(
        *,
        base_url: str,
        name: str,
        version: str,
        fallback: Mapping[str, object],
    ) -> Mapping[str, object]:
        try:
            return _get_json(
                f"{base_url}/api/v1/packages/{_quote_path(name)}/versions/"
                f"{_quote_path(version)}"
            )
        except ValueError:
            return fallback

    @staticmethod
    def _version_from_raw_package(
        *,
        base_url: str,
        name: str,
        version: str,
        raw_version: Mapping[str, object],
    ) -> PluginMarketplaceVersion:
        version_context = _version_package_context(raw_version=raw_version, name=name)
        digest = _artifact_digest(version_context)
        warnings = _warnings_for_package(version_context, digest=digest)
        unsupported_reason = _unsupported_reason(version_context)
        if not unsupported_reason:
            unsupported_reason = _compatibility_unsupported_reason(version_context)
        source = PluginInstallSource(
            kind=PluginInstallSourceKind.HTTP_ARCHIVE,
            value=_artifact_download_url(base_url=base_url, name=name, version=version),
            adapter="openclaw",
            sha=digest,
        )
        return PluginMarketplaceVersion(
            version=version,
            source=source,
            warnings=warnings,
            unsupported_reason=unsupported_reason,
        )


def _get_json(url: str) -> Mapping[str, object]:
    request = Request(
        url,
        headers={"User-Agent": "relay-teams-clawhub-plugin-provider"},
    )
    try:
        with _url_opener().open(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise ValueError(f"Failed to load ClawHub marketplace: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid ClawHub marketplace JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("ClawHub marketplace response must be an object")
    return {str(key): value for key, value in raw.items()}


def _object_list_field(
    raw: Mapping[str, object],
    key: str,
) -> tuple[Mapping[str, object], ...]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ValueError(f"ClawHub marketplace field must be a list: {key}")
    items: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("ClawHub marketplace package entries must be objects")
        items.append({str(key): value for key, value in item.items()})
    return tuple(items)


def _merged_package_detail(
    package: Mapping[str, object],
    detail: Mapping[str, object],
) -> Mapping[str, object]:
    merged = {str(key): value for key, value in package.items()}
    merged.update({str(key): value for key, value in detail.items()})
    return merged


def _raw_package_from_entry(entry: PluginMarketplaceEntry) -> Mapping[str, object]:
    raw: dict[str, object] = {
        "name": entry.name,
        "description": entry.description,
        "family": entry.provider_family,
    }
    if entry.latest:
        raw["version"] = entry.latest
    return raw


def _version_detail_with_fallback(
    *,
    fallback_package: Mapping[str, object],
    raw_version: Mapping[str, object],
    raw_detail: Mapping[str, object],
) -> Mapping[str, object]:
    version_detail = _merged_package_detail(raw_version, raw_detail)
    identity_detail = _version_detail_with_package_identity(
        fallback_package=fallback_package,
        version_detail=version_detail,
    )
    if _has_compatibility_metadata(identity_detail):
        return identity_detail
    if (
        _compatibility_for_package(fallback_package)[0]
        == PluginMarketplaceCompatibility.DIRECT
    ):
        return _merged_package_detail(fallback_package, version_detail)
    return identity_detail


def _version_detail_after_failed_lookup(
    *,
    fallback_package: Mapping[str, object],
    raw_version: Mapping[str, object],
) -> Mapping[str, object]:
    if _has_compatibility_metadata(raw_version):
        return _version_detail_with_package_identity(
            fallback_package=fallback_package,
            version_detail=raw_version,
        )
    if (
        _compatibility_for_package(fallback_package)[0]
        == PluginMarketplaceCompatibility.DIRECT
    ):
        return _merged_package_detail(fallback_package, raw_version)
    return raw_version


def _version_detail_with_package_identity(
    *,
    fallback_package: Mapping[str, object],
    version_detail: Mapping[str, object],
) -> Mapping[str, object]:
    merged = {str(key): value for key, value in version_detail.items()}
    for key in ("name", "family", "type"):
        if _optional_string(merged, key):
            continue
        value = _optional_string(fallback_package, key)
        if value:
            merged[key] = value
    return merged


def _version_package_context(
    *, raw_version: Mapping[str, object], name: str
) -> Mapping[str, object]:
    if _optional_string(raw_version, "name"):
        return raw_version
    version_context = {str(key): value for key, value in raw_version.items()}
    version_context["name"] = name
    return version_context


def _detail_package_has_complete_metadata(raw: Mapping[str, object]) -> bool:
    return bool(_package_version_or_empty(raw)) and _has_compatibility_metadata(raw)


def _has_compatibility_metadata(raw: Mapping[str, object]) -> bool:
    return (
        _package_family(raw) == "bundle-plugin"
        or _has_mappable_component_metadata(raw)
        or _has_runtime_extensions(raw)
        or _bool_field(raw, "executesCode")
        or bool(_compatibility_value(raw))
    )


def _required_string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"ClawHub marketplace field is required: {key}")
    return value.strip()


def _package_name_or_fallback(
    raw: Mapping[str, object],
    *,
    fallback_name: str = "",
) -> str:
    value = raw.get("name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    fallback = fallback_name.strip()
    if fallback:
        return fallback
    raise ValueError("ClawHub marketplace field is required: name")


def _optional_string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


def _package_version(raw: Mapping[str, object]) -> str:
    version = _package_version_or_empty(raw)
    if version:
        return version
    raise ValueError("ClawHub package version is required")


def _package_version_or_empty(raw: Mapping[str, object]) -> str:
    version = _optional_string(raw, "version")
    if version:
        return version
    latest_version = raw.get("latestVersion")
    if isinstance(latest_version, str) and latest_version.strip():
        return latest_version.strip()
    if isinstance(latest_version, Mapping):
        latest = _optional_string(
            {str(key): value for key, value in latest_version.items()},
            "version",
        )
        if latest:
            return latest
    tags = raw.get("tags")
    if isinstance(tags, Mapping):
        latest_tag = tags.get("latest")
        if isinstance(latest_tag, str) and latest_tag.strip():
            return latest_tag.strip()
    return ""


def _package_description(raw: Mapping[str, object]) -> str:
    summary = _optional_string(raw, "summary")
    if summary:
        return summary
    description = _optional_string(raw, "description")
    if description:
        return description
    return _optional_string(raw, "displayName")


def _package_log_name(raw: Mapping[str, object]) -> str:
    for key in ("name", "displayName", "runtimeId"):
        value = _optional_string(raw, key)
        if value:
            return value
    return "<unknown>"


def _package_family(raw: Mapping[str, object]) -> str:
    family = _optional_string(raw, "family")
    if family:
        return family
    package_type = _optional_string(raw, "type")
    if package_type:
        return package_type
    return "code-plugin"


def _compatibility_for_package(
    raw: Mapping[str, object],
) -> tuple[PluginMarketplaceCompatibility, str]:
    family = _package_family(raw)
    if family == "bundle-plugin":
        return (
            PluginMarketplaceCompatibility.DIRECT,
            "Bundle plugin with static components that Relay Teams can map.",
        )
    if _has_mappable_component_metadata(raw):
        if _has_runtime_extensions(raw) or _bool_field(raw, "executesCode"):
            return (
                PluginMarketplaceCompatibility.PARTIAL,
                "Contains Relay Teams mappable components plus OpenClaw native runtime features.",
            )
        return (
            PluginMarketplaceCompatibility.DIRECT,
            "Contains Relay Teams mappable static plugin components.",
        )
    if _has_runtime_extensions(raw):
        return (
            PluginMarketplaceCompatibility.NATIVE_ONLY,
            "OpenClaw native runtime plugin; Relay Teams cannot execute native runtime extensions.",
        )
    return (
        PluginMarketplaceCompatibility.UNKNOWN,
        "Install-time inspection is required to confirm Relay Teams component mapping.",
    )


def _artifact_digest(raw: Mapping[str, object]) -> str:
    for key in (
        "clawPackDigest",
        "clawpackDigest",
        "digest",
        "sha256",
        "npmIntegrity",
        "integrity",
        "npmShasum",
    ):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    artifact = raw.get("artifact")
    if isinstance(artifact, Mapping):
        return _artifact_digest({str(key): value for key, value in artifact.items()})
    return ""


def _warnings_for_package(
    raw: Mapping[str, object],
    *,
    digest: str,
) -> tuple[str, ...]:
    warnings: list[str] = []
    channel = _optional_string(raw, "channel")
    if channel and channel != "official":
        warnings.append(f"ClawHub package channel is {channel}; review before install.")
    if _bool_field(raw, "executesCode"):
        warnings.append("ClawHub package executes code.")
    scan_status = _optional_string(raw, "scanStatus")
    if not scan_status:
        warnings.append("ClawHub scan status is missing.")
    elif scan_status not in {"clean", "benign", "approved"}:
        warnings.append(f"ClawHub scan status is {scan_status}.")
    artifact_kind = _optional_string(raw, "artifactKind")
    if artifact_kind == "legacy-zip":
        warnings.append("ClawHub package uses a legacy ZIP artifact.")
    if _has_runtime_extensions(raw):
        warnings.append(
            "ClawHub package declares OpenClaw native runtime extensions; "
            "Relay Teams only loads mapped plugin components."
        )
    if _compatibility_value(raw):
        warnings.append(
            "ClawHub package declares OpenClaw compatibility metadata; "
            "Relay Teams does not execute OpenClaw native plugin APIs."
        )
    if not digest:
        warnings.append("ClawHub package artifact has no digest metadata.")
    return tuple(warnings)


def _unsupported_reason(raw: Mapping[str, object]) -> str:
    package_name = _optional_string(raw, "name")
    if package_name in _KNOWN_UNMAPPABLE_PACKAGES:
        return "ClawHub package artifact does not contain Relay Teams mappable plugin components"
    moderation_state = _optional_string(raw, "moderationState")
    if moderation_state in {"quarantined", "revoked"}:
        return f"ClawHub package release is {moderation_state}"
    blocked = raw.get("blockedFromDownload")
    if isinstance(blocked, bool) and blocked:
        return "ClawHub package release is blocked from download"
    family = _package_family(raw)
    if family not in _CLAWHUB_FAMILIES:
        return f"Unsupported ClawHub package family: {family}"
    bundle_format = _bundle_format(raw)
    if family == "bundle-plugin" and bundle_format in {"bundle format"}:
        return "ClawHub bundle plugin does not declare a concrete bundle format"
    if (
        family == "bundle-plugin"
        and bundle_format
        and bundle_format not in _SUPPORTED_BUNDLE_FORMATS
    ):
        return f"Unsupported ClawHub bundle format: {bundle_format}"
    if family == "bundle-plugin" and _has_invalid_bundle_host_targets(raw):
        return "ClawHub bundle plugin host target metadata is invalid"
    return ""


def _compatibility_unsupported_reason(raw: Mapping[str, object]) -> str:
    compatibility, compatibility_reason = _compatibility_for_package(raw)
    if compatibility == PluginMarketplaceCompatibility.DIRECT:
        return ""
    reason = (
        "ClawHub plugin is not directly compatible with Relay Teams"
        f" (compatibility={compatibility.value})"
    )
    if compatibility_reason:
        reason = f"{reason}: {compatibility_reason}"
    return reason


def _artifact_download_url(*, base_url: str, name: str, version: str) -> str:
    return (
        f"{base_url}/api/v1/packages/{_quote_path(name)}/versions/"
        f"{_quote_path(version)}/artifact/download"
    )


def _quote_path(value: str) -> str:
    return quote(value, safe="")


def _entry_name(name: str) -> str:
    return name.strip()


def _normalized_base_url(value: str) -> str:
    normalized = value.strip() or _DEFAULT_CLAWHUB_BASE_URL
    return normalized.rstrip("/")


def _safe_limit(value: int) -> int:
    return max(1, min(_MAX_LIMIT, value))


def _url_opener() -> OpenerDirector:
    return build_opener(ProxyHandler(_urllib_proxy_map()))


def _urllib_proxy_map() -> dict[str, str]:
    env = load_proxy_env_config().normalized_env()
    proxies: dict[str, str] = {}
    http_proxy = env.get("HTTP_PROXY")
    https_proxy = env.get("HTTPS_PROXY")
    all_proxy = env.get("ALL_PROXY")
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    if all_proxy:
        proxies.setdefault("http", all_proxy)
        proxies.setdefault("https", all_proxy)
        proxies["all"] = all_proxy
    return proxies


def _bool_field(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    return value is True


def _has_runtime_extensions(raw: Mapping[str, object]) -> bool:
    runtime_extensions = raw.get("runtimeExtensions")
    if isinstance(runtime_extensions, list) and runtime_extensions:
        return True
    manifest = raw.get("manifest")
    if isinstance(manifest, Mapping):
        return _has_runtime_extensions(
            {str(key): value for key, value in manifest.items()}
        )
    return False


def _compatibility_value(raw: Mapping[str, object]) -> object:
    compat = raw.get("compat") or raw.get("compatibility")
    if compat:
        return compat
    manifest = raw.get("manifest")
    if isinstance(manifest, Mapping):
        return _compatibility_value(
            {str(key): value for key, value in manifest.items()}
        )
    return None


def _bundle_format(raw: Mapping[str, object]) -> str:
    bundle_format = _normalize_bundle_metadata_value(
        _optional_string(raw, "bundleFormat")
    )
    if bundle_format:
        return bundle_format
    capability_tags = raw.get("capabilityTags")
    if isinstance(capability_tags, list | tuple):
        for tag in capability_tags:
            if not isinstance(tag, str):
                continue
            normalized = tag.strip()
            if normalized.lower().startswith("format:"):
                tag_format = _normalize_bundle_metadata_value(
                    normalized.split(":", 1)[1]
                )
                if tag_format:
                    return tag_format
    capabilities = raw.get("capabilities")
    if isinstance(capabilities, Mapping):
        nested_format = _bundle_format(
            {str(key): value for key, value in capabilities.items()}
        )
        if nested_format:
            return nested_format
    manifest = raw.get("manifest")
    if isinstance(manifest, Mapping):
        return _bundle_format({str(key): value for key, value in manifest.items()})
    return ""


def _has_invalid_bundle_host_targets(raw: Mapping[str, object]) -> bool:
    host_targets = raw.get("hostTargets")
    if isinstance(host_targets, list | tuple):
        for target in host_targets:
            if not isinstance(target, str):
                continue
            normalized = target.strip()
            if '"' in normalized or ":" in normalized:
                return True
    capability_tags = raw.get("capabilityTags")
    if isinstance(capability_tags, list | tuple):
        for tag in capability_tags:
            if not isinstance(tag, str):
                continue
            normalized = tag.strip()
            if not normalized.lower().startswith("host:"):
                continue
            host_target = normalized.split(":", 1)[1].strip()
            if '"' in host_target or ":" in host_target:
                return True
    capabilities = raw.get("capabilities")
    if isinstance(capabilities, Mapping):
        if _has_invalid_bundle_host_targets(
            {str(key): value for key, value in capabilities.items()}
        ):
            return True
    manifest = raw.get("manifest")
    if isinstance(manifest, Mapping):
        return _has_invalid_bundle_host_targets(
            {str(key): value for key, value in manifest.items()}
        )
    return False


def _normalize_bundle_metadata_value(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _has_mappable_component_metadata(raw: Mapping[str, object]) -> bool:
    for key in (
        "skills",
        "bundledSkills",
        "skillNames",
        "agents",
        "roles",
        "commands",
        "commandNames",
        "mcpServers",
        "mcp_servers",
        "hooks",
    ):
        value = raw.get(key)
        if isinstance(value, list | tuple | Mapping) and len(value) > 0:
            return True
        if isinstance(value, str) and value.strip():
            return True
    capabilities = raw.get("capabilities")
    if isinstance(capabilities, Mapping):
        return _has_mappable_component_metadata(
            {str(key): value for key, value in capabilities.items()}
        )
    manifest = raw.get("manifest")
    if isinstance(manifest, Mapping):
        return _has_mappable_component_metadata(
            {str(key): value for key, value in manifest.items()}
        )
    return False


def _decode_family_cursors(cursor: str) -> dict[str, str]:
    normalized = cursor.strip()
    if not normalized:
        return {}
    try:
        raw = json.loads(normalized)
    except json.JSONDecodeError:
        return {"code-plugin": normalized}
    if not isinstance(raw, Mapping):
        return {"code-plugin": normalized}
    cursors: dict[str, str] = {}
    for family in _CLAWHUB_FAMILIES:
        if family not in raw:
            continue
        value = raw.get(family)
        if isinstance(value, str):
            cursors[family] = value.strip()
    return cursors


def _encode_family_cursors(cursors: Mapping[str, str]) -> str:
    payload = {
        family: cursor
        for family, cursor in cursors.items()
        if family in _CLAWHUB_FAMILIES
    }
    if not payload:
        return ""
    return json.dumps(payload, separators=(",", ":"))


def parse_sri_digest(value: str) -> tuple[str, str] | None:
    match = _SRI_RE.match(value.strip())
    if match is None:
        return None
    algorithm = match.group(1)
    try:
        digest = base64.b64decode(match.group(2), validate=True).hex()
    except ValueError:
        return None
    return algorithm, digest


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)
