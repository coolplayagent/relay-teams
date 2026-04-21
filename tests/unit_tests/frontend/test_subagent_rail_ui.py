# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_subagent_rail_refresh_delegates_to_unified_subagent_cache(
    tmp_path: Path,
) -> None:
    payload = _run_subagent_rail_script(
        tmp_path=tmp_path,
        runner_source="""
const { refreshSubagentRail } = await import("./subagentRail.mjs");

const result = await refreshSubagentRail("session-1");

console.log(JSON.stringify({
    ensureCalls: globalThis.__ensureSessionSubagentsCalls,
    result,
}));
""".strip(),
    )

    assert payload["ensureCalls"] == [
        {
            "sessionId": "session-1",
            "options": {"force": True, "emitLoadingEvents": False},
        }
    ]
    assert payload["result"] == [{"instanceId": "inst-1", "runId": "run-1"}]


def test_subagent_rail_exports_safe_noops_for_removed_right_rail(
    tmp_path: Path,
) -> None:
    payload = _run_subagent_rail_script(
        tmp_path=tmp_path,
        runner_source="""
const rail = await import("./subagentRail.mjs");

rail.initializeSubagentRail();
rail.rememberLiveSubagent("inst-1", "Writer");
rail.markSubagentStatus("inst-1", "running");
rail.selectSubagentRole("Writer", { reveal: true });
rail.focusSubagent("inst-1", "Writer");
rail.syncSelectedRoleByInstance("inst-1", "Writer");
rail.setSubagentRailExpanded(false);

console.log(JSON.stringify({ ok: true }));
""".strip(),
    )

    assert payload == {"ok": True}


def _run_subagent_rail_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentRail.js"
    )

    module_under_test_path = tmp_path / "subagentRail.mjs"
    runner_path = tmp_path / "runner.mjs"
    mock_subagent_sessions_path = tmp_path / "mockSubagentSessions.mjs"

    source_text = source_path.read_text(encoding="utf-8").replace(
        "./subagentSessions.js", "./mockSubagentSessions.mjs"
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    mock_subagent_sessions_path.write_text(
        """
export async function ensureSessionSubagents(sessionId, options = {}) {
    globalThis.__ensureSessionSubagentsCalls.push({ sessionId, options });
    return [{ instanceId: "inst-1", runId: "run-1" }];
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        f"""
globalThis.__ensureSessionSubagentsCalls = [];

{runner_source}
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=3,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\\n"
            f"STDOUT:\\n{completed.stdout}\\n"
            f"STDERR:\\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
