# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_retry_status_updates_single_round_card_and_clears_live_state(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "app" / "retryStatus.js"
    module_under_test_path = tmp_path / "retryStatus.mjs"
    mock_rounds_path = tmp_path / "mockRounds.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_rounds_path.write_text(
        """
export const appended = [];
export const removed = [];
export const updated = [];

export function appendRoundRetryEvent(runId, payload) {
    appended.push({ runId, payload });
}

export function removeRoundRetryEvent(runId, eventId) {
    removed.push({ runId, eventId });
}

export function updateRoundRetryEvent(runId, eventId, payload) {
    updated.push({ runId, eventId, payload });
}
""".strip(),
        encoding="utf-8",
    )
    source_text = source_path.read_text(encoding="utf-8").replace(
        "../components/rounds.js",
        "./mockRounds.mjs",
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    runner_path.write_text(
        """
import { appended, removed, updated } from "./mockRounds.mjs";
import {
    beginLlmRetryAttempt,
    clearLlmRetryStatus,
    markLlmRetryFailed,
    markLlmRetrySucceeded,
    showLlmRetryStatus,
} from "./retryStatus.mjs";

globalThis.window = {
    setInterval() {
        return 1;
    },
    clearInterval() {},
};

showLlmRetryStatus(
    {
        role_id: "Coordinator",
        instance_id: "inst-1",
        attempt_number: 2,
        total_attempts: 6,
        retry_in_ms: 1000,
        error_code: "2062",
        error_message: "busy",
    },
    { run_id: "run-1", occurred_at: "2026-03-20T00:00:00Z" },
);

beginLlmRetryAttempt();

showLlmRetryStatus(
    {
        role_id: "Coordinator",
        instance_id: "inst-1",
        attempt_number: 3,
        total_attempts: 6,
        retry_in_ms: 1900,
        error_code: "2062",
        error_message: "still busy",
    },
    { run_id: "run-1", occurred_at: "2026-03-20T00:00:03Z" },
);

beginLlmRetryAttempt();
markLlmRetryFailed("final provider error");

const beforeSuccess = {
    appended: appended.slice(),
    removed: removed.slice(),
    updated: updated.slice(),
};

showLlmRetryStatus(
    {
        role_id: "Coordinator",
        instance_id: "inst-1",
        attempt_number: 2,
        total_attempts: 6,
        retry_in_ms: 1000,
        error_code: "2062",
        error_message: "busy again",
    },
    { run_id: "run-2", occurred_at: "2026-03-20T00:00:10Z" },
);

beginLlmRetryAttempt();
markLlmRetrySucceeded();

const beforeClear = {
    appended: appended.slice(),
    removed: removed.slice(),
    updated: updated.slice(),
};

clearLlmRetryStatus();

console.log(JSON.stringify({
    beforeSuccess,
    beforeClear,
    afterClear: {
        appended: appended.slice(),
        removed: removed.slice(),
        updated: updated.slice(),
    },
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    before_success = payload["beforeSuccess"]
    appended = before_success["appended"]
    removed = before_success["removed"]
    updated = before_success["updated"]

    assert len(appended) == 2
    assert appended[0]["runId"] == "run-1"
    assert appended[0]["payload"]["attempt_number"] == 2
    assert appended[1]["payload"]["attempt_number"] == 3
    assert appended[0]["payload"]["phase"] == "scheduled"
    assert appended[0]["payload"]["occurred_at"] == "2026-03-20T00:00:00Z"
    assert removed[0]["runId"] == "run-1"
    assert len(updated) >= 2
    phase_updates = [item["payload"].get("phase") for item in updated]
    assert "retrying" in phase_updates
    assert "failed" in phase_updates
    failed_update = next(
        item for item in updated if item["payload"].get("phase") == "failed"
    )
    assert failed_update["payload"]["error_message"] == "final provider error"

    before_clear_removed = payload["beforeClear"]["removed"]
    assert len(before_clear_removed) == 2
    assert before_clear_removed[-1]["runId"] == "run-2"

    after_clear_removed = payload["afterClear"]["removed"]
    assert len(after_clear_removed) == 2
