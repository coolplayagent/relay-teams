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


def test_terminal_run_override_wins_over_stale_active_snapshot(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sessionSidebarStore.js"
    )
    module_path = tmp_path / "sessionSidebarStore.mjs"
    module_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    runner_path = tmp_path / "runner-terminal-override.mjs"
    runner_path.write_text(
        """
const {
    getSidebarDataSnapshot,
    markSidebarSessionRunTerminal,
    rememberSidebarDataSnapshot,
} = await import('./sessionSidebarStore.mjs');

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'stale active' },
            has_active_run: true,
            active_run_id: 'run-1',
            active_run_status: 'stopping',
        },
    ],
});
markSidebarSessionRunTerminal('session-1', { runId: 'run-1', status: 'stopped' });

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'stale active' },
            has_active_run: true,
            active_run_id: 'run-1',
            active_run_status: 'stopping',
        },
    ],
});
const merged = getSidebarDataSnapshot().sessions[0];

console.log(JSON.stringify({
    hasActiveRun: merged.has_active_run,
    activeRunId: merged.active_run_id,
    activeRunStatus: merged.active_run_status,
    latestTerminalRunId: merged.latest_terminal_run_id,
    latestTerminalRunStatus: merged.latest_terminal_run_status,
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
        "hasActiveRun": False,
        "activeRunId": "",
        "activeRunStatus": "",
        "latestTerminalRunId": "run-1",
        "latestTerminalRunStatus": "stopped",
    }


def test_terminal_run_override_wins_over_stale_empty_snapshot(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sessionSidebarStore.js"
    )
    module_path = tmp_path / "sessionSidebarStore.mjs"
    module_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    runner_path = tmp_path / "runner-terminal-empty-override.mjs"
    runner_path.write_text(
        """
const {
    getSidebarDataSnapshot,
    markSidebarSessionRunTerminal,
    rememberSidebarDataSnapshot,
} = await import('./sessionSidebarStore.mjs');

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'hello' },
        },
    ],
});
markSidebarSessionRunTerminal('session-1', { runId: 'run-1', status: 'completed' });

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'hello' },
            has_active_run: false,
            active_run_id: '',
            active_run_status: '',
            has_unread_terminal_run: false,
        },
    ],
});
const merged = getSidebarDataSnapshot().sessions[0];

console.log(JSON.stringify({
    hasActiveRun: merged.has_active_run,
    activeRunId: merged.active_run_id,
    activeRunStatus: merged.active_run_status,
    latestTerminalRunId: merged.latest_terminal_run_id,
    latestTerminalRunStatus: merged.latest_terminal_run_status,
    unread: merged.has_unread_terminal_run,
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
        "hasActiveRun": False,
        "activeRunId": "",
        "activeRunStatus": "",
        "latestTerminalRunId": "run-1",
        "latestTerminalRunStatus": "completed",
        "unread": True,
    }


def test_session_status_machine_suppresses_stale_unread_after_viewed(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sessionSidebarStore.js"
    )
    module_path = tmp_path / "sessionSidebarStore.mjs"
    module_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    runner_path = tmp_path / "runner-status-machine.mjs"
    runner_path.write_text(
        """
const {
    getSidebarDataSnapshot,
    markSidebarSessionRunActive,
    markSidebarSessionRunTerminal,
    markSidebarSessionTerminalViewed,
    rememberSidebarDataSnapshot,
} = await import('./sessionSidebarStore.mjs');

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'hello' },
        },
    ],
});

markSidebarSessionRunActive('session-1', { runId: 'run-1', status: 'running' });
const running = getSidebarDataSnapshot().sessions[0];

markSidebarSessionRunTerminal('session-1', {
    runId: 'run-1',
    status: 'completed',
    viewed: false,
});
const unread = getSidebarDataSnapshot().sessions[0];

markSidebarSessionTerminalViewed('session-1');
const viewed = getSidebarDataSnapshot().sessions[0];

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'hello' },
            latest_terminal_run_id: 'run-1',
            latest_terminal_run_status: 'completed',
            has_unread_terminal_run: true,
        },
    ],
});
const staleSameTerminal = getSidebarDataSnapshot().sessions[0];

rememberSidebarDataSnapshot({
    sessions: [
        {
            session_id: 'session-1',
            workspace_id: 'workspace-1',
            metadata: { title: 'hello again' },
            latest_terminal_run_id: 'run-2',
            latest_terminal_run_status: 'completed',
            has_unread_terminal_run: true,
        },
    ],
});
const newTerminal = getSidebarDataSnapshot().sessions[0];

console.log(JSON.stringify({
    running: {
        hasActiveRun: running.has_active_run,
        activeRunId: running.active_run_id,
        activeRunStatus: running.active_run_status,
        unread: running.has_unread_terminal_run,
    },
    unread: {
        hasActiveRun: unread.has_active_run,
        latestTerminalRunId: unread.latest_terminal_run_id,
        latestTerminalRunStatus: unread.latest_terminal_run_status,
        unread: unread.has_unread_terminal_run,
    },
    viewed: {
        latestTerminalRunId: viewed.latest_terminal_run_id,
        unread: viewed.has_unread_terminal_run,
    },
    staleSameTerminal: {
        latestTerminalRunId: staleSameTerminal.latest_terminal_run_id,
        unread: staleSameTerminal.has_unread_terminal_run,
    },
    newTerminal: {
        latestTerminalRunId: newTerminal.latest_terminal_run_id,
        unread: newTerminal.has_unread_terminal_run,
    },
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
        "running": {
            "hasActiveRun": True,
            "activeRunId": "run-1",
            "activeRunStatus": "running",
            "unread": False,
        },
        "unread": {
            "hasActiveRun": False,
            "latestTerminalRunId": "run-1",
            "latestTerminalRunStatus": "completed",
            "unread": True,
        },
        "viewed": {
            "latestTerminalRunId": "run-1",
            "unread": False,
        },
        "staleSameTerminal": {
            "latestTerminalRunId": "run-1",
            "unread": False,
        },
        "newTerminal": {
            "latestTerminalRunId": "run-2",
            "unread": True,
        },
    }
