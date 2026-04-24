# -*- coding: utf-8 -*-
from __future__ import annotations

import difflib
import mimetypes
import posixpath
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from relay_teams.logger import get_logger, log_event
from relay_teams.paths import (
    get_project_config_dir,
    iter_dir_paths,
    path_exists,
    path_is_dir,
    path_is_file,
    read_bytes_file,
    unlink_path,
)
from relay_teams.workspace.directory_opener import open_workspace_directory
from relay_teams.workspace.git_worktree import GitWorktreeClient
from relay_teams.workspace.ssh_profile_service import SshProfileService
from relay_teams.workspace.workspace_models import (
    WorkspaceDiffChangeType,
    WorkspaceDiffFile,
    WorkspaceDiffFileSummary,
    WorkspaceDiffListing,
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceMountCapabilities,
    WorkspaceLocalMountConfig,
    WorkspaceProfile,
    WorkspaceRecord,
    WorkspaceSearchResponse,
    WorkspaceSearchResult,
    WorkspaceSshMountConfig,
    WorkspaceSnapshot,
    WorkspaceTreeListing,
    WorkspaceTreeNode,
    WorkspaceTreeNodeKind,
    default_mount_capabilities,
    default_workspace_profile,
    legacy_workspace_mount_from_profile,
)
from relay_teams.workspace.workspace_repository import WorkspaceRepository


_NON_WORKSPACE_ID_CHARS = re.compile(r"[^a-z0-9]+")
_GIT_TIMEOUT_SECONDS = 30.0
_BINARY_DIFF_MESSAGE = "Binary file changed"
_SSH_TREE_LIST_TIMEOUT_SECONDS = 30.0
_SSH_TREE_ENTRY_SEPARATOR = "\t"
_SSH_TREE_NOT_DIRECTORY_MARKER = "relay-teams-error:not-directory"
_SEARCH_MAX_VISITED = 3000
_SEARCH_CACHE_TTL_SECONDS = 300.0
_SEARCH_COLD_BUILD_TIMEOUT_SECONDS = 0.35
_SEARCH_RIPGREP_TIMEOUT_SECONDS = 15.0
_SEARCH_SKIP_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
_SSH_TREE_LIST_SCRIPT = """
set -eu
dir=$1
if [ ! -d "$dir" ]; then
    printf '%s\\n' 'relay-teams-error:not-directory' >&2
    exit 2
fi
find "$dir" -mindepth 1 -maxdepth 1 ! -name .git -exec sh -c '
for path do
    name=${path##*/}
    if [ -d "$path" ] && [ ! -L "$path" ]; then
        kind=directory
        if find "$path" -mindepth 1 -maxdepth 1 ! -name .git -print -quit | grep -q .; then
            has_children=1
        else
            has_children=0
        fi
    else
        kind=file
        has_children=0
    fi
    printf "%s\\t%s\\t%s\\n" "$kind" "$has_children" "$name"
done
' sh {} +
"""
_WORKSPACE_IMAGE_MEDIA_TYPES = frozenset(
    {
        "image/avif",
        "image/bmp",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
_logger = get_logger(__name__)


class _WorkspaceDiffCandidate:
    __slots__ = ("path", "change_type", "previous_path")

    def __init__(
        self,
        *,
        path: str,
        change_type: WorkspaceDiffChangeType,
        previous_path: str | None = None,
    ) -> None:
        self.path = path
        self.change_type = change_type
        self.previous_path = previous_path


class _WorkspaceSearchCacheEntry:
    __slots__ = ("candidates", "indexed_at", "refreshing")

    def __init__(
        self,
        *,
        candidates: tuple[WorkspaceSearchResult, ...],
        indexed_at: float,
        refreshing: bool = False,
    ) -> None:
        self.candidates = candidates
        self.indexed_at = indexed_at
        self.refreshing = refreshing


class WorkspaceService:
    def __init__(
        self,
        *,
        repository: WorkspaceRepository,
        git_worktree_client: GitWorktreeClient | None = None,
        ssh_profile_service: SshProfileService | None = None,
    ) -> None:
        self._repository = repository
        self._git_worktree_client = git_worktree_client or GitWorktreeClient()
        self._ssh_profile_service = ssh_profile_service
        self._search_cache: dict[str, _WorkspaceSearchCacheEntry] = {}
        self._search_cache_lock = threading.RLock()

    def _mount_capabilities(
        self,
        mount: WorkspaceMountRecord,
    ) -> WorkspaceMountCapabilities:
        return mount.capabilities or default_mount_capabilities(mount.provider)

    def create_workspace(
        self,
        *,
        workspace_id: str,
        root_path: Path | None = None,
        profile: WorkspaceProfile | None = None,
        mounts: Optional[tuple[WorkspaceMountRecord, ...]] = None,
        default_mount_name: Optional[str] = None,
    ) -> WorkspaceRecord:
        if self._repository.exists(workspace_id):
            raise ValueError(f"Workspace already exists: {workspace_id}")
        resolved_mounts, resolved_default_mount_name = self._resolve_workspace_mounts(
            root_path=root_path,
            profile=profile,
            mounts=mounts,
            default_mount_name=default_mount_name,
        )
        return self._repository.create(
            workspace_id=workspace_id,
            mounts=resolved_mounts,
            default_mount_name=resolved_default_mount_name,
        )

    def update_workspace(
        self,
        workspace_id: str,
        *,
        mounts: tuple[WorkspaceMountRecord, ...],
        default_mount_name: str,
    ) -> WorkspaceRecord:
        resolved_mounts, resolved_default_mount_name = self._resolve_workspace_mounts(
            mounts=mounts,
            default_mount_name=default_mount_name,
        )
        return self._repository.update(
            workspace_id=workspace_id,
            mounts=resolved_mounts,
            default_mount_name=resolved_default_mount_name,
        )

    def _resolve_workspace_mounts(
        self,
        *,
        root_path: Path | None = None,
        profile: WorkspaceProfile | None = None,
        mounts: Optional[tuple[WorkspaceMountRecord, ...]] = None,
        default_mount_name: Optional[str] = None,
    ) -> tuple[tuple[WorkspaceMountRecord, ...], str]:
        if mounts is None:
            if root_path is None:
                raise ValueError("Workspace creation requires root_path or mounts")
            resolved_root = self._validate_root(root_path)
            mount_name = default_mount_name or "default"
            resolved_mounts = (
                legacy_workspace_mount_from_profile(
                    root_path=resolved_root,
                    profile=profile or default_workspace_profile(),
                    mount_name=mount_name,
                ),
            )
            resolved_default_mount_name = mount_name
            self._validate_local_mount_scope_paths(
                mount=resolved_mounts[0],
                root_path=resolved_root,
            )
        else:
            provided_mounts = tuple(mounts)
            if len(provided_mounts) == 0:
                raise ValueError("Workspace must include at least one mount")
            normalized_mounts: list[WorkspaceMountRecord] = []
            for mount in provided_mounts:
                normalized_mount = mount
                if mount.provider == WorkspaceMountProvider.LOCAL:
                    local_root = mount.local_root_path()
                    if local_root is None:
                        raise ValueError(
                            f"Workspace local mount is missing root path: {mount.mount_name}"
                        )
                    resolved_local_root = self._validate_root(local_root)
                    self._validate_local_mount_scope_paths(
                        mount=mount,
                        root_path=resolved_local_root,
                    )
                    normalized_mount = mount.model_copy(
                        update={
                            "provider_config": WorkspaceLocalMountConfig(
                                root_path=resolved_local_root
                            )
                        }
                    )
                if mount.provider == WorkspaceMountProvider.SSH:
                    provider_config = mount.provider_config
                    if not isinstance(provider_config, WorkspaceSshMountConfig):
                        raise ValueError(
                            f"Workspace ssh mount is missing ssh config: {mount.mount_name}"
                        )
                    ssh_profile_id = provider_config.ssh_profile_id
                    if self._ssh_profile_service is not None:
                        try:
                            self._ssh_profile_service.require_profile(ssh_profile_id)
                        except KeyError as exc:
                            raise ValueError(
                                "Workspace ssh mount references unknown ssh profile: "
                                f"{ssh_profile_id}"
                            ) from exc
                normalized_mounts.append(normalized_mount)
            resolved_mounts = tuple(normalized_mounts)
            resolved_default_mount_name = (
                default_mount_name or resolved_mounts[0].mount_name
            )
        self._validate_default_mount(
            mounts=resolved_mounts,
            default_mount_name=resolved_default_mount_name,
        )
        return resolved_mounts, resolved_default_mount_name

    def create_workspace_for_root(
        self,
        *,
        root_path: Path,
        profile: WorkspaceProfile | None = None,
    ) -> WorkspaceRecord:
        resolved_root = self._validate_root(root_path)
        existing = self._find_workspace_by_root(resolved_root)
        if existing is not None:
            return existing

        workspace_id = self._next_workspace_id_for_root(resolved_root)
        return self.create_workspace(
            workspace_id=workspace_id,
            root_path=resolved_root,
            profile=profile,
        )

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord:
        return self._repository.get(workspace_id)

    def open_workspace_root(
        self,
        workspace_id: str,
        *,
        mount_name: Optional[str] = None,
    ) -> Path:
        record = self._repository.get(workspace_id)
        target_mount = (
            self._resolve_mount(record, mount_name)
            if mount_name is not None
            else self._primary_local_mount(record)
        )
        if target_mount is None:
            raise ValueError("Workspace has no local mount to open")
        if target_mount.provider != WorkspaceMountProvider.LOCAL:
            raise ValueError(f"Workspace mount is not local: {target_mount.mount_name}")
        root_path = self._resolve_local_mount_root(target_mount)
        try:
            open_workspace_directory(root_path)
        except RuntimeError:
            log_event(
                _logger,
                30,
                event="workspace.open_root.failed",
                message="Failed to open workspace root in native file manager",
                payload={
                    "workspace_id": record.workspace_id,
                    "mount_name": target_mount.mount_name,
                    "root_path": str(root_path),
                },
            )
            raise

        log_event(
            _logger,
            20,
            event="workspace.open_root.started",
            message="Opened workspace root in native file manager",
            payload={
                "workspace_id": record.workspace_id,
                "mount_name": target_mount.mount_name,
                "root_path": str(root_path),
            },
        )
        return root_path

    def get_workspace_snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
        record = self._repository.get(workspace_id)
        default_mount_root = record.default_mount.local_root_path()
        mount_children = tuple(
            WorkspaceTreeNode(
                name=mount.mount_name,
                path=mount.mount_name,
                kind=WorkspaceTreeNodeKind.DIRECTORY,
                has_children=self._mount_has_children(mount),
                children=(),
            )
            for mount in record.mounts
        )
        return WorkspaceSnapshot(
            workspace_id=record.workspace_id,
            default_mount_name=record.default_mount_name,
            default_mount_root=default_mount_root,
            tree=WorkspaceTreeNode(
                name=record.workspace_id,
                path=".",
                kind=WorkspaceTreeNodeKind.DIRECTORY,
                has_children=len(mount_children) > 0,
                children=mount_children,
            ),
        )

    def get_workspace_tree_listing(
        self,
        workspace_id: str,
        *,
        directory_path: str,
        mount_name: Optional[str] = None,
    ) -> WorkspaceTreeListing:
        record = self._repository.get(workspace_id)
        mount = self._resolve_mount(record, mount_name)
        if mount.provider != WorkspaceMountProvider.LOCAL:
            if mount.provider == WorkspaceMountProvider.SSH:
                return self._get_ssh_workspace_tree_listing(
                    record=record,
                    mount=mount,
                    directory_path=directory_path,
                )
            raise ValueError(
                f"Workspace mount does not support tree listing: {mount.mount_name}"
            )
        root_path = self._resolve_local_mount_root(mount)
        target_path = self._resolve_tree_path(
            root_path=root_path,
            directory_path=directory_path,
        )
        if not path_is_dir(target_path) or target_path.is_symlink():
            raise ValueError(f"Workspace path is not a directory: {directory_path}")
        children = tuple(
            self._build_tree_node(
                root_path=root_path,
                current_path=child_path,
                include_children=False,
            )
            for child_path in self._iter_tree_entries(target_path)
        )
        normalized_directory_path = "."
        if target_path != root_path:
            normalized_directory_path = target_path.relative_to(root_path).as_posix()
        return WorkspaceTreeListing(
            workspace_id=record.workspace_id,
            mount_name=mount.mount_name,
            directory_path=normalized_directory_path,
            children=children,
        )

    def search_workspace_paths(
        self,
        workspace_id: str,
        *,
        query: str = "",
        limit: int = 40,
        mount_name: Optional[str] = None,
    ) -> WorkspaceSearchResponse:
        record = self._repository.get(workspace_id)
        mount = self._resolve_search_mount(record=record, mount_name=mount_name)
        root_path = self._resolve_local_mount_root(mount)
        normalized_query = str(query or "").strip().replace("\\", "/")
        safe_query = normalized_query.casefold()
        safe_limit = max(1, min(int(limit), 500))
        candidates = self._get_search_candidates(
            workspace_id=record.workspace_id,
            mount=mount,
            root_path=root_path,
        )
        results = self._rank_search_candidates(
            candidates=candidates,
            query=safe_query,
            limit=safe_limit,
        )
        if safe_query.endswith("/") and not results:
            results = self._search_directory_children(
                record=record,
                mount=mount,
                directory_path=normalized_query,
                limit=safe_limit,
            )
        return WorkspaceSearchResponse(
            workspace_id=record.workspace_id,
            query=str(query or "").strip(),
            results=results,
        )

    def _search_directory_children(
        self,
        *,
        record: WorkspaceRecord,
        mount: WorkspaceMountRecord,
        directory_path: str,
        limit: int,
    ) -> tuple[WorkspaceSearchResult, ...]:
        try:
            listing = self.get_workspace_tree_listing(
                record.workspace_id,
                directory_path=directory_path,
                mount_name=mount.mount_name,
            )
        except (KeyError, ValueError, OSError):
            return ()
        return tuple(
            WorkspaceSearchResult(
                name=item.name,
                path=item.path,
                kind=item.kind,
                mount_name=mount.mount_name,
            )
            for item in listing.children[:limit]
        )

    def _get_search_candidates(
        self,
        *,
        workspace_id: str,
        mount: WorkspaceMountRecord,
        root_path: Path,
    ) -> tuple[WorkspaceSearchResult, ...]:
        cache_key = f"{workspace_id}\n{mount.mount_name}\n{root_path}"
        now = time.monotonic()
        with self._search_cache_lock:
            cached = self._search_cache.get(cache_key)
            if cached is not None:
                if now - cached.indexed_at <= _SEARCH_CACHE_TTL_SECONDS:
                    return cached.candidates
                if not cached.refreshing:
                    self._start_search_refresh_locked(
                        cache_key=cache_key,
                        root_path=root_path,
                        mount=mount,
                    )
                return cached.candidates
        try:
            candidates = self._build_search_candidates(
                root_path=root_path,
                mount=mount,
                timeout_seconds=_SEARCH_COLD_BUILD_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            candidates = self._build_shallow_search_candidates(
                root_path=root_path,
                mount=mount,
            )
            with self._search_cache_lock:
                self._search_cache[cache_key] = _WorkspaceSearchCacheEntry(
                    candidates=candidates,
                    indexed_at=0.0,
                    refreshing=True,
                )
                self._start_search_refresh_locked(
                    cache_key=cache_key,
                    root_path=root_path,
                    mount=mount,
                )
            return candidates
        with self._search_cache_lock:
            self._store_search_cache_locked(
                cache_key=cache_key,
                candidates=candidates,
            )
        return candidates

    def _build_search_candidates(
        self,
        *,
        root_path: Path,
        mount: WorkspaceMountRecord,
        timeout_seconds: Optional[float] = None,
    ) -> tuple[WorkspaceSearchResult, ...]:
        ripgrep_candidates = self._build_ripgrep_search_candidates(
            root_path=root_path,
            mount=mount,
            timeout_seconds=timeout_seconds,
        )
        if ripgrep_candidates is not None:
            return ripgrep_candidates
        if timeout_seconds is not None:
            raise subprocess.TimeoutExpired(
                cmd="workspace search index build",
                timeout=timeout_seconds,
            )
        return self._build_walk_search_candidates(root_path=root_path, mount=mount)

    def _build_ripgrep_search_candidates(
        self,
        *,
        root_path: Path,
        mount: WorkspaceMountRecord,
        timeout_seconds: Optional[float],
    ) -> Optional[tuple[WorkspaceSearchResult, ...]]:
        ripgrep_binary = shutil.which("rg")
        if ripgrep_binary is None:
            return None
        args = [
            ripgrep_binary,
            "--no-config",
            "--files",
            "--hidden",
            "--glob=!.git/*",
        ]
        for ignored_name in sorted(_SEARCH_SKIP_DIRECTORY_NAMES):
            args.append(f"--glob=!**/{ignored_name}/**")
        args.append(".")
        completed = subprocess.run(
            args,
            cwd=root_path,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=timeout_seconds or _SEARCH_RIPGREP_TIMEOUT_SECONDS,
        )
        if completed.returncode not in {0, 1}:
            return None
        return self._search_candidates_from_file_paths(
            paths=tuple(
                line.strip().replace("\\", "/")
                for line in completed.stdout.splitlines()
                if line.strip()
            ),
            mount=mount,
        )

    @staticmethod
    def _search_candidates_from_file_paths(
        *,
        paths: tuple[str, ...],
        mount: WorkspaceMountRecord,
    ) -> tuple[WorkspaceSearchResult, ...]:
        results: list[WorkspaceSearchResult] = []
        seen_files: set[str] = set()
        seen_dirs: set[str] = set()
        for raw_path in paths:
            normalized_path = raw_path.strip().strip("/").replace("\\", "/")
            if normalized_path.startswith("./"):
                normalized_path = normalized_path[2:]
            if not normalized_path or normalized_path.startswith("../"):
                continue
            parts = tuple(part for part in normalized_path.split("/") if part)
            if not parts or any(part in _SEARCH_SKIP_DIRECTORY_NAMES for part in parts):
                continue
            current_parts: list[str] = []
            for directory_part in parts[:-1]:
                current_parts.append(directory_part)
                directory_path = f"{'/'.join(current_parts)}/"
                if directory_path in seen_dirs:
                    continue
                seen_dirs.add(directory_path)
                results.append(
                    WorkspaceSearchResult(
                        name=directory_part,
                        path=directory_path,
                        kind=WorkspaceTreeNodeKind.DIRECTORY,
                        mount_name=mount.mount_name,
                    )
                )
            if normalized_path in seen_files:
                continue
            seen_files.add(normalized_path)
            results.append(
                WorkspaceSearchResult(
                    name=parts[-1],
                    path=normalized_path,
                    kind=WorkspaceTreeNodeKind.FILE,
                    mount_name=mount.mount_name,
                )
            )
        return tuple(results)

    def _build_walk_search_candidates(
        self,
        *,
        root_path: Path,
        mount: WorkspaceMountRecord,
    ) -> tuple[WorkspaceSearchResult, ...]:
        results: list[WorkspaceSearchResult] = []
        visited = 0
        pending = list(self._iter_tree_entries(root_path))
        while pending and visited < _SEARCH_MAX_VISITED:
            current_path = pending.pop(0)
            visited += 1
            is_directory = path_is_dir(current_path) and not current_path.is_symlink()
            relative_path = current_path.relative_to(root_path).as_posix()
            results.append(
                WorkspaceSearchResult(
                    name=current_path.name,
                    path=f"{relative_path}/" if is_directory else relative_path,
                    kind=(
                        WorkspaceTreeNodeKind.DIRECTORY
                        if is_directory
                        else WorkspaceTreeNodeKind.FILE
                    ),
                    mount_name=mount.mount_name,
                )
            )
            if is_directory and current_path.name not in _SEARCH_SKIP_DIRECTORY_NAMES:
                pending.extend(self._iter_tree_entries(current_path))
        return tuple(results)

    def _build_shallow_search_candidates(
        self,
        *,
        root_path: Path,
        mount: WorkspaceMountRecord,
    ) -> tuple[WorkspaceSearchResult, ...]:
        results: list[WorkspaceSearchResult] = []
        for current_path in self._iter_tree_entries(root_path):
            is_directory = path_is_dir(current_path) and not current_path.is_symlink()
            relative_path = current_path.relative_to(root_path).as_posix()
            results.append(
                WorkspaceSearchResult(
                    name=current_path.name,
                    path=f"{relative_path}/" if is_directory else relative_path,
                    kind=(
                        WorkspaceTreeNodeKind.DIRECTORY
                        if is_directory
                        else WorkspaceTreeNodeKind.FILE
                    ),
                    mount_name=mount.mount_name,
                )
            )
        return tuple(results)

    def _start_search_refresh_locked(
        self,
        *,
        cache_key: str,
        root_path: Path,
        mount: WorkspaceMountRecord,
    ) -> None:
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            cached.refreshing = True

        def refresh() -> None:
            try:
                candidates = self._build_search_candidates(
                    root_path=root_path,
                    mount=mount,
                    timeout_seconds=None,
                )
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                log_event(
                    _logger,
                    30,
                    event="workspace.search_index_refresh_failed",
                    message="Failed to refresh workspace search index",
                    payload={
                        "mount_name": mount.mount_name,
                        "root_path": str(root_path),
                        "detail": str(exc),
                    },
                )
                with self._search_cache_lock:
                    failed = self._search_cache.get(cache_key)
                    if failed is not None:
                        failed.refreshing = False
                return
            with self._search_cache_lock:
                self._store_search_cache_locked(
                    cache_key=cache_key,
                    candidates=candidates,
                )

        thread = threading.Thread(
            target=refresh,
            name="workspace-search-index-refresh",
            daemon=True,
        )
        thread.start()

    def _store_search_cache_locked(
        self,
        *,
        cache_key: str,
        candidates: tuple[WorkspaceSearchResult, ...],
    ) -> None:
        self._search_cache[cache_key] = _WorkspaceSearchCacheEntry(
            candidates=candidates,
            indexed_at=time.monotonic(),
            refreshing=False,
        )
        if len(self._search_cache) > 16:
            oldest_key = min(
                self._search_cache,
                key=lambda key: self._search_cache[key].indexed_at,
            )
            self._search_cache.pop(oldest_key, None)

    def _rank_search_candidates(
        self,
        *,
        candidates: tuple[WorkspaceSearchResult, ...],
        query: str,
        limit: int,
    ) -> tuple[WorkspaceSearchResult, ...]:
        prefer_hidden = query.startswith(".") or "/." in query
        scored: list[tuple[tuple[int, int, int, int, str], WorkspaceSearchResult]] = []
        for index, candidate in enumerate(candidates):
            score = self._score_search_candidate(
                candidate=candidate,
                query=query,
                index=index,
                prefer_hidden=prefer_hidden,
            )
            if score is not None:
                scored.append((score, candidate))
        scored.sort(key=lambda item: item[0])
        return tuple(candidate for _score, candidate in scored[:limit])

    def _score_search_candidate(
        self,
        *,
        candidate: WorkspaceSearchResult,
        query: str,
        index: int,
        prefer_hidden: bool,
    ) -> Optional[tuple[int, int, int, int, str]]:
        candidate_path = candidate.path.casefold()
        candidate_name = candidate.name.casefold()
        hidden_rank = (
            0 if prefer_hidden or not self._is_hidden_search_path(candidate.path) else 1
        )
        depth = candidate.path.count("/")
        kind_rank = 0 if candidate.kind == WorkspaceTreeNodeKind.DIRECTORY else 1
        if query.endswith("/"):
            if candidate_path == query:
                return None
            if candidate_path.startswith(query):
                child_path = candidate_path[len(query) :].strip("/")
                if not child_path:
                    return None
                child_depth = child_path.count("/")
                return hidden_rank, 0, child_depth, kind_rank, candidate_path
        if not query:
            return hidden_rank, kind_rank, depth, index, candidate_path
        if candidate_path == query or candidate_name == query:
            return hidden_rank, 0, depth, index, candidate_path
        if candidate_path.startswith(query) or candidate_name.startswith(query):
            return hidden_rank, 1, depth, index, candidate_path
        if query in candidate_path or query in candidate_name:
            return hidden_rank, 2, depth, index, candidate_path
        fuzzy_score = self._fuzzy_subsequence_score(query=query, target=candidate_path)
        if fuzzy_score is None:
            return None
        return hidden_rank, 3, fuzzy_score, depth, candidate_path

    @staticmethod
    def _fuzzy_subsequence_score(
        *,
        query: str,
        target: str,
    ) -> Optional[int]:
        target_index = 0
        score = 0
        last_match = -1
        for char in query:
            found_index = target.find(char, target_index)
            if found_index < 0:
                return None
            score += found_index - target_index
            if last_match >= 0 and found_index == last_match + 1:
                score -= 1
            last_match = found_index
            target_index = found_index + 1
        return max(score, 0)

    @staticmethod
    def _is_hidden_search_path(path: str) -> bool:
        return any(
            part.startswith(".") and len(part) > 1
            for part in path.strip("/").split("/")
        )

    def _get_ssh_workspace_tree_listing(
        self,
        *,
        record: WorkspaceRecord,
        mount: WorkspaceMountRecord,
        directory_path: str,
    ) -> WorkspaceTreeListing:
        if self._ssh_profile_service is None:
            raise ValueError(
                f"Workspace ssh mount cannot list files without ssh profiles: {mount.mount_name}"
            )
        provider_config = mount.provider_config
        if not isinstance(provider_config, WorkspaceSshMountConfig):
            raise ValueError(
                f"Workspace ssh mount is missing ssh config: {mount.mount_name}"
            )
        remote_path, normalized_directory_path = self._resolve_ssh_tree_path(
            mount=mount,
            directory_path=directory_path,
        )
        result = self._ssh_profile_service.run_remote_command(
            ssh_profile_id=provider_config.ssh_profile_id,
            command=self._build_ssh_tree_list_command(remote_path),
            timeout_seconds=_SSH_TREE_LIST_TIMEOUT_SECONDS,
        )
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout).strip()
            if _SSH_TREE_NOT_DIRECTORY_MARKER in detail:
                raise ValueError(f"Workspace path is not a directory: {directory_path}")
            raise ValueError(
                "Failed to list workspace ssh mount "
                f"{mount.mount_name}: {detail or f'exit code {result.exit_code}'}"
            )
        return WorkspaceTreeListing(
            workspace_id=record.workspace_id,
            mount_name=mount.mount_name,
            directory_path=normalized_directory_path,
            children=self._parse_ssh_tree_entries(
                mount=mount,
                directory_path=normalized_directory_path,
                output=result.stdout,
            ),
        )

    def get_workspace_diffs(
        self,
        workspace_id: str,
        *,
        mount_name: Optional[str] = None,
    ) -> WorkspaceDiffListing:
        record = self._repository.get(workspace_id)
        mount = self._resolve_mount(record, mount_name)
        capabilities = self._mount_capabilities(mount)
        if not capabilities.can_diff:
            return WorkspaceDiffListing(
                workspace_id=record.workspace_id,
                mount_name=mount.mount_name,
                root_path=mount.root_reference,
                diff_files=(),
                is_git_repository=False,
                git_root_path=None,
                diff_message=f"Workspace mount does not support diff: {mount.mount_name}",
            )
        if mount.provider != WorkspaceMountProvider.LOCAL:
            return WorkspaceDiffListing(
                workspace_id=record.workspace_id,
                mount_name=mount.mount_name,
                root_path=mount.root_reference,
                diff_files=(),
                is_git_repository=False,
                git_root_path=None,
                diff_message=f"Workspace mount provider is not yet diff-enabled: {mount.mount_name}",
            )
        root_path = self._resolve_local_mount_root(mount)
        try:
            git_root_path = self._resolve_git_root(root_path)
        except ValueError as exc:
            return WorkspaceDiffListing(
                workspace_id=record.workspace_id,
                mount_name=mount.mount_name,
                root_path=root_path,
                diff_files=(),
                is_git_repository=False,
                git_root_path=None,
                diff_message=str(exc),
            )

        has_head = self._git_head_exists(root_path)
        try:
            candidates = self._list_diff_candidates(
                workspace_root=root_path,
                has_head=has_head,
            )
            diff_files = tuple(
                WorkspaceDiffFileSummary(
                    path=candidate.path,
                    change_type=candidate.change_type,
                    previous_path=candidate.previous_path,
                )
                for candidate in candidates
            )
        except ValueError as exc:
            log_event(
                _logger,
                30,
                event="workspace.snapshot.diff_failed",
                message="Failed to collect workspace diff summary",
                payload={
                    "workspace_root": str(root_path),
                    "detail": str(exc),
                },
            )
            return WorkspaceDiffListing(
                workspace_id=record.workspace_id,
                mount_name=mount.mount_name,
                root_path=root_path,
                diff_files=(),
                is_git_repository=True,
                git_root_path=git_root_path,
                diff_message=str(exc),
            )

        return WorkspaceDiffListing(
            workspace_id=record.workspace_id,
            mount_name=mount.mount_name,
            root_path=root_path,
            diff_files=diff_files,
            is_git_repository=True,
            git_root_path=git_root_path,
            diff_message=None,
        )

    def get_workspace_diff_file(
        self,
        workspace_id: str,
        *,
        path: str,
        mount_name: Optional[str] = None,
    ) -> WorkspaceDiffFile:
        record = self._repository.get(workspace_id)
        mount = self._resolve_mount(record, mount_name)
        capabilities = self._mount_capabilities(mount)
        if not capabilities.can_diff:
            raise ValueError(
                f"Workspace mount does not support diff: {mount.mount_name}"
            )
        if mount.provider != WorkspaceMountProvider.LOCAL:
            raise ValueError(
                f"Workspace mount provider is not yet diff-enabled: {mount.mount_name}"
            )
        root_path = self._resolve_local_mount_root(mount)
        normalized_path = self._normalize_workspace_relative_path(path)

        _ = self._resolve_git_root(root_path)
        has_head = self._git_head_exists(root_path)
        candidates = self._list_diff_candidates(
            workspace_root=root_path,
            has_head=has_head,
        )
        for candidate in candidates:
            if candidate.path == normalized_path:
                return self._build_diff_file(
                    mount_name=mount.mount_name,
                    workspace_root=root_path,
                    candidate=candidate,
                    has_head=has_head,
                )
        raise ValueError(f"Workspace diff file not found: {path}")

    def get_workspace_image_preview_file(
        self,
        workspace_id: str,
        *,
        path: str,
        mount_name: Optional[str] = None,
    ) -> tuple[Path, str]:
        record = self._repository.get(workspace_id)
        mount = self._resolve_mount(record, mount_name)
        capabilities = self._mount_capabilities(mount)
        if not capabilities.can_preview:
            raise ValueError(
                f"Workspace mount does not support preview: {mount.mount_name}"
            )
        if mount.provider != WorkspaceMountProvider.LOCAL:
            raise ValueError(
                f"Workspace mount provider is not yet preview-enabled: {mount.mount_name}"
            )
        root_path = self._resolve_local_mount_root(mount)
        resolved_path = self._resolve_workspace_file_path(
            root_path=root_path,
            file_path=path,
        )
        if not path_exists(resolved_path) or not path_is_file(resolved_path):
            raise FileNotFoundError(f"Workspace file not found: {path}")

        media_type, _ = mimetypes.guess_type(resolved_path.name)
        if media_type not in _WORKSPACE_IMAGE_MEDIA_TYPES:
            raise ValueError(f"Workspace file is not a supported image: {path}")
        return resolved_path, media_type

    def list_workspaces(self) -> tuple[WorkspaceRecord, ...]:
        return self._repository.list_all()

    def delete_workspace(self, workspace_id: str) -> None:
        _ = self.delete_workspace_with_options(
            workspace_id=workspace_id,
            remove_directory=False,
        )

    def delete_workspace_with_options(
        self,
        *,
        workspace_id: str,
        remove_directory: bool,
    ) -> WorkspaceRecord:
        record = self._repository.get(workspace_id)
        if remove_directory:
            self._remove_workspace_directory(record)
        self._repository.delete(workspace_id)
        return record

    def fork_workspace(
        self,
        *,
        source_workspace_id: str,
        name: str,
        start_ref: str | None = None,
    ) -> WorkspaceRecord:
        source_record = self._repository.get(source_workspace_id)
        source_mount = self._primary_local_mount(source_record)
        if source_mount is None:
            raise ValueError("Workspace has no local mount to fork")
        source_root = self._resolve_local_mount_root(source_mount)
        normalized_workspace_id = self._normalize_workspace_id(name)
        if self._repository.exists(normalized_workspace_id):
            raise ValueError(f"Workspace already exists: {normalized_workspace_id}")

        repository_root = self._git_worktree_client.ensure_repository(source_root)
        if start_ref is None:
            try:
                self._git_worktree_client.fetch_ref(
                    repository_root, remote="origin", ref="main"
                )
            except ValueError as exc:
                if "timed out" not in str(exc).lower():
                    raise
                log_event(
                    _logger,
                    30,
                    event="workspace.fork.fetch_ref_timeout_fallback",
                    message="Falling back to cached origin/main after git fetch timeout",
                    payload={
                        "repository_root": str(repository_root),
                        "source_workspace_id": source_workspace_id,
                    },
                )
            resolved_start_ref = "origin/main"
        else:
            resolved_start_ref = start_ref
        start_point = self._git_worktree_client.resolve_ref(
            repository_root,
            resolved_start_ref,
        )
        target_path = self._workspace_storage_dir(normalized_workspace_id) / "worktree"
        if path_exists(target_path):
            raise ValueError(f"Workspace root already exists: {target_path}")

        branch_name = f"fork/{normalized_workspace_id}"
        self._git_worktree_client.add_worktree(
            repository_root=repository_root,
            branch_name=branch_name,
            target_path=target_path,
            start_point=start_point,
        )
        try:
            return self.create_workspace(
                workspace_id=normalized_workspace_id,
                mounts=(
                    legacy_workspace_mount_from_profile(
                        root_path=target_path,
                        profile=WorkspaceProfile(),
                        mount_name="default",
                    ).model_copy(
                        update={
                            "branch_name": branch_name,
                            "source_root_path": str(repository_root),
                            "forked_from_workspace_id": source_workspace_id,
                        }
                    ),
                ),
                default_mount_name="default",
            )
        except Exception:
            self._git_worktree_client.remove_worktree(
                repository_root=repository_root,
                target_path=target_path,
            )
            self._git_worktree_client.prune(repository_root)
            shutil.rmtree(
                self._workspace_storage_dir(normalized_workspace_id),
                ignore_errors=True,
            )
            raise

    def require_workspace(self, workspace_id: str) -> WorkspaceRecord:
        return self.get_workspace(workspace_id)

    def _validate_root(self, root_path: Path) -> Path:
        resolved_root = root_path.resolve()
        if not path_exists(resolved_root):
            raise ValueError(f"Workspace root does not exist: {resolved_root}")
        if not path_is_dir(resolved_root):
            raise ValueError(f"Workspace root is not a directory: {resolved_root}")
        return resolved_root

    def _validate_local_mount_scope_paths(
        self,
        *,
        mount: WorkspaceMountRecord,
        root_path: Path,
    ) -> None:
        self._validate_mount_relative_root(root_path, mount.working_directory)
        for relative_path in mount.readable_paths:
            self._validate_mount_relative_root(root_path, relative_path)
        for relative_path in mount.writable_paths:
            self._validate_mount_relative_root(root_path, relative_path)

    def _validate_mount_relative_root(
        self, root_path: Path, relative_path: str
    ) -> None:
        candidate = (root_path / relative_path).resolve()
        resolved_root = root_path.resolve()
        if candidate != resolved_root and resolved_root not in candidate.parents:
            raise ValueError(
                f"Workspace file scope escapes mount root: {relative_path}"
            )

    def _validate_default_mount(
        self,
        *,
        mounts: tuple[WorkspaceMountRecord, ...],
        default_mount_name: str,
    ) -> None:
        for mount in mounts:
            if mount.mount_name != default_mount_name:
                continue
            return
        raise ValueError(f"default mount does not exist: {default_mount_name}")

    def _resolve_mount(
        self,
        record: WorkspaceRecord,
        mount_name: str | None,
    ) -> WorkspaceMountRecord:
        if mount_name is None:
            return record.default_mount
        return record.mount_by_name(mount_name)

    def _resolve_search_mount(
        self,
        *,
        record: WorkspaceRecord,
        mount_name: str | None,
    ) -> WorkspaceMountRecord:
        requested_mount = self._resolve_mount(record, mount_name)
        if requested_mount.provider == WorkspaceMountProvider.LOCAL:
            return requested_mount
        if mount_name is not None:
            raise ValueError(
                f"Workspace mount is not local: {requested_mount.mount_name}"
            )
        fallback_mount = record.first_local_mount()
        if fallback_mount is None:
            raise ValueError("Workspace path search requires a local workspace root")
        return fallback_mount

    def _primary_local_mount(
        self, record: WorkspaceRecord
    ) -> WorkspaceMountRecord | None:
        default_mount = record.default_mount
        if default_mount.provider == WorkspaceMountProvider.LOCAL:
            return default_mount
        return record.first_local_mount()

    def _resolve_local_mount_root(self, mount: WorkspaceMountRecord) -> Path:
        if mount.provider != WorkspaceMountProvider.LOCAL:
            raise ValueError(f"Workspace mount is not local: {mount.mount_name}")
        root_path = mount.local_root_path()
        if root_path is None:
            raise ValueError(
                f"Workspace local mount is missing root path: {mount.mount_name}"
            )
        return self._validate_root(root_path)

    def _mount_has_children(self, mount: WorkspaceMountRecord) -> bool:
        if mount.provider != WorkspaceMountProvider.LOCAL:
            return True
        root_path = mount.local_root_path()
        if root_path is None or not path_exists(root_path):
            return False
        return len(self._iter_tree_entries(root_path.resolve())) > 0

    def _remove_workspace_directory(self, record: WorkspaceRecord) -> None:
        removal_targets: list[Path] = []
        for mount in record.mounts:
            if mount.provider != WorkspaceMountProvider.LOCAL:
                raise RuntimeError(
                    f"Cannot remove directory for non-local workspace mount: {mount.mount_name}"
                )
            root_path = self._resolve_local_mount_root(mount)
            if mount.source_root_path is not None:
                repository_root = self._resolve_worktree_repository_root(mount)
                self._git_worktree_client.remove_worktree(
                    repository_root=repository_root,
                    target_path=root_path,
                )
                self._git_worktree_client.prune(repository_root)
                removal_targets.append(self._workspace_storage_dir(record.workspace_id))
                continue
            removal_targets.append(root_path)
        try:
            seen: set[Path] = set()
            for target_path in removal_targets:
                resolved = target_path.resolve()
                if resolved in seen:
                    continue
                self._remove_filesystem_path(resolved)
                seen.add(resolved)
        except OSError as exc:
            failed_target = (
                removal_targets[0]
                if len(removal_targets) > 0
                else Path(record.workspace_id)
            )
            raise RuntimeError(
                f"Failed to remove workspace path: {failed_target}"
            ) from exc

    def _remove_filesystem_path(self, target_path: Path) -> None:
        if not path_exists(target_path):
            return
        if path_is_file(target_path):
            unlink_path(target_path, missing_ok=True)
            return
        shutil.rmtree(target_path)

    def _find_workspace_by_root(self, root_path: Path) -> WorkspaceRecord | None:
        for workspace in self._repository.list_all():
            for mount in workspace.mounts:
                mount_root = mount.local_root_path()
                if mount_root is not None and mount_root == root_path:
                    return workspace
        return None

    def _next_workspace_id_for_root(self, root_path: Path) -> str:
        base_name = root_path.name.strip() or "project"
        normalized_base = self._normalize_workspace_id(base_name)
        existing_ids = {
            workspace.workspace_id for workspace in self._repository.list_all()
        }
        if normalized_base not in existing_ids:
            return normalized_base

        suffix = 2
        while True:
            candidate = f"{normalized_base}-{suffix}"
            if candidate not in existing_ids:
                return candidate
            suffix += 1

    def _normalize_workspace_id(self, value: str) -> str:
        base_id = _NON_WORKSPACE_ID_CHARS.sub("-", value.strip().lower()).strip("-")
        normalized = base_id or "project"
        return normalized

    def _workspace_storage_dir(self, workspace_id: str) -> Path:
        config_dir = get_project_config_dir()
        return config_dir / "workspaces" / workspace_id

    def _resolve_worktree_repository_root(self, mount: WorkspaceMountRecord) -> Path:
        source_root_path = mount.source_root_path
        if not source_root_path:
            raise ValueError(
                f"Workspace mount {mount.mount_name} is missing worktree source_root_path"
            )
        return Path(source_root_path).expanduser().resolve()

    def _build_tree_node(
        self,
        *,
        root_path: Path,
        current_path: Path,
        include_children: bool,
    ) -> WorkspaceTreeNode:
        path_text = "."
        if current_path != root_path:
            path_text = current_path.relative_to(root_path).as_posix()
        is_directory = path_is_dir(current_path) and not current_path.is_symlink()
        has_children = False
        children: tuple[WorkspaceTreeNode, ...] = ()
        if is_directory:
            entries = self._iter_tree_entries(current_path)
            has_children = len(entries) > 0
            if include_children:
                children = tuple(
                    self._build_tree_node(
                        root_path=root_path,
                        current_path=child_path,
                        include_children=False,
                    )
                    for child_path in entries
                )
        return WorkspaceTreeNode(
            name=current_path.name or root_path.anchor or ".",
            path=path_text,
            kind=(
                WorkspaceTreeNodeKind.DIRECTORY
                if is_directory
                else WorkspaceTreeNodeKind.FILE
            ),
            has_children=has_children,
            children=children,
        )

    def _resolve_ssh_tree_path(
        self,
        *,
        mount: WorkspaceMountRecord,
        directory_path: str,
    ) -> tuple[str, str]:
        provider_config = mount.provider_config
        if not isinstance(provider_config, WorkspaceSshMountConfig):
            raise ValueError(
                f"Workspace ssh mount is missing ssh config: {mount.mount_name}"
            )
        remote_root = posixpath.normpath(provider_config.remote_root.strip())
        if not posixpath.isabs(remote_root):
            raise ValueError(
                f"Workspace ssh mount remote root must be absolute: {mount.mount_name}"
            )

        normalized_input = directory_path.strip().replace("\\", "/") or "."
        if posixpath.isabs(normalized_input):
            raise ValueError(f"Workspace path must be relative: {directory_path}")
        normalized_path = posixpath.normpath(normalized_input)
        if normalized_path == ".." or normalized_path.startswith("../"):
            raise ValueError(f"Workspace path escapes root: {directory_path}")

        remote_path = posixpath.normpath(posixpath.join(remote_root, normalized_path))
        if remote_root != "/":
            root_prefix = remote_root.rstrip("/")
            if remote_path != remote_root and not remote_path.startswith(
                root_prefix + "/"
            ):
                raise ValueError(f"Workspace path escapes root: {directory_path}")
        normalized_directory_path = (
            "."
            if remote_path == remote_root
            else posixpath.relpath(remote_path, remote_root)
        )
        return remote_path, normalized_directory_path

    def _build_ssh_tree_list_command(self, remote_path: str) -> str:
        return (
            f"sh -c {shlex.quote(_SSH_TREE_LIST_SCRIPT)} sh {shlex.quote(remote_path)}"
        )

    def _parse_ssh_tree_entries(
        self,
        *,
        mount: WorkspaceMountRecord,
        directory_path: str,
        output: str,
    ) -> tuple[WorkspaceTreeNode, ...]:
        nodes: list[WorkspaceTreeNode] = []
        for raw_line in output.splitlines():
            line = raw_line.rstrip("\n")
            if not line:
                continue
            parts = line.split(_SSH_TREE_ENTRY_SEPARATOR, maxsplit=2)
            if len(parts) != 3:
                raise ValueError(
                    f"Workspace ssh mount returned malformed tree entry: {mount.mount_name}"
                )
            kind_text, has_children_text, name = parts
            if not name or "/" in name or name in {".", ".."}:
                raise ValueError(
                    f"Workspace ssh mount returned invalid tree entry: {mount.mount_name}"
                )
            if kind_text == WorkspaceTreeNodeKind.DIRECTORY.value:
                kind = WorkspaceTreeNodeKind.DIRECTORY
            elif kind_text == WorkspaceTreeNodeKind.FILE.value:
                kind = WorkspaceTreeNodeKind.FILE
            else:
                raise ValueError(
                    f"Workspace ssh mount returned unknown tree entry kind: {mount.mount_name}"
                )
            child_path = name
            if directory_path != ".":
                child_path = posixpath.join(directory_path, name)
            nodes.append(
                WorkspaceTreeNode(
                    name=name,
                    path=child_path,
                    kind=kind,
                    has_children=has_children_text == "1",
                    children=(),
                )
            )
        return tuple(
            sorted(
                nodes,
                key=lambda node: (
                    0 if node.kind == WorkspaceTreeNodeKind.DIRECTORY else 1,
                    node.name.casefold(),
                ),
            )
        )

    def _resolve_tree_path(self, *, root_path: Path, directory_path: str) -> Path:
        normalized_path = directory_path.strip() or "."
        candidate = Path(normalized_path)
        if candidate.is_absolute():
            raise ValueError(f"Workspace path must be relative: {directory_path}")
        resolved_path = (root_path / candidate).resolve()
        if resolved_path != root_path and root_path not in resolved_path.parents:
            raise ValueError(f"Workspace path escapes root: {directory_path}")
        return resolved_path

    def _normalize_workspace_relative_path(self, path: str) -> str:
        normalized_path = str(path).strip().replace("\\", "/")
        if not normalized_path or normalized_path == ".":
            raise ValueError("Workspace path must not be empty")
        candidate = Path(normalized_path)
        if candidate.is_absolute():
            raise ValueError(f"Workspace path must be relative: {path}")
        normalized_parts = tuple(
            part for part in candidate.parts if part not in {"", "."}
        )
        if not normalized_parts or any(part == ".." for part in normalized_parts):
            raise ValueError(f"Workspace path escapes root: {path}")
        return Path(*normalized_parts).as_posix()

    def _resolve_workspace_file_path(self, *, root_path: Path, file_path: str) -> Path:
        raw_path = str(file_path).strip()
        if not raw_path or raw_path == ".":
            raise ValueError("Workspace path must not be empty")

        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            resolved_path = candidate.resolve()
        else:
            normalized_path = self._normalize_workspace_relative_path(raw_path)
            resolved_path = (root_path / normalized_path).resolve()

        if resolved_path == root_path or root_path not in resolved_path.parents:
            raise ValueError(f"Workspace path escapes root: {file_path}")
        return resolved_path

    def _iter_tree_entries(self, current_path: Path) -> tuple[Path, ...]:
        try:
            entries = tuple(
                child
                for child in iter_dir_paths(current_path)
                if child.name not in {".git"}
            )
        except OSError as exc:
            log_event(
                _logger,
                30,
                event="workspace.snapshot.tree_read_failed",
                message="Failed to inspect workspace directory entry",
                payload={
                    "path": str(current_path),
                    "detail": str(exc),
                },
            )
            return ()
        return tuple(
            sorted(
                entries,
                key=lambda child: (
                    0 if path_is_dir(child) and not child.is_symlink() else 1,
                    child.name.casefold(),
                ),
            )
        )

    def _list_diff_candidates(
        self,
        *,
        workspace_root: Path,
        has_head: bool,
    ) -> tuple[_WorkspaceDiffCandidate, ...]:
        candidates_by_path: dict[str, _WorkspaceDiffCandidate] = {}
        tracked_candidates = self._parse_name_status_output(
            self._tracked_diff_output(
                workspace_root=workspace_root,
                has_head=has_head,
            )
        )
        for candidate in tracked_candidates:
            candidates_by_path[candidate.path] = candidate

        for rel_path in self._untracked_paths(workspace_root):
            if rel_path in candidates_by_path:
                continue
            candidates_by_path[rel_path] = _WorkspaceDiffCandidate(
                path=rel_path,
                change_type=WorkspaceDiffChangeType.UNTRACKED,
            )

        return tuple(
            sorted(candidates_by_path.values(), key=lambda candidate: candidate.path)
        )

    def _tracked_diff_output(self, *, workspace_root: Path, has_head: bool) -> str:
        if has_head:
            completed = self._run_git(
                ("diff", "--name-status", "--find-renames", "HEAD", "--"),
                cwd=workspace_root,
            )
            stdout = completed.stdout
            if not isinstance(stdout, str):
                raise ValueError("Git returned non-text diff status output")
            return stdout
        completed = self._run_git(
            ("diff", "--cached", "--name-status", "--find-renames", "--"),
            cwd=workspace_root,
        )
        stdout = completed.stdout
        if not isinstance(stdout, str):
            raise ValueError("Git returned non-text diff status output")
        return stdout

    def _untracked_paths(self, workspace_root: Path) -> tuple[str, ...]:
        completed = self._run_git(
            ("ls-files", "--others", "--exclude-standard"),
            cwd=workspace_root,
        )
        stdout = completed.stdout
        if not isinstance(stdout, str):
            raise ValueError("Git returned non-text untracked path output")
        return tuple(line.strip() for line in stdout.splitlines() if line.strip())

    def _parse_name_status_output(
        self,
        output: str,
    ) -> tuple[_WorkspaceDiffCandidate, ...]:
        candidates: list[_WorkspaceDiffCandidate] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t")
            status = parts[0]
            if status.startswith("R") and len(parts) >= 3:
                candidates.append(
                    _WorkspaceDiffCandidate(
                        path=parts[2],
                        change_type=WorkspaceDiffChangeType.RENAMED,
                        previous_path=parts[1],
                    )
                )
                continue
            if status.startswith("C") and len(parts) >= 3:
                candidates.append(
                    _WorkspaceDiffCandidate(
                        path=parts[2],
                        change_type=WorkspaceDiffChangeType.COPIED,
                        previous_path=parts[1],
                    )
                )
                continue
            if len(parts) < 2:
                continue
            change_type = self._map_git_change_type(status)
            if change_type is None:
                continue
            candidates.append(
                _WorkspaceDiffCandidate(
                    path=parts[1],
                    change_type=change_type,
                )
            )
        return tuple(candidates)

    def _map_git_change_type(
        self,
        status: str,
    ) -> WorkspaceDiffChangeType | None:
        code = status[:1]
        if code == "A":
            return WorkspaceDiffChangeType.ADDED
        if code == "M":
            return WorkspaceDiffChangeType.MODIFIED
        if code == "D":
            return WorkspaceDiffChangeType.DELETED
        if code == "U":
            return WorkspaceDiffChangeType.CONFLICTED
        if code == "T":
            return WorkspaceDiffChangeType.TYPE_CHANGED
        return None

    def _build_diff_file(
        self,
        *,
        mount_name: str,
        workspace_root: Path,
        candidate: _WorkspaceDiffCandidate,
        has_head: bool,
    ) -> WorkspaceDiffFile:
        current_path = workspace_root / Path(candidate.path)
        base_path = candidate.previous_path or candidate.path
        base_bytes = None
        if has_head and candidate.change_type != WorkspaceDiffChangeType.UNTRACKED:
            base_bytes = self._read_git_blob_bytes(workspace_root, base_path)
        current_bytes = self._read_workspace_bytes(current_path)

        if candidate.change_type == WorkspaceDiffChangeType.DELETED:
            current_bytes = b""
        if current_bytes is None:
            current_bytes = b""

        is_binary = self._is_binary_bytes(base_bytes) or self._is_binary_bytes(
            current_bytes
        )
        diff_text = (
            _BINARY_DIFF_MESSAGE
            if is_binary
            else self._build_unified_diff(
                before_path=base_path,
                after_path=candidate.path,
                before_text=self._decode_bytes(base_bytes),
                after_text=self._decode_bytes(current_bytes),
            )
        )
        return WorkspaceDiffFile(
            mount_name=mount_name,
            path=candidate.path,
            previous_path=candidate.previous_path,
            change_type=candidate.change_type,
            diff=diff_text,
            is_binary=is_binary,
        )

    def _resolve_git_root(self, workspace_root: Path) -> Path:
        completed = self._run_git(
            ("rev-parse", "--show-toplevel"),
            cwd=workspace_root,
        )
        stdout = completed.stdout
        if not isinstance(stdout, str):
            raise ValueError("Git returned non-text output for git root")
        return Path(stdout.strip()).expanduser().resolve()

    def _git_head_exists(self, workspace_root: Path) -> bool:
        try:
            _ = self._run_git(("rev-parse", "--verify", "HEAD"), cwd=workspace_root)
        except ValueError:
            return False
        return True

    def _read_git_blob_bytes(
        self,
        workspace_root: Path,
        relative_path: str,
    ) -> bytes | None:
        try:
            completed = self._run_git(
                ("show", f"HEAD:{relative_path.replace(chr(92), '/')}"),
                cwd=workspace_root,
                text=False,
            )
        except ValueError:
            return None
        return completed.stdout if isinstance(completed.stdout, bytes) else None

    def _read_workspace_bytes(self, path: Path) -> bytes | None:
        if not path_exists(path) or not path_is_file(path):
            return None
        try:
            return read_bytes_file(path)
        except OSError as exc:
            log_event(
                _logger,
                30,
                event="workspace.snapshot.file_read_failed",
                message="Failed to read workspace file while building diff",
                payload={
                    "path": str(path),
                    "detail": str(exc),
                },
            )
            return None

    def _is_binary_bytes(self, content: bytes | None) -> bool:
        if content is None:
            return False
        if b"\0" in content:
            return True
        try:
            _ = content.decode("utf-8")
        except UnicodeDecodeError:
            return True
        return False

    def _decode_bytes(self, content: bytes | None) -> str:
        if content is None:
            return ""
        return content.decode("utf-8")

    def _build_unified_diff(
        self,
        *,
        before_path: str,
        after_path: str,
        before_text: str,
        after_text: str,
    ) -> str:
        diff_lines = list(
            difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"a/{before_path}",
                tofile=f"b/{after_path}",
                lineterm="",
            )
        )
        if not diff_lines:
            return f"--- a/{before_path}\n+++ b/{after_path}"
        return "\n".join(diff_lines)

    def _run_git(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        git_binary = shutil.which("git")
        if git_binary is None:
            raise ValueError("Git executable is not available")

        command = [git_binary, *args]
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                check=False,
                capture_output=True,
                text=text,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except OSError as exc:
            raise ValueError(f"Failed to execute git: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Git command timed out") from exc

        if completed.returncode != 0:
            stderr = (
                completed.stderr.strip()
                if isinstance(completed.stderr, str)
                else completed.stderr.decode("utf-8", errors="ignore").strip()
            )
            stdout = (
                completed.stdout.strip()
                if isinstance(completed.stdout, str)
                else completed.stdout.decode("utf-8", errors="ignore").strip()
            )
            detail = stderr or stdout or "unknown git error"
            raise ValueError(f"Git command failed: {detail}")
        return completed
