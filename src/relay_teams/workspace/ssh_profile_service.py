# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
from math import ceil
from pathlib import Path
import os
import shutil
import shlex
import subprocess
from tempfile import TemporaryDirectory, mkdtemp
import time

from pydantic import BaseModel, ConfigDict

from relay_teams.env import build_subprocess_env
from relay_teams.workspace.ssh_profile_models import (
    SshProfileCommandResult,
    SshProfileConfig,
    SshProfileConnectivityDiagnostics,
    SshProfileConnectivityProbeRequest,
    SshProfileConnectivityProbeResult,
    SshProfilePasswordRevealView,
    SshProfilePreparedCommand,
    SshProfileRecord,
    SshProfileStoredConfig,
)
from relay_teams.workspace.ssh_profile_repository import SshProfileRepository
from relay_teams.workspace.ssh_profile_secret_store import (
    SshProfileSecretStore,
    get_ssh_profile_secret_store,
)

_DEFAULT_CONNECT_TIMEOUT_SECONDS = 15
_DEFAULT_COMMAND_TIMEOUT_SECONDS = 30
_MAX_CONNECT_TIMEOUT_SECONDS = 300
_SSH_PROBE_COMMAND = "echo relay-teams-ssh-probe"
_SSH_USERNAME_REQUIRED_MESSAGE = (
    "SSH profile username is required. Edit the SSH profile and set the "
    "remote login username before using it."
)


class _ResolvedSshProbeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssh_profile_id: str | None = None
    config: SshProfileConfig
    password: str | None = None
    private_key: str | None = None


class _SshFilesystemMountSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    ssh_profile_id: str
    host: str
    username: str
    port: int | None = None
    remote_root: str


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

    def run_remote_command(
        self,
        *,
        ssh_profile_id: str,
        command: str,
        timeout_seconds: float | None = None,
    ) -> SshProfileCommandResult:
        remote_command = command.strip()
        if not remote_command:
            raise ValueError("SSH remote command must not be empty")
        request = SshProfileConnectivityProbeRequest(ssh_profile_id=ssh_profile_id)
        resolved = self._resolve_probe_config(request)
        connect_timeout_seconds = self._resolve_probe_timeout_seconds(
            request,
            resolved.config,
        )
        command_timeout_seconds = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(_DEFAULT_COMMAND_TIMEOUT_SECONDS)
        )
        if command_timeout_seconds <= 0:
            raise ValueError("SSH remote command timeout must be positive")

        ssh_path = self._ssh_path_lookup("ssh")
        if ssh_path is None:
            raise ValueError("ssh executable was not found on PATH.")

        with TemporaryDirectory(prefix="relay-teams-ssh-command-") as temp_dir:
            temp_root = Path(temp_dir)
            private_key_path = self._write_probe_private_key(
                temp_root=temp_root,
                private_key=resolved.private_key,
            )
            env = self._build_probe_env(
                temp_root=temp_root,
                password=resolved.password,
            )
            ssh_command = self._build_ssh_command(
                ssh_path=ssh_path,
                config=resolved.config,
                private_key_path=private_key_path,
                timeout_seconds=connect_timeout_seconds,
                uses_password=resolved.password is not None,
                remote_command=remote_command,
            )
            try:
                completed = self._process_runner(
                    ssh_command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    stdin=subprocess.DEVNULL,
                    timeout=connect_timeout_seconds + command_timeout_seconds + 2,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError("SSH remote command timed out.") from exc
            except OSError as exc:
                raise ValueError(f"SSH remote command failed: {exc}") from exc

        return SshProfileCommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def prepare_remote_command(
        self,
        *,
        ssh_profile_id: str,
        command: str,
        cwd: str,
        env: dict[str, str] | None = None,
        tty: bool = False,
    ) -> SshProfilePreparedCommand:
        remote_command = command.strip()
        if not remote_command:
            raise ValueError("SSH remote command must not be empty")
        remote_cwd = cwd.strip()
        if not remote_cwd:
            raise ValueError("SSH remote command cwd must not be empty")
        request = SshProfileConnectivityProbeRequest(ssh_profile_id=ssh_profile_id)
        resolved = self._resolve_probe_config(request)
        ssh_path = self._ssh_path_lookup("ssh")
        if ssh_path is None:
            raise ValueError("ssh executable was not found on PATH.")
        temp_root = Path(mkdtemp(prefix="relay-teams-ssh-process-"))
        private_key_path = self._write_probe_private_key(
            temp_root=temp_root,
            private_key=resolved.private_key,
        )
        process_env = self._build_probe_env(
            temp_root=temp_root,
            password=resolved.password,
        )
        argv = self._build_ssh_command(
            ssh_path=ssh_path,
            config=resolved.config,
            private_key_path=private_key_path,
            timeout_seconds=self._resolve_probe_timeout_seconds(
                request,
                resolved.config,
            ),
            uses_password=resolved.password is not None,
            remote_command=self._build_remote_shell_command(
                config=resolved.config,
                command=remote_command,
                cwd=remote_cwd,
                env=env,
            ),
            allocate_tty=tty,
        )
        return SshProfilePreparedCommand(
            argv=argv,
            env=process_env,
            temp_root=temp_root,
        )

    def ensure_filesystem_mount(
        self,
        *,
        ssh_profile_id: str,
        remote_root: str,
        local_root: Path,
    ) -> None:
        resolved_local_root = local_root.expanduser().resolve()
        resolved_local_root.mkdir(parents=True, exist_ok=True)
        resolved = self._resolve_probe_config(
            SshProfileConnectivityProbeRequest(ssh_profile_id=ssh_profile_id)
        )
        config = resolved.config
        normalized_remote_root = remote_root.strip()
        if not normalized_remote_root:
            raise ValueError("SSH filesystem mount remote root must not be empty")
        signature = self._build_filesystem_mount_signature(
            ssh_profile_id=ssh_profile_id,
            config=config,
            remote_root=normalized_remote_root,
        )
        if resolved_local_root.is_mount():
            self._validate_existing_filesystem_mount(
                local_root=resolved_local_root,
                expected=signature,
            )
            return
        sshfs_path = self._ssh_path_lookup("sshfs")
        if sshfs_path is None:
            raise ValueError(
                "sshfs executable was not found on PATH; cannot materialize ssh workspace mount"
            )
        with TemporaryDirectory(prefix="relay-teams-sshfs-") as temp_dir:
            temp_root = Path(temp_dir)
            private_key_path = self._write_probe_private_key(
                temp_root=temp_root,
                private_key=resolved.private_key,
            )
            env = self._build_probe_env(
                temp_root=temp_root,
                password=resolved.password,
            )
            command = self._build_sshfs_command(
                sshfs_path=sshfs_path,
                config=config,
                remote_root=normalized_remote_root,
                local_root=resolved_local_root,
                private_key_path=private_key_path,
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
                    timeout=self._resolve_probe_timeout_seconds(
                        SshProfileConnectivityProbeRequest(
                            ssh_profile_id=ssh_profile_id
                        ),
                        config,
                    )
                    + 10,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError("SSH filesystem mount timed out.") from exc
            except OSError as exc:
                raise ValueError(f"SSH filesystem mount failed: {exc}") from exc
        if completed.returncode != 0:
            detail = _combined_process_output(completed)
            raise ValueError(
                f"SSH filesystem mount failed: {detail or f'exit code {completed.returncode}'}"
            )
        self._write_filesystem_mount_signature(
            local_root=resolved_local_root,
            signature=signature,
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
            _ = _require_ssh_username(request.override.username)
            return _ResolvedSshProbeConfig(
                ssh_profile_id=None,
                config=request.override,
                password=request.override.password,
                private_key=request.override.private_key,
            )

        record = self._repository.get(request.ssh_profile_id)
        override = request.override
        username = (
            _require_ssh_username(override.username)
            if override is not None
            else _require_ssh_username(record.username)
        )
        config = SshProfileConfig(
            host=override.host if override is not None else record.host,
            username=username,
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
                "@echo off\r\n"
                "powershell.exe -NoProfile -NonInteractive "
                "-ExecutionPolicy Bypass "
                '-Command "[Console]::Out.WriteLine($env:RELAY_TEAMS_SSH_PASSWORD)"'
                "\r\n",
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
        return self._build_ssh_command(
            ssh_path=ssh_path,
            config=config,
            private_key_path=private_key_path,
            timeout_seconds=timeout_seconds,
            uses_password=uses_password,
            remote_command=_SSH_PROBE_COMMAND,
        )

    def _build_ssh_command(
        self,
        *,
        ssh_path: str,
        config: SshProfileConfig,
        private_key_path: Path | None,
        timeout_seconds: float,
        uses_password: bool,
        remote_command: str,
        allocate_tty: bool = False,
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
        if allocate_tty:
            command.append("-tt")
        if private_key_path is not None:
            command.extend(["-i", str(private_key_path), "-o", "IdentitiesOnly=yes"])
        if config.port is not None:
            command.extend(["-p", str(config.port)])
        command.extend(["-l", config.username])
        command.extend(["--", config.host, remote_command])
        return tuple(command)

    def _build_remote_shell_command(
        self,
        *,
        config: SshProfileConfig,
        command: str,
        cwd: str,
        env: dict[str, str] | None,
    ) -> str:
        shell = config.remote_shell or "bash"
        env_prefix = ""
        if env:
            env_parts = [
                f"{key}={shlex.quote(value)}"
                for key, value in sorted(env.items())
                if _is_portable_env_name(key)
            ]
            if env_parts:
                env_prefix = "env " + " ".join(env_parts) + " "
        return (
            f"cd {shlex.quote(cwd)} && "
            f"{env_prefix}{shlex.quote(shell)} -lc {shlex.quote(command)}"
        )

    def _build_sshfs_command(
        self,
        *,
        sshfs_path: str,
        config: SshProfileConfig,
        remote_root: str,
        local_root: Path,
        private_key_path: Path | None,
        uses_password: bool,
    ) -> tuple[str, ...]:
        remote_target = f"{config.username}@{config.host}"
        command = [
            sshfs_path,
            f"{remote_target}:{remote_root}",
            str(local_root),
            "-o",
            "reconnect",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "NumberOfPasswordPrompts=1",
            "-o",
            f"BatchMode={'no' if uses_password else 'yes'}",
        ]
        if config.port is not None:
            command.extend(["-p", str(config.port)])
        if private_key_path is not None:
            command.extend(
                [
                    "-o",
                    f"IdentityFile={private_key_path}",
                    "-o",
                    "IdentitiesOnly=yes",
                ]
            )
        return tuple(command)

    def _build_filesystem_mount_signature(
        self,
        *,
        ssh_profile_id: str,
        config: SshProfileConfig,
        remote_root: str,
    ) -> _SshFilesystemMountSignature:
        return _SshFilesystemMountSignature(
            ssh_profile_id=ssh_profile_id,
            host=config.host,
            username=config.username,
            port=config.port,
            remote_root=remote_root,
        )

    def _validate_existing_filesystem_mount(
        self,
        *,
        local_root: Path,
        expected: _SshFilesystemMountSignature,
    ) -> None:
        existing = self._read_filesystem_mount_signature(local_root)
        if existing == expected:
            return
        if existing is None:
            detail = "missing relay-teams mount metadata"
        else:
            detail = "relay-teams mount metadata does not match the requested target"
        raise ValueError(
            f"Existing SSH filesystem mount cannot be reused at {local_root}: "
            f"{detail}. Unmount it before changing the SSH profile or remote root."
        )

    def _read_filesystem_mount_signature(
        self,
        local_root: Path,
    ) -> _SshFilesystemMountSignature | None:
        signature_path = self._filesystem_mount_signature_path(local_root)
        try:
            raw_signature = signature_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            return None
        try:
            return _SshFilesystemMountSignature.model_validate_json(raw_signature)
        except (ValueError, json.JSONDecodeError):
            return None

    def _write_filesystem_mount_signature(
        self,
        *,
        local_root: Path,
        signature: _SshFilesystemMountSignature,
    ) -> None:
        signature_path = self._filesystem_mount_signature_path(local_root)
        signature_path.parent.mkdir(parents=True, exist_ok=True)
        signature_path.write_text(
            f"{signature.model_dump_json(indent=2)}\n",
            encoding="utf-8",
        )

    def _filesystem_mount_signature_path(self, local_root: Path) -> Path:
        signature_name = f".{local_root.name or 'root'}.sshfs.json"
        return local_root.parent / signature_name

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


def _require_ssh_username(username: str | None) -> str:
    if username is None:
        raise ValueError(_SSH_USERNAME_REQUIRED_MESSAGE)
    normalized = username.strip()
    if not normalized:
        raise ValueError(_SSH_USERNAME_REQUIRED_MESSAGE)
    return normalized


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


def _is_portable_env_name(value: str) -> bool:
    if not value:
        return False
    first = value[0]
    if not (first == "_" or first.isalpha()):
        return False
    return all(char == "_" or char.isalnum() for char in value)
