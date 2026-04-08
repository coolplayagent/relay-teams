# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import asyncio
import json
import os
from pathlib import Path
from time import perf_counter
import re
import subprocess

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.env.github_config_models import GitHubConfig
from relay_teams.env.github_env import build_github_cli_env, normalize_github_token
from relay_teams.env.proxy_env import (
    ProxyEnvConfig,
    build_subprocess_env,
    proxy_applies_to_url,
)
from relay_teams.tools.workspace_tools.github_cli import BIN_DIR, get_gh_path
from relay_teams.tools.workspace_tools.github_cli_errors import GitHubCliNotFoundError

_MAX_GITHUB_PROBE_TIMEOUT_MS = 300_000
_DEFAULT_TIMEOUT_SECONDS = 15.0
_STATUS_CODE_RE = re.compile(r"\bHTTP (?P<status>\d{3})\b")


class GitHubConnectivityProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str | None = None
    timeout_ms: int | None = Field(
        default=None,
        ge=1000,
        le=_MAX_GITHUB_PROBE_TIMEOUT_MS,
    )


class GitHubConnectivityProbeDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binary_available: bool
    auth_valid: bool
    used_proxy: bool
    bundled_binary: bool


class GitHubConnectivityProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    username: str | None = None
    host: str = "github.com"
    gh_path: str | None = None
    gh_version: str | None = None
    status_code: int | None = Field(default=None, ge=100, le=599)
    exit_code: int | None = None
    latency_ms: int = Field(ge=0)
    checked_at: datetime
    diagnostics: GitHubConnectivityProbeDiagnostics
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None


class GitHubConnectivityProbeService:
    def __init__(
        self,
        *,
        get_github_config: Callable[[], GitHubConfig],
        get_proxy_config: Callable[[], ProxyEnvConfig],
    ) -> None:
        self._get_github_config = get_github_config
        self._get_proxy_config = get_proxy_config

    def probe(
        self,
        request: GitHubConnectivityProbeRequest,
    ) -> GitHubConnectivityProbeResult:
        checked_at = datetime.now(timezone.utc)
        started = perf_counter()
        proxy_config = self._get_proxy_config()
        used_proxy = proxy_applies_to_url("https://github.com", proxy_config)
        token = normalize_github_token(request.token) or self._get_github_config().token
        if token is None:
            return self._build_result(
                ok=False,
                checked_at=checked_at,
                started=started,
                used_proxy=used_proxy,
                binary_available=False,
                auth_valid=False,
                bundled_binary=False,
                error_code="missing_token",
                error_message="Configure a GitHub token before testing the connection.",
            )

        try:
            gh_path = asyncio.run(get_gh_path())
        except GitHubCliNotFoundError as exc:
            return self._build_result(
                ok=False,
                checked_at=checked_at,
                started=started,
                used_proxy=used_proxy,
                binary_available=False,
                auth_valid=False,
                bundled_binary=False,
                error_code="gh_unavailable",
                error_message=str(exc),
            )

        timeout_seconds = (
            _DEFAULT_TIMEOUT_SECONDS
            if request.timeout_ms is None
            else request.timeout_ms / 1000.0
        )
        env = build_subprocess_env(
            base_env=os.environ,
            extra_env=build_github_cli_env(token),
        )
        env["PATH"] = _prepend_to_path(env.get("PATH"), gh_path.parent)
        gh_version = _read_gh_version(gh_path, env=env)

        try:
            completed = subprocess.run(
                [str(gh_path), "api", "user"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return self._build_result(
                ok=False,
                checked_at=checked_at,
                started=started,
                used_proxy=used_proxy,
                gh_path=gh_path,
                gh_version=gh_version,
                binary_available=True,
                auth_valid=False,
                bundled_binary=_is_bundled_binary(gh_path),
                retryable=True,
                error_code="network_timeout",
                error_message=str(exc) or "GitHub CLI probe timed out.",
            )

        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            error_message = stderr or stdout or "GitHub CLI probe failed."
            status_code = _parse_status_code(error_message)
            error_code = "github_error"
            auth_valid = False
            retryable = False
            if status_code in {401, 403}:
                error_code = "auth_failed"
            elif status_code is None:
                lowered_error = error_message.lower()
                if "timed out" in lowered_error:
                    error_code = "network_timeout"
                    retryable = True
                elif "could not resolve host" in lowered_error:
                    error_code = "dns_error"
                    retryable = True
                elif "proxy" in lowered_error:
                    error_code = "proxy_error"
                    retryable = True
                else:
                    error_code = "network_error"
                    retryable = True

            return self._build_result(
                ok=False,
                checked_at=checked_at,
                started=started,
                used_proxy=used_proxy,
                gh_path=gh_path,
                gh_version=gh_version,
                status_code=status_code,
                exit_code=completed.returncode,
                binary_available=True,
                auth_valid=auth_valid,
                bundled_binary=_is_bundled_binary(gh_path),
                retryable=retryable,
                error_code=error_code,
                error_message=error_message,
            )

        username = _parse_username(completed.stdout)
        return self._build_result(
            ok=True,
            checked_at=checked_at,
            started=started,
            used_proxy=used_proxy,
            gh_path=gh_path,
            gh_version=gh_version,
            exit_code=completed.returncode,
            username=username,
            binary_available=True,
            auth_valid=True,
            bundled_binary=_is_bundled_binary(gh_path),
        )

    def _build_result(
        self,
        *,
        ok: bool,
        checked_at: datetime,
        started: float,
        used_proxy: bool,
        binary_available: bool,
        auth_valid: bool,
        bundled_binary: bool,
        username: str | None = None,
        gh_path: Path | None = None,
        gh_version: str | None = None,
        status_code: int | None = None,
        exit_code: int | None = None,
        retryable: bool = False,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> GitHubConnectivityProbeResult:
        return GitHubConnectivityProbeResult(
            ok=ok,
            username=username,
            gh_path=None if gh_path is None else str(gh_path),
            gh_version=gh_version,
            status_code=status_code,
            exit_code=exit_code,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            checked_at=checked_at,
            diagnostics=GitHubConnectivityProbeDiagnostics(
                binary_available=binary_available,
                auth_valid=auth_valid,
                used_proxy=used_proxy,
                bundled_binary=bundled_binary,
            ),
            retryable=retryable,
            error_code=error_code,
            error_message=error_message,
        )


def _parse_username(stdout: str) -> str | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    login = payload.get("login")
    if isinstance(login, str) and login.strip():
        return login.strip()
    return None


def _read_gh_version(gh_path: Path, *, env: dict[str, str]) -> str | None:
    completed = subprocess.run(
        [str(gh_path), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        return None
    first_line = completed.stdout.strip().splitlines()
    if not first_line:
        return None
    parts = first_line[0].split()
    if len(parts) >= 3:
        return parts[2]
    return first_line[0].strip() or None


def _prepend_to_path(existing_path: str | None, directory: Path) -> str:
    path_parts = [str(directory)]
    if existing_path:
        path_parts.append(existing_path)
    return os.pathsep.join(path_parts)


def _is_bundled_binary(gh_path: Path) -> bool:
    return gh_path.parent == BIN_DIR


def _parse_status_code(value: str) -> int | None:
    match = _STATUS_CODE_RE.search(value)
    if match is None:
        return None
    return int(match.group("status"))
