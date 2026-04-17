# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import platform
import shutil
import subprocess


_OPEN_DIRECTORY_ERROR_MESSAGE = "Native file manager is unavailable"
_OPEN_DIRECTORY_START_TIMEOUT_SECONDS = 1.0


def open_workspace_directory(path: Path) -> None:
    target_path = path.expanduser().resolve()
    platform_name = platform.system()

    if platform_name == "Windows":
        _open_directory_windows(target_path)
        return
    if platform_name == "Darwin":
        opener = shutil.which("open")
        if opener is None:
            raise RuntimeError(_OPEN_DIRECTORY_ERROR_MESSAGE)
        _start_detached_process([opener, str(target_path)], platform_name=platform_name)
        return
    if platform_name == "Linux":
        command = _build_linux_open_command(target_path)
        _start_detached_process(command, platform_name=platform_name)
        return

    raise RuntimeError(_OPEN_DIRECTORY_ERROR_MESSAGE)


def _open_directory_windows(target_path: Path) -> None:
    explorer = shutil.which("explorer")
    if explorer is None:
        explorer = shutil.which("explorer.exe")
    if explorer is not None:
        _start_detached_process(
            [explorer, str(target_path)],
            platform_name="Windows",
            skip_startup_check=True,
        )
        return

    shell = shutil.which("powershell")
    if shell is None:
        shell = shutil.which("pwsh")
    if shell is None:
        raise RuntimeError(_OPEN_DIRECTORY_ERROR_MESSAGE)

    escaped_path = _escape_powershell_string(str(target_path))
    _start_detached_process(
        [
            shell,
            "-NoProfile",
            "-Command",
            f'Start-Process -FilePath "{escaped_path}"',
        ],
        platform_name="Windows",
    )


def _build_linux_open_command(target_path: Path) -> list[str]:
    xdg_open = shutil.which("xdg-open")
    if xdg_open is not None:
        return [xdg_open, str(target_path)]

    gio = shutil.which("gio")
    if gio is not None:
        return [gio, "open", str(target_path)]

    kde_open = shutil.which("kde-open")
    if kde_open is not None:
        return [kde_open, str(target_path)]

    kde_open5 = shutil.which("kde-open5")
    if kde_open5 is not None:
        return [kde_open5, str(target_path)]

    kioclient5 = shutil.which("kioclient5")
    if kioclient5 is not None:
        return [kioclient5, "exec", str(target_path)]

    raise RuntimeError(_OPEN_DIRECTORY_ERROR_MESSAGE)


def _start_detached_process(
    command: list[str],
    *,
    platform_name: str,
    skip_startup_check: bool = False,
) -> None:
    try:
        if platform_name == "Windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
            startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
            creationflags = (
                int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                | int(getattr(subprocess, "DETACHED_PROCESS", 0))
                | int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            )
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
            if not skip_startup_check:
                _ensure_process_started(process)
            return

        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        _ensure_process_started(process)
    except OSError as exc:
        raise RuntimeError(f"Failed to launch native file manager: {exc}") from exc


def _ensure_process_started(process: subprocess.Popen[bytes]) -> None:
    try:
        returncode = process.wait(timeout=_OPEN_DIRECTORY_START_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return
    if returncode != 0:
        raise RuntimeError(
            "Failed to launch native file manager: opener exited before startup completed"
        )


def _escape_powershell_string(value: str) -> str:
    return value.replace("`", "``").replace('"', '`"')
