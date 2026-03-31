# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_recovery_ui_tracks_exec_sessions_in_banner_and_events() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    recovery_script = (
        repo_root / "frontend" / "dist" / "js" / "app" / "recovery.js"
    ).read_text(encoding="utf-8")
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
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
    assert (
        "const chatContainer = els.chatContainer || els.chatMessages?.parentElement;"
        in recovery_script
    )
    assert (
        "if (!chatContainer || !inputContainer || inputContainer.parentNode !== chatContainer) return null;"
        in recovery_script
    )
    assert "chatContainer.insertBefore(host, inputContainer);" in recovery_script
    assert "host.className = 'background-task-strip-host';" in recovery_script
    assert (
        '<div class="background-task-strip" role="status" aria-live="polite">'
        in recovery_script
    )
    assert '<div class="background-task-chip-list">' in recovery_script
    assert (
        'class="background-task-chip background-task-chip-${chipTone}"'
        in recovery_script
    )
    assert (
        "const activeTerminals = terminals.filter(terminal => isExecSessionActive(terminal));"
        in recovery_script
    )
    assert (
        "const hidePanel = !runId || activeTerminals.length === 0;" in recovery_script
    )
    assert "filter(Boolean)" in recovery_script
    assert "const nextExecSessions = found" in recovery_script
    assert ".background-task-strip-host" in components_css
    assert ".background-task-strip" in components_css
    assert ".background-task-chip" in components_css
    assert ".background-task-chip-stop" in components_css
    assert "recovery.exec_session.panel_label" in i18n_script
    assert "exec_session_started" in event_router_script
    assert "exec_session_completed" in event_router_script
    assert "recovery.exec_session.stop" in i18n_script
