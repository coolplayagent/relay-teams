# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_prompt_token_renderer_builds_typed_chips(tmp_path: Path) -> None:
    source = Path("frontend/dist/js/utils/promptTokens.js").read_text(encoding="utf-8")
    module_path = tmp_path / "promptTokens.mjs"
    module_path.write_text(source, encoding="utf-8")

    runner = """
import {
    extractPromptTokens,
    renderPromptTokenChipsHtml,
} from './promptTokens.mjs';

const source = '/skill-installer @src/relay_teams/main.py @Main Agent /opsx:propose';
const segments = extractPromptTokens(source, {
    skills: ['skill-installer'],
    commands: ['opsx:propose'],
});
const chips = renderPromptTokenChipsHtml(source, {
    skills: ['skill-installer'],
    commands: ['opsx:propose'],
});

console.log(JSON.stringify({ segments, chips }));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    payload = json.loads(result.stdout)
    tokens = [
        segment["token"]
        for segment in payload["segments"]
        if segment["kind"] == "token"
    ]

    assert [token["type"] for token in tokens] == [
        "skill",
        "file",
        "agent",
        "command",
    ]
    assert [token["label"] for token in tokens] == [
        "Skill Installer",
        "main.py",
        "@Main Agent",
        "/opsx:propose",
    ]
    assert "prompt-token-skill" in payload["chips"]
    assert "prompt-token-file" in payload["chips"]
    assert "prompt-token-agent" in payload["chips"]
    assert "prompt-token-command" in payload["chips"]


def test_round_intent_uses_prompt_token_renderer() -> None:
    timeline_source = Path("frontend/dist/js/components/rounds/timeline.js").read_text(
        encoding="utf-8"
    )
    layout_css = Path("frontend/dist/css/layout.css").read_text(encoding="utf-8")

    assert "renderPromptTokenizedText(previewEl, normalized)" in timeline_source
    assert "round-detail-intent-text" in timeline_source
    assert ".prompt-token-chip" in layout_css
