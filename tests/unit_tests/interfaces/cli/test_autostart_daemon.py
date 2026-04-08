# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.interfaces.cli import app as cli_app


class _FakeStartupInfo:
    def __init__(self) -> None:
        self.dwFlags = 0
        self.wShowWindow = 0


def test_start_server_daemon_hides_windows_console(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_popen(*args: object, **kwargs: object) -> object:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(cli_app.sys, "platform", "win32")
    monkeypatch.setattr(cli_app.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        cli_app.subprocess, "STARTUPINFO", _FakeStartupInfo, raising=False
    )
    monkeypatch.setattr(
        cli_app.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False
    )
    monkeypatch.setattr(cli_app.subprocess, "DETACHED_PROCESS", 0x8, raising=False)
    monkeypatch.setattr(
        cli_app.subprocess, "CREATE_NO_WINDOW", 0x8000000, raising=False
    )
    monkeypatch.setattr(cli_app.subprocess, "STARTF_USESHOWWINDOW", 0x1, raising=False)
    monkeypatch.setattr(cli_app.subprocess, "SW_HIDE", 0, raising=False)

    cli_app._start_server_daemon(host="127.0.0.1", port=8011)

    args = captured["args"]
    assert isinstance(args, tuple)
    assert list(args[0]) == [
        cli_app.sys.executable,
        "-m",
        "relay_teams",
        "server",
        "start",
        "--host",
        "127.0.0.1",
        "--port",
        "8011",
    ]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["creationflags"] == (0x200 | 0x8 | 0x8000000)
    startupinfo = kwargs["startupinfo"]
    assert isinstance(startupinfo, _FakeStartupInfo)
    assert startupinfo.dwFlags == 0x1
    assert startupinfo.wShowWindow == 0
