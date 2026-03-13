# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
import subprocess
from typing import cast


def test_frontend_logger_batches_and_posts_structured_events(tmp_path: Path) -> None:
    payload = _run_frontend_logger_script(
        tmp_path=tmp_path,
        runner_source="""
import { flushFrontendLogs, logError } from "./logger.mjs";

logError("frontend.test.failure", "frontend failed", {
    component: "composer",
});
await flushFrontendLogs();

console.log(JSON.stringify(globalThis.__capturedBatches));
""".strip(),
    )

    assert len(payload) == 1
    batch = cast(dict[str, JsonValue], payload[0])
    assert "events" in batch
    events = cast(list[dict[str, JsonValue]], batch["events"])
    event = cast(dict[str, JsonValue], events[0])
    assert event["event"] == "frontend.test.failure"
    assert event["message"] == "frontend failed"
    assert event["page"] == "agent-teams"
    assert event["route"] == "/chat"
    browser_session_id = cast(str, event["browser_session_id"])
    assert browser_session_id.startswith("browser_")


def _run_frontend_logger_script(
    tmp_path: Path, runner_source: str
) -> list[dict[str, JsonValue]]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "utils" / "logger.js"

    logger_module_path = tmp_path / "logger.mjs"
    mock_dom_path = tmp_path / "mockDom.mjs"
    mock_state_path = tmp_path / "mockState.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_dom_path.write_text(
        """
export const els = {
    systemLogs: null,
};
""".strip(),
        encoding="utf-8",
    )
    mock_state_path.write_text(
        """
export const state = {
    currentSessionId: "session-ui",
    activeRunId: "run-ui",
};
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("./dom.js", "./mockDom.mjs")
    )
    logger_module_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
globalThis.__capturedBatches = [];
globalThis.location = {{ pathname: "/chat" }};
globalThis.document = {{ title: "agent-teams" }};
Object.defineProperty(globalThis, "navigator", {{
    configurable: true,
    value: {{
        userAgent: "node-test",
        sendBeacon() {{
            return false;
        }},
    }},
}});
globalThis.addEventListener = () => {{}};
globalThis.fetch = async (_url, options) => {{
    globalThis.__capturedBatches.push(JSON.parse(options.body));
    return {{
        ok: true,
        async json() {{
            return {{}};
        }},
    }};
}};

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
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)
