# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from relay_teams.env.clawhub_cli import ClawHubCliInstallResult
from relay_teams.env.clawhub_config_models import ClawHubConfig
from relay_teams.env.clawhub_connectivity import (
    ClawHubConnectivityProbeRequest,
    ClawHubConnectivityProbeService,
)


def test_clawhub_probe_requires_token(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    service = ClawHubConnectivityProbeService(
        config_dir=Path("/tmp/.relay-teams"),
        get_clawhub_config=lambda: ClawHubConfig(token=None),
    )

    result = service.probe(ClawHubConnectivityProbeRequest())

    assert result.ok is False
    assert result.error_code == "missing_token"
    assert result.diagnostics.binary_available is True
    assert result.diagnostics.token_configured is False
    assert result.diagnostics.installation_attempted is False
    assert result.diagnostics.installed_during_probe is False


def test_clawhub_probe_reports_missing_binary(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.resolve_existing_clawhub_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.install_clawhub_via_npm",
        lambda *, timeout_seconds, base_env=None: ClawHubCliInstallResult(
            ok=False,
            attempted=False,
            error_code="npm_unavailable",
            error_message="npm is not available on PATH.",
        ),
    )
    service = ClawHubConnectivityProbeService(
        config_dir=Path("/tmp/.relay-teams"),
        get_clawhub_config=lambda: ClawHubConfig(token="ch_saved"),
    )

    result = service.probe(ClawHubConnectivityProbeRequest())

    assert result.ok is False
    assert result.error_code == "npm_unavailable"
    assert result.diagnostics.binary_available is False
    assert result.diagnostics.token_configured is True
    assert result.diagnostics.installation_attempted is False
    assert result.diagnostics.installed_during_probe is False


def test_clawhub_probe_logs_in_before_reporting_success(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    observed_commands: list[list[str]] = []

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = kwargs.get("args")
        if not isinstance(command, list):
            command = _args[0]
        assert isinstance(command, list)
        observed_commands.append(command)
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert env["CLAWHUB_TOKEN"] == "ch_secret"
        assert "CLAWHUB_SITE" not in env
        assert "CLAWHUB_REGISTRY" not in env
        if command[1] == "--cli-version":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="0.4.2\n",
                stderr="",
            )
        if command[1] == "login":
            assert command[2:] == ["--token", "ch_secret", "--no-browser"]
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="Logged in.\n",
                stderr="",
            )
        assert command[1] == "whoami"
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="steven\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = ClawHubConnectivityProbeService(
        config_dir=Path("/tmp/.relay-teams"),
        get_clawhub_config=lambda: ClawHubConfig(token=None),
    )

    result = service.probe(ClawHubConnectivityProbeRequest(token="ch_secret"))

    assert result.ok is True
    assert result.clawhub_version == "clawhub 0.4.2"
    assert result.clawhub_path == "/usr/bin/clawhub"
    assert result.diagnostics.binary_available is True
    assert result.diagnostics.token_configured is True
    assert result.diagnostics.installation_attempted is False
    assert result.diagnostics.installed_during_probe is False
    assert observed_commands == [
        ["/usr/bin/clawhub", "--cli-version"],
        [
            "/usr/bin/clawhub",
            "login",
            "--token",
            "ch_secret",
            "--no-browser",
        ],
        ["/usr/bin/clawhub", "whoami"],
    ]


def test_clawhub_probe_uses_remaining_timeout_budget_for_each_step(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    perf_counter_values = iter([100.0, 101.0, 104.0, 106.5, 107.0])
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.perf_counter",
        lambda: next(perf_counter_values),
    )
    observed_timeouts: list[float] = []

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = kwargs.get("args")
        if not isinstance(command, list):
            command = _args[0]
        assert isinstance(command, list)
        timeout = kwargs.get("timeout")
        assert isinstance(timeout, float)
        observed_timeouts.append(timeout)
        if command[1] == "--cli-version":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="0.4.2\n",
                stderr="",
            )
        if command[1] == "login":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="Logged in.\n",
                stderr="",
            )
        assert command[1] == "whoami"
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="steven\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = ClawHubConnectivityProbeService(
        config_dir=Path("/tmp/.relay-teams"),
        get_clawhub_config=lambda: ClawHubConfig(token=None),
    )

    result = service.probe(
        ClawHubConnectivityProbeRequest(token="ch_secret", timeout_ms=10_000)
    )

    assert result.ok is True
    assert observed_timeouts == [9.0, 6.0, 3.5]


def test_clawhub_probe_rejects_invalid_token_when_login_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = kwargs.get("args")
        if not isinstance(command, list):
            command = _args[0]
        assert isinstance(command, list)
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert env["CLAWHUB_TOKEN"] == "ch_secret"
        if command[1] == "--cli-version":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="0.4.2\n",
                stderr="",
            )
        assert command[1] == "login"
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr="- Verifying token\nError: Invalid token",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = ClawHubConnectivityProbeService(
        config_dir=Path("/tmp/.relay-teams"),
        get_clawhub_config=lambda: ClawHubConfig(token=None),
    )

    result = service.probe(ClawHubConnectivityProbeRequest(token="ch_secret"))

    assert result.ok is False
    assert result.error_code == "auth_failed"
    assert result.error_message == "Error: Invalid token"
    assert result.clawhub_version == "clawhub 0.4.2"
    assert result.exit_code == 1


def test_clawhub_probe_installs_missing_binary(monkeypatch) -> None:
    installed_clawhub_path = Path("/opt/tools/clawhub/bin/clawhub")
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.resolve_existing_clawhub_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.install_clawhub_via_npm",
        lambda *, timeout_seconds, base_env=None: ClawHubCliInstallResult(
            ok=True,
            attempted=True,
            clawhub_path=str(installed_clawhub_path),
            npm_path="/usr/bin/npm",
            registry="https://mirrors.huaweicloud.com/repository/npm/",
        ),
    )

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = kwargs.get("args")
        if not isinstance(command, list):
            command = _args[0]
        assert isinstance(command, list)
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert env["CLAWHUB_TOKEN"] == "ch_secret"
        assert env["PATH"].split(os.pathsep)[0] == str(installed_clawhub_path.parent)
        assert "CLAWHUB_SITE" not in env
        assert "CLAWHUB_REGISTRY" not in env
        if command[1] == "--cli-version":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="0.9.0\n",
                stderr="",
            )
        if command[1] == "login":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="Logged in.\n",
                stderr="",
            )
        assert command[1] == "whoami"
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="steven\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = ClawHubConnectivityProbeService(
        config_dir=Path("/tmp/.relay-teams"),
        get_clawhub_config=lambda: ClawHubConfig(token=None),
    )

    result = service.probe(ClawHubConnectivityProbeRequest(token="ch_secret"))

    assert result.ok is True
    assert result.clawhub_path == str(installed_clawhub_path)
    assert result.clawhub_version == "clawhub 0.9.0"
    assert result.diagnostics.binary_available is True
    assert result.diagnostics.token_configured is True
    assert result.diagnostics.installation_attempted is True
    assert result.diagnostics.installed_during_probe is True
