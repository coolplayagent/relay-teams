# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess

import pytest

import relay_teams.workspace.ssh_profile_service as ssh_profile_service_module
from relay_teams.secrets import AppSecretStore
from relay_teams.workspace import (
    SshProfileConfig,
    SshProfileConnectivityProbeRequest,
    SshProfileRepository,
    SshProfileSecretStore,
    SshProfileService,
    SshProfileStoredConfig,
)


class _FileOnlySecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


def test_ssh_profile_service_stores_password_and_private_key_in_secret_store(
    tmp_path: Path,
) -> None:
    secret_store = SshProfileSecretStore(secret_store=_FileOnlySecretStore())
    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=secret_store,
    )

    saved = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(
            host="prod-alias",
            username="deploy",
            password="secret",
            private_key=(
                "-----BEGIN OPENSSH PRIVATE KEY-----\r\n"
                "abc123\r\n"
                "-----END OPENSSH PRIVATE KEY-----\r\n"
            ),
            private_key_name="id_ed25519",
        ),
    )

    fetched = service.get_profile("prod")

    assert saved.has_password is True
    assert saved.has_private_key is True
    assert fetched.private_key_name == "id_ed25519"
    assert service.reveal_password("prod").password == "secret"
    assert secret_store.get_password(tmp_path, "prod") == "secret"
    assert secret_store.get_private_key(tmp_path, "prod") == (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nabc123\n-----END OPENSSH PRIVATE KEY-----"
    )


def test_ssh_profile_service_preserves_existing_secrets_and_deletes_them(
    tmp_path: Path,
) -> None:
    secret_store = SshProfileSecretStore(secret_store=_FileOnlySecretStore())
    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=secret_store,
    )
    _ = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(
            host="prod-alias",
            username="deploy",
            password="secret",
            private_key="-----BEGIN KEY-----\ncontent\n-----END KEY-----",
            private_key_name="id_rsa",
        ),
    )

    updated = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(
            host="prod-alias-2",
            username="ops",
        ),
    )

    assert updated.host == "prod-alias-2"
    assert updated.has_password is True
    assert updated.has_private_key is True
    assert updated.private_key_name == "id_rsa"
    assert secret_store.get_password(tmp_path, "prod") == "secret"
    assert secret_store.get_private_key(tmp_path, "prod") is not None

    service.delete_profile("prod")

    with pytest.raises(KeyError):
        service.get_profile("prod")
    assert secret_store.get_password(tmp_path, "prod") is None
    assert secret_store.get_private_key(tmp_path, "prod") is None


def test_ssh_profile_config_rejects_whitespace_only_host() -> None:
    with pytest.raises(ValueError, match="host"):
        _ = SshProfileConfig(host="   ", username="deploy")


def test_ssh_profile_config_requires_username() -> None:
    with pytest.raises(ValueError, match="username"):
        _ = SshProfileConfig(host="prod-alias", username="   ")


def test_ssh_profile_service_probes_saved_profile_with_secrets(
    tmp_path: Path,
) -> None:
    secret_store = SshProfileSecretStore(secret_store=_FileOnlySecretStore())
    captured_command: list[tuple[str, ...]] = []
    captured_env: list[dict[str, str]] = []

    def run_probe(
        command: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command_tuple = tuple(command)
        captured_command.append(command_tuple)
        env = kwargs.get("env")
        assert isinstance(env, dict)
        captured_env.append({str(key): str(value) for key, value in env.items()})
        identity_index = command_tuple.index("-i") + 1
        identity_path = Path(command_tuple[identity_index])
        assert identity_path.read_text(encoding="utf-8") == (
            "-----BEGIN KEY-----\ncontent\n-----END KEY-----\n"
        )
        return subprocess.CompletedProcess(
            args=command_tuple,
            returncode=0,
            stdout="relay-teams-ssh-probe\n",
            stderr="",
        )

    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=secret_store,
        ssh_path_lookup=lambda _name: "/usr/bin/ssh",
        process_runner=run_probe,
        now=lambda: datetime(2026, 4, 21, tzinfo=timezone.utc),
    )
    _ = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(
            host="prod-alias",
            username="deploy",
            password="secret",
            port=2222,
            private_key="-----BEGIN KEY-----\ncontent\n-----END KEY-----",
            private_key_name="id_ed25519",
            connect_timeout_seconds=12,
        ),
    )

    result = service.probe_connectivity(
        SshProfileConnectivityProbeRequest(ssh_profile_id="prod")
    )

    assert result.ok is True
    assert result.host == "prod-alias"
    assert result.port == 2222
    assert result.username == "deploy"
    assert result.diagnostics.binary_available is True
    assert result.diagnostics.used_password is True
    assert result.diagnostics.used_private_key is True
    assert result.diagnostics.used_system_config is False
    assert captured_env[0]["RELAY_TEAMS_SSH_PASSWORD"] == "secret"
    assert captured_env[0]["SSH_ASKPASS_REQUIRE"] == "force"
    assert captured_command[0][:2] == ("/usr/bin/ssh", "-o")
    assert "ConnectTimeout=12" in captured_command[0]
    assert "BatchMode=no" in captured_command[0]
    assert captured_command[0][-4:] == (
        "deploy",
        "--",
        "prod-alias",
        "echo relay-teams-ssh-probe",
    )


def test_ssh_profile_service_probe_command_terminates_options_before_host(
    tmp_path: Path,
) -> None:
    captured_command: list[tuple[str, ...]] = []

    def run_probe(
        command: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command_tuple = tuple(command)
        captured_command.append(command_tuple)
        return subprocess.CompletedProcess(
            args=command_tuple,
            returncode=0,
            stdout="relay-teams-ssh-probe\n",
            stderr="",
        )

    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=SshProfileSecretStore(secret_store=_FileOnlySecretStore()),
        ssh_path_lookup=lambda _name: "/usr/bin/ssh",
        process_runner=run_probe,
    )

    result = service.probe_connectivity(
        SshProfileConnectivityProbeRequest(
            override=SshProfileConfig(
                host="-V",
                username="deploy",
            )
        )
    )

    assert result.ok is True
    option_terminator_index = captured_command[0].index("--")
    assert captured_command[0][option_terminator_index + 1 :] == (
        "-V",
        "echo relay-teams-ssh-probe",
    )


def test_ssh_profile_service_runs_saved_profile_remote_command(
    tmp_path: Path,
) -> None:
    captured_command: list[tuple[str, ...]] = []
    captured_env: list[dict[str, str]] = []

    def run_command(
        command: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command_tuple = tuple(command)
        captured_command.append(command_tuple)
        env = kwargs.get("env")
        assert isinstance(env, dict)
        captured_env.append({str(key): str(value) for key, value in env.items()})
        return subprocess.CompletedProcess(
            args=command_tuple,
            returncode=0,
            stdout="ok\n",
            stderr="",
        )

    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=SshProfileSecretStore(secret_store=_FileOnlySecretStore()),
        ssh_path_lookup=lambda _name: "/usr/bin/ssh",
        process_runner=run_command,
    )
    _ = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(
            host="prod-alias",
            username="deploy",
            password="secret",
            port=2222,
        ),
    )

    result = service.run_remote_command(
        ssh_profile_id="prod",
        command="printf ok",
        timeout_seconds=3,
    )

    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert captured_env[0]["RELAY_TEAMS_SSH_PASSWORD"] == "secret"
    assert captured_command[0][-5:] == (
        "-l",
        "deploy",
        "--",
        "prod-alias",
        "printf ok",
    )
    assert "-p" in captured_command[0]
    assert "2222" in captured_command[0]
    assert "BatchMode=no" in captured_command[0]


def test_ssh_profile_service_materializes_filesystem_mount(
    tmp_path: Path,
) -> None:
    captured_command: list[tuple[str, ...]] = []
    captured_env: list[dict[str, str]] = []

    def run_command(
        command: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command_tuple = tuple(command)
        captured_command.append(command_tuple)
        env = kwargs.get("env")
        assert isinstance(env, dict)
        captured_env.append({str(key): str(value) for key, value in env.items()})
        return subprocess.CompletedProcess(
            args=command_tuple,
            returncode=0,
            stdout="",
            stderr="",
        )

    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=SshProfileSecretStore(secret_store=_FileOnlySecretStore()),
        ssh_path_lookup=lambda name: f"/usr/bin/{name}",
        process_runner=run_command,
    )
    _ = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(
            host="prod-alias",
            username="deploy",
            password="secret",
            port=2222,
        ),
    )
    local_root = tmp_path / "ssh_mount"

    service.ensure_filesystem_mount(
        ssh_profile_id="prod",
        remote_root="/srv/app",
        local_root=local_root,
    )

    assert local_root.is_dir()
    assert captured_env[0]["RELAY_TEAMS_SSH_PASSWORD"] == "secret"
    assert captured_command[0][0] == "/usr/bin/sshfs"
    assert captured_command[0][1] == "deploy@prod-alias:/srv/app"
    assert captured_command[0][2] == str(local_root.resolve())
    assert "-p" in captured_command[0]
    assert "2222" in captured_command[0]
    assert "BatchMode=no" in captured_command[0]


def test_ssh_profile_service_validates_existing_filesystem_mount_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_command: list[tuple[str, ...]] = []

    def run_command(
        command: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command_tuple = tuple(command)
        captured_command.append(command_tuple)
        return subprocess.CompletedProcess(
            args=command_tuple,
            returncode=0,
            stdout="",
            stderr="",
        )

    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=SshProfileSecretStore(secret_store=_FileOnlySecretStore()),
        ssh_path_lookup=lambda name: f"/usr/bin/{name}",
        process_runner=run_command,
    )
    _ = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(host="prod-alias", username="deploy", port=2222),
    )
    local_root = tmp_path / "ssh_mount"
    service.ensure_filesystem_mount(
        ssh_profile_id="prod",
        remote_root="/srv/app",
        local_root=local_root,
    )
    signature_path = local_root.parent / ".ssh_mount.sshfs.json"
    assert signature_path.is_file()

    original_is_mount = Path.is_mount

    def fake_is_mount(path: Path) -> bool:
        if path == local_root.resolve():
            return True
        return original_is_mount(path)

    monkeypatch.setattr(Path, "is_mount", fake_is_mount)

    captured_command.clear()
    service.ensure_filesystem_mount(
        ssh_profile_id="prod",
        remote_root="/srv/app",
        local_root=local_root,
    )
    assert captured_command == []

    _ = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(host="new-prod-alias", username="deploy", port=2222),
    )
    with pytest.raises(ValueError, match="cannot be reused"):
        service.ensure_filesystem_mount(
            ssh_profile_id="prod",
            remote_root="/srv/app",
            local_root=local_root,
        )


def test_ssh_profile_service_prepares_remote_process_command(
    tmp_path: Path,
) -> None:
    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=SshProfileSecretStore(secret_store=_FileOnlySecretStore()),
        ssh_path_lookup=lambda name: f"/usr/bin/{name}",
    )
    _ = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(
            host="prod-alias",
            username="deploy",
            password="secret",
            port=2222,
            remote_shell="/bin/bash",
        ),
    )

    prepared = service.prepare_remote_command(
        ssh_profile_id="prod",
        command='printf "$AGENT_TEAMS_CURRENT_ROLE_ID"',
        cwd="/srv/app",
        env={"AGENT_TEAMS_CURRENT_ROLE_ID": "writer", "1INVALID": "ignored"},
        tty=True,
    )

    assert prepared.argv[:2] == ("/usr/bin/ssh", "-o")
    assert "-tt" in prepared.argv
    assert prepared.argv[-5:-1] == ("-l", "deploy", "--", "prod-alias")
    assert "cd /srv/app && env AGENT_TEAMS_CURRENT_ROLE_ID=writer" in prepared.argv[-1]
    assert "/bin/bash -lc" in prepared.argv[-1]
    assert "1INVALID" not in prepared.argv[-1]
    assert prepared.env["RELAY_TEAMS_SSH_PASSWORD"] == "secret"
    assert prepared.temp_root.is_dir()
    shutil.rmtree(prepared.temp_root)


def test_ssh_profile_service_windows_askpass_reads_password_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=SshProfileSecretStore(secret_store=_FileOnlySecretStore()),
    )

    monkeypatch.setattr(ssh_profile_service_module.os, "name", "nt")
    askpass_path = service._write_askpass_script(tmp_path)
    askpass_script = askpass_path.read_text(encoding="utf-8")

    assert askpass_path.name == "askpass.cmd"
    assert "powershell.exe -NoProfile -NonInteractive" in askpass_script
    assert "$env:RELAY_TEAMS_SSH_PASSWORD" in askpass_script
    assert "%RELAY_TEAMS_SSH_PASSWORD%" not in askpass_script


def test_ssh_profile_service_probe_reports_auth_failure(
    tmp_path: Path,
) -> None:
    def run_probe(
        command: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=tuple(command),
            returncode=255,
            stdout="",
            stderr="Permission denied (publickey).",
        )

    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=SshProfileSecretStore(secret_store=_FileOnlySecretStore()),
        ssh_path_lookup=lambda _name: "/usr/bin/ssh",
        process_runner=run_probe,
    )

    result = service.probe_connectivity(
        SshProfileConnectivityProbeRequest(
            override=SshProfileConfig(host="prod-alias", username="deploy")
        )
    )

    assert result.ok is False
    assert result.error_code == "auth_failed"
    assert result.retryable is False
    assert result.diagnostics.host_reachable is True
    assert result.diagnostics.used_system_config is True


def test_ssh_profile_service_rejects_saved_profile_without_username_before_subprocess(
    tmp_path: Path,
) -> None:
    captured_command: list[tuple[str, ...]] = []

    def run_command(
        command: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured_command.append(tuple(command))
        return subprocess.CompletedProcess(args=tuple(command), returncode=0)

    repository = SshProfileRepository(tmp_path / "workspace.db")
    _ = repository.save(
        ssh_profile_id="legacy",
        config=SshProfileStoredConfig(
            host="prod-alias",
        ),
    )
    service = SshProfileService(
        repository=repository,
        config_dir=tmp_path,
        secret_store=SshProfileSecretStore(secret_store=_FileOnlySecretStore()),
        ssh_path_lookup=lambda _name: "/usr/bin/ssh",
        process_runner=run_command,
    )

    with pytest.raises(ValueError, match="username is required"):
        _ = service.run_remote_command(
            ssh_profile_id="legacy",
            command="pwd",
        )

    assert captured_command == []
