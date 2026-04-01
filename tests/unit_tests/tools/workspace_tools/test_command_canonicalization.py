# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.tools.workspace_tools.command_canonicalization import (
    canonicalize_shell_command,
)


def test_canonicalize_shell_command_unwraps_git_bash_path() -> None:
    command = r'''"C:\Program Files\Git\bin\bash.exe" -lc "pwd"'''

    assert canonicalize_shell_command(command) == "pwd"


def test_canonicalize_shell_command_unwraps_powershell_wrapper() -> None:
    command = (
        r""""C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" """
        r'''-NoProfile -Command "Get-Location"'''
    )

    assert canonicalize_shell_command(command) == "Get-Location"


def test_canonicalize_shell_command_strips_powershell_utf8_prefix() -> None:
    command = (
        "powershell -NoProfile -Command "
        '"[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)\n'
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
        "$OutputEncoding = [Console]::OutputEncoding\n"
        'Write-Output hello"'
    )

    assert canonicalize_shell_command(command) == "Write-Output hello"


def test_canonicalize_shell_command_preserves_multiline_body_whitespace() -> None:
    command = "bash -lc \"\n  cat <<'EOF'\n    hello\n\nEOF\n\""

    assert canonicalize_shell_command(command) == (
        "\n  cat <<'EOF'\n    hello\n\nEOF\n"
    )
