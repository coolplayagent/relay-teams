# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_round_user_prompts_are_collapsible_plaintext_blocks() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    timeline_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "timeline.js"
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
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")

    assert "collapsibleUserPrompts: true," in timeline_script
    assert (
        "collapseUserPrompt: role === 'user' && options.collapsibleUserPrompts === true,"
        in history_script
    )
    assert "function appendUserPromptText(contentEl, text) {" in block_script
    assert "function updateUserPromptText(promptEl, text) {" in block_script
    assert "bodyEl.textContent = normalized;" in block_script
    assert ".user-prompt-block {" in components_css
    assert ".user-prompt-summary {" in components_css
    assert ".user-prompt-preview {" in components_css
    assert "-webkit-line-clamp: 2;" in components_css
    assert ".user-prompt-text {" in components_css
    assert "function buildRoundIntentBlock(intentText) {" in timeline_script
    assert "function normalizeRoundIntentText(intentText) {" in timeline_script
    assert (
        "header.appendChild(buildRoundIntentBlock(round.intent || t('rounds.no_intent')));"
        in timeline_script
    )
    assert ".round-detail-intent-summary {" in components_css
    assert ".round-detail-intent-preview {" in components_css
    assert ".round-detail-intent-body {" in components_css


def test_thinking_blocks_use_compact_summary_spacing() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")

    assert ".thinking-block {" in components_css
    assert "margin: 0 0 0.45rem;" in components_css
    assert "padding: 0.34rem 0.65rem;" in components_css
    assert "padding: 0 0.65rem 0.45rem;" in components_css
