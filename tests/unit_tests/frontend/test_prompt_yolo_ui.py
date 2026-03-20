# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_chat_input_renders_yolo_and_thinking_controls() -> None:
    html = Path("frontend/dist/index.html").read_text(encoding="utf-8")

    assert 'id="yolo-toggle"' in html
    assert 'id="thinking-mode-toggle"' in html
    assert 'id="thinking-effort-select"' in html


def test_send_user_prompt_includes_yolo_and_thinking(tmp_path: Path) -> None:
    source = Path("frontend/dist/js/core/api/runs.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "api"
    temp_dir.mkdir()
    (temp_dir / "runs.js").write_text(source, encoding="utf-8")
    (temp_dir / "request.js").write_text(
        """
export async function requestJson(url, options, errorMessage) {
    globalThis.__captured = {
        url,
        errorMessage,
        method: options.method,
        body: JSON.parse(options.body),
    };
    return { run_id: "run-1", session_id: "session-1" };
}
""".strip(),
        encoding="utf-8",
    )
    runner = """
import { sendUserPrompt } from "./runs.js";

await sendUserPrompt("session-1", "ship it", true, { enabled: true, effort: "high" });
console.log(JSON.stringify(globalThis.__captured));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["url"] == "/api/runs"
    assert payload["method"] == "POST"
    assert payload["body"]["yolo"] is True
    assert payload["body"]["execution_mode"] == "ai"
    assert payload["body"]["thinking"] == {"enabled": True, "effort": "high"}
