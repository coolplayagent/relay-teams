# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import shutil
import subprocess

from pydantic import BaseModel, ConfigDict

from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

CLAWHUB_NPM_PACKAGE_NAME = "clawhub"
CLAWHUB_PREFERRED_NPM_REGISTRY = "https://mirrors.huaweicloud.com/repository/npm/"
_DEFAULT_INSTALL_TIMEOUT_SECONDS = 180.0
_NPM_RESOLVE_TIMEOUT_SECONDS = 10.0
_WINDOWS_EXECUTABLE_SUFFIXES = (".cmd", ".ps1", ".exe", "")
_POSIX_EXECUTABLE_SUFFIXES = ("",)

_clawhub_path_cache: Path | None = None
_clawhub_path_cache_ready = False


class ClawHubCliInstallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    attempted: bool
    clawhub_path: str | None = None
    npm_path: str | None = None
    registry: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def clear_clawhub_path_cache() -> None:
    global _clawhub_path_cache
    global _clawhub_path_cache_ready

    _clawhub_path_cache = None
    _clawhub_path_cache_ready = False


def resolve_existing_clawhub_path() -> Path | None:
    global _clawhub_path_cache
    global _clawhub_path_cache_ready

    if _clawhub_path_cache is not None and _clawhub_path_cache.is_file():
        return _clawhub_path_cache
    if _clawhub_path_cache_ready:
        return None

    try:
        system_path = resolve_system_clawhub_path()
        if system_path is not None:
            _clawhub_path_cache = system_path
            _clawhub_path_cache_ready = True
            return system_path

        npm_global_path = resolve_npm_global_clawhub_path()
        if npm_global_path is not None:
            _clawhub_path_cache = npm_global_path
            _clawhub_path_cache_ready = True
            return npm_global_path
    except Exception:
        _clawhub_path_cache = None
        _clawhub_path_cache_ready = True
        return None

    _clawhub_path_cache = None
    _clawhub_path_cache_ready = True
    return None


def resolve_system_clawhub_path() -> Path | None:
    resolved = shutil.which("clawhub")
    if not resolved:
        return None
    path = Path(resolved)
    return path if path.is_file() else None


def resolve_npm_path() -> Path | None:
    for candidate in ("npm", "npm.cmd", "npm.exe"):
        resolved = shutil.which(candidate)
        if not resolved:
            continue
        path = Path(resolved)
        if path.is_file():
            return path
    return None


def resolve_npm_global_bin_dir(
    npm_path: Path | None = None,
    *,
    base_env: Mapping[str, str] | None = None,
) -> Path | None:
    resolved_npm_path = resolve_npm_path() if npm_path is None else npm_path
    if resolved_npm_path is None:
        return None
    env = dict(os.environ if base_env is None else base_env)
    try:
        completed = subprocess.run(
            [str(resolved_npm_path), "config", "get", "prefix"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=_NPM_RESOLVE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    prefix_text = _first_meaningful_line(completed.stdout, completed.stderr)
    if prefix_text is None:
        return None
    normalized_prefix = prefix_text.strip()
    if not normalized_prefix or normalized_prefix.lower() == "undefined":
        return None
    prefix_path = Path(normalized_prefix)
    if os.name == "nt":
        return prefix_path
    return prefix_path / "bin"


def resolve_npm_global_clawhub_path(
    npm_path: Path | None = None,
    *,
    base_env: Mapping[str, str] | None = None,
) -> Path | None:
    global_bin_dir = resolve_npm_global_bin_dir(npm_path, base_env=base_env)
    if global_bin_dir is None:
        return None
    for suffix in _candidate_executable_suffixes():
        candidate = global_bin_dir / f"clawhub{suffix}"
        if candidate.is_file():
            return candidate
    return None


def install_clawhub_via_npm(
    *,
    timeout_seconds: float = _DEFAULT_INSTALL_TIMEOUT_SECONDS,
    base_env: Mapping[str, str] | None = None,
) -> ClawHubCliInstallResult:
    npm_path = resolve_npm_path()
    if npm_path is None:
        return ClawHubCliInstallResult(
            ok=False,
            attempted=False,
            error_code="npm_unavailable",
            error_message="npm is not available on PATH, so ClawHub CLI could not be installed automatically.",
        )

    npm_bin_dir = resolve_npm_global_bin_dir(npm_path, base_env=base_env)
    command_env = dict(os.environ if base_env is None else base_env)
    if npm_bin_dir is not None:
        command_env["PATH"] = _prepend_to_path(command_env.get("PATH"), npm_bin_dir)

    last_error_message = "Failed to install ClawHub CLI."
    attempted = False
    for registry in _registry_candidates():
        attempted = True
        command = [
            str(npm_path),
            "install",
            "--global",
            "--loglevel",
            "error",
            CLAWHUB_NPM_PACKAGE_NAME,
        ]
        if registry is not None:
            command.extend(["--registry", registry])
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=command_env,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            last_error_message = (
                "ClawHub CLI installation timed out while waiting for npm."
            )
            LOGGER.warning(
                "Timed out installing ClawHub CLI via npm registry %s",
                registry or "default",
            )
            continue
        except OSError as exc:
            last_error_message = str(exc) or "Failed to execute npm."
            LOGGER.warning(
                "Failed to execute npm while installing ClawHub CLI: %s",
                exc,
            )
            continue

        if completed.returncode == 0:
            clear_clawhub_path_cache()
            installed_path = resolve_existing_clawhub_path()
            if installed_path is not None:
                LOGGER.info("Installed ClawHub CLI at %s", installed_path)
                return ClawHubCliInstallResult(
                    ok=True,
                    attempted=True,
                    clawhub_path=str(installed_path),
                    npm_path=str(npm_path),
                    registry=registry,
                )
            last_error_message = "ClawHub CLI installation completed, but the executable could not be found afterwards."
            LOGGER.warning(last_error_message)
            continue

        last_error_message = (
            _first_meaningful_line(
                completed.stderr,
                completed.stdout,
            )
            or "Failed to install ClawHub CLI."
        )
        LOGGER.warning(
            "Failed to install ClawHub CLI via npm registry %s: %s",
            registry or "default",
            last_error_message,
        )

    return ClawHubCliInstallResult(
        ok=False,
        attempted=attempted,
        npm_path=str(npm_path),
        error_code="clawhub_install_failed",
        error_message=last_error_message,
    )


def _candidate_executable_suffixes() -> tuple[str, ...]:
    if os.name == "nt":
        return _WINDOWS_EXECUTABLE_SUFFIXES
    return _POSIX_EXECUTABLE_SUFFIXES


def _first_meaningful_line(*chunks: str) -> str | None:
    for chunk in chunks:
        for line in chunk.splitlines():
            normalized = line.strip()
            if normalized:
                return normalized
    return None


def _prepend_to_path(existing_path: str | None, directory: Path) -> str:
    path_parts = [str(directory)]
    if existing_path:
        path_parts.append(existing_path)
    return os.pathsep.join(path_parts)


def _registry_candidates() -> tuple[str | None, ...]:
    return (CLAWHUB_PREFERRED_NPM_REGISTRY, None)
