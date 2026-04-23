# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from relay_teams.secrets import AppSecretStore
from relay_teams.workspace import (
    FileScopeBackend,
    GitWorktreeClient,
    SshProfileConfig,
    SshProfileRepository,
    SshProfileSecretStore,
    SshProfileService,
    WorkspaceFileScope,
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceProfile,
    WorkspaceRepository,
    WorkspaceService,
    WorkspaceSshMountConfig,
    build_local_workspace_mount,
)


class StorageScopedWorkspaceService(WorkspaceService):
    def __init__(
        self,
        *,
        repository: WorkspaceRepository,
        storage_root: Path,
        git_worktree_client: GitWorktreeClient | None = None,
    ) -> None:
        super().__init__(
            repository=repository,
            git_worktree_client=git_worktree_client,
        )
        self._storage_root = storage_root

    def _workspace_storage_dir(self, workspace_id: str) -> Path:
        return self._storage_root / workspace_id


class FakeGitWorktreeClient(GitWorktreeClient):
    def __init__(self) -> None:
        self.ensure_calls: list[Path] = []
        self.head_calls: list[Path] = []
        self.fetch_calls: list[tuple[Path, str, str]] = []
        self.resolve_ref_calls: list[tuple[Path, str]] = []
        self.add_calls: list[tuple[Path, str, Path, str]] = []
        self.remove_calls: list[tuple[Path, Path]] = []
        self.prune_calls: list[Path] = []
        self.fetch_error: ValueError | None = None

    def ensure_repository(self, repository_root: Path) -> Path:
        self.ensure_calls.append(repository_root)
        return repository_root.resolve()

    def current_head(self, repository_root: Path) -> str:
        self.head_calls.append(repository_root)
        return "abc123"

    def fetch_ref(
        self,
        repository_root: Path,
        *,
        remote: str = "origin",
        ref: str = "main",
    ) -> None:
        self.fetch_calls.append((repository_root, remote, ref))
        if self.fetch_error is not None:
            raise self.fetch_error

    def resolve_ref(self, repository_root: Path, ref_name: str) -> str:
        self.resolve_ref_calls.append((repository_root, ref_name))
        return f"resolved:{ref_name}"

    def add_worktree(
        self,
        *,
        repository_root: Path,
        branch_name: str,
        target_path: Path,
        start_point: str,
    ) -> None:
        self.add_calls.append((repository_root, branch_name, target_path, start_point))
        target_path.mkdir(parents=True, exist_ok=True)

    def remove_worktree(self, *, repository_root: Path, target_path: Path) -> None:
        self.remove_calls.append((repository_root, target_path))
        shutil.rmtree(target_path, ignore_errors=True)

    def prune(self, repository_root: Path) -> None:
        self.prune_calls.append(repository_root)


class _FileOnlySecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


def test_workspace_service_creates_and_lists_workspace(tmp_path: Path) -> None:
    db_path = tmp_path / "workspace_service.db"
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    service = WorkspaceService(repository=WorkspaceRepository(db_path))

    created = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    assert created.workspace_id == "project-alpha"
    assert created.root_path == root_path.resolve()
    listed = service.list_workspaces()
    assert len(listed) == 1
    assert listed[0].workspace_id == "project-alpha"


def test_workspace_service_updates_mounts_and_default_mount(tmp_path: Path) -> None:
    db_path = tmp_path / "workspace_service.db"
    app_root = tmp_path / "app-root"
    ops_root = tmp_path / "ops-root"
    service = WorkspaceService(repository=WorkspaceRepository(db_path))
    app_root.mkdir()
    ops_root.mkdir()
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=app_root,
    )

    updated = service.update_workspace(
        "project-alpha",
        default_mount_name="ops",
        mounts=(
            build_local_workspace_mount(mount_name="app", root_path=app_root),
            build_local_workspace_mount(mount_name="ops", root_path=ops_root),
        ),
    )

    assert updated.default_mount_name == "ops"
    assert [mount.mount_name for mount in updated.mounts] == ["app", "ops"]
    assert updated.root_path == ops_root.resolve()


def test_workspace_service_rejects_local_mount_scope_escape_on_create(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "workspace_service.db"
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    service = WorkspaceService(repository=WorkspaceRepository(db_path))

    with pytest.raises(ValueError, match="Workspace file scope escapes mount root"):
        _ = service.create_workspace(
            workspace_id="project-alpha",
            mounts=(
                build_local_workspace_mount(
                    mount_name="default",
                    root_path=root_path,
                    working_directory="../outside",
                ),
            ),
            default_mount_name="default",
        )


def test_workspace_service_persists_local_mount_root_as_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "workspace_service.db"
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    service = WorkspaceService(repository=WorkspaceRepository(db_path))
    monkeypatch.chdir(tmp_path)

    created = service.create_workspace(
        workspace_id="project-alpha",
        mounts=(
            build_local_workspace_mount(
                mount_name="default",
                root_path=Path("workspace-root"),
            ),
        ),
        default_mount_name="default",
    )

    assert created.mounts[0].local_root_path() == root_path.resolve()
    assert (
        service.get_workspace("project-alpha").mounts[0].local_root_path()
        == root_path.resolve()
    )


def test_workspace_service_rejects_local_mount_scope_escape_on_update(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "workspace_service.db"
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    service = WorkspaceService(repository=WorkspaceRepository(db_path))
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    with pytest.raises(ValueError, match="Workspace file scope escapes mount root"):
        _ = service.update_workspace(
            "project-alpha",
            mounts=(
                build_local_workspace_mount(
                    mount_name="default",
                    root_path=root_path,
                    writable_paths=("../outside",),
                ),
            ),
            default_mount_name="default",
        )


def test_workspace_service_rejects_missing_root(tmp_path: Path) -> None:
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )

    with pytest.raises(ValueError, match="does not exist"):
        _ = service.create_workspace(
            workspace_id="missing",
            root_path=tmp_path / "missing-root",
        )


def test_workspace_service_create_for_root_reuses_existing_workspace(
    tmp_path: Path,
) -> None:
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    root_path = tmp_path / "Project Root"
    root_path.mkdir()

    created = service.create_workspace_for_root(root_path=root_path)
    reused = service.create_workspace_for_root(root_path=root_path)

    assert created.workspace_id == "project-root"
    assert reused.workspace_id == "project-root"
    assert len(service.list_workspaces()) == 1


def test_workspace_service_create_for_root_generates_unique_workspace_id(
    tmp_path: Path,
) -> None:
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    first_root = tmp_path / "Demo Project"
    second_root = tmp_path / "demo-project"
    first_root.mkdir()
    second_root.mkdir()

    first = service.create_workspace_for_root(root_path=first_root)
    second = service.create_workspace_for_root(root_path=second_root)

    assert first.workspace_id == "demo-project"
    assert second.workspace_id == "demo-project-2"


def test_workspace_service_forks_workspace_into_git_worktree(tmp_path: Path) -> None:
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    git_client = FakeGitWorktreeClient()
    service = StorageScopedWorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db"),
        storage_root=tmp_path / "storage",
        git_worktree_client=git_client,
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    created = service.fork_workspace(
        source_workspace_id="project-alpha",
        name="Alpha Project Fork",
    )

    assert created.workspace_id == "alpha-project-fork"
    assert (
        created.root_path
        == (tmp_path / "storage" / "alpha-project-fork" / "worktree").resolve()
    )
    assert created.profile.file_scope.backend == FileScopeBackend.GIT_WORKTREE
    assert created.profile.file_scope.branch_name == "fork/alpha-project-fork"
    assert created.profile.file_scope.source_root_path == str(root_path.resolve())
    assert created.profile.file_scope.forked_from_workspace_id == "project-alpha"
    assert git_client.ensure_calls == [root_path.resolve()]
    assert git_client.head_calls == []
    assert git_client.fetch_calls == [(root_path.resolve(), "origin", "main")]
    assert git_client.resolve_ref_calls == [(root_path.resolve(), "origin/main")]
    assert git_client.add_calls == [
        (
            root_path.resolve(),
            "fork/alpha-project-fork",
            (tmp_path / "storage" / "alpha-project-fork" / "worktree").resolve(),
            "resolved:origin/main",
        )
    ]


def test_workspace_service_forks_workspace_from_explicit_start_ref(
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    git_client = FakeGitWorktreeClient()
    service = StorageScopedWorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db"),
        storage_root=tmp_path / "storage",
        git_worktree_client=git_client,
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    created = service.fork_workspace(
        source_workspace_id="project-alpha",
        name="Release Fork",
        start_ref="origin/release",
    )

    assert created.workspace_id == "release-fork"
    assert git_client.fetch_calls == []
    assert git_client.resolve_ref_calls == [(root_path.resolve(), "origin/release")]
    assert git_client.add_calls == [
        (
            root_path.resolve(),
            "fork/release-fork",
            (tmp_path / "storage" / "release-fork" / "worktree").resolve(),
            "resolved:origin/release",
        )
    ]


def test_workspace_service_fork_workspace_falls_back_to_cached_origin_main_after_fetch_timeout(
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    git_client = FakeGitWorktreeClient()
    git_client.fetch_error = ValueError("Git command timed out")
    service = StorageScopedWorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db"),
        storage_root=tmp_path / "storage",
        git_worktree_client=git_client,
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    created = service.fork_workspace(
        source_workspace_id="project-alpha",
        name="Alpha Project Fork",
    )

    assert created.workspace_id == "alpha-project-fork"
    assert git_client.fetch_calls == [(root_path.resolve(), "origin", "main")]
    assert git_client.resolve_ref_calls == [(root_path.resolve(), "origin/main")]
    assert git_client.add_calls == [
        (
            root_path.resolve(),
            "fork/alpha-project-fork",
            (tmp_path / "storage" / "alpha-project-fork" / "worktree").resolve(),
            "resolved:origin/main",
        )
    ]


def test_workspace_service_deletes_git_worktree_when_requested(tmp_path: Path) -> None:
    git_client = FakeGitWorktreeClient()
    service = StorageScopedWorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db"),
        storage_root=tmp_path / "storage",
        git_worktree_client=git_client,
    )
    root_path = tmp_path / "storage" / "alpha-project-fork" / "worktree"
    root_path.mkdir(parents=True)
    _ = service.create_workspace(
        workspace_id="alpha-project-fork",
        root_path=root_path,
        profile=WorkspaceProfile(
            file_scope=WorkspaceFileScope(
                backend=FileScopeBackend.GIT_WORKTREE,
                branch_name="fork/alpha-project-fork",
                source_root_path=str((tmp_path / "workspace-root").resolve()),
                forked_from_workspace_id="project-alpha",
            )
        ),
    )

    deleted = service.delete_workspace_with_options(
        workspace_id="alpha-project-fork",
        remove_directory=True,
    )

    assert deleted.workspace_id == "alpha-project-fork"
    assert git_client.remove_calls == [
        ((tmp_path / "workspace-root").resolve(), root_path.resolve())
    ]
    assert git_client.prune_calls == [(tmp_path / "workspace-root").resolve()]
    assert root_path.exists() is False
    assert service.list_workspaces() == ()


def test_workspace_service_deletes_workspace(tmp_path: Path) -> None:
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    service.delete_workspace("project-alpha")

    assert root_path.exists() is True
    assert service.list_workspaces() == ()


def test_workspace_service_deletes_workspace_directory_when_requested(
    tmp_path: Path,
) -> None:
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    deleted = service.delete_workspace_with_options(
        workspace_id="project-alpha",
        remove_directory=True,
    )

    assert deleted.workspace_id == "project-alpha"
    assert root_path.exists() is False
    assert service.list_workspaces() == ()


def test_workspace_service_keeps_record_when_directory_removal_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    original_rmtree = shutil.rmtree

    def fail_rmtree(path: Path, ignore_errors: bool = False) -> None:
        _ = ignore_errors
        if Path(path) == root_path:
            raise PermissionError("permission denied")
        original_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", fail_rmtree)

    with pytest.raises(RuntimeError, match="Failed to remove workspace path"):
        _ = service.delete_workspace_with_options(
            workspace_id="project-alpha",
            remove_directory=True,
        )

    assert root_path.exists() is True
    assert [workspace.workspace_id for workspace in service.list_workspaces()] == [
        "project-alpha"
    ]


def test_workspace_service_returns_progressive_snapshot_and_tree_listing(
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "workspace-root"
    (root_path / "src" / "nested").mkdir(parents=True)
    (root_path / "docs").mkdir(parents=True)
    (root_path / "src" / "app.py").write_text('print("new")' + "\n", encoding="utf-8")
    (root_path / "src" / "nested" / "tool.py").write_text(
        'print("tool")' + "\n", encoding="utf-8"
    )
    (root_path / "docs" / "README.md").write_text(
        "# Root Docs" + "\n", encoding="utf-8"
    )
    (root_path / "package.json").write_text("{}" + "\n", encoding="utf-8")

    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    snapshot = service.get_workspace_snapshot("project-alpha")
    src_listing = service.get_workspace_tree_listing(
        "project-alpha",
        directory_path="src",
    )

    assert snapshot.workspace_id == "project-alpha"
    assert snapshot.root_path == root_path.resolve()
    assert snapshot.default_mount_name == "default"
    assert snapshot.tree.path == "."
    assert [item.path for item in snapshot.tree.children] == ["default"]
    assert snapshot.tree.children[0].has_children is True
    assert snapshot.tree.children[0].children == ()
    assert src_listing.workspace_id == "project-alpha"
    assert src_listing.mount_name == "default"
    assert src_listing.directory_path == "src"
    assert [item.path for item in src_listing.children] == [
        "src/nested",
        "src/app.py",
    ]
    assert src_listing.children[0].has_children is True
    assert src_listing.children[1].has_children is False


def test_workspace_service_lists_ssh_mount_tree_with_saved_profile(
    tmp_path: Path,
) -> None:
    captured_commands: list[tuple[str, ...]] = []

    def run_ssh_command(
        command: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command_tuple = tuple(command)
        captured_commands.append(command_tuple)
        remote_command = command_tuple[-1]
        stdout = (
            "file\t0\tmain.py\n"
            if "/srv/app/src" in remote_command
            else "file\t0\tREADME.md\ndirectory\t1\tsrc\n"
        )
        return subprocess.CompletedProcess(
            args=command_tuple,
            returncode=0,
            stdout=stdout,
            stderr="",
        )

    local_root = tmp_path / "local-root"
    local_root.mkdir()
    ssh_profile_service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "ssh_profiles.db"),
        config_dir=tmp_path,
        secret_store=SshProfileSecretStore(secret_store=_FileOnlySecretStore()),
        ssh_path_lookup=lambda _name: "/usr/bin/ssh",
        process_runner=run_ssh_command,
    )
    _ = ssh_profile_service.save_profile(
        ssh_profile_id="container",
        config=SshProfileConfig(
            host="127.0.0.1",
            username="root",
            port=2222,
            password="secret",
        ),
    )
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db"),
        ssh_profile_service=ssh_profile_service,
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        mounts=(
            build_local_workspace_mount(mount_name="default", root_path=local_root),
            WorkspaceMountRecord(
                mount_name="container",
                provider=WorkspaceMountProvider.SSH,
                provider_config=WorkspaceSshMountConfig(
                    ssh_profile_id="container",
                    remote_root="/srv/app",
                ),
            ),
        ),
        default_mount_name="default",
    )

    root_listing = service.get_workspace_tree_listing(
        "project-alpha",
        directory_path=".",
        mount_name="container",
    )
    src_listing = service.get_workspace_tree_listing(
        "project-alpha",
        directory_path="src",
        mount_name="container",
    )

    assert root_listing.mount_name == "container"
    assert root_listing.directory_path == "."
    assert [item.path for item in root_listing.children] == ["src", "README.md"]
    assert root_listing.children[0].has_children is True
    assert src_listing.directory_path == "src"
    assert [item.path for item in src_listing.children] == ["src/main.py"]
    assert captured_commands[0][-3:-1] == ("--", "127.0.0.1")
    assert "BatchMode=no" in captured_commands[0]
    assert "/srv/app" in captured_commands[0][-1]
    assert "/srv/app/src" in captured_commands[1][-1]


def test_workspace_service_rejects_tree_path_that_escapes_workspace_root(
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    with pytest.raises(ValueError, match="escapes root"):
        _ = service.get_workspace_tree_listing(
            "project-alpha",
            directory_path="../outside",
        )


def test_workspace_service_returns_image_preview_for_absolute_workspace_path(
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "workspace-root"
    image_path = root_path / "artifacts" / "brief.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"not-a-real-png")
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    preview_path, media_type = service.get_workspace_image_preview_file(
        "project-alpha",
        path=str(image_path.resolve()),
    )

    assert preview_path == image_path.resolve()
    assert media_type == "image/png"


def test_workspace_service_rejects_non_image_preview_file(tmp_path: Path) -> None:
    root_path = tmp_path / "workspace-root"
    text_path = root_path / "notes.txt"
    root_path.mkdir()
    text_path.write_text("hello\n", encoding="utf-8")
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    with pytest.raises(ValueError, match="supported image"):
        _ = service.get_workspace_image_preview_file(
            "project-alpha",
            path="notes.txt",
        )


def test_workspace_service_returns_git_diffs_separately(tmp_path: Path) -> None:
    root_path = tmp_path / "workspace-root"
    (root_path / "src").mkdir(parents=True)
    (root_path / "docs").mkdir()
    (root_path / "notes").mkdir()
    (root_path / "src" / "app.py").write_text('print("new")' + "\n", encoding="utf-8")
    (root_path / "docs" / "README.md").write_text(
        "# Root Docs" + "\n", encoding="utf-8"
    )
    (root_path / "notes" / "todo.txt").write_text("todo" + "\n", encoding="utf-8")
    (root_path / "package.json").write_text("{}" + "\n", encoding="utf-8")

    class SnapshotWorkspaceService(WorkspaceService):
        def _run_git(
            self,
            args: tuple[str, ...],
            *,
            cwd: Path,
            text: bool = True,
        ) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
            _ = cwd
            if args == ("rev-parse", "--show-toplevel"):
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=0,
                    stdout=f"{root_path.resolve()}\n",
                    stderr="",
                )
            if args == ("rev-parse", "--verify", "HEAD"):
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=0,
                    stdout="abc123\n",
                    stderr="",
                )
            if args == ("diff", "--name-status", "--find-renames", "HEAD", "--"):
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=0,
                    stdout="R100\tREADME.md\tdocs/README.md\nM\tsrc/app.py\n",
                    stderr="",
                )
            if args == ("ls-files", "--others", "--exclude-standard"):
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=0,
                    stdout="notes/todo.txt\n",
                    stderr="",
                )
            if args == ("show", "HEAD:README.md"):
                if text:
                    return subprocess.CompletedProcess(
                        args=["git", *args],
                        returncode=0,
                        stdout="# Root Docs\n",
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=0,
                    stdout=b"# Root Docs\n",
                    stderr=b"",
                )
            if args == ("show", "HEAD:src/app.py"):
                if text:
                    return subprocess.CompletedProcess(
                        args=["git", *args],
                        returncode=0,
                        stdout='print("old")\n',
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=0,
                    stdout=b'print("old")\n',
                    stderr=b"",
                )
            raise AssertionError(f"Unexpected git command: {args}")

    service = SnapshotWorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    diffs = service.get_workspace_diffs("project-alpha")
    diff_file = service.get_workspace_diff_file(
        "project-alpha",
        path="src/app.py",
    )

    assert diffs.workspace_id == "project-alpha"
    assert diffs.root_path == root_path.resolve()
    assert diffs.is_git_repository is True
    assert diffs.git_root_path == root_path.resolve()
    assert [item.path for item in diffs.diff_files] == [
        "docs/README.md",
        "notes/todo.txt",
        "src/app.py",
    ]
    assert diffs.diff_files[0].change_type.value == "renamed"
    assert diffs.diff_files[0].previous_path == "README.md"
    assert diffs.diff_files[1].change_type.value == "untracked"
    assert diffs.diff_files[2].change_type.value == "modified"

    assert diff_file.path == "src/app.py"
    assert diff_file.change_type.value == "modified"
    assert '-print("old")' in diff_file.diff
    assert '+print("new")' in diff_file.diff


def test_workspace_service_rejects_missing_diff_file(tmp_path: Path) -> None:
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    (root_path / "src").mkdir()
    (root_path / "src" / "app.py").write_text('print("new")' + "\n", encoding="utf-8")

    class SnapshotWorkspaceService(WorkspaceService):
        def _run_git(
            self,
            args: tuple[str, ...],
            *,
            cwd: Path,
            text: bool = True,
        ) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
            _ = cwd
            if args == ("rev-parse", "--show-toplevel"):
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=0,
                    stdout=f"{root_path.resolve()}\n",
                    stderr="",
                )
            if args == ("rev-parse", "--verify", "HEAD"):
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=0,
                    stdout="abc123\n",
                    stderr="",
                )
            if args == ("diff", "--name-status", "--find-renames", "HEAD", "--"):
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=0,
                    stdout="M	src/app.py\n",
                    stderr="",
                )
            if args == ("ls-files", "--others", "--exclude-standard"):
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=0,
                    stdout="",
                    stderr="",
                )
            if args == ("show", "HEAD:src/app.py"):
                if text:
                    return subprocess.CompletedProcess(
                        args=["git", *args],
                        returncode=0,
                        stdout='print("old")\n',
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=0,
                    stdout=b'print("old")\n',
                    stderr=b"",
                )
            raise AssertionError(f"Unexpected git command: {args}")

    service = SnapshotWorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    with pytest.raises(ValueError, match="not found"):
        _ = service.get_workspace_diff_file(
            "project-alpha",
            path="missing.py",
        )
