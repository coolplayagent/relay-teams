# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_merge_existing_round_page_preserves_loaded_history_cursor(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "paging.js"
    )
    source = (
        source_path.read_text(encoding="utf-8")
        .replace("../../core/api.js", "./mockApi.mjs")
        .replace("../../core/state.js", "./mockState.mjs")
        .replace("./state.js", "./mockRoundsState.mjs")
    )
    (tmp_path / "paging.mjs").write_text(source, encoding="utf-8")
    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchSessionRounds() {
    return { items: [] };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = { currentSessionId: 'session-1' };
export function setRunPrimaryRole() {}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRoundsState.mjs").write_text(
        """
export const roundsState = {
    currentRounds: [],
    pageSize: 10,
    paging: {
        hasMore: false,
        nextCursor: null,
        loading: false,
    },
};
""".strip(),
        encoding="utf-8",
    )
    runner_path = tmp_path / "runner.mjs"
    runner_path.write_text(
        """
import { applyRoundPage } from './paging.mjs';
import { roundsState } from './mockRoundsState.mjs';

roundsState.currentRounds = [
    { run_id: 'run-1', created_at: '2026-04-25T11:01:00' },
    { run_id: 'run-2', created_at: '2026-04-25T11:02:00' },
    { run_id: 'run-3', created_at: '2026-04-25T11:03:00' },
];
roundsState.paging = {
    hasMore: true,
    nextCursor: 'run-1',
    loading: true,
};

applyRoundPage(
    {
        items: [
            { run_id: 'run-3', created_at: '2026-04-25T11:03:00', intent: 'updated' },
            { run_id: 'run-4', created_at: '2026-04-25T11:04:00' },
        ],
        has_more: true,
        next_cursor: 'run-3',
    },
    { prepend: false, mergeExisting: true },
);
const preserved = {
    runIds: roundsState.currentRounds.map(round => round.run_id),
    cursor: roundsState.paging.nextCursor,
    hasMore: roundsState.paging.hasMore,
    loading: roundsState.paging.loading,
    updatedIntent: roundsState.currentRounds.find(round => round.run_id === 'run-3').intent,
};

roundsState.currentRounds = [];
roundsState.paging = {
    hasMore: false,
    nextCursor: null,
    loading: true,
};
applyRoundPage(
    {
        items: [{ run_id: 'run-5', created_at: '2026-04-25T11:05:00' }],
        has_more: true,
        next_cursor: 'run-5',
    },
    { prepend: false, mergeExisting: true },
);
const initial = {
    cursor: roundsState.paging.nextCursor,
    hasMore: roundsState.paging.hasMore,
    loading: roundsState.paging.loading,
};

roundsState.currentRounds = [
    { run_id: 'run-10', created_at: '2026-04-25T11:10:00' },
    { run_id: 'run-11', created_at: '2026-04-25T11:11:00' },
];
roundsState.paging = {
    hasMore: true,
    nextCursor: 'stale-run',
    loading: true,
};
applyRoundPage(
    {
        items: [
            { run_id: 'run-11', created_at: '2026-04-25T11:11:00' },
            { run_id: 'run-12', created_at: '2026-04-25T11:12:00' },
        ],
        has_more: true,
        next_cursor: 'run-11',
    },
    { prepend: false, mergeExisting: true },
);
const refreshed = {
    runIds: roundsState.currentRounds.map(round => round.run_id),
    cursor: roundsState.paging.nextCursor,
    hasMore: roundsState.paging.hasMore,
    loading: roundsState.paging.loading,
};

console.log(JSON.stringify({
    preserved,
    initialCursor: initial.cursor,
    initialHasMore: initial.hasMore,
    initialLoading: initial.loading,
    refreshed,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )

    assert json.loads(completed.stdout) == {
        "preserved": {
            "runIds": ["run-1", "run-2", "run-3", "run-4"],
            "cursor": "run-1",
            "hasMore": True,
            "loading": False,
            "updatedIntent": "updated",
        },
        "initialCursor": "run-5",
        "initialHasMore": True,
        "initialLoading": False,
        "refreshed": {
            "runIds": ["run-10", "run-11", "run-12"],
            "cursor": "run-11",
            "hasMore": True,
            "loading": False,
        },
    }
