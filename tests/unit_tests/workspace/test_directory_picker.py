# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from relay_teams.workspace import directory_picker


class _FakeTkRoot:
    def __init__(self) -> None:
        self.withdraw_calls = 0
        self.wm_attributes_calls: list[tuple[str, bool]] = []
        self.destroy_calls = 0

    def withdraw(self) -> None:
        self.withdraw_calls += 1

    def wm_attributes(self, name: str, value: bool) -> None:
        self.wm_attributes_calls.append((name, value))

    def destroy(self) -> None:
        self.destroy_calls += 1


class _FakeTkModule:
    TclError = RuntimeError

    def __init__(self, root: _FakeTkRoot) -> None:
        self._root = root

    def Tk(self) -> _FakeTkRoot:
        return self._root


class _FakeTkFileDialog:
    def __init__(self, selected_path: str) -> None:
        self._selected_path = selected_path
        self.calls: list[dict[str, object]] = []

    def askdirectory(
        self,
        *,
        initialdir: str,
        mustexist: bool,
        parent: _FakeTkRoot,
        title: str,
    ) -> str:
        self.calls.append(
            {
                "initialdir": initialdir,
                "mustexist": mustexist,
                "parent": parent,
                "title": title,
            }
        )
        return self._selected_path


def test_pick_workspace_directory_uses_tkinter_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _FakeTkRoot()
    file_dialog = _FakeTkFileDialog(str(tmp_path / "picked-with-tkinter"))

    monkeypatch.setattr(directory_picker.platform, "system", lambda: "Windows")
    monkeypatch.setattr(directory_picker, "tkinter_module", _FakeTkModule(root))
    monkeypatch.setattr(directory_picker, "tkinter_filedialog", file_dialog)
    monkeypatch.setattr(
        directory_picker.shutil,
        "which",
        lambda value: (_ for _ in ()).throw(
            AssertionError(f"unexpected shell lookup: {value}")
        ),
    )

    selected = directory_picker.pick_workspace_directory(initial_dir=tmp_path / "start")

    assert selected == (tmp_path / "picked-with-tkinter").resolve()
    assert root.withdraw_calls == 1
    assert root.wm_attributes_calls == [("-topmost", True)]
    assert root.destroy_calls == 1
    assert file_dialog.calls == [
        {
            "initialdir": str((tmp_path / "start").resolve()),
            "mustexist": True,
            "parent": root,
            "title": "Select project folder",
        }
    ]


def test_pick_workspace_directory_returns_none_when_tkinter_picker_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _FakeTkRoot()
    file_dialog = _FakeTkFileDialog("")

    monkeypatch.setattr(directory_picker.platform, "system", lambda: "Windows")
    monkeypatch.setattr(directory_picker, "tkinter_module", _FakeTkModule(root))
    monkeypatch.setattr(directory_picker, "tkinter_filedialog", file_dialog)
    monkeypatch.setattr(
        directory_picker.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected powershell fallback")
        ),
    )

    selected = directory_picker.pick_workspace_directory(initial_dir=tmp_path / "start")

    assert selected is None
    assert root.destroy_calls == 1


def test_pick_workspace_directory_falls_back_to_powershell_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=f"{tmp_path / 'picked-with-powershell'}\n",
            stderr="",
        )

    monkeypatch.setattr(directory_picker.platform, "system", lambda: "Windows")
    monkeypatch.setattr(directory_picker, "tkinter_module", None)
    monkeypatch.setattr(directory_picker, "tkinter_filedialog", None)
    monkeypatch.setattr(
        directory_picker.shutil,
        "which",
        lambda value: (
            "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            if value == "powershell"
            else None
        ),
    )
    monkeypatch.setattr(directory_picker.subprocess, "run", fake_run)

    selected = directory_picker.pick_workspace_directory(initial_dir=tmp_path / "start")

    assert selected == (tmp_path / "picked-with-powershell").resolve()
    assert captured["command"] == [
        "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "-NoProfile",
        "-STA",
        "-Command",
        "Add-Type -AssemblyName System.Windows.Forms\n"
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog\n"
        '$dialog.Description = "Select project folder"\n'
        "$dialog.ShowNewFolderButton = $false\n"
        f'$dialog.SelectedPath = "{(tmp_path / "start").resolve()}"\n'
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {\n"
        "    [Console]::Out.Write($dialog.SelectedPath)\n"
        "}",
    ]
    assert captured["check"] is False
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 60.0


def test_pick_workspace_directory_uses_zenity_on_linux(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=f"{tmp_path / 'selected-root'}\n",
            stderr="",
        )

    monkeypatch.setattr(directory_picker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        directory_picker.shutil,
        "which",
        lambda value: f"/usr/bin/{value}" if value == "zenity" else None,
    )
    monkeypatch.setattr(directory_picker.subprocess, "run", fake_run)

    selected = directory_picker.pick_workspace_directory(initial_dir=tmp_path / "start")

    assert selected == (tmp_path / "selected-root").resolve()
    assert captured["command"] == [
        "/usr/bin/zenity",
        "--file-selection",
        "--directory",
        "--title",
        "Select project folder",
        "--filename",
        f"{(tmp_path / 'start').resolve()}/",
    ]
    assert captured["check"] is False
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 60.0


def test_pick_workspace_directory_uses_kdialog_fallback_on_linux(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        _ = (check, capture_output, text, timeout)
        captured["command"] = command
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=f"{tmp_path / 'picked-from-kdialog'}\n",
            stderr="",
        )

    def fake_which(value: str) -> str | None:
        if value == "kdialog":
            return "/usr/bin/kdialog"
        return None

    monkeypatch.setattr(directory_picker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(directory_picker.shutil, "which", fake_which)
    monkeypatch.setattr(directory_picker.subprocess, "run", fake_run)

    selected = directory_picker.pick_workspace_directory(initial_dir=tmp_path / "start")

    assert selected == (tmp_path / "picked-from-kdialog").resolve()
    assert captured["command"] == [
        "/usr/bin/kdialog",
        "--getexistingdirectory",
        str((tmp_path / "start").resolve()),
        "--title",
        "Select project folder",
    ]


def test_pick_workspace_directory_returns_none_when_linux_picker_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        _ = (command, check, capture_output, text, timeout)
        return subprocess.CompletedProcess(
            args=["/usr/bin/zenity"],
            returncode=1,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(directory_picker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        directory_picker.shutil,
        "which",
        lambda value: f"/usr/bin/{value}" if value == "zenity" else None,
    )
    monkeypatch.setattr(directory_picker.subprocess, "run", fake_run)

    selected = directory_picker.pick_workspace_directory(initial_dir=tmp_path)

    assert selected is None


def test_pick_workspace_directory_raises_when_no_linux_picker_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(directory_picker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(directory_picker.shutil, "which", lambda value: None)

    with pytest.raises(RuntimeError, match="Native directory picker is unavailable"):
        _ = directory_picker.pick_workspace_directory(initial_dir=tmp_path)
