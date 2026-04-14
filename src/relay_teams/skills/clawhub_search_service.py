# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from time import perf_counter
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
from relay_teams.skills.clawhub_models import (
    ClawHubRemoteSkillSummary,
    ClawHubSkillSearchDiagnostics,
    ClawHubSkillSearchRequest,
    ClawHubSkillSearchResult,
)

_DEFAULT_TIMEOUT_SECONDS = 20.0
_DEFAULT_INSTALL_TIMEOUT_SECONDS = 180.0
_SEARCH_LINE_RE = re.compile(
    r"^(?P<slug>\S+)(?:\s+(?P<version>v?\d[^\s]*))?\s{2,}"
    r"(?P<title>.+?)\s+\((?P<score>-?\d+(?:\.\d+)?)\)\s*$"
)


class ClawHubSkillSearchService:
    def __init__(
        self,
        *,
        config_dir: Path,
        get_clawhub_config: Callable[[], ClawHubConfig],
    ) -> None:
        self._config_dir = config_dir
        self._get_clawhub_config = get_clawhub_config

    def search(
        self,
        request: ClawHubSkillSearchRequest,
    ) -> ClawHubSkillSearchResult:
        token = (
            normalize_clawhub_token(request.token) or self._get_clawhub_config().token
        )
        return search_clawhub_skills(
            query=request.query,
            limit=request.limit,
            token=token,
            config_dir=self._config_dir,
        )


def search_clawhub_skills(
    *,
    query: str,
    limit: int = 10,
    token: str | None = None,
    config_dir: Path | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> ClawHubSkillSearchResult:
    checked_at = datetime.now(timezone.utc)
    started = perf_counter()
    normalized_query = query.strip()
    normalized_token = normalize_clawhub_token(token)
    clawhub_path = resolve_existing_clawhub_path()
    installation_attempted = False
    installed_during_search = False

    if clawhub_path is None:
        install_result = install_clawhub_via_npm(
            timeout_seconds=max(timeout_seconds, _DEFAULT_INSTALL_TIMEOUT_SECONDS),
            base_env=build_clawhub_subprocess_env(
                None,
                config_dir=config_dir,
                base_env=os.environ,
            ),
        )
        installation_attempted = install_result.attempted
        if install_result.ok and install_result.clawhub_path is not None:
            clawhub_path = Path(install_result.clawhub_path)
            installed_during_search = True
        else:
            return _build_result(
                ok=False,
                query=normalized_query,
                checked_at=checked_at,
                started=started,
                binary_available=False,
                token_configured=normalized_token is not None,
                installation_attempted=installation_attempted,
                installed_during_search=installed_during_search,
                error_code=install_result.error_code or "clawhub_unavailable",
                error_message=install_result.error_message
                or "ClawHub CLI is not available on PATH.",
            )

    env = build_clawhub_subprocess_env(
        normalized_token,
        config_dir=config_dir,
        base_env=os.environ,
    )
    env["PATH"] = _prepend_to_path(env.get("PATH"), clawhub_path.parent)
    registry = resolve_clawhub_registry_from_env(env)
    endpoint_fallback_used = False
    command = [
        str(clawhub_path),
        "search",
        normalized_query,
        "--limit",
        str(limit),
    ]
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
            query=normalized_query,
            checked_at=checked_at,
            started=started,
            clawhub_path=clawhub_path,
            binary_available=True,
            token_configured=normalized_token is not None,
            installation_attempted=installation_attempted,
            installed_during_search=installed_during_search,
            registry=registry,
            endpoint_fallback_used=endpoint_fallback_used,
            retryable=True,
            error_code="search_timeout",
            error_message=str(exc) or "ClawHub skill search timed out.",
        )

    if completed.returncode != 0:
        reason = (
            summarize_clawhub_command_failure(completed.stderr, completed.stdout)
            or "ClawHub skill search failed."
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
                    str(exc) or "ClawHub skill search timed out.",
                )
                return _build_result(
                    ok=False,
                    query=normalized_query,
                    checked_at=checked_at,
                    started=started,
                    clawhub_path=clawhub_path,
                    binary_available=True,
                    token_configured=normalized_token is not None,
                    installation_attempted=installation_attempted,
                    installed_during_search=installed_during_search,
                    registry=registry,
                    endpoint_fallback_used=endpoint_fallback_used,
                    retryable=True,
                    error_code="search_timeout",
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
                    or "ClawHub skill search failed."
                )
                reason = combine_clawhub_failure_messages(reason, fallback_reason)
                return _build_result(
                    ok=False,
                    query=normalized_query,
                    checked_at=checked_at,
                    started=started,
                    clawhub_path=clawhub_path,
                    binary_available=True,
                    token_configured=normalized_token is not None,
                    installation_attempted=installation_attempted,
                    installed_during_search=installed_during_search,
                    registry=registry,
                    endpoint_fallback_used=endpoint_fallback_used,
                    error_code="search_failed",
                    error_message=explain_clawhub_failure(
                        reason,
                        endpoint_overrides_configured=registry is not None,
                        endpoint_fallback_used=endpoint_fallback_used,
                    ),
                )
        else:
            return _build_result(
                ok=False,
                query=normalized_query,
                checked_at=checked_at,
                started=started,
                clawhub_path=clawhub_path,
                binary_available=True,
                token_configured=normalized_token is not None,
                installation_attempted=installation_attempted,
                installed_during_search=installed_during_search,
                registry=registry,
                endpoint_fallback_used=endpoint_fallback_used,
                error_code="search_failed",
                error_message=explain_clawhub_failure(
                    reason,
                    endpoint_overrides_configured=registry is not None,
                    endpoint_fallback_used=endpoint_fallback_used,
                ),
            )

    try:
        items = _parse_search_output(completed.stdout)
    except ValueError as exc:
        return _build_result(
            ok=False,
            query=normalized_query,
            checked_at=checked_at,
            started=started,
            clawhub_path=clawhub_path,
            binary_available=True,
            token_configured=normalized_token is not None,
            installation_attempted=installation_attempted,
            installed_during_search=installed_during_search,
            registry=registry,
            endpoint_fallback_used=endpoint_fallback_used,
            error_code="search_parse_failed",
            error_message=str(exc),
        )

    return _build_result(
        ok=True,
        query=normalized_query,
        checked_at=checked_at,
        started=started,
        clawhub_path=clawhub_path,
        binary_available=True,
        token_configured=normalized_token is not None,
        installation_attempted=installation_attempted,
        installed_during_search=installed_during_search,
        registry=registry,
        endpoint_fallback_used=endpoint_fallback_used,
        items=items,
    )


def _build_result(
    *,
    ok: bool,
    query: str,
    checked_at: datetime,
    started: float,
    binary_available: bool,
    token_configured: bool,
    clawhub_path: Path | None = None,
    installation_attempted: bool = False,
    installed_during_search: bool = False,
    registry: str | None = None,
    endpoint_fallback_used: bool = False,
    items: tuple[ClawHubRemoteSkillSummary, ...] = (),
    retryable: bool = False,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ClawHubSkillSearchResult:
    return ClawHubSkillSearchResult(
        ok=ok,
        query=query,
        items=items,
        clawhub_path=None if clawhub_path is None else str(clawhub_path),
        latency_ms=max(0, int((perf_counter() - started) * 1000)),
        checked_at=checked_at,
        diagnostics=ClawHubSkillSearchDiagnostics(
            binary_available=binary_available,
            token_configured=token_configured,
            installation_attempted=installation_attempted,
            installed_during_search=installed_during_search,
            registry=registry,
            endpoint_fallback_used=endpoint_fallback_used,
        ),
        retryable=retryable,
        error_code=error_code,
        error_message=error_message,
    )


def _parse_search_output(raw_output: str) -> tuple[ClawHubRemoteSkillSummary, ...]:
    items: list[ClawHubRemoteSkillSummary] = []
    saw_unparseable_result_line = False
    for raw_line in raw_output.splitlines():
        normalized_line = raw_line.strip()
        if not normalized_line or normalized_line.startswith("- Searching"):
            continue
        parsed = _parse_search_line(normalized_line)
        if parsed is None:
            saw_unparseable_result_line = True
            continue
        items.append(parsed)
    if items:
        return tuple(items)
    if saw_unparseable_result_line:
        raise ValueError("ClawHub search returned an unexpected output format.")
    return ()


def _parse_search_line(raw_line: str) -> ClawHubRemoteSkillSummary | None:
    match = _SEARCH_LINE_RE.match(raw_line)
    if match is None:
        return None
    score_text = match.group("score")
    score = float(score_text) if score_text else None
    version = match.group("version")
    return ClawHubRemoteSkillSummary(
        slug=match.group("slug"),
        version=version,
        title=match.group("title").strip(),
        score=score,
    )


def _prepend_to_path(existing_path: str | None, directory: Path) -> str:
    path_parts = [str(directory)]
    if existing_path:
        path_parts.append(existing_path)
    return os.pathsep.join(path_parts)
