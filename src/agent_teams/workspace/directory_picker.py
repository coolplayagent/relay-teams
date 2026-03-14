# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import platform
import shutil
import subprocess


_PICKER_ERROR_MESSAGE = "Native directory picker is unavailable"
_PICKER_TIMEOUT_SECONDS = 60.0
_PICKER_TITLE = "Select project folder"
_LINUX_GTK_PICKERS: tuple[str, ...] = ("zenity", "qarma", "yad")


def pick_workspace_directory(initial_dir: Path | None = None) -> Path | None:
    start_dir = (initial_dir or Path.home()).expanduser().resolve()
    platform_name = platform.system()

    if platform_name == "Windows":
        selected = _pick_directory_windows(start_dir)
    elif platform_name == "Darwin":
        selected = _pick_directory_macos(start_dir)
    elif platform_name == "Linux":
        selected = _pick_directory_linux(start_dir)
    else:
        raise RuntimeError(_PICKER_ERROR_MESSAGE)

    if not selected:
        return None
    return Path(selected).expanduser().resolve()


def _pick_directory_windows(start_dir: Path) -> str | None:
    shell = shutil.which("powershell")
    if shell is None:
        shell = shutil.which("pwsh")
    if shell is None:
        raise RuntimeError(_PICKER_ERROR_MESSAGE)

    escaped_dir = _escape_powershell_string(str(start_dir))
    script = "\n".join(
        (
            "Add-Type -AssemblyName System.Windows.Forms",
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
            f'$dialog.Description = "{_PICKER_TITLE}"',
            "$dialog.ShowNewFolderButton = $false",
            f'$dialog.SelectedPath = "{escaped_dir}"',
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {",
            "    [Console]::Out.Write($dialog.SelectedPath)",
            "}",
        )
    )
    return _run_picker_command(
        [shell, "-NoProfile", "-STA", "-Command", script],
        cancellation_markers=("cancel",),
    )


def _pick_directory_macos(start_dir: Path) -> str | None:
    command = shutil.which("osascript")
    if command is None:
        raise RuntimeError(_PICKER_ERROR_MESSAGE)

    escaped_dir = _escape_applescript_string(str(start_dir))
    return _run_picker_command(
        [
            command,
            "-e",
            (
                f'set chosenFolder to choose folder with prompt "{_PICKER_TITLE}" '
                f'default location POSIX file "{escaped_dir}"'
            ),
            "-e",
            "POSIX path of chosenFolder",
        ],
        cancellation_markers=("user canceled", "user cancelled"),
    )


def _pick_directory_linux(start_dir: Path) -> str | None:
    for picker in _LINUX_GTK_PICKERS:
        command = shutil.which(picker)
        if command is None:
            continue
        return _run_picker_command(
            [
                command,
                "--file-selection",
                "--directory",
                "--title",
                _PICKER_TITLE,
                "--filename",
                f"{start_dir}/",
            ],
            cancellation_markers=("cancel",),
        )

    kdialog = shutil.which("kdialog")
    if kdialog is not None:
        return _run_picker_command(
            [
                kdialog,
                "--getexistingdirectory",
                str(start_dir),
                "--title",
                _PICKER_TITLE,
            ],
            cancellation_markers=("cancel",),
        )

    raise RuntimeError(_PICKER_ERROR_MESSAGE)


def _run_picker_command(
    command: list[str],
    *,
    cancellation_markers: tuple[str, ...],
) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=_PICKER_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        raise RuntimeError(_PICKER_ERROR_MESSAGE) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(_PICKER_ERROR_MESSAGE) from exc

    if completed.returncode == 0:
        selected = completed.stdout.strip()
        return selected or None

    stderr = completed.stderr.strip().lower()
    if completed.returncode == 1:
        if not stderr:
            return None
        if any(marker in stderr for marker in cancellation_markers):
            return None

    raise RuntimeError(_PICKER_ERROR_MESSAGE)


def _escape_powershell_string(value: str) -> str:
    return value.replace("`", "``").replace('"', '`"')


def _escape_applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
