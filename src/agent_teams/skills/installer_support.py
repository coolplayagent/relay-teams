# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from io import BytesIO
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.builtin import get_builtin_roles_dir
from agent_teams.env import load_proxy_env_config, sync_proxy_env_to_process_env
from agent_teams.paths import get_app_config_dir
from agent_teams.roles import RoleDocumentDraft, default_memory_profile
from agent_teams.roles.settings_service import RoleSettingsService
from agent_teams.skills.discovery import get_app_skills_dir
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.tools.registry import build_default_registry

_DEFAULT_GITHUB_API_BASE = "https://api.github.com"
_DEFAULT_GITHUB_BASE = "https://github.com"
_HTTP_TIMEOUT_SECONDS = 30.0
_CURRENT_ROLE_ENV_KEY = "AGENT_TEAMS_CURRENT_ROLE_ID"
_GITHUB_TREE_URL_RE = re.compile(
    r"https://github\.com/(?P<repo>[^/]+/[^/]+)/(?:tree|blob)/(?P<ref>[^/]+)/(?P<path>[^\"'\s<]+)"
)


class SkillInstallerError(RuntimeError):
    pass


class InstallMethod(str, Enum):
    AUTO = "auto"
    DOWNLOAD = "download"
    GIT = "git"


class SkillListingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    installed: bool = False


class SkillListingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    path: str = Field(min_length=1)
    entries: tuple[SkillListingEntry, ...]


class SkillSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    path: str = Field(min_length=1)


class SkillInstallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str = Field(min_length=1)
    destination: Path
    source: SkillSource
    mounted_roles: tuple[str, ...] = ()


def build_listing_payload(
    *,
    repo: str,
    ref: str,
    path: str,
) -> dict[str, object]:
    remote_names = fetch_remote_skill_names(repo=repo, ref=ref, path=path)
    installed_names = discover_installed_skill_names()
    payload = SkillListingPayload(
        repo=repo,
        ref=ref,
        path=path,
        entries=tuple(
            SkillListingEntry(name=name, installed=name in installed_names)
            for name in remote_names
        ),
    )
    return payload.model_dump(mode="json")


def render_listing_text(payload: dict[str, object]) -> str:
    listing = SkillListingPayload.model_validate(payload)
    source_label = f"{listing.repo} ({listing.path})"
    lines = [f"Skills from {source_label}:", ""]
    if not listing.entries:
        lines.append("<none>")
    else:
        for entry in listing.entries:
            suffix = " (already installed)" if entry.installed else ""
            lines.append(f"{entry.name}{suffix}")
    return "\n".join(lines)


def render_install_results_text(results: tuple[SkillInstallResult, ...]) -> str:
    lines: list[str] = []
    for result in results:
        lines.append(
            f"Installed {result.skill_name} -> {result.destination.resolve().as_posix()}"
        )
        if result.mounted_roles:
            lines.append("Mounted on roles: " + ", ".join(result.mounted_roles))
    lines.append("Restart Agent Teams to pick up new skills.")
    return "\n".join(lines)


def fetch_remote_skill_names(
    *,
    repo: str,
    ref: str,
    path: str,
) -> tuple[str, ...]:
    sync_network_environment()
    api_url = f"{github_api_base()}/repos/{repo}/contents/{path}?ref={ref}"
    payload = _request_json(api_url)
    if not isinstance(payload, list):
        raise SkillInstallerError(
            f"GitHub API returned an unexpected payload for {api_url}"
        )
    entries: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name")
        raw_type = item.get("type")
        if (
            isinstance(raw_name, str)
            and isinstance(raw_type, str)
            and raw_type == "dir"
        ):
            entries.append(raw_name)
    return tuple(sorted(entries))


def discover_installed_skill_names() -> frozenset[str]:
    return frozenset(SkillRegistry.from_default_scopes().list_names())


def install_from_url(
    *,
    url: str,
    dest_root: str | None,
    name: str | None,
    role_ids: tuple[str, ...],
    method: InstallMethod,
) -> tuple[SkillInstallResult, ...]:
    source = resolve_source_from_url(url)
    return install_from_repo_paths(
        repo=source.repo,
        ref=source.ref,
        paths=(source.path,),
        dest_root=dest_root,
        name=name,
        role_ids=role_ids,
        method=method,
    )


def install_from_repo_paths(
    *,
    repo: str,
    ref: str,
    paths: tuple[str, ...],
    dest_root: str | None,
    name: str | None,
    role_ids: tuple[str, ...],
    method: InstallMethod,
) -> tuple[SkillInstallResult, ...]:
    normalized_paths = tuple(
        _normalize_repo_path(path) for path in paths if path.strip()
    )
    if not normalized_paths:
        raise SkillInstallerError("At least one --path value is required")
    if name and len(normalized_paths) != 1:
        raise SkillInstallerError("--name can only be used with a single --path")

    resolved_dest_root = resolve_destination_root(dest_root)
    source_specs = tuple(
        SkillSource(
            repo=repo,
            ref=ref,
            path=path,
        )
        for path in normalized_paths
    )
    destinations = tuple(
        build_destination_path(
            dest_root=resolved_dest_root,
            skill_name=name if index == 0 and name else derive_skill_name(spec.path),
        )
        for index, spec in enumerate(source_specs)
    )
    _ensure_destinations_available(destinations)

    resolved_role_ids = _resolve_role_mount_targets(role_ids)

    if method == InstallMethod.DOWNLOAD:
        results = _install_via_download(
            source_specs=source_specs,
            destinations=destinations,
            override_name=name,
        )
        return _mount_roles_for_results(results, resolved_role_ids)
    if method == InstallMethod.GIT:
        results = _install_via_git(
            source_specs=source_specs,
            destinations=destinations,
            override_name=name,
        )
        return _mount_roles_for_results(results, resolved_role_ids)

    try:
        results = _install_via_download(
            source_specs=source_specs,
            destinations=destinations,
            override_name=name,
        )
        return _mount_roles_for_results(results, resolved_role_ids)
    except _DownloadAuthError:
        results = _install_via_git(
            source_specs=source_specs,
            destinations=destinations,
            override_name=name,
        )
        return _mount_roles_for_results(results, resolved_role_ids)


def resolve_source_from_url(url: str) -> SkillSource:
    github_source = parse_github_tree_url(url)
    if github_source is not None:
        return github_source

    sync_network_environment()
    page_text = _request_text(url)
    match = _GITHUB_TREE_URL_RE.search(page_text)
    if match is None:
        raise SkillInstallerError(f"Could not extract a GitHub tree URL from {url}")
    repo = match.group("repo")
    ref = match.group("ref")
    path = match.group("path")
    return SkillSource(repo=repo, ref=ref, path=_normalize_repo_path(path))


def parse_github_tree_url(url: str) -> SkillSource | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 5:
        return None
    if segments[2] not in {"tree", "blob"}:
        return None
    repo = f"{segments[0]}/{segments[1]}"
    ref = segments[3]
    path = "/".join(segments[4:])
    if not path:
        return None
    return SkillSource(repo=repo, ref=ref, path=_normalize_repo_path(path))


def resolve_destination_root(dest_root: str | None) -> Path:
    if dest_root is not None and dest_root.strip():
        return Path(dest_root).expanduser().resolve()
    return get_app_skills_dir().resolve()


def derive_skill_name(path: str) -> str:
    parts = [part for part in path.split("/") if part and part not in {"."}]
    if not parts:
        raise SkillInstallerError(f"Could not derive skill name from path: {path}")
    return parts[-1]


def build_destination_path(*, dest_root: Path, skill_name: str) -> Path:
    return dest_root / skill_name


def sync_network_environment() -> None:
    sync_proxy_env_to_process_env(load_proxy_env_config())


def github_api_base() -> str:
    return os.environ.get(
        "AGENT_TEAMS_SKILL_GITHUB_API_BASE", _DEFAULT_GITHUB_API_BASE
    ).rstrip("/")


def github_base() -> str:
    return os.environ.get("AGENT_TEAMS_SKILL_GITHUB_BASE", _DEFAULT_GITHUB_BASE).rstrip(
        "/"
    )


def _install_via_download(
    *,
    source_specs: tuple[SkillSource, ...],
    destinations: tuple[Path, ...],
    override_name: str | None,
) -> tuple[SkillInstallResult, ...]:
    if not source_specs:
        return ()
    sync_network_environment()
    archive_bytes = _download_repo_archive(
        repo=source_specs[0].repo,
        ref=source_specs[0].ref,
    )
    return _extract_archive_to_destinations(
        archive_bytes=archive_bytes,
        source_specs=source_specs,
        destinations=destinations,
        override_name=override_name,
    )


def _install_via_git(
    *,
    source_specs: tuple[SkillSource, ...],
    destinations: tuple[Path, ...],
    override_name: str | None,
) -> tuple[SkillInstallResult, ...]:
    if not source_specs:
        return ()
    sync_network_environment()
    with TemporaryDirectory() as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        checkout_dir = tmp_dir / "checkout"
        checkout_dir.mkdir(parents=True, exist_ok=True)
        last_error: SkillInstallerError | None = None
        for remote_url in _iter_git_remote_urls(source_specs[0].repo):
            try:
                _checkout_sparse_repo(
                    checkout_dir=checkout_dir,
                    remote_url=remote_url,
                    ref=source_specs[0].ref,
                    paths=tuple(spec.path for spec in source_specs),
                )
                return _copy_checked_out_paths(
                    checkout_dir=checkout_dir,
                    source_specs=source_specs,
                    destinations=destinations,
                    override_name=override_name,
                )
            except SkillInstallerError as exc:
                last_error = exc
                _remove_tree(checkout_dir)
                checkout_dir.mkdir(parents=True, exist_ok=True)
        if last_error is None:
            raise SkillInstallerError("Git fallback failed")
        raise last_error


def _extract_archive_to_destinations(
    *,
    archive_bytes: bytes,
    source_specs: tuple[SkillSource, ...],
    destinations: tuple[Path, ...],
    override_name: str | None,
) -> tuple[SkillInstallResult, ...]:
    results: list[SkillInstallResult] = []
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        archive_names = archive.namelist()
        for index, (spec, destination) in enumerate(
            zip(source_specs, destinations, strict=True)
        ):
            _extract_single_skill_from_archive(
                archive=archive,
                archive_names=archive_names,
                repo_path=spec.path,
                destination=destination,
            )
            results.append(
                SkillInstallResult(
                    skill_name=override_name
                    if index == 0 and override_name
                    else derive_skill_name(spec.path),
                    destination=destination,
                    source=spec,
                )
            )
    return tuple(results)


def _copy_checked_out_paths(
    *,
    checkout_dir: Path,
    source_specs: tuple[SkillSource, ...],
    destinations: tuple[Path, ...],
    override_name: str | None,
) -> tuple[SkillInstallResult, ...]:
    results: list[SkillInstallResult] = []
    for index, (spec, destination) in enumerate(
        zip(source_specs, destinations, strict=True)
    ):
        source_path = checkout_dir / Path(spec.path)
        if not source_path.is_dir():
            raise SkillInstallerError(
                f"Installed path not found after git checkout: {spec.path}"
            )
        shutil.copytree(source_path, destination)
        results.append(
            SkillInstallResult(
                skill_name=override_name
                if index == 0 and override_name
                else derive_skill_name(spec.path),
                destination=destination,
                source=spec,
            )
        )
    return tuple(results)


def _download_repo_archive(*, repo: str, ref: str) -> bytes:
    url = f"{github_api_base()}/repos/{repo}/zipball/{ref}"
    try:
        return _request_bytes(url)
    except HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise _DownloadAuthError(
                f"Direct download failed for {repo}@{ref}: HTTP {exc.code}"
            ) from exc
        raise SkillInstallerError(
            f"Direct download failed for {repo}@{ref}: HTTP {exc.code}"
        ) from exc
    except URLError as exc:
        raise SkillInstallerError(
            f"Direct download failed for {repo}@{ref}: {exc}"
        ) from exc


def _extract_single_skill_from_archive(
    *,
    archive: zipfile.ZipFile,
    archive_names: list[str],
    repo_path: str,
    destination: Path,
) -> None:
    normalized_repo_path = _normalize_repo_path(repo_path)
    matching_names = tuple(
        name
        for name in archive_names
        if _archive_member_matches_repo_path(name=name, repo_path=normalized_repo_path)
    )
    if not matching_names:
        raise SkillInstallerError(
            f"Skill path not found in archive: {normalized_repo_path}"
        )
    destination.mkdir(parents=True, exist_ok=False)
    for member_name in matching_names:
        relative_path = _archive_member_relative_path(
            name=member_name,
            repo_path=normalized_repo_path,
        )
        if relative_path is None or not relative_path.parts:
            continue
        target_path = destination / relative_path
        if member_name.endswith("/"):
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member_name) as source_handle:
            target_path.write_bytes(source_handle.read())


def _archive_member_matches_repo_path(*, name: str, repo_path: str) -> bool:
    relative_path = _archive_member_relative_path(name=name, repo_path=repo_path)
    return relative_path is not None


def _archive_member_relative_path(*, name: str, repo_path: str) -> Path | None:
    member_path = Path(name)
    if len(member_path.parts) < 2:
        return None
    relative_parts = member_path.parts[1:]
    repo_parts = tuple(part for part in repo_path.split("/") if part)
    if tuple(relative_parts[: len(repo_parts)]) != repo_parts:
        return None
    remaining_parts = relative_parts[len(repo_parts) :]
    if not remaining_parts:
        return Path(".")
    return Path(*remaining_parts)


def _checkout_sparse_repo(
    *,
    checkout_dir: Path,
    remote_url: str,
    ref: str,
    paths: tuple[str, ...],
) -> None:
    _run_git(checkout_dir.parent, "git", "init", checkout_dir.name)
    _run_git(checkout_dir, "git", "remote", "add", "origin", remote_url)
    _run_git(checkout_dir, "git", "config", "core.sparseCheckout", "true")
    sparse_checkout_file = checkout_dir / ".git" / "info" / "sparse-checkout"
    sparse_checkout_file.parent.mkdir(parents=True, exist_ok=True)
    sparse_patterns: list[str] = []
    for path in paths:
        normalized = _normalize_repo_path(path)
        sparse_patterns.append(normalized)
        sparse_patterns.append(f"{normalized}/")
        sparse_patterns.append(f"{normalized}/**")
    sparse_checkout_file.write_text("\n".join(sparse_patterns) + "\n", encoding="utf-8")
    _run_git(checkout_dir, "git", "fetch", "--depth", "1", "origin", ref)
    _run_git(checkout_dir, "git", "checkout", "FETCH_HEAD")


def _run_git(cwd: Path, *args: str) -> None:
    completed = subprocess.run(
        list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or "git command failed"
        raise SkillInstallerError(detail)


def _iter_git_remote_urls(repo: str) -> tuple[str, ...]:
    parsed_base = urlparse(github_base())
    host = parsed_base.netloc or "github.com"
    https_url = f"{github_base()}/{repo}.git"
    ssh_url = f"git@{host}:{repo}.git"
    return (https_url, ssh_url)


def _request_json(url: str) -> object:
    body = _request_text(url)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SkillInstallerError(f"Failed to decode JSON from {url}") from exc


def _request_text(url: str) -> str:
    return _request_bytes(url).decode("utf-8")


def _request_bytes(url: str) -> bytes:
    request = Request(
        url,
        headers=_request_headers(),
        method="GET",
    )
    with urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


def _request_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "agent-teams-skill-installer",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_token() -> str:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _ensure_destinations_available(destinations: tuple[Path, ...]) -> None:
    seen_paths: set[Path] = set()
    for destination in destinations:
        resolved = destination.expanduser().resolve()
        if resolved in seen_paths:
            raise SkillInstallerError(
                f"Duplicate destination requested: {resolved.as_posix()}"
            )
        seen_paths.add(resolved)
        if resolved.exists():
            raise SkillInstallerError(
                f"Destination skill directory already exists: {resolved.as_posix()}"
            )


def _mount_roles_for_results(
    results: tuple[SkillInstallResult, ...],
    role_ids: tuple[str, ...],
) -> tuple[SkillInstallResult, ...]:
    if not results:
        return ()
    mounted_role_ids = mount_skills_to_roles(
        role_ids=role_ids,
        skill_names=tuple(result.skill_name for result in results),
    )
    return tuple(
        result.model_copy(update={"mounted_roles": mounted_role_ids})
        for result in results
    )


def mount_skills_to_roles(
    *,
    role_ids: tuple[str, ...],
    skill_names: tuple[str, ...],
) -> tuple[str, ...]:
    normalized_role_ids = _dedupe_non_empty(role_ids)
    normalized_skill_names = _dedupe_non_empty(skill_names)
    if not normalized_skill_names:
        return ()

    role_service = RoleSettingsService(
        roles_dir=get_app_config_dir().resolve() / "roles",
        builtin_roles_dir=get_builtin_roles_dir(),
        get_tool_registry=build_default_registry,
        get_mcp_registry=McpRegistry,
        get_skill_registry=lambda: SkillRegistry.from_default_scopes(),
        on_roles_reloaded=lambda registry: None,
    )
    known_skills = frozenset(SkillRegistry.from_default_scopes().list_names())
    missing = [
        skill_name
        for skill_name in normalized_skill_names
        if skill_name not in known_skills
    ]
    if missing:
        raise SkillInstallerError(f"Unknown skills after install: {missing}")

    for role_id in normalized_role_ids:
        record = role_service.get_role_document(role_id)
        merged_skills = _merge_names(record.skills, normalized_skill_names)
        if merged_skills == record.skills:
            continue
        role_service.save_role_document(
            role_id,
            RoleDocumentDraft(
                source_role_id=role_id,
                role_id=record.role_id,
                name=record.name,
                description=record.description,
                version=record.version,
                tools=record.tools,
                mcp_servers=record.mcp_servers,
                skills=merged_skills,
                model_profile=record.model_profile,
                memory_profile=record.memory_profile or default_memory_profile(),
                system_prompt=record.system_prompt,
            ),
        )
    return normalized_role_ids


def _normalize_repo_path(path: str) -> str:
    return path.strip().strip("/")


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


class _DownloadAuthError(SkillInstallerError):
    pass


def _resolve_role_mount_targets(role_ids: tuple[str, ...]) -> tuple[str, ...]:
    normalized = _dedupe_non_empty(role_ids)
    if normalized:
        return normalized
    current_role_id = os.environ.get(_CURRENT_ROLE_ENV_KEY, "").strip()
    if current_role_id:
        return (current_role_id,)
    return ("MainAgent",)


def _dedupe_non_empty(items: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    resolved: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(normalized)
    return tuple(resolved)


def _merge_names(
    existing: tuple[str, ...],
    additions: tuple[str, ...],
) -> tuple[str, ...]:
    merged = list(existing)
    known = set(existing)
    for item in additions:
        if item not in known:
            known.add(item)
            merged.append(item)
    return tuple(merged)
