# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import os
from pathlib import Path
from time import perf_counter
import re
import subprocess

from relay_teams.env.clawhub_cli import (
    install_clawhub_via_npm,
    resolve_existing_clawhub_path,
)
from relay_teams.env.clawhub_command_errors import (
    combine_clawhub_failure_messages,
    explain_clawhub_failure,
    should_retry_clawhub_without_endpoint_overrides,
    summarize_clawhub_command_failure,
)
from relay_teams.env.clawhub_config_models import ClawHubConfig
from relay_teams.env.clawhub_env import (
    build_clawhub_subprocess_env,
    normalize_clawhub_token,
    resolve_clawhub_registry_from_env,
    strip_clawhub_endpoint_overrides,
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
_INSTALLABLE_SKILL_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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
    normalized_version = _normalize_optional_text(version)
    normalized_token = normalize_clawhub_token(token)
    try:
        normalized_slug = _normalize_installable_slug(slug)
    except ValueError as exc:
        return _build_result(
            ok=False,
            slug=slug.strip() or "<invalid>",
            requested_version=normalized_version,
            checked_at=checked_at,
            started=started,
            binary_available=False,
            token_configured=normalized_token is not None,
            error_code="unsupported_slug",
            error_message=str(exc),
        )
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
            timeout_seconds=max(timeout_seconds, _DEFAULT_INSTALL_TIMEOUT_SECONDS),
            base_env=build_clawhub_subprocess_env(
                None,
                config_dir=resolved_config_dir,
                base_env=os.environ,
            ),
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

    env = build_clawhub_subprocess_env(
        normalized_token,
        config_dir=resolved_config_dir,
        base_env=os.environ,
    )
    env["PATH"] = _prepend_to_path(env.get("PATH"), clawhub_path.parent)
    registry = resolve_clawhub_registry_from_env(env)
    endpoint_fallback_used = False
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
    deadline = started + timeout_seconds

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
            endpoint_fallback_used=endpoint_fallback_used,
            workdir=resolved_config_dir,
            retryable=True,
            error_code="install_timeout",
            error_message=str(exc) or "ClawHub skill install timed out.",
        )

    if completed.returncode != 0:
        reason = (
            summarize_clawhub_command_failure(completed.stderr, completed.stdout)
            or "ClawHub skill install failed."
        )
        if should_retry_clawhub_without_endpoint_overrides(
            reason,
            endpoint_overrides_configured=registry is not None,
        ):
            endpoint_fallback_used = True
            fallback_env = dict(env)
            strip_clawhub_endpoint_overrides(fallback_env)
            remaining_timeout_seconds = max(deadline - perf_counter(), 0.001)
            try:
                fallback_completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=fallback_env,
                    timeout=remaining_timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                combined_reason = combine_clawhub_failure_messages(
                    reason,
                    str(exc) or "ClawHub skill install timed out.",
                )
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
                    endpoint_fallback_used=endpoint_fallback_used,
                    workdir=resolved_config_dir,
                    retryable=True,
                    error_code="install_timeout",
                    error_message=explain_clawhub_failure(
                        combined_reason,
                        endpoint_overrides_configured=registry is not None,
                        endpoint_fallback_used=endpoint_fallback_used,
                    ),
                )
            if fallback_completed.returncode == 0:
                completed = fallback_completed
            else:
                fallback_reason = (
                    summarize_clawhub_command_failure(
                        fallback_completed.stderr,
                        fallback_completed.stdout,
                    )
                    or "ClawHub skill install failed."
                )
                reason = combine_clawhub_failure_messages(reason, fallback_reason)
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
                    endpoint_fallback_used=endpoint_fallback_used,
                    workdir=resolved_config_dir,
                    error_code="install_failed",
                    error_message=explain_clawhub_failure(
                        reason,
                        endpoint_overrides_configured=registry is not None,
                        endpoint_fallback_used=endpoint_fallback_used,
                    ),
                )
        else:
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
                endpoint_fallback_used=endpoint_fallback_used,
                workdir=resolved_config_dir,
                error_code="install_failed",
                error_message=explain_clawhub_failure(
                    reason,
                    endpoint_overrides_configured=registry is not None,
                    endpoint_fallback_used=endpoint_fallback_used,
                ),
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
            endpoint_fallback_used=endpoint_fallback_used,
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
                endpoint_fallback_used=endpoint_fallback_used,
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
        endpoint_fallback_used=endpoint_fallback_used,
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
    endpoint_fallback_used: bool = False,
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
            endpoint_fallback_used=endpoint_fallback_used,
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


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    return normalized_value or None


def _normalize_installable_slug(value: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError("ClawHub skill slug cannot be blank.")
    if not _INSTALLABLE_SKILL_SLUG_PATTERN.fullmatch(normalized_value):
        raise ValueError(
            "Unsupported ClawHub skill slug. Use letters, digits, '.', '_', or '-'."
        )
    return normalized_value


def _prepend_to_path(existing_path: str | None, directory: Path) -> str:
    path_parts = [str(directory)]
    if existing_path:
        path_parts.append(existing_path)
    return os.pathsep.join(path_parts)
