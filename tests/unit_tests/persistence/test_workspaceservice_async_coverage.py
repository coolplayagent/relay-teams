"""Coverage tests for WorkspaceService async wrapper methods."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from relay_teams.workspace.workspace_service import WorkspaceService


@pytest.mark.asyncio
async def test_create_workspace_async_delegates() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.create_workspace_async
    await method(
        mock_self,
        workspace_id=cast(Any, ""),
        mounts=cast(Any, ""),
    )
    getattr(mock_self, "create_workspace").assert_called_once()


@pytest.mark.asyncio
async def test_update_workspace_async_delegates() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.update_workspace_async
    await method(
        mock_self,
        cast(Any, ""),
        mounts=cast(Any, ""),
        default_mount_name=cast(Any, ""),
    )
    getattr(mock_self, "update_workspace").assert_called_once()


@pytest.mark.asyncio
async def test_create_workspace_for_root_async_delegates() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.create_workspace_for_root_async
    await method(mock_self, root_path=cast(Any, ""))
    getattr(mock_self, "create_workspace_for_root").assert_called_once()


@pytest.mark.asyncio
async def test_get_workspace_snapshot_async_delegates() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.get_workspace_snapshot_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "get_workspace_snapshot").assert_called_once()


@pytest.mark.asyncio
async def test_search_workspace_paths_async_delegates() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.search_workspace_paths_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "search_workspace_paths").assert_called_once()


@pytest.mark.asyncio
async def test_list_workspaces_async_delegates() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.list_workspaces_async
    await method(mock_self)
    getattr(mock_self, "list_workspaces").assert_called_once()


@pytest.mark.asyncio
async def test_delete_workspace_with_options_async_delegates() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.delete_workspace_with_options_async
    await method(mock_self, workspace_id=cast(Any, ""))
    getattr(mock_self, "delete_workspace_with_options").assert_called_once()


@pytest.mark.asyncio
async def test_fork_workspace_async_delegates() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.fork_workspace_async
    await method(mock_self, cast(Any, ""), name=cast(Any, ""))
    getattr(mock_self, "fork_workspace").assert_called_once()


@pytest.mark.asyncio
async def test_get_workspace_diff_file_async_no_mount() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.get_workspace_diff_file_async
    await method(mock_self, cast(Any, ""), path=cast(Any, ""))
    getattr(mock_self, "get_workspace_diff_file").assert_called_once()


@pytest.mark.asyncio
async def test_get_workspace_diff_file_async_with_mount() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.get_workspace_diff_file_async
    await method(mock_self, cast(Any, ""), path=cast(Any, ""), mount_name=cast(Any, ""))
    getattr(mock_self, "get_workspace_diff_file").assert_called_once()


@pytest.mark.asyncio
async def test_get_workspace_image_preview_file_async_no_mount() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.get_workspace_image_preview_file_async
    await method(mock_self, cast(Any, ""), path=cast(Any, ""))
    getattr(mock_self, "get_workspace_image_preview_file").assert_called_once()


@pytest.mark.asyncio
async def test_get_workspace_image_preview_file_async_with_mount() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.get_workspace_image_preview_file_async
    await method(mock_self, cast(Any, ""), path=cast(Any, ""), mount_name=cast(Any, ""))
    getattr(mock_self, "get_workspace_image_preview_file").assert_called_once()


@pytest.mark.asyncio
async def test_get_workspace_diffs_async_no_mount() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.get_workspace_diffs_async
    await method(mock_self, cast(Any, ""))
    getattr(mock_self, "get_workspace_diffs").assert_called_once()


@pytest.mark.asyncio
async def test_get_workspace_diffs_async_with_mount() -> None:
    mock_self = MagicMock()
    method = WorkspaceService.get_workspace_diffs_async
    await method(mock_self, cast(Any, ""), mount_name=cast(Any, ""))
    getattr(mock_self, "get_workspace_diffs").assert_called_once()
