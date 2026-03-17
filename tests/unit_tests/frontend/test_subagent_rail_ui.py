# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast


def test_subagent_rail_filters_dynamic_coordinator_role(tmp_path: Path) -> None:
    payload = _run_subagent_rail_script(
        tmp_path=tmp_path,
        runner_source="""
const { refreshSubagentRail, rememberLiveSubagent } = await import("./subagentRail.mjs");
const { state } = await import("./mockState.mjs");

state.currentSessionId = "session-1";
state.coordinatorRoleId = "Coordinator";

await refreshSubagentRail("session-1");
rememberLiveSubagent("coord-2", "Coordinator");
rememberLiveSubagent("writer-2", "writer");

console.log(JSON.stringify({
    sessionAgents: state.sessionAgents,
    sessionTasks: state.sessionTasks,
    selectedRoleId: state.selectedRoleId,
    summaryText: globalThis.__elements.subagentStatusSummary.textContent,
    selectorHtml: globalThis.__elements.subagentRoleSelect.innerHTML,
    metaHtml: globalThis.__elements.subagentRoleMeta.innerHTML,
    metaHidden: globalThis.__elements.subagentRoleMeta.hidden,
    openAgentPanelCalls: globalThis.__openAgentPanelCalls,
}));
""".strip(),
    )

    assert payload["sessionAgents"] == [
        {
            "instance_id": "writer-2",
            "role_id": "writer",
            "status": "running",
            "created_at": "2026-03-13T00:01:00Z",
            "updated_at": "2026-03-13T00:02:00.000Z",
            "reflection_summary_preview": "Use concise drafts.",
            "reflection_updated_at": "2026-03-13T00:01:30Z",
            "runtime_system_prompt": "You are the runtime writer.",
            "runtime_tools_json": '{"local_tools":[],"skill_tools":[],"mcp_tools":[]}',
        }
    ]
    assert payload["sessionTasks"] == [
        {
            "task_id": "task-writer",
            "title": "Write result",
            "role_id": "writer",
            "status": "running",
            "instance_id": "writer-1",
            "run_id": "run-1",
            "created_at": "2026-03-13T00:01:10Z",
            "updated_at": "2026-03-13T00:01:40Z",
        }
    ]
    assert payload["selectedRoleId"] == "writer"
    assert payload["summaryText"] == "1 running / 1 roles"
    selector_html = cast(str, payload["selectorHtml"])
    meta_html = cast(str, payload["metaHtml"])
    open_agent_panel_calls = cast(list[object], payload["openAgentPanelCalls"])

    assert "Coordinator" not in selector_html
    assert "writer" in selector_html
    assert meta_html == ""
    assert payload["metaHidden"] is True
    assert open_agent_panel_calls[-1] == {
        "instanceId": "writer-2",
        "roleId": "writer",
        "options": {
            "reveal": False,
            "forceRefresh": False,
        },
    }


def _run_subagent_rail_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentRail.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_state_path = tmp_path / "mockState.mjs"
    mock_agent_panel_path = tmp_path / "mockAgentPanel.mjs"
    mock_dom_path = tmp_path / "mockDom.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "subagentRail.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
export async function fetchSessionAgents() {
    return [
        {
            instance_id: "coord-1",
            role_id: "Coordinator",
            status: "running",
            created_at: "2026-03-13T00:00:00Z",
            updated_at: "2026-03-13T00:00:00Z",
        },
        {
            instance_id: "writer-1",
            role_id: "writer",
            status: "running",
            created_at: "2026-03-13T00:01:00Z",
            updated_at: "2026-03-13T00:01:00Z",
            reflection_summary_preview: "Use concise drafts.",
            reflection_updated_at: "2026-03-13T00:01:30Z",
            runtime_system_prompt: "You are the runtime writer.",
            runtime_tools_json: '{"local_tools":[],"skill_tools":[],"mcp_tools":[]}',
        },
    ];
}

export async function fetchSessionTasks() {
    return [
        {
            task_id: "task-coordinator",
            title: "Coordinate run",
            role_id: "Coordinator",
            status: "completed",
            instance_id: "coord-1",
            run_id: "run-1",
        },
        {
            task_id: "task-writer",
            title: "Write result",
            role_id: "writer",
            status: "running",
            instance_id: "writer-1",
            run_id: "run-1",
            created_at: "2026-03-13T00:01:10Z",
            updated_at: "2026-03-13T00:01:40Z",
        },
    ];
}
""".strip(),
        encoding="utf-8",
    )
    mock_state_path.write_text(
        """
export const state = {
    currentSessionId: null,
    sessionAgents: [],
    sessionTasks: [],
    selectedRoleId: null,
    pausedSubagent: null,
    currentRecoverySnapshot: null,
    activeAgentRoleId: null,
    coordinatorRoleId: null,
    rightRailExpanded: true,
};

export function isCoordinatorRoleId(roleId) {
    const safeRoleId = String(roleId || "").trim();
    const coordinatorRoleId = String(state.coordinatorRoleId || "").trim();
    return !!safeRoleId && !!coordinatorRoleId && safeRoleId === coordinatorRoleId;
}
""".strip(),
        encoding="utf-8",
    )
    mock_agent_panel_path.write_text(
        """
export function openAgentPanel(instanceId, roleId, options = {}) {
    globalThis.__openAgentPanelCalls.push({ instanceId, roleId, options });
}
""".strip(),
        encoding="utf-8",
    )
    mock_dom_path.write_text(
        """
export const els = globalThis.__elements;
""".strip(),
        encoding="utf-8",
    )
    mock_logger_path.write_text(
        """
export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("./agentPanel.js", "./mockAgentPanel.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
const RealDate = Date;

class FixedDate extends RealDate {{
    constructor(...args) {{
        super(...(args.length > 0 ? args : ["2026-03-13T00:02:00.000Z"]));
    }}

    static now() {{
        return new RealDate("2026-03-13T00:02:00.000Z").getTime();
    }}
}}

function createClassList() {{
    return {{
        toggle() {{
            return undefined;
        }},
    }};
}}

function createElement() {{
    return {{
        innerHTML: "",
        textContent: "",
        disabled: false,
        hidden: false,
        value: "",
        classList: createClassList(),
    }};
}}

globalThis.Date = FixedDate;
globalThis.__elements = {{
    toggleSubagentsBtn: createElement(),
    subagentRoleSelect: createElement(),
    subagentStatusSummary: createElement(),
    subagentRoleMeta: createElement(),
    rightRail: createElement(),
    rightRailResizer: createElement(),
}};
globalThis.__openAgentPanelCalls = [];

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
        encoding="utf-8",
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
