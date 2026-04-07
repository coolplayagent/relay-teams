# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import zipfile

import pytest


class TestGitHubCliPath:
    def test_platform_detection(self) -> None:
        from agent_teams.tools.workspace_tools import github_cli

        key = github_cli._get_platform_key()
        assert key in github_cli.PLATFORM_MAP

    @pytest.mark.asyncio
    async def test_local_cache(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "bin"
        cache_dir.mkdir()

        gh_name = "gh.exe" if os.name == "nt" else "gh"
        gh = cache_dir / gh_name
        gh.write_bytes(b"fake")

        with patch("shutil.which", return_value=None):
            with patch(
                "agent_teams.tools.workspace_tools.github_cli.BIN_DIR",
                cache_dir,
            ):
                from agent_teams.tools.workspace_tools import github_cli

                github_cli.clear_gh_path_cache()
                path = await github_cli.get_gh_path()
                assert path == gh

    @pytest.mark.asyncio
    async def test_get_gh_path_prefers_system_gh_over_bundled_binary(
        self,
        tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "bin"
        cache_dir.mkdir()

        bundled_name = "gh.exe" if os.name == "nt" else "gh"
        bundled_gh = cache_dir / bundled_name
        bundled_gh.write_bytes(b"bundled")
        system_gh = tmp_path / "system-gh.exe"
        system_gh.write_bytes(b"system")

        with patch("shutil.which", return_value=str(system_gh)):
            with patch(
                "agent_teams.tools.workspace_tools.github_cli.BIN_DIR",
                cache_dir,
            ):
                from agent_teams.tools.workspace_tools import github_cli

                github_cli.clear_gh_path_cache()
                path = await github_cli.get_gh_path()

        assert path == system_gh

    @pytest.mark.asyncio
    async def test_get_gh_path_downloads_once_for_parallel_calls(
        self,
        tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "bin"
        cache_dir.mkdir()

        gh_name = "gh.exe" if os.name == "nt" else "gh"
        gh = cache_dir / gh_name

        with patch("shutil.which", return_value=None):
            with patch(
                "agent_teams.tools.workspace_tools.github_cli.BIN_DIR",
                cache_dir,
            ):
                from agent_teams.tools.workspace_tools import github_cli

                async def fake_download(target: Path) -> None:
                    await asyncio.sleep(0.01)
                    target.write_bytes(b"fake")

                github_cli.clear_gh_path_cache()
                with patch(
                    "agent_teams.tools.workspace_tools.github_cli._download_gh",
                    new=AsyncMock(side_effect=fake_download),
                ) as mock_download:
                    first, second = await asyncio.gather(
                        github_cli.get_gh_path(),
                        github_cli.get_gh_path(),
                    )

                assert first == gh
                assert second == gh
                assert mock_download.await_count == 1

    @pytest.mark.asyncio
    async def test_get_gh_path_uses_system_gh_without_attempting_download(
        self,
        tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "bin"
        cache_dir.mkdir()

        system_gh = tmp_path / "system_gh"
        system_gh.write_bytes(b"system")

        with patch("shutil.which", return_value=str(system_gh)):
            with patch(
                "agent_teams.tools.workspace_tools.github_cli.BIN_DIR",
                cache_dir,
            ):
                from agent_teams.tools.workspace_tools import github_cli

                github_cli.clear_gh_path_cache()
                with patch(
                    "agent_teams.tools.workspace_tools.github_cli._download_gh",
                    new=AsyncMock(side_effect=RuntimeError("no network")),
                ) as mock_download:
                    path = await github_cli.get_gh_path()

        assert path == system_gh
        assert mock_download.await_count == 0

    @pytest.mark.asyncio
    async def test_resolve_existing_gh_path_does_not_attempt_download(
        self,
        tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "bin"
        cache_dir.mkdir()

        with patch("shutil.which", return_value=None):
            with patch(
                "agent_teams.tools.workspace_tools.github_cli.BIN_DIR",
                cache_dir,
            ):
                from agent_teams.tools.workspace_tools import github_cli

                github_cli.clear_gh_path_cache()
                with patch(
                    "agent_teams.tools.workspace_tools.github_cli._download_gh",
                    new=AsyncMock(side_effect=RuntimeError("no network")),
                ) as mock_download:
                    path = github_cli.resolve_existing_gh_path()

        assert path is None
        assert mock_download.await_count == 0

    def test_resolve_existing_gh_path_does_not_create_bin_dir(
        self,
        tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "missing-bin"

        with patch("shutil.which", return_value=None):
            with patch(
                "agent_teams.tools.workspace_tools.github_cli.BIN_DIR",
                cache_dir,
            ):
                from agent_teams.tools.workspace_tools import github_cli

                github_cli.clear_gh_path_cache()
                path = github_cli.resolve_existing_gh_path()

        assert path is None
        assert not cache_dir.exists()

    def test_resolve_existing_gh_path_swallow_lookup_errors(
        self,
        tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "bin"

        with patch("shutil.which", return_value=None):
            with patch(
                "agent_teams.tools.workspace_tools.github_cli.BIN_DIR",
                cache_dir,
            ):
                from agent_teams.tools.workspace_tools import github_cli

                github_cli.clear_gh_path_cache()
                with patch.object(
                    github_cli.Path,
                    "is_file",
                    side_effect=OSError("read-only"),
                ):
                    path = github_cli.resolve_existing_gh_path()

        assert path is None


class TestGitHubCliDownload:
    @pytest.mark.asyncio
    async def test_download_enables_redirect_following(self, tmp_path: Path) -> None:
        from agent_teams.tools.workspace_tools import github_cli

        target = tmp_path / "gh.exe"
        response = MagicMock(status_code=200, content=b"fake-zip")
        client = AsyncMock()
        client.get = AsyncMock(return_value=response)
        client_cm = AsyncMock()
        client_cm.__aenter__.return_value = client
        client_cm.__aexit__.return_value = None

        with patch(
            "agent_teams.tools.workspace_tools.github_cli.create_async_http_client",
            return_value=client_cm,
        ) as mock_client_cls:
            with patch(
                "agent_teams.tools.workspace_tools.github_cli._get_platform_key",
                return_value="x64-windows",
            ):
                with patch(
                    "agent_teams.tools.workspace_tools.github_cli._extract_zip"
                ) as mock_extract_zip:
                    with patch("agent_teams.tools.workspace_tools.github_cli.os.chmod"):
                        await github_cli._download_gh(target)

        mock_client_cls.assert_called_once_with(follow_redirects=True)
        client.get.assert_awaited_once()
        mock_extract_zip.assert_called_once_with(b"fake-zip", target)

    def test_extract_zip_replaces_existing_target(self, tmp_path: Path) -> None:
        from agent_teams.tools.workspace_tools import github_cli

        target = tmp_path / "gh.exe"
        target.write_bytes(b"old")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("gh_2.88.1_windows_amd64/bin/gh.exe", b"new")

        github_cli._extract_zip(buffer.getvalue(), target)

        assert target.read_bytes() == b"new"
