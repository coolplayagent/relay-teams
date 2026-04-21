# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time

from pydantic import BaseModel, ConfigDict

from relay_teams.env import build_subprocess_env
from relay_teams.workspace.ssh_profile_models import (
    SshProfileConfig,
    SshProfileConnectivityDiagnostics,
    SshProfileConnectivityProbeRequest,
    SshProfileConnectivityProbeResult,
    SshProfilePasswordRevealView,
    SshProfileRecord,
    SshProfileStoredConfig,
)
from relay_teams.workspace.ssh_profile_repository import SshProfileRepository
from relay_teams.workspace.ssh_profile_secret_store import (
    SshProfileSecretStore,
    get_ssh_profile_secret_store,
)

_DEFAULT_CONNECT_TIMEOUT_SECONDS = 15
_MAX_CONNECT_TIMEOUT_SECONDS = 300
_SSH_PROBE_COMMAND = "echo relay-teams-ssh-probe"


class _ResolvedSshProbeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssh_profile_id: str | None = None
    config: SshProfileConfig
    password: str | None = None
    private_key: str | None = None


class SshProfileService:
    def __init__(
        self,
        *,
        repository: SshProfileRepository,
        config_dir: Path,
        secret_store: SshProfileSecretStore | None = None,
        ssh_path_lookup: Callable[[str], str | None] | None = None,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._config_dir = Path(config_dir)
        self._secret_store = (
            get_ssh_profile_secret_store() if secret_store is None else secret_store
        )
        self._ssh_path_lookup = (
            shutil.which if ssh_path_lookup is None else ssh_path_lookup
        )
        self._process_runner = (
            subprocess.run if process_runner is None else process_runner
        )
        if now is None:
            self._now = lambda: datetime.now(timezone.utc)
        else:
            self._now = now

    def list_profiles(self) -> tuple[SshProfileRecord, ...]:
        return tuple(
            self._enrich_record(record) for record in self._repository.list_all()
        )

    def get_profile(self, ssh_profile_id: str) -> SshProfileRecord:
        return self._enrich_record(self._repository.get(ssh_profile_id))

    def reveal_password(self, ssh_profile_id: str) -> SshProfilePasswordRevealView:
        _ = self._repository.get(ssh_profile_id)
        return SshProfilePasswordRevealView(
            password=self._secret_store.get_password(
                self._config_dir,
                ssh_profile_id,
            )
        )

    def probe_connectivity(
        self,
        request: SshProfileConnectivityProbeRequest,
    ) -> SshProfileConnectivityProbeResult:
        checked_at = self._now()
        started = time.monotonic()
        resolved = self._resolve_probe_config(request)
        config = resolved.config
        timeout_seconds = self._resolve_probe_timeout_seconds(request, config)
        ssh_path = self._ssh_path_lookup("ssh")
        if ssh_path is None:
            return self._build_probe_result(
                ok=False,
                checked_at=checked_at,
                started=started,
                resolved=resolved,
                binary_available=False,
                host_reachable=False,
                exit_code=None,
                error_code="ssh_unavailable",
                error_message="ssh executable was not found on PATH.",
                retryable=False,
            )

        with TemporaryDirectory(prefix="relay-teams-ssh-probe-") as temp_dir:
            temp_root = Path(temp_dir)
            private_key_path = self._write_probe_private_key(
                temp_root=temp_root,
                private_key=resolved.private_key,
            )
            env = self._build_probe_env(
                temp_root=temp_root,
                password=resolved.password,
            )
            command = self._build_ssh_probe_command(
                ssh_path=ssh_path,
                config=config,
                private_key_path=private_key_path,
                timeout_seconds=timeout_seconds,
                uses_password=resolved.password is not None,
            )
            try:
                completed = self._process_runner(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    stdin=subprocess.DEVNULL,
                    timeout=timeout_seconds + 2,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return self._build_probe_result(
                    ok=False,
                    checked_at=checked_at,
                    started=started,
                    resolved=resolved,
                    binary_available=True,
                    host_reachable=False,
                    exit_code=None,
                    error_code="network_timeout",
                    error_message="SSH connection timed out.",
                    retryable=True,
                )
            except OSError as exc:
                return self._build_probe_result(
                    ok=False,
                    checked_at=checked_at,
                    started=started,
                    resolved=resolved,
                    binary_available=True,
                    host_reachable=False,
                    exit_code=None,
                    error_code="ssh_failed",
                    error_message=str(exc),
                    retryable=False,
                )

        if completed.returncode == 0:
            return self._build_probe_result(
                ok=True,
                checked_at=checked_at,
                started=started,
                resolved=resolved,
                binary_available=True,
                host_reachable=True,
                exit_code=completed.returncode,
            )

        error_message = _combined_process_output(completed)
        error_code, retryable, host_reachable = _classify_ssh_failure(
            error_message=error_message,
            returncode=completed.returncode,
        )
        return self._build_probe_result(
            ok=False,
            checked_at=checked_at,
            started=started,
            resolved=resolved,
            binary_available=True,
            host_reachable=host_reachable,
            exit_code=completed.returncode,
            error_code=error_code,
            error_message=error_message or "SSH probe failed.",
            retryable=retryable,
        )

    def save_profile(
        self,
        *,
        ssh_profile_id: str,
        config: SshProfileConfig,
    ) -> SshProfileRecord:
        existing = self._get_existing_record(ssh_profile_id)
        has_existing_private_key = (
            False
            if existing is None
            else self._secret_store.get_secret_flags(
                self._config_dir,
                ssh_profile_id,
            )[1]
        )
        record = self._repository.save(
            ssh_profile_id=ssh_profile_id,
            config=SshProfileStoredConfig(
                host=config.host,
                username=config.username,
                port=config.port,
                remote_shell=config.remote_shell,
                connect_timeout_seconds=config.connect_timeout_seconds,
                private_key_name=(
                    config.private_key_name
                    if config.private_key is not None
                    else (
                        existing.private_key_name
                        if existing is not None and has_existing_private_key
                        else None
                    )
                ),
            ),
        )
        if config.password is not None:
            self._secret_store.set_password(
                self._config_dir,
                ssh_profile_id,
                config.password,
            )
        if config.private_key is not None:
            self._secret_store.set_private_key(
                self._config_dir,
                ssh_profile_id,
                config.private_key,
            )
        return self._enrich_record(record)

    def delete_profile(self, ssh_profile_id: str) -> None:
        if not self._repository.exists(ssh_profile_id):
            raise KeyError(f"Unknown ssh_profile_id: {ssh_profile_id}")
        self._repository.delete(ssh_profile_id)
        self._secret_store.delete_profile_secrets(self._config_dir, ssh_profile_id)

    def require_profile(self, ssh_profile_id: str) -> SshProfileRecord:
        return self.get_profile(ssh_profile_id)

    def _enrich_record(self, record: SshProfileRecord) -> SshProfileRecord:
        has_password, has_private_key = self._secret_store.get_secret_flags(
            self._config_dir,
            record.ssh_profile_id,
        )
        return record.model_copy(
            update={
                "has_password": has_password,
                "has_private_key": has_private_key,
            }
        )

    def _get_existing_record(self, ssh_profile_id: str) -> SshProfileRecord | None:
        try:
            return self._repository.get(ssh_profile_id)
        except KeyError:
            return None

    def _resolve_probe_config(
        self,
        request: SshProfileConnectivityProbeRequest,
    ) -> _ResolvedSshProbeConfig:
        if request.ssh_profile_id is None:
            if request.override is None:
                raise ValueError("override is required when ssh_profile_id is omitted")
            return _ResolvedSshProbeConfig(
                ssh_profile_id=None,
                config=request.override,
                password=request.override.password,
                private_key=request.override.private_key,
            )

        record = self._repository.get(request.ssh_profile_id)
        override = request.override
        config = SshProfileConfig(
            host=override.host if override is not None else record.host,
            username=override.username if override is not None else record.username,
            password=override.password if override is not None else None,
            port=override.port if override is not None else record.port,
            remote_shell=(
                override.remote_shell if override is not None else record.remote_shell
            ),
            connect_timeout_seconds=(
                override.connect_timeout_seconds
                if override is not None
                else record.connect_timeout_seconds
            ),
            private_key=override.private_key if override is not None else None,
            private_key_name=(
                override.private_key_name
                if override is not None and override.private_key is not None
                else record.private_key_name
            ),
        )
        password = (
            config.password
            if config.password is not None
            else self._secret_store.get_password(
                self._config_dir, request.ssh_profile_id
            )
        )
        private_key = (
            config.private_key
            if config.private_key is not None
            else self._secret_store.get_private_key(
                self._config_dir,
                request.ssh_profile_id,
            )
        )
        return _ResolvedSshProbeConfig(
            ssh_profile_id=request.ssh_profile_id,
            config=config,
            password=password,
            private_key=private_key,
        )

    def _resolve_probe_timeout_seconds(
        self,
        request: SshProfileConnectivityProbeRequest,
        config: SshProfileConfig,
    ) -> float:
        if request.timeout_ms is not None:
            return min(request.timeout_ms / 1000.0, _MAX_CONNECT_TIMEOUT_SECONDS)
        if config.connect_timeout_seconds is not None:
            return min(
                float(config.connect_timeout_seconds),
                _MAX_CONNECT_TIMEOUT_SECONDS,
            )
        return float(_DEFAULT_CONNECT_TIMEOUT_SECONDS)

    def _write_probe_private_key(
        self,
        *,
        temp_root: Path,
        private_key: str | None,
    ) -> Path | None:
        if private_key is None:
            return None
        key_path = temp_root / "identity"
        key_path.write_text(f"{private_key.rstrip()}\n", encoding="utf-8")
        key_path.chmod(0o600)
        return key_path

    def _build_probe_env(
        self,
        *,
        temp_root: Path,
        password: str | None,
    ) -> dict[str, str]:
        if password is None:
            return build_subprocess_env(base_env=os.environ)

        askpass_path = self._write_askpass_script(temp_root)
        env = build_subprocess_env(
            base_env=os.environ,
            extra_env={
                "DISPLAY": os.environ.get("DISPLAY", "relay-teams"),
                "RELAY_TEAMS_SSH_PASSWORD": password,
                "SSH_ASKPASS": str(askpass_path),
                "SSH_ASKPASS_REQUIRE": "force",
            },
        )
        return env

    def _write_askpass_script(self, temp_root: Path) -> Path:
        if os.name == "nt":
            askpass_path = temp_root / "askpass.cmd"
            askpass_path.write_text(
                "@echo off\r\necho %RELAY_TEAMS_SSH_PASSWORD%\r\n",
                encoding="utf-8",
            )
            return askpass_path

        askpass_path = temp_root / "askpass.sh"
        askpass_path.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$RELAY_TEAMS_SSH_PASSWORD\"\n",
            encoding="utf-8",
        )
        askpass_path.chmod(0o700)
        return askpass_path

    def _build_ssh_probe_command(
        self,
        *,
        ssh_path: str,
        config: SshProfileConfig,
        private_key_path: Path | None,
        timeout_seconds: float,
        uses_password: bool,
    ) -> tuple[str, ...]:
        command = [
            ssh_path,
            "-o",
            f"ConnectTimeout={max(1, ceil(timeout_seconds))}",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=1",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "NumberOfPasswordPrompts=1",
            "-o",
            f"BatchMode={'no' if uses_password else 'yes'}",
        ]
        if private_key_path is not None:
            command.extend(["-i", str(private_key_path), "-o", "IdentitiesOnly=yes"])
        if config.port is not None:
            command.extend(["-p", str(config.port)])
        if config.username is not None:
            command.extend(["-l", config.username])
        command.extend([config.host, _SSH_PROBE_COMMAND])
        return tuple(command)

    def _build_probe_result(
        self,
        *,
        ok: bool,
        checked_at: datetime,
        started: float,
        resolved: _ResolvedSshProbeConfig,
        binary_available: bool,
        host_reachable: bool,
        exit_code: int | None,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool = False,
    ) -> SshProfileConnectivityProbeResult:
        config = resolved.config
        return SshProfileConnectivityProbeResult(
            ok=ok,
            ssh_profile_id=resolved.ssh_profile_id,
            host=config.host,
            port=config.port,
            username=config.username,
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
            checked_at=checked_at,
            diagnostics=SshProfileConnectivityDiagnostics(
                binary_available=binary_available,
                host_reachable=host_reachable,
                used_password=resolved.password is not None,
                used_private_key=resolved.private_key is not None,
                used_system_config=(
                    resolved.password is None and resolved.private_key is None
                ),
                exit_code=exit_code,
            ),
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
        )


def _combined_process_output(completed: subprocess.CompletedProcess[str]) -> str:
    parts = [completed.stderr or "", completed.stdout or ""]
    return "\n".join(part.strip() for part in parts if part.strip())[:2000]


def _classify_ssh_failure(
    *,
    error_message: str,
    returncode: int,
) -> tuple[str, bool, bool]:
    lowered = error_message.lower()
    if "permission denied" in lowered or "authentication failed" in lowered:
        return "auth_failed", False, True
    if "host key verification failed" in lowered:
        return "host_key_verification_failed", False, True
    if "remote host identification has changed" in lowered:
        return "host_key_verification_failed", False, True
    if "could not resolve hostname" in lowered:
        return "dns_failed", True, False
    if "name or service not known" in lowered:
        return "dns_failed", True, False
    if "connection timed out" in lowered or "operation timed out" in lowered:
        return "network_timeout", True, False
    if "connection refused" in lowered:
        return "connection_refused", True, False
    if "no route to host" in lowered or "network is unreachable" in lowered:
        return "network_unreachable", True, False
    if returncode == 255:
        return "ssh_failed", True, False
    return "ssh_failed", False, False
