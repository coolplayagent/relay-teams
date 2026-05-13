# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_viewed_terminal_override_does_not_mask_new_unread_terminal_run(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sessionSidebarStore.js"
    )
    module_path = tmp_path / "sessionSidebarStore.mjs"
    module_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    runner_path = tmp_path / "runner.mjs"
    runner_path.write_text(
        """
const {
    getSidebarDataSnapshot,
    markSidebarSessionTerminalViewed,
    rememberSidebarDataSnapshot,
} = await import('./sessionSidebarStore.mjs');

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'old terminal' },
            latest_terminal_run_id: 'run-1',
            has_unread_terminal_run: true,
        },
    ],
});
markSidebarSessionTerminalViewed('session-1');
const afterViewed = getSidebarDataSnapshot().sessions[0];

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'new terminal' },
            latest_terminal_run_id: 'run-2',
            has_unread_terminal_run: true,
        },
    ],
});
const afterNewTerminal = getSidebarDataSnapshot().sessions[0];

console.log(JSON.stringify({
    afterViewedUnread: afterViewed.has_unread_terminal_run,
    afterNewTerminalUnread: afterNewTerminal.has_unread_terminal_run,
    afterNewTerminalRunId: afterNewTerminal.latest_terminal_run_id,
}));
""".strip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(runner_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    assert json.loads(result.stdout) == {
        "afterViewedUnread": False,
        "afterNewTerminalUnread": True,
        "afterNewTerminalRunId": "run-2",
    }


def test_optimistic_session_removed_when_server_snapshot_drops_session(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sessionSidebarStore.js"
    )
    module_path = tmp_path / "sessionSidebarStore.mjs"
    module_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    runner_path = tmp_path / "runner-remove.mjs"
    runner_path.write_text(
        """
const {
    getSidebarDataSnapshot,
    markSidebarSessionTerminalViewed,
    rememberSidebarDataSnapshot,
} = await import('./sessionSidebarStore.mjs');

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'delete me' },
            latest_terminal_run_id: 'run-1',
            has_unread_terminal_run: true,
        },
    ],
});
markSidebarSessionTerminalViewed('session-1');
const afterOptimistic = getSidebarDataSnapshot().sessions.map(session => session.session_id);

rememberSidebarDataSnapshot({
    sessions: [],
});
const afterDeleteSnapshot = getSidebarDataSnapshot().sessions.map(session => session.session_id);

console.log(JSON.stringify({
    afterOptimistic,
    afterDeleteSnapshot,
}));
""".strip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(runner_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    assert json.loads(result.stdout) == {
        "afterOptimistic": ["session-1"],
        "afterDeleteSnapshot": [],
    }


def test_remove_sidebar_session_drops_stored_and_optimistic_rows(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sessionSidebarStore.js"
    )
    module_path = tmp_path / "sessionSidebarStore.mjs"
    module_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    runner_path = tmp_path / "runner-remove-sidebar-session.mjs"
    runner_path.write_text(
        """
const {
    getSidebarDataSnapshot,
    removeSidebarSession,
    updateOptimisticSessionTitle,
    rememberSidebarDataSnapshot,
} = await import('./sessionSidebarStore.mjs');

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'keep' },
            created_at: '2026-01-01T00:00:00.000Z',
        },
        {
            session_id: 'session-2',
            workspace_id: 'workspace-1',
            metadata: { title: 'delete' },
            created_at: '2026-01-01T00:00:00.000Z',
            updated_at: '2999-01-01T00:00:00.000Z',
        },
    ],
});
updateOptimisticSessionTitle('session-2', 'optimistic delete');
removeSidebarSession('session-2');
const afterRemove = getSidebarDataSnapshot().sessions.map(session => ({
    sessionId: session.session_id,
    title: session.metadata?.title || '',
}));
rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'keep' },
            created_at: '2026-01-01T00:00:00.000Z',
        },
        {
            session_id: 'session-2',
            workspace_id: 'workspace-1',
            metadata: { title: 'stale delete' },
            created_at: '2026-01-01T00:00:00.000Z',
            updated_at: '2999-01-02T00:00:00.000Z',
        },
    ],
});
const afterStaleSnapshot = getSidebarDataSnapshot().sessions.map(session => ({
    sessionId: session.session_id,
    title: session.metadata?.title || '',
}));
rememberSidebarDataSnapshot({
    sessions: [
        { session_id: 'session-1', workspace_id: 'workspace-1', metadata: { title: 'keep' } },
        {
            session_id: 'session-2',
            workspace_id: 'workspace-1',
            metadata: { title: 'fresh reappeared' },
            created_at: '2999-01-02T00:00:00.000Z',
            updated_at: '2999-01-02T00:00:00.000Z',
        },
    ],
});
const afterImmediateFreshSnapshot = getSidebarDataSnapshot().sessions.map(session => ({
    sessionId: session.session_id,
    title: session.metadata?.title || '',
}));

console.log(JSON.stringify({ afterRemove, afterStaleSnapshot, afterImmediateFreshSnapshot }));
""".strip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(runner_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    assert json.loads(result.stdout) == {
        "afterRemove": [{"sessionId": "session-1", "title": "keep"}],
        "afterStaleSnapshot": [{"sessionId": "session-1", "title": "keep"}],
        "afterImmediateFreshSnapshot": [{"sessionId": "session-1", "title": "keep"}],
    }


def test_remove_sidebar_session_preserves_existing_rows_from_stale_snapshot(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sessionSidebarStore.js"
    )
    module_path = tmp_path / "sessionSidebarStore.mjs"
    module_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    runner_path = tmp_path / "runner-remove-sidebar-session-stale-merge.mjs"
    runner_path.write_text(
        """
const {
    getSidebarDataSnapshot,
    removeSidebarSession,
    rememberSidebarDataSnapshot,
} = await import('./sessionSidebarStore.mjs');

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'keep' },
            created_at: '2026-01-01T00:00:00.000Z',
        },
        {
            session_id: 'session-2',
            workspace_id: 'workspace-1',
            metadata: { title: 'delete' },
            created_at: '2026-01-01T00:00:00.000Z',
        },
    ],
});
removeSidebarSession('session-2');
rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-2',
            workspace_id: 'workspace-1',
            metadata: { title: 'stale delete' },
            created_at: '2026-01-01T00:00:00.000Z',
        },
    ],
});
const afterStaleSnapshot = getSidebarDataSnapshot().sessions.map(session => ({
    sessionId: session.session_id,
    title: session.metadata?.title || '',
}));

console.log(JSON.stringify({ afterStaleSnapshot }));
""".strip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(runner_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    assert json.loads(result.stdout) == {
        "afterStaleSnapshot": [{"sessionId": "session-1", "title": "keep"}],
    }
