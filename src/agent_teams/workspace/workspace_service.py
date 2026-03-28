# -*- coding: utf-8 -*-
from __future__ import annotations

import difflib
import mimetypes
import re
import shutil
import subprocess
from pathlib import Path

from agent_teams.logger import get_logger, log_event
from agent_teams.paths import get_project_config_dir
from agent_teams.workspace.git_worktree import GitWorktreeClient
from agent_teams.workspace.workspace_models import (
    BranchBinding,
    FileScopeBackend,
    WorkspaceDiffChangeType,
    WorkspaceDiffFile,
    WorkspaceDiffFileSummary,
    WorkspaceDiffListing,
    WorkspaceFileScope,
    WorkspaceProfile,
    WorkspaceRecord,
    WorkspaceSnapshot,
    WorkspaceTreeListing,
    WorkspaceTreeNode,
    WorkspaceTreeNodeKind,
)
from agent_teams.workspace.workspace_repository import WorkspaceRepository


_NON_WORKSPACE_ID_CHARS = re.compile(r"[^a-z0-9]+")
_GIT_TIMEOUT_SECONDS = 30.0
_BINARY_DIFF_MESSAGE = "Binary file changed"
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


class WorkspaceService:
    def __init__(
        self,
        *,
        repository: WorkspaceRepository,
        git_worktree_client: GitWorktreeClient | None = None,
    ) -> None:
        self._repository = repository
        self._git_worktree_client = git_worktree_client or GitWorktreeClient()

    def create_workspace(
        self,
        *,
        workspace_id: str,
        root_path: Path,
        profile: WorkspaceProfile | None = None,
    ) -> WorkspaceRecord:
        resolved_root = self._validate_root(root_path)
        if self._repository.exists(workspace_id):
            raise ValueError(f"Workspace already exists: {workspace_id}")
        return self._repository.create(
            workspace_id=workspace_id,
            root_path=resolved_root,
            profile=profile,
        )

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
        return self._repository.create(
            workspace_id=workspace_id,
            root_path=resolved_root,
            profile=profile,
        )

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord:
        return self._repository.get(workspace_id)

    def get_workspace_snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
        record = self._repository.get(workspace_id)
        root_path = self._validate_root(record.root_path)
        tree = self._build_tree_node(
            root_path=root_path,
            current_path=root_path,
            include_children=True,
        )
        return WorkspaceSnapshot(
            workspace_id=record.workspace_id,
            root_path=root_path,
            tree=tree,
        )

    def get_workspace_tree_listing(
        self,
        workspace_id: str,
        *,
        directory_path: str,
    ) -> WorkspaceTreeListing:
        record = self._repository.get(workspace_id)
        root_path = self._validate_root(record.root_path)
        target_path = self._resolve_tree_path(
            root_path=root_path, directory_path=directory_path
        )
        if not target_path.is_dir() or target_path.is_symlink():
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
            directory_path=normalized_directory_path,
            children=children,
        )

    def get_workspace_diffs(self, workspace_id: str) -> WorkspaceDiffListing:
        record = self._repository.get(workspace_id)
        root_path = self._validate_root(record.root_path)
        try:
            git_root_path = self._resolve_git_root(root_path)
        except ValueError as exc:
            return WorkspaceDiffListing(
                workspace_id=record.workspace_id,
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
                root_path=root_path,
                diff_files=(),
                is_git_repository=True,
                git_root_path=git_root_path,
                diff_message=str(exc),
            )

        return WorkspaceDiffListing(
            workspace_id=record.workspace_id,
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
    ) -> WorkspaceDiffFile:
        record = self._repository.get(workspace_id)
        root_path = self._validate_root(record.root_path)
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
    ) -> tuple[Path, str]:
        record = self._repository.get(workspace_id)
        root_path = self._validate_root(record.root_path)
        resolved_path = self._resolve_workspace_file_path(
            root_path=root_path,
            file_path=path,
        )
        if not resolved_path.exists() or not resolved_path.is_file():
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
            remove_worktree=False,
        )

    def delete_workspace_with_options(
        self,
        *,
        workspace_id: str,
        remove_worktree: bool,
    ) -> WorkspaceRecord:
        record = self._repository.get(workspace_id)
        if (
            remove_worktree
            and record.profile.file_scope.backend == FileScopeBackend.GIT_WORKTREE
        ):
            repository_root = self._resolve_worktree_repository_root(record)
            self._git_worktree_client.remove_worktree(
                repository_root=repository_root,
                target_path=record.root_path,
            )
            self._git_worktree_client.prune(repository_root)
            shutil.rmtree(self._workspace_storage_dir(workspace_id), ignore_errors=True)
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
        normalized_workspace_id = self._normalize_workspace_id(name)
        if self._repository.exists(normalized_workspace_id):
            raise ValueError(f"Workspace already exists: {normalized_workspace_id}")

        repository_root = self._git_worktree_client.ensure_repository(
            source_record.root_path
        )
        if start_ref is None:
            self._git_worktree_client.fetch_ref(repository_root, remote="origin", ref="main")
            resolved_start_ref = "origin/main"
        else:
            resolved_start_ref = start_ref
        start_point = self._git_worktree_client.resolve_ref(
            repository_root,
            resolved_start_ref,
        )
        target_path = self._workspace_storage_dir(normalized_workspace_id) / "worktree"
        if target_path.exists():
            raise ValueError(f"Workspace root already exists: {target_path}")

        branch_name = f"fork/{normalized_workspace_id}"
        self._git_worktree_client.add_worktree(
            repository_root=repository_root,
            branch_name=branch_name,
            target_path=target_path,
            start_point=start_point,
        )

        profile = WorkspaceProfile(
            file_scope=WorkspaceFileScope(
                backend=FileScopeBackend.GIT_WORKTREE,
                branch_binding=BranchBinding.SHARED,
                branch_name=branch_name,
                source_root_path=str(repository_root),
                forked_from_workspace_id=source_workspace_id,
            )
        )
        try:
            return self._repository.create(
                workspace_id=normalized_workspace_id,
                root_path=target_path,
                profile=profile,
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
        if not resolved_root.exists():
            raise ValueError(f"Workspace root does not exist: {resolved_root}")
        if not resolved_root.is_dir():
            raise ValueError(f"Workspace root is not a directory: {resolved_root}")
        return resolved_root

    def _find_workspace_by_root(self, root_path: Path) -> WorkspaceRecord | None:
        for workspace in self._repository.list_all():
            if workspace.root_path == root_path:
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

    def _resolve_worktree_repository_root(self, record: WorkspaceRecord) -> Path:
        source_root_path = record.profile.file_scope.source_root_path
        if not source_root_path:
            raise ValueError(
                f"Workspace {record.workspace_id} is missing worktree source_root_path"
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
        is_directory = current_path.is_dir() and not current_path.is_symlink()
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
                child for child in current_path.iterdir() if child.name not in {".git"}
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
                    0 if child.is_dir() and not child.is_symlink() else 1,
                    child.name.casefold(),
                ),
            )
        )

    def _collect_workspace_diffs(
        self,
        workspace_root: Path,
    ) -> tuple[bool, Path | None, tuple[WorkspaceDiffFile, ...], str | None]:
        try:
            git_root_path = self._resolve_git_root(workspace_root)
        except ValueError as exc:
            return False, None, (), str(exc)

        has_head = self._git_head_exists(workspace_root)
        try:
            candidates = self._list_diff_candidates(
                workspace_root=workspace_root,
                has_head=has_head,
            )
            diff_files = tuple(
                self._build_diff_file(
                    workspace_root=workspace_root,
                    candidate=candidate,
                    has_head=has_head,
                )
                for candidate in candidates
            )
        except ValueError as exc:
            log_event(
                _logger,
                30,
                event="workspace.snapshot.diff_failed",
                message="Failed to collect workspace diff",
                payload={
                    "workspace_root": str(workspace_root),
                    "detail": str(exc),
                },
            )
            return True, git_root_path, (), str(exc)
        return True, git_root_path, diff_files, None

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
            parts = line.split("	")
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
            path=candidate.path,
            previous_path=candidate.previous_path,
            change_type=candidate.change_type,
            diff=diff_text,
            is_binary=is_binary,
        )

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
        if not path.exists() or not path.is_file():
            return None
        try:
            return path.read_bytes()
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
