# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

from relay_teams.env.env_cli import _auto_start_if_needed


def test_auto_start_force_calls_subprocess_stop() -> None:
    """When force=True and server is not healthy, subprocess.run is called."""
    with (
        patch("relay_teams.env.env_cli._is_server_healthy", return_value=False),
        patch("relay_teams.env.env_cli._start_server_daemon") as mock_start,
        patch("relay_teams.env.env_cli._wait_until_healthy", return_value=True),
        patch("relay_teams.env.env_cli.subprocess.run") as mock_subprocess,
    ):
        _auto_start_if_needed(
            base_url="http://127.0.0.1:8080",
            autostart=True,
            force=True,
        )
        mock_subprocess.assert_called_once_with(
            [
                mock_subprocess.call_args[0][0][0],  # sys.executable
                "-m",
                "relay_teams",
                "server",
                "stop",
                "--force",
            ],
            check=False,
            timeout=30,
        )
        mock_start.assert_called_once()


def test_auto_start_no_force_skips_subprocess_stop() -> None:
    """When force=False, subprocess.run is not called."""
    with (
        patch("relay_teams.env.env_cli._is_server_healthy", return_value=False),
        patch("relay_teams.env.env_cli._start_server_daemon") as mock_start,
        patch("relay_teams.env.env_cli._wait_until_healthy", return_value=True),
        patch("relay_teams.env.env_cli.subprocess.run") as mock_subprocess,
    ):
        _auto_start_if_needed(
            base_url="http://127.0.0.1:8080",
            autostart=True,
            force=False,
        )
        mock_subprocess.assert_not_called()
        mock_start.assert_called_once()
