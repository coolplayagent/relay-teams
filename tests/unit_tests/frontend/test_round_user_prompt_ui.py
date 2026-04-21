# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess

from .css_helpers import load_components_css


def test_round_user_prompts_are_collapsible_plaintext_blocks() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    timeline_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "timeline.js"
    ).read_text(encoding="utf-8")
    i18n_script = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "i18n.js"
    ).read_text(encoding="utf-8")
    history_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "history.js"
    ).read_text(encoding="utf-8")
    block_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers"
        / "block.js"
    ).read_text(encoding="utf-8")
    components_css = load_components_css()

    assert "collapsibleUserPrompts: true," in timeline_script
    assert (
        "collapseUserPrompt: role === 'user' && options.collapsibleUserPrompts === true,"
        in history_script
    )
    assert "function appendUserPromptText(contentEl, text) {" in block_script
    assert "function appendStructuredUserPrompt(contentEl, items) {" in block_script
    assert (
        "function updateStructuredUserPrompt(promptEl, bodyEl, items) {" in block_script
    )
    assert "export function buildStructuredUserPromptSummary(items) {" in block_script
    assert "export function userPromptItemToStructuredPart(item) {" in block_script
    assert "appendStructuredContentPart(bodyEl, structuredPart);" in block_script
    assert "function updateUserPromptText(promptEl, text) {" in block_script
    assert "bodyEl.textContent = normalized;" in block_script
    assert ".user-prompt-block {" in components_css
    assert ".user-prompt-summary {" in components_css
    assert ".user-prompt-preview {" in components_css
    assert "-webkit-line-clamp: 2;" in components_css
    assert ".user-prompt-text {" in components_css
    assert (
        "function buildRoundIntentBlock(intentText, intentParts) {" in timeline_script
    )
    assert "function normalizeRoundIntentText(intentText) {" in timeline_script
    assert "function normalizeRoundIntentParts(promptPayload) {" in timeline_script
    assert (
        "function renderRoundIntentBody(targetEl, fallbackText, intentParts) {"
        in timeline_script
    )
    assert (
        "header.appendChild(buildRoundIntentBlock(round.intent, round.intent_parts));"
        in timeline_script
    )
    assert (
        "intentEl.replaceWith(buildRoundIntentBlock(round.intent, round.intent_parts));"
        in timeline_script
    )
    assert "appendStructuredContentPart(targetEl, structuredPart);" in timeline_script
    assert "t('rounds.expand')" in timeline_script
    assert "t('rounds.collapse')" in timeline_script
    assert "'rounds.expand': 'Expand'," in i18n_script
    assert "'rounds.collapse': 'Collapse'," in i18n_script
    assert "'rounds.expand': '展开'," in i18n_script
    assert "'rounds.collapse': '收起'," in i18n_script
    assert ".round-detail-intent-summary {" in components_css
    assert ".round-detail-intent-preview {" in components_css
    assert ".round-detail-intent-body {" in components_css
    assert ".round-detail-intent-actions {" in components_css
    assert ".round-detail-intent-collapse {" in components_css


def test_thinking_blocks_use_compact_summary_spacing() -> None:
    components_css = load_components_css()

    assert ".thinking-block {" in components_css
    assert "margin: 0 0 0.45rem;" in components_css
    assert "padding: 0.34rem 0.65rem;" in components_css
    assert "padding: 0 0.65rem 0.45rem;" in components_css


def test_structured_user_prompt_summary_keeps_text_parts(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers"
        / "block.js"
    )
    source_text = source_path.read_text(encoding="utf-8")
    module_text = (
        source_text.replace("../../../core/state.js", "./mockState.mjs")
        .replace("../../../utils/i18n.js", "./mockI18n.mjs")
        .replace("./toolBlocks.js", "./mockToolBlocks.mjs")
        .replace("./content.js", "./mockContent.mjs")
    )

    module_path = tmp_path / "block.mjs"
    module_path.write_text(module_text, encoding="utf-8")
    (tmp_path / "mockState.mjs").write_text(
        """
export function getPrimaryRoleLabel() {
    return "Coordinator";
}

export function isCoordinatorRoleId() {
    return false;
}

export function isMainAgentRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return key === "subagent.task_prompt" ? "task prompt" : key;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockToolBlocks.mjs").write_text(
        """
export function applyToolReturn() {
    return undefined;
}

export function buildToolBlock() {
    return null;
}

export function indexPendingToolBlock() {
    return undefined;
}

export function resolvePendingToolBlock() {
    return null;
}

export function setToolValidationFailureState() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockContent.mjs").write_text(
        """
export function appendStructuredContentPart() {
    return undefined;
}

export function renderRichContent() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import { buildStructuredUserPromptSummary } from "./block.mjs";

const summary = buildStructuredUserPromptSummary([
    { kind: "text", text: "Summarize weather findings" },
    { kind: "binary", media_type: "image/png", name: "" },
]);

console.log(JSON.stringify(summary));
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
    assert payload == {
        "title": "Summarize weather findings",
        "preview": "Summarize weather findings · Image",
    }
