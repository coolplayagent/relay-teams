# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_recovery_ui_uses_automatic_stream_reconnect_without_connect_button() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    recovery_script = (
        repo_root / "frontend" / "dist" / "js" / "app" / "recovery.js"
    ).read_text(encoding="utf-8")
    session_script = (
        repo_root / "frontend" / "dist" / "js" / "app" / "session.js"
    ).read_text(encoding="utf-8")
    prompt_script = (
        repo_root / "frontend" / "dist" / "js" / "app" / "prompt.js"
    ).read_text(encoding="utf-8")
    timeline_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "timeline.js"
    ).read_text(encoding="utf-8")
    stream_script = (
        repo_root / "frontend" / "dist" / "js" / "core" / "stream.js"
    ).read_text(encoding="utf-8")
    sidebar_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sidebar.js"
    ).read_text(encoding="utf-8")
    renderer_stream_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "stream.js"
    ).read_text(encoding="utf-8")
    history_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "history.js"
    ).read_text(encoding="utf-8")

    assert "Connect Stream" not in recovery_script
    assert "The live stream reconnects automatically." in recovery_script
    assert "isReservedSystemRoleId(roleId)" in recovery_script
    assert "await ensureAutomaticRecoveryStream(snapshot," in recovery_script
    assert "resumeRunStream(activeRun.run_id, safeSessionId, null," in recovery_script
    assert (
        "const lastEventId = Number(activeRun.last_event_id || 0);" in recovery_script
    )
    assert (
        "const checkpointEventId = Number(activeRun.checkpoint_event_id || 0);"
        in recovery_script
    )
    assert (
        "if (e?.status === 404 && state.currentSessionId === safeSessionId) {"
        in recovery_script
    )
    assert "stopSessionContinuity(safeSessionId);" in recovery_script
    assert (
        "detachActiveStreamForSessionSwitch({ focusPrompt: false });" in session_script
    )
    assert "clearAllStreamState({ preserveOverlay: true });" in session_script
    assert "clearAllStreamState({ preserveOverlay: true });" in prompt_script
    assert "clearAllStreamState({ preserveOverlay: true });" in timeline_script
    assert "export function attachRunStream(" in stream_script
    assert "const backgroundStreams = new Map();" in stream_script
    assert "const unavailableSessionCooldownUntil = new Map();" in stream_script
    assert "const SESSION_NOT_FOUND_COOLDOWN_MS = 30000;" in stream_script
    assert (
        "export function detachActiveStreamForSessionSwitch(options = {}) {"
        in stream_script
    )
    assert (
        "function promoteBackgroundStream(connection, options = {}) {" in stream_script
    )
    assert (
        "function applyBackgroundRunEvent(connection, evType, payload, eventMeta) {"
        in stream_script
    )
    assert "reason: 'background-reconnect'," in stream_script
    assert (
        "export function syncBackgroundStreamsForSessions(sessionRecords = []) {"
        in stream_script
    )
    assert "const sessions = await fetchSessions();" in stream_script
    assert "reason: 'background-discovery'," in stream_script
    assert "const snapshot = await fetchSessionRecovery(sessionId);" in stream_script
    assert (
        "finishActiveConnection(connection, { preserveRunStreamState: true });"
        in stream_script
    )
    assert "const unavailableRunCooldownUntil = new Map();" in stream_script
    assert "const RUN_NOT_FOUND_COOLDOWN_MS = 30000;" in stream_script
    assert "if (isRunNotFoundError(data.error)) {" in stream_script
    assert "if (e?.status === 404) {" in stream_script
    assert "markSessionUnavailable(sessionId);" in stream_script
    assert "if (isSessionUnavailable(sessionId)) {" in stream_script
    assert "if (!ignoreUnavailable && isRunUnavailable(safeRunId)) {" in stream_script
    assert "const streamCore = await import('../core/stream.js');" in sidebar_script
    assert "streamCore.syncBackgroundStreamsForSessions(sessions);" in sidebar_script
    assert "export function clearRenderedStreamState()" in renderer_stream_script
    assert "export function clearAllStreamState(options = {})" in renderer_stream_script
    assert (
        "export function applyStreamOverlayEvent(evType, payload, options = {}) {"
        in renderer_stream_script
    )
    assert "const overlayCleanupTimers = new Map();" in renderer_stream_script
    assert (
        "scheduleOverlayEntryCleanup(runId, streamKey, roleId, cleanupDelayMs);"
        in renderer_stream_script
    )
    assert "scheduleRunOverlayCleanup(runId, cleanupDelayMs);" in renderer_stream_script
    assert "st = createStreamState({" in renderer_stream_script
    assert "function findReusableStreamState({" in renderer_stream_script
    assert (
        "const overlayEntry = resolveOverlayEntry(runId, instanceId, roleId, label);"
        in renderer_stream_script
    )
    assert "function findReusableMessageWrapper({" in renderer_stream_script
    assert (
        "function resolveOverlayEntry(runId, instanceId, roleId, label) {"
        in renderer_stream_script
    )
    assert (
        "const thinkingBinding = bindReusableThinkingState(contentEl, overlayEntry);"
        in renderer_stream_script
    )
    assert (
        "function bindReusableThinkingState(contentEl, overlayEntry) {"
        in renderer_stream_script
    )
    assert (
        "function findReusableThinkingTextElement(contentEl, key, partIndex) {"
        in renderer_stream_script
    )
    assert "function findLastReusableTextElement(contentEl) {" in renderer_stream_script
    assert "function resolveReusableRawText(overlayEntry) {" in renderer_stream_script
    assert "let lastRenderedMessage = null;" in history_script
    assert (
        "renderStreamOverlayEntry(container, streamOverlayEntry, pendingToolBlocks, lastRenderedMessage);"
        in history_script
    )
    assert "return lastRenderedMessage.contentEl;" in history_script
    assert (
        "const lastMessageContentEl = findLastCompatibleMessageContent(container, safeLabel);"
        in history_script
    )
    assert (
        "function findLastCompatibleMessageContent(container, label) {"
        in history_script
    )
