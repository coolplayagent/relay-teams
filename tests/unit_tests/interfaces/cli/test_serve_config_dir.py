# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from types import ModuleType
import sys

from relay_teams.interfaces.server import cli as server_cli
from relay_teams.interfaces.server.runtime_identity import ServerRuntimeIdentity


def test_start_runs_uvicorn_and_tracks_managed_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    process_file = tmp_path / "server-process.json"

    fake_uvicorn = ModuleType("uvicorn")

    def fake_run(
        app: object,
        host: str,
        port: int,
        ws: str,
        timeout_graceful_shutdown: int,
    ) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["ws"] = ws
        captured["timeout_graceful_shutdown"] = timeout_graceful_shutdown
        captured["managed_process"] = (
            server_cli.ManagedServerProcess.model_validate_json(
                process_file.read_text(encoding="utf-8")
            )
        )

    setattr(fake_uvicorn, "run", fake_run)

    fake_server_module = ModuleType("relay_teams.interfaces.server.app")
    sentinel_app = object()
    setattr(fake_server_module, "app", sentinel_app)

    monkeypatch.setattr(
        server_cli,
        "get_server_process_file_path",
        lambda project_root=None: process_file,
    )
    monkeypatch.setattr(
        server_cli,
        "_get_current_runtime_identity",
        lambda: ServerRuntimeIdentity(
            python_executable="D:/workspace/agent_teams/.venv/Scripts/python.exe",
            package_root="D:/workspace/agent_teams/src/relay_teams",
            config_dir="C:/Users/test/.agent-teams",
            builtin_roles_dir="D:/workspace/agent_teams/src/relay_teams/builtin/roles",
            builtin_skills_dir="D:/workspace/agent_teams/src/relay_teams/builtin/skills",
        ),
    )
    monkeypatch.setattr(server_cli.os, "getpid", lambda: 4321)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setitem(
        sys.modules, "relay_teams.interfaces.server.app", fake_server_module
    )

    server_cli.start(host="127.0.0.1", port=8911)

    managed_process = captured["managed_process"]
    assert isinstance(managed_process, server_cli.ManagedServerProcess)
    assert captured == {
        "app": sentinel_app,
        "host": "127.0.0.1",
        "port": 8911,
        "ws": "websockets-sansio",
        "timeout_graceful_shutdown": 10,
        "managed_process": managed_process,
    }
    assert managed_process.model_dump() == {
        "pid": 4321,
        "host": "127.0.0.1",
        "port": 8911,
        "python_executable": "D:/workspace/agent_teams/.venv/Scripts/python.exe",
        "package_root": "D:/workspace/agent_teams/src/relay_teams",
        "builtin_skills_dir": "D:/workspace/agent_teams/src/relay_teams/builtin/skills",
    }
    assert not process_file.exists()
