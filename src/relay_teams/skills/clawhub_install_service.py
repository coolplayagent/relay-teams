# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import os
from pathlib import Path
from time import perf_counter
import subprocess

from relay_teams.env.clawhub_cli import (
    install_clawhub_via_npm,
    resolve_existing_clawhub_path,
)
from relay_teams.env.clawhub_config_models import ClawHubConfig
from relay_teams.env.clawhub_env import (
    build_clawhub_cli_env,
    normalize_clawhub_token,
    resolve_clawhub_registry_from_env,
)
from relay_teams.paths import get_app_config_dir
from relay_teams.skills.clawhub_models import (
    ClawHubSkillInstallDiagnostics,
    ClawHubSkillInstallRequest,
    ClawHubSkillInstallResult,
    ClawHubSkillSummary,
)
from relay_teams.skills.clawhub_skill_service import ClawHubSkillService

_DEFAULT_TIMEOUT_SECONDS = 180.0
_DEFAULT_INSTALL_TIMEOUT_SECONDS = 180.0


class ClawHubSkillInstallService:
    def __init__(
        self,
        *,
        config_dir: Path,
        get_clawhub_config: Callable[[], ClawHubConfig],
        on_skill_installed: Callable[[], None] | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._get_clawhub_config = get_clawhub_config
        self._on_skill_installed = on_skill_installed

    def install(
        self,
        request: ClawHubSkillInstallRequest,
    ) -> ClawHubSkillInstallResult:
        token = (
            normalize_clawhub_token(request.token) or self._get_clawhub_config().token
        )
        return install_clawhub_skill(
            slug=request.slug,
            version=request.version,
            force=request.force,
            token=token,
            config_dir=self._config_dir,
            on_skill_installed=self._on_skill_installed,
        )


def install_clawhub_skill(
    *,
    slug: str,
    version: str | None = None,
    force: bool = False,
    token: str | None = None,
    config_dir: Path | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    on_skill_installed: Callable[[], None] | None = None,
) -> ClawHubSkillInstallResult:
    checked_at = datetime.now(timezone.utc)
    started = perf_counter()
    normalized_slug = slug.strip()
    normalized_version = _normalize_optional_text(version)
    normalized_token = normalize_clawhub_token(token)
    resolved_config_dir = (
        get_app_config_dir()
        if config_dir is None
        else config_dir.expanduser().resolve()
    )
    resolved_config_dir.mkdir(parents=True, exist_ok=True)
    clawhub_path = resolve_existing_clawhub_path()
    installation_attempted = False
    installed_during_install = False

    if clawhub_path is None:
        install_result = install_clawhub_via_npm(
            timeout_seconds=max(timeout_seconds, _DEFAULT_INSTALL_TIMEOUT_SECONDS)
        )
        installation_attempted = install_result.attempted
        if install_result.ok and install_result.clawhub_path is not None:
            clawhub_path = Path(install_result.clawhub_path)
            installed_during_install = True
        else:
            return _build_result(
                ok=False,
                slug=normalized_slug,
                requested_version=normalized_version,
                checked_at=checked_at,
                started=started,
                binary_available=False,
                token_configured=normalized_token is not None,
                installation_attempted=installation_attempted,
                installed_during_install=installed_during_install,
                workdir=resolved_config_dir,
                error_code=install_result.error_code or "clawhub_unavailable",
                error_message=install_result.error_message
                or "ClawHub CLI is not available on PATH.",
            )

    env = dict(os.environ)
    env.update(build_clawhub_cli_env(normalized_token))
    env["PATH"] = _prepend_to_path(env.get("PATH"), clawhub_path.parent)
    registry = resolve_clawhub_registry_from_env(env)
    command = [
        str(clawhub_path),
        "--workdir",
        str(resolved_config_dir),
        "--no-input",
        "install",
        normalized_slug,
    ]
    if normalized_version is not None:
        command.extend(["--version", normalized_version])
    if force:
        command.append("--force")

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _build_result(
            ok=False,
            slug=normalized_slug,
            requested_version=normalized_version,
            checked_at=checked_at,
            started=started,
            clawhub_path=clawhub_path,
            binary_available=True,
            token_configured=normalized_token is not None,
            installation_attempted=installation_attempted,
            installed_during_install=installed_during_install,
            registry=registry,
            workdir=resolved_config_dir,
            retryable=True,
            error_code="install_timeout",
            error_message=str(exc) or "ClawHub skill install timed out.",
        )

    if completed.returncode != 0:
        return _build_result(
            ok=False,
            slug=normalized_slug,
            requested_version=normalized_version,
            checked_at=checked_at,
            started=started,
            clawhub_path=clawhub_path,
            binary_available=True,
            token_configured=normalized_token is not None,
            installation_attempted=installation_attempted,
            installed_during_install=installed_during_install,
            registry=registry,
            workdir=resolved_config_dir,
            error_code="install_failed",
            error_message=_first_meaningful_line(completed.stderr, completed.stdout)
            or "ClawHub skill install failed.",
        )

    installed_skill = _load_installed_skill_summary(
        config_dir=resolved_config_dir,
        skill_id=normalized_slug,
    )
    if installed_skill is None:
        return _build_result(
            ok=False,
            slug=normalized_slug,
            requested_version=normalized_version,
            checked_at=checked_at,
            started=started,
            clawhub_path=clawhub_path,
            binary_available=True,
            token_configured=normalized_token is not None,
            installation_attempted=installation_attempted,
            installed_during_install=installed_during_install,
            registry=registry,
            workdir=resolved_config_dir,
            error_code="runtime_skill_unavailable",
            error_message=(
                "ClawHub installed the package, but Agent Teams could not discover "
                f"the runtime skill under {resolved_config_dir / 'skills' / normalized_slug}."
            ),
        )

    skills_reloaded = False
    if on_skill_installed is not None:
        try:
            on_skill_installed()
            skills_reloaded = True
        except Exception as exc:
            return _build_result(
                ok=False,
                slug=normalized_slug,
                requested_version=normalized_version,
                installed_skill=installed_skill,
                checked_at=checked_at,
                started=started,
                clawhub_path=clawhub_path,
                binary_available=True,
                token_configured=normalized_token is not None,
                installation_attempted=installation_attempted,
                installed_during_install=installed_during_install,
                registry=registry,
                workdir=resolved_config_dir,
                error_code="skills_reload_failed",
                error_message=str(exc),
            )

    return _build_result(
        ok=True,
        slug=normalized_slug,
        requested_version=normalized_version,
        installed_skill=installed_skill,
        checked_at=checked_at,
        started=started,
        clawhub_path=clawhub_path,
        binary_available=True,
        token_configured=normalized_token is not None,
        installation_attempted=installation_attempted,
        installed_during_install=installed_during_install,
        registry=registry,
        workdir=resolved_config_dir,
        skills_reloaded=skills_reloaded,
    )


def _build_result(
    *,
    ok: bool,
    slug: str,
    requested_version: str | None,
    checked_at: datetime,
    started: float,
    binary_available: bool,
    token_configured: bool,
    clawhub_path: Path | None = None,
    installed_skill: ClawHubSkillSummary | None = None,
    installation_attempted: bool = False,
    installed_during_install: bool = False,
    registry: str | None = None,
    workdir: Path | None = None,
    skills_reloaded: bool = False,
    retryable: bool = False,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ClawHubSkillInstallResult:
    return ClawHubSkillInstallResult(
        ok=ok,
        slug=slug,
        requested_version=requested_version,
        installed_skill=installed_skill,
        clawhub_path=None if clawhub_path is None else str(clawhub_path),
        latency_ms=max(0, int((perf_counter() - started) * 1000)),
        checked_at=checked_at,
        diagnostics=ClawHubSkillInstallDiagnostics(
            binary_available=binary_available,
            token_configured=token_configured,
            installation_attempted=installation_attempted,
            installed_during_install=installed_during_install,
            registry=registry,
            workdir=None if workdir is None else str(workdir),
            skills_reloaded=skills_reloaded,
        ),
        retryable=retryable,
        error_code=error_code,
        error_message=error_message,
    )


def _load_installed_skill_summary(
    *,
    config_dir: Path,
    skill_id: str,
) -> ClawHubSkillSummary | None:
    try:
        detail = ClawHubSkillService(config_dir=config_dir).get_skill(skill_id)
    except (KeyError, ValueError):
        return None
    return ClawHubSkillSummary(
        skill_id=detail.skill_id,
        runtime_name=detail.runtime_name,
        description=detail.description,
        ref=detail.ref,
        scope=detail.scope,
        directory=detail.directory,
        manifest_path=detail.manifest_path,
        valid=detail.valid,
        error=detail.error,
    )


def _first_meaningful_line(*chunks: str) -> str | None:
    for chunk in chunks:
        for line in chunk.splitlines():
            normalized_line = line.strip()
            if normalized_line:
                return normalized_line
    return None


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    return normalized_value or None


def _prepend_to_path(existing_path: str | None, directory: Path) -> str:
    path_parts = [str(directory)]
    if existing_path:
        path_parts.append(existing_path)
    return os.pathsep.join(path_parts)
