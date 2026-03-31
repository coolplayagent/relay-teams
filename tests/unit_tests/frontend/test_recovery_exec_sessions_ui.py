# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_recovery_ui_tracks_exec_sessions_in_banner_and_events() -> None:
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

    assert "stopExecSession" in recovery_script
    assert "backgroundTerminals" in recovery_script
    assert "renderExecSessionPanel" in recovery_script
    assert "ensureExecSessionHost" in recovery_script
    assert "renderExecSessionList" in recovery_script
    assert "handleExecSessionAction" in recovery_script
    assert "handleExecSessionPanelToggle" in recovery_script
    assert "data-exec-session-panel-toggle" in recovery_script
    assert "recovery.exec_session.panel_label" in i18n_script
    assert "recovery.exec_session.collapse" in i18n_script
    assert "recovery.exec_session.expand" in i18n_script
    assert "exec_session_started" in event_router_script
    assert "exec_session_completed" in event_router_script
    assert "recovery.exec_session.stop" in i18n_script
