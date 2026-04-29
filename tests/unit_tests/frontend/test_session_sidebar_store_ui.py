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
