# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import os
from pathlib import Path
from time import perf_counter
import subprocess

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.env.clawhub_cli import (
    install_clawhub_via_npm,
    resolve_existing_clawhub_path,
)
from relay_teams.env.clawhub_auth import (
    build_clawhub_managed_subprocess_env,
    ensure_clawhub_cli_login,
)
from relay_teams.env.clawhub_config_models import ClawHubConfig
from relay_teams.env.clawhub_command_errors import (
    combine_clawhub_failure_messages,
    explain_clawhub_failure,
    should_retry_clawhub_without_endpoint_overrides,
    summarize_clawhub_command_failure,
)
from relay_teams.env.clawhub_env import (
    build_clawhub_subprocess_env,
    normalize_clawhub_token,
    resolve_clawhub_registry_from_env,
    strip_clawhub_endpoint_overrides,
)

_MAX_CLAWHUB_PROBE_TIMEOUT_MS = 300_000
_DEFAULT_TIMEOUT_SECONDS = 15.0
_DEFAULT_INSTALL_TIMEOUT_SECONDS = 180.0


class ClawHubConnectivityProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str | None = None
    timeout_ms: int | None = Field(
        default=None,
        ge=1000,
        le=_MAX_CLAWHUB_PROBE_TIMEOUT_MS,
    )


class ClawHubConnectivityProbeDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binary_available: bool
    token_configured: bool
    installation_attempted: bool = False
    installed_during_probe: bool = False
    registry: str | None = None
    endpoint_fallback_used: bool = False


class ClawHubConnectivityProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    clawhub_path: str | None = None
    clawhub_version: str | None = None
    exit_code: int | None = None
    latency_ms: int = Field(ge=0)
    checked_at: datetime
    diagnostics: ClawHubConnectivityProbeDiagnostics
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None


class ClawHubConnectivityProbeService:
    def __init__(
        self,
        *,
        config_dir: Path,
        get_clawhub_config: Callable[[], ClawHubConfig],
    ) -> None:
        self._config_dir = config_dir
        self._get_clawhub_config = get_clawhub_config

    def probe(
        self,
        request: ClawHubConnectivityProbeRequest,
    ) -> ClawHubConnectivityProbeResult:
        checked_at = datetime.now(timezone.utc)
        started = perf_counter()
        probe_deadline = _resolve_probe_deadline(started, request.timeout_ms)
        token = (
            normalize_clawhub_token(request.token) or self._get_clawhub_config().token
        )
        clawhub_path = resolve_existing_clawhub_path()
        binary_available = clawhub_path is not None
        installation_attempted = False
        installed_during_probe = False

        if token is None:
            return self._build_result(
                ok=False,
                checked_at=checked_at,
                started=started,
                binary_available=binary_available,
                token_configured=False,
                clawhub_path=clawhub_path,
                installation_attempted=installation_attempted,
                installed_during_probe=installed_during_probe,
                error_code="missing_token",
                error_message="Configure a ClawHub token before testing the connection.",
            )

        if clawhub_path is None:
            install_timeout_seconds = _resolve_install_timeout_seconds(
                request.timeout_ms,
                probe_deadline=probe_deadline,
            )
            if install_timeout_seconds is None:
                return self._build_result(
                    ok=False,
                    checked_at=checked_at,
                    started=started,
                    binary_available=False,
                    token_configured=True,
                    clawhub_path=None,
                    installation_attempted=installation_attempted,
                    installed_during_probe=installed_during_probe,
                    retryable=True,
                    error_code="clawhub_install_timeout",
                    error_message="ClawHub CLI installation timed out.",
                )
            install_result = install_clawhub_via_npm(
                timeout_seconds=install_timeout_seconds,
                base_env=build_clawhub_subprocess_env(
                    None,
                    config_dir=self._config_dir,
                    base_env=os.environ,
                ),
            )
            installation_attempted = install_result.attempted
            if install_result.ok and install_result.clawhub_path is not None:
                clawhub_path = Path(install_result.clawhub_path)
                binary_available = True
                installed_during_probe = True
            else:
                return self._build_result(
                    ok=False,
                    checked_at=checked_at,
                    started=started,
                    binary_available=False,
                    token_configured=True,
                    installation_attempted=installation_attempted,
                    installed_during_probe=installed_during_probe,
                    error_code=install_result.error_code or "clawhub_unavailable",
                    error_message=install_result.error_message
                    or "ClawHub CLI is not available on PATH.",
                )

        env = build_clawhub_managed_subprocess_env(
            token,
            config_dir=self._config_dir,
            base_env=os.environ,
        )
        env["PATH"] = _prepend_to_path(env.get("PATH"), clawhub_path.parent)
        registry = resolve_clawhub_registry_from_env(env)
        endpoint_fallback_used = False
        version_text: str | None = None
        version_timeout_seconds = _resolve_remaining_timeout_seconds(probe_deadline)
        if version_timeout_seconds is not None:
            version_text = _read_clawhub_version(
                clawhub_path,
                env=env,
                timeout_seconds=version_timeout_seconds,
            )

        auth_timeout_seconds = _resolve_remaining_timeout_seconds(probe_deadline)
        if auth_timeout_seconds is None:
            return self._build_result(
                ok=False,
                checked_at=checked_at,
                started=started,
                binary_available=True,
                token_configured=True,
                clawhub_path=clawhub_path,
                clawhub_version=version_text,
                installation_attempted=installation_attempted,
                installed_during_probe=installed_during_probe,
                registry=registry,
                endpoint_fallback_used=endpoint_fallback_used,
                retryable=True,
                error_code="auth_timeout",
                error_message="ClawHub CLI auth bootstrap timed out.",
            )
        auth_result = ensure_clawhub_cli_login(
            token,
            config_dir=self._config_dir,
            base_env=os.environ,
            clawhub_path=clawhub_path,
            timeout_seconds=auth_timeout_seconds,
        )
        registry = auth_result.registry
        endpoint_fallback_used = auth_result.endpoint_fallback_used
        env = auth_result.env or env
        if not auth_result.ok:
            error_code, retryable = _classify_probe_error(
                auth_result.error_message or "ClawHub CLI auth bootstrap failed.",
                default_error_code=auth_result.error_code or "auth_failed",
            )
            return self._build_result(
                ok=False,
                checked_at=checked_at,
                started=started,
                binary_available=True,
                token_configured=True,
                clawhub_path=clawhub_path,
                clawhub_version=version_text,
                installation_attempted=installation_attempted,
                installed_during_probe=installed_during_probe,
                registry=registry,
                endpoint_fallback_used=endpoint_fallback_used,
                retryable=retryable,
                error_code=error_code,
                error_message=auth_result.error_message,
            )

        whoami_timeout_seconds = _resolve_remaining_timeout_seconds(probe_deadline)
        if whoami_timeout_seconds is None:
            return self._build_result(
                ok=False,
                checked_at=checked_at,
                started=started,
                binary_available=True,
                token_configured=True,
                clawhub_path=clawhub_path,
                clawhub_version=version_text,
                installation_attempted=installation_attempted,
                installed_during_probe=installed_during_probe,
                registry=registry,
                endpoint_fallback_used=endpoint_fallback_used,
                retryable=True,
                error_code="whoami_timeout",
                error_message="ClawHub CLI auth verification timed out.",
            )

        try:
            whoami_completed = subprocess.run(
                [str(clawhub_path), "whoami"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=whoami_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return self._build_result(
                ok=False,
                checked_at=checked_at,
                started=started,
                binary_available=True,
                token_configured=True,
                clawhub_path=clawhub_path,
                clawhub_version=version_text,
                installation_attempted=installation_attempted,
                installed_during_probe=installed_during_probe,
                registry=registry,
                endpoint_fallback_used=endpoint_fallback_used,
                retryable=True,
                error_code="whoami_timeout",
                error_message=str(exc) or "ClawHub CLI auth verification timed out.",
            )

        if whoami_completed.returncode != 0:
            reason = (
                summarize_clawhub_command_failure(
                    whoami_completed.stderr,
                    whoami_completed.stdout,
                )
                or "ClawHub CLI auth verification failed."
            )
            if (
                not endpoint_fallback_used
            ) and should_retry_clawhub_without_endpoint_overrides(
                reason,
                endpoint_overrides_configured=registry is not None,
            ):
                endpoint_fallback_used = True
                fallback_env = dict(env)
                strip_clawhub_endpoint_overrides(fallback_env)
                fallback_timeout_seconds = _resolve_remaining_timeout_seconds(
                    probe_deadline
                )
                if fallback_timeout_seconds is None:
                    formatted_reason = explain_clawhub_failure(
                        reason,
                        endpoint_overrides_configured=registry is not None,
                        endpoint_fallback_used=endpoint_fallback_used,
                    )
                    return self._build_result(
                        ok=False,
                        checked_at=checked_at,
                        started=started,
                        binary_available=True,
                        token_configured=True,
                        clawhub_path=clawhub_path,
                        clawhub_version=version_text,
                        installation_attempted=installation_attempted,
                        installed_during_probe=installed_during_probe,
                        registry=registry,
                        endpoint_fallback_used=endpoint_fallback_used,
                        retryable=True,
                        error_code="whoami_timeout",
                        error_message=formatted_reason,
                    )
                try:
                    fallback_completed = subprocess.run(
                        [str(clawhub_path), "whoami"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=fallback_env,
                        timeout=fallback_timeout_seconds,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    combined_reason = combine_clawhub_failure_messages(
                        reason,
                        str(exc) or "ClawHub CLI auth verification timed out.",
                    )
                    formatted_reason = explain_clawhub_failure(
                        combined_reason,
                        endpoint_overrides_configured=registry is not None,
                        endpoint_fallback_used=endpoint_fallback_used,
                    )
                    return self._build_result(
                        ok=False,
                        checked_at=checked_at,
                        started=started,
                        binary_available=True,
                        token_configured=True,
                        clawhub_path=clawhub_path,
                        clawhub_version=version_text,
                        installation_attempted=installation_attempted,
                        installed_during_probe=installed_during_probe,
                        registry=registry,
                        endpoint_fallback_used=endpoint_fallback_used,
                        retryable=True,
                        error_code="whoami_timeout",
                        error_message=formatted_reason,
                    )
                if fallback_completed.returncode == 0:
                    return self._build_result(
                        ok=True,
                        checked_at=checked_at,
                        started=started,
                        binary_available=True,
                        token_configured=True,
                        clawhub_path=clawhub_path,
                        clawhub_version=version_text,
                        exit_code=fallback_completed.returncode,
                        installation_attempted=installation_attempted,
                        installed_during_probe=installed_during_probe,
                        registry=registry,
                        endpoint_fallback_used=endpoint_fallback_used,
                    )
                fallback_reason = (
                    summarize_clawhub_command_failure(
                        fallback_completed.stderr,
                        fallback_completed.stdout,
                    )
                    or "ClawHub CLI auth verification failed."
                )
                whoami_completed = fallback_completed
                reason = combine_clawhub_failure_messages(reason, fallback_reason)
            formatted_reason = explain_clawhub_failure(
                reason,
                endpoint_overrides_configured=registry is not None,
                endpoint_fallback_used=endpoint_fallback_used,
            )
            error_code, retryable = _classify_probe_error(
                formatted_reason,
                default_error_code="auth_failed",
            )
            return self._build_result(
                ok=False,
                checked_at=checked_at,
                started=started,
                binary_available=True,
                token_configured=True,
                clawhub_path=clawhub_path,
                clawhub_version=version_text,
                exit_code=whoami_completed.returncode,
                installation_attempted=installation_attempted,
                installed_during_probe=installed_during_probe,
                registry=registry,
                endpoint_fallback_used=endpoint_fallback_used,
                retryable=retryable,
                error_code=error_code,
                error_message=formatted_reason,
            )

        return self._build_result(
            ok=True,
            checked_at=checked_at,
            started=started,
            binary_available=True,
            token_configured=True,
            clawhub_path=clawhub_path,
            clawhub_version=version_text,
            exit_code=whoami_completed.returncode,
            installation_attempted=installation_attempted,
            installed_during_probe=installed_during_probe,
            registry=registry,
            endpoint_fallback_used=endpoint_fallback_used,
        )

    def _build_result(
        self,
        *,
        ok: bool,
        checked_at: datetime,
        started: float,
        binary_available: bool,
        token_configured: bool,
        clawhub_path: Path | None = None,
        clawhub_version: str | None = None,
        exit_code: int | None = None,
        installation_attempted: bool = False,
        installed_during_probe: bool = False,
        registry: str | None = None,
        endpoint_fallback_used: bool = False,
        retryable: bool = False,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ClawHubConnectivityProbeResult:
        return ClawHubConnectivityProbeResult(
            ok=ok,
            clawhub_path=None if clawhub_path is None else str(clawhub_path),
            clawhub_version=clawhub_version,
            exit_code=exit_code,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            checked_at=checked_at,
            diagnostics=ClawHubConnectivityProbeDiagnostics(
                binary_available=binary_available,
                token_configured=token_configured,
                installation_attempted=installation_attempted,
                installed_during_probe=installed_during_probe,
                registry=registry,
                endpoint_fallback_used=endpoint_fallback_used,
            ),
            retryable=retryable,
            error_code=error_code,
            error_message=error_message,
        )


def _prepend_to_path(existing_path: str | None, directory: Path) -> str:
    path_parts = [str(directory)]
    if existing_path:
        path_parts.append(existing_path)
    return os.pathsep.join(path_parts)


def _read_clawhub_version(
    clawhub_path: Path,
    *,
    env: dict[str, str],
    timeout_seconds: float,
) -> str | None:
    try:
        completed = subprocess.run(
            [str(clawhub_path), "--cli-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return _normalize_clawhub_version_text(
        _resolve_command_output_text(completed.stdout, completed.stderr)
    )


def _resolve_command_output_text(*candidates: str) -> str | None:
    for candidate in candidates:
        non_empty_lines = [
            line.strip() for line in candidate.splitlines() if line.strip()
        ]
        if non_empty_lines:
            return non_empty_lines[-1]
    return None


def _normalize_clawhub_version_text(version_text: str | None) -> str | None:
    if version_text is None:
        return None
    if version_text.lower().startswith("clawhub "):
        return version_text
    if version_text and version_text[0].isdigit():
        return f"clawhub {version_text}"
    return version_text


def _resolve_probe_deadline(started: float, timeout_ms: int | None) -> float:
    return started + _resolve_probe_timeout_seconds(timeout_ms)


def _resolve_probe_timeout_seconds(timeout_ms: int | None) -> float:
    if timeout_ms is None:
        return _DEFAULT_TIMEOUT_SECONDS
    return timeout_ms / 1000.0


def _resolve_remaining_timeout_seconds(deadline: float) -> float | None:
    remaining_seconds = deadline - perf_counter()
    if remaining_seconds <= 0:
        return None
    return max(remaining_seconds, 0.001)


def _resolve_install_timeout_seconds(
    timeout_ms: int | None,
    *,
    probe_deadline: float | None = None,
) -> float | None:
    if timeout_ms is None:
        return _DEFAULT_INSTALL_TIMEOUT_SECONDS
    if probe_deadline is None:
        return _resolve_probe_timeout_seconds(timeout_ms)
    return _resolve_remaining_timeout_seconds(probe_deadline)


def _classify_probe_error(
    error_message: str,
    *,
    default_error_code: str,
) -> tuple[str, bool]:
    lowered = error_message.lower()
    if (
        "not logged in" in lowered
        or "invalid token" in lowered
        or "unauthorized" in lowered
        or "forbidden" in lowered
    ):
        return ("auth_failed", False)
    if "timed out" in lowered or "timeout" in lowered:
        return ("network_timeout", True)
    if "could not resolve host" in lowered or "dns" in lowered:
        return ("dns_error", True)
    if "proxy" in lowered:
        return ("proxy_error", True)
    if "network" in lowered or "connect" in lowered:
        return ("network_error", True)
    return (default_error_code, False)
