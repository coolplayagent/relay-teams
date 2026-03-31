# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_recovery_ui_tracks_background_terminals_in_banner_and_events() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    recovery_script = (
        repo_root / "frontend" / "dist" / "js" / "app" / "recovery.js"
    ).read_text(encoding="utf-8")
    event_router_script = (
        repo_root / "frontend" / "dist" / "js" / "core" / "eventRouter" / "index.js"
    ).read_text(encoding="utf-8")
    i18n_script = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "i18n.js"
    ).read_text(encoding="utf-8")

    assert "stopBackgroundTerminal" in recovery_script
    assert "backgroundTerminals" in recovery_script
    assert "renderBackgroundTerminalList" in recovery_script
    assert "handleBackgroundTerminalAction" in recovery_script
    assert "background_terminal_started" in event_router_script
    assert "background_terminal_completed" in event_router_script
    assert "recovery.background_terminal.stop" in i18n_script
