# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_round_state_uses_running_delegated_tasks(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "utils.js"
    )
    module_under_test_path = tmp_path / "roundUtils.mjs"
    runner_path = tmp_path / "runner.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
        .replace("../../core/state.js", "./mockState.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    (tmp_path / "mockI18n.mjs").write_text(
        """
const translations = {
    'rounds.state.running': 'Running',
    'rounds.state.completed': 'Completed',
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    sessionTasks: [],
};
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
const { state } = await import('./mockState.mjs');
const {
    effectiveRoundStatus,
    roundIsRunning,
    roundStateLabel,
    roundStateTone,
} = await import('./roundUtils.mjs');

const round = {
    run_id: 'run-1',
    run_status: 'completed',
    run_phase: 'terminal',
};

state.sessionTasks = [
    {
        task_id: 'task-writer',
        run_id: 'run-1',
        status: 'running',
        assigned_instance_id: 'writer-1',
        assigned_role_id: 'writer',
    },
];

console.log(JSON.stringify({
    status: effectiveRoundStatus(round),
    running: roundIsRunning(round),
    label: roundStateLabel(round),
    tone: roundStateTone(round),
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
        encoding="utf-8",
        timeout=3,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert payload == {
        "status": "running",
        "running": True,
        "label": "Running",
        "tone": "running",
    }
