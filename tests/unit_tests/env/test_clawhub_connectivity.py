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
        lambda *, timeout_seconds: ClawHubCliInstallResult(
            ok=False,
            attempted=False,
            error_code="npm_unavailable",
            error_message="npm is not available on PATH.",
        ),
    )
    service = ClawHubConnectivityProbeService(
        get_clawhub_config=lambda: ClawHubConfig(token="ch_saved"),
    )

    result = service.probe(ClawHubConnectivityProbeRequest())

    assert result.ok is False
    assert result.error_code == "npm_unavailable"
    assert result.diagnostics.binary_available is False
    assert result.diagnostics.token_configured is True
    assert result.diagnostics.installation_attempted is False
    assert result.diagnostics.installed_during_probe is False


def test_clawhub_probe_reads_version(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert env["CLAWHUB_TOKEN"] == "ch_secret"
        return subprocess.CompletedProcess(
            args=["clawhub", "--cli-version"],
            returncode=0,
            stdout="0.4.2\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = ClawHubConnectivityProbeService(
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


def test_clawhub_probe_installs_missing_binary(monkeypatch) -> None:
    installed_clawhub_path = Path("/opt/tools/clawhub/bin/clawhub")
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.resolve_existing_clawhub_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "relay_teams.env.clawhub_connectivity.install_clawhub_via_npm",
        lambda *, timeout_seconds: ClawHubCliInstallResult(
            ok=True,
            attempted=True,
            clawhub_path=str(installed_clawhub_path),
            npm_path="/usr/bin/npm",
            registry="https://mirrors.huaweicloud.com/repository/npm/",
        ),
    )

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert env["CLAWHUB_TOKEN"] == "ch_secret"
        assert env["PATH"].split(os.pathsep)[0] == str(installed_clawhub_path.parent)
        return subprocess.CompletedProcess(
            args=[str(installed_clawhub_path), "--cli-version"],
            returncode=0,
            stdout="0.9.0\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = ClawHubConnectivityProbeService(
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
