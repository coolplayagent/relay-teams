# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import shutil
import subprocess

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.env.clawhub_cli import resolve_existing_clawhub_path
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

_DEFAULT_CLAWHUB_LOGIN_TIMEOUT_SECONDS = 30.0
_CLAWHUB_RUNTIME_HOME_PARTS = ("runtime", "clawhub-home")


class ClawHubCliLoginResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    env: dict[str, str] = Field(default_factory=dict)
    registry: str | None = None
    endpoint_fallback_used: bool = False
    error_code: str | None = None
    error_message: str | None = None


def get_clawhub_runtime_home(config_dir: Path) -> Path:
    resolved_config_dir = config_dir.expanduser().resolve()
    return resolved_config_dir.joinpath(*_CLAWHUB_RUNTIME_HOME_PARTS)


def get_clawhub_runtime_config_path(config_dir: Path) -> Path:
    return get_clawhub_runtime_home(config_dir) / ".config" / "clawhub" / "config.json"


def clear_clawhub_runtime_home(config_dir: Path) -> None:
    runtime_home = get_clawhub_runtime_home(config_dir)
    shutil.rmtree(runtime_home, ignore_errors=True)


def build_clawhub_managed_subprocess_env(
    token: str | None,
    *,
    config_dir: Path,
    base_env: Mapping[str, str] | None = None,
    site: str | None = None,
    registry: str | None = None,
) -> dict[str, str]:
    resolved_env = build_clawhub_subprocess_env(
        token,
        config_dir=config_dir,
        base_env=base_env,
        site=site,
        registry=registry,
    )
    runtime_home = get_clawhub_runtime_home(config_dir)
    xdg_config_home = runtime_home / ".config"
    resolved_env["HOME"] = str(runtime_home)
    resolved_env["USERPROFILE"] = str(runtime_home)
    resolved_env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    return resolved_env


def ensure_clawhub_cli_login(
    token: str | None,
    *,
    config_dir: Path,
    base_env: Mapping[str, str] | None = None,
    clawhub_path: Path | None = None,
    timeout_seconds: float = _DEFAULT_CLAWHUB_LOGIN_TIMEOUT_SECONDS,
) -> ClawHubCliLoginResult:
    normalized_token = normalize_clawhub_token(token)
    if normalized_token is None:
        return ClawHubCliLoginResult(
            ok=False,
            error_code="missing_token",
            error_message="Configure a ClawHub token before authenticating the CLI.",
        )

    resolved_clawhub_path = (
        resolve_existing_clawhub_path() if clawhub_path is None else clawhub_path
    )
    if resolved_clawhub_path is None:
        return ClawHubCliLoginResult(
            ok=False,
            error_code="clawhub_unavailable",
            error_message="ClawHub CLI is not available on PATH.",
        )

    resolved_config_dir = config_dir.expanduser().resolve()
    resolved_env = build_clawhub_managed_subprocess_env(
        normalized_token,
        config_dir=resolved_config_dir,
        base_env=base_env,
    )
    resolved_env["PATH"] = _prepend_to_path(
        resolved_env.get("PATH"),
        resolved_clawhub_path.parent,
    )
    registry = resolve_clawhub_registry_from_env(resolved_env)
    if _stored_runtime_token_matches(resolved_config_dir, normalized_token):
        return ClawHubCliLoginResult(
            ok=True,
            env=resolved_env,
            registry=registry,
        )

    command = [
        str(resolved_clawhub_path),
        "login",
        "--token",
        normalized_token,
        "--no-browser",
    ]
    completed = _run_login_command(
        command,
        env=resolved_env,
        timeout_seconds=timeout_seconds,
    )
    if completed.returncode == 0:
        return ClawHubCliLoginResult(
            ok=True,
            env=resolved_env,
            registry=registry,
        )

    reason = (
        summarize_clawhub_command_failure(completed.stderr, completed.stdout)
        or "ClawHub login failed."
    )
    if should_retry_clawhub_without_endpoint_overrides(
        reason,
        endpoint_overrides_configured=registry is not None,
    ):
        fallback_env = dict(resolved_env)
        strip_clawhub_endpoint_overrides(fallback_env)
        fallback_completed = _run_login_command(
            command,
            env=fallback_env,
            timeout_seconds=timeout_seconds,
        )
        if fallback_completed.returncode == 0:
            return ClawHubCliLoginResult(
                ok=True,
                env=fallback_env,
                registry=registry,
                endpoint_fallback_used=True,
            )
        fallback_reason = (
            summarize_clawhub_command_failure(
                fallback_completed.stderr,
                fallback_completed.stdout,
            )
            or "ClawHub login failed."
        )
        reason = combine_clawhub_failure_messages(reason, fallback_reason)
        return ClawHubCliLoginResult(
            ok=False,
            env=fallback_env,
            registry=registry,
            endpoint_fallback_used=True,
            error_code="auth_failed",
            error_message=explain_clawhub_failure(
                reason,
                endpoint_overrides_configured=registry is not None,
                endpoint_fallback_used=True,
            ),
        )

    return ClawHubCliLoginResult(
        ok=False,
        env=resolved_env,
        registry=registry,
        error_code="auth_failed",
        error_message=reason,
    )


def _run_login_command(
    command: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout_seconds,
        check=False,
    )


def _stored_runtime_token_matches(config_dir: Path, token: str) -> bool:
    config_file = get_clawhub_runtime_config_path(config_dir)
    if not config_file.exists() or not config_file.is_file():
        return False
    try:
        payload = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    stored_token = normalize_clawhub_token(_read_text_field(payload, "token"))
    return stored_token == token


def _read_text_field(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    return value


def _prepend_to_path(existing_path: str | None, directory: Path) -> str:
    path_parts = [str(directory)]
    if existing_path:
        path_parts.append(existing_path)
    return os.pathsep.join(path_parts)
