# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

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
    assert "function updateUserPromptText(promptEl, text) {" in block_script
    assert "function updateThinkingText(textEl, text, options = {}) {" in block_script
    assert "appendPromptContentBlock(contentEl, text, {" in block_script
    assert "return updatePromptContentBlock(promptEl, text, {" in block_script
    assert (
        "enableWorkspaceImagePreview: options.enableWorkspaceImagePreview !== false,"
        in block_script
    )
    assert (
        "updateMessageText(textEl, text, {\n"
        "        ...options,\n"
        "        enableWorkspaceImagePreview: false,\n"
        "        forcePlainText: true,\n"
        "    });" in block_script
    )
    assert ".user-prompt-block {" in components_css
    assert ".user-prompt-summary {" in components_css
    assert ".user-prompt-preview {" in components_css
    assert "-webkit-line-clamp: 2;" in components_css
    assert ".user-prompt-text {" in components_css
    assert (
        "function buildRoundIntentBlock(runId, intentText, intentParts = null, options = {}) {"
        in timeline_script
    )
    assert "block.open = hasMedia;" not in timeline_script
    assert "const roundIntentOpenState = new Map();" in timeline_script
    assert "roundIntentOpenState.clear();" not in timeline_script
    assert (
        "const stateKey = roundIntentOpenStateKey(runId, intentKey);" in timeline_script
    )
    assert "block.dataset.intentKey = intentKey;" in timeline_script
    assert "block.dataset.intentStateKey = stateKey;" in timeline_script
    assert "block.dataset.restoreOpen = 'true';" in timeline_script
    assert (
        "function getRoundIntentKey(intentText, intentParts = null) {"
        in timeline_script
    )
    assert (
        "function roundIntentKeyFromNormalized(normalizedText, normalizedParts = null) {"
        in timeline_script
    )
    assert "function roundIntentOpenStateKey(runId, intentKey) {" in timeline_script
    assert "return safeRunId;" in timeline_script
    assert "function hasStructuredIntentContent(parts) {" in timeline_script
    assert "block.dataset.hasStructuredContent" in timeline_script
    assert "function rememberRoundIntentOpenState(block) {" in timeline_script
    assert "function forgetRoundIntentOpenState(block) {" in timeline_script
    assert "return `parts:${JSON.stringify(normalizedParts)}`;" in timeline_script
    assert "function normalizeRoundIntentText(intentText) {" in timeline_script
    assert "function normalizeRoundIntentParts(promptPayload) {" in timeline_script
    assert (
        "function renderRoundIntentStructuredContent(bodyEl, parts) {"
        in timeline_script
    )
    assert "renderPromptTokenizedText(previewEl, normalized)" in timeline_script
    assert (
        "renderPromptTokenizedText(textEl, String(part.text || ''))" in timeline_script
    )
    assert "round-detail-intent-text" in timeline_script
    assert (
        "header.appendChild(buildRoundIntentBlock(round.run_id, round.intent, round.intent_parts));"
        in timeline_script
    )
    assert (
        "section.appendChild(renderRoundHistoryDivider(round.compaction_marker_before));"
        in timeline_script
    )
    assert (
        "const promptBlock = buildRoundPromptBlock(round.intent, round.intent_parts);"
        not in timeline_script
    )
    assert (
        "const nextIntentKey = getRoundIntentKey(round.intent, round.intent_parts);"
        in timeline_script
    )
    assert "if (intentEl.dataset.intentKey === nextIntentKey) {" in timeline_script
    assert "scheduleRoundIntentOverflowMeasure(intentEl);" in timeline_script
    assert (
        "const initialOpen = intentEl.open === true || intentEl.dataset.restoreOpen === 'true';"
        in timeline_script
    )
    assert (
        "intentEl.replaceWith(buildRoundIntentBlock(round.run_id, round.intent, round.intent_parts, { initialOpen }));"
        in timeline_script
    )
    assert "patchRoundPromptBlock(section, round);" not in timeline_script
    assert "t('rounds.expand')" in timeline_script
    assert "t('rounds.collapse')" in timeline_script
    assert "block.dataset.overflow = 'pending';" in timeline_script
    assert (
        "function scheduleRoundIntentOverflowMeasure(block, attempt = 0) {"
        in timeline_script
    )
    assert (
        "function updateRoundIntentOverflowState(block, attempt = 0) {"
        in timeline_script
    )
    assert (
        "const hasStructuredContent = block.dataset.hasStructuredContent === 'true';"
        in timeline_script
    )
    assert "|| scrollHeight > clientHeight + 1" in timeline_script
    assert (
        "const shouldRestoreOpen = block.dataset.restoreOpen === 'true';"
        in timeline_script
    )
    assert "block.dataset.overflow = hasOverflow ? 'true' : 'false';" in timeline_script
    assert "if (hasOverflow && shouldRestoreOpen) {" in timeline_script
    assert "delete block.dataset.restoreOpen;" in timeline_script
    assert "forgetRoundIntentOpenState(block);" in timeline_script
    assert "summaryEl.tabIndex = hasOverflow ? 0 : -1;" in timeline_script
    assert (
        "summaryEl.setAttribute('aria-disabled', hasOverflow ? 'false' : 'true');"
        in timeline_script
    )
    assert "if (block.dataset.overflow !== 'true') {" in timeline_script
    assert "'rounds.expand': 'Expand'," in i18n_script
    assert "'rounds.collapse': 'Collapse'," in i18n_script
    assert "'rounds.expand': '展开'," in i18n_script
    assert "'rounds.collapse': '收起'," in i18n_script
    assert ".round-detail-intent-summary {" in components_css
    assert ".round-detail-intent-preview {" in components_css
    assert ".round-detail-intent-body {" in components_css
    assert ".round-detail-intent-actions {" in components_css
    assert ".round-detail-intent-collapse {" in components_css
    assert (
        '.round-detail-intent[data-overflow="true"] .round-detail-intent-toggle {'
        in components_css
    )
    assert (
        '.round-detail-intent[data-overflow="true"][open] > .round-detail-intent-summary {'
        in components_css
    )
    assert "border-radius: 7px;" in components_css
    assert (
        "background: color-mix(in srgb, var(--bg-surface) 78%, transparent);"
        in components_css
    )
    assert (
        "transition: border-color 0.16s ease, background-color 0.16s ease, color 0.16s ease;"
        in components_css
    )
    assert ".round-detail-intent-collapse:hover {" in components_css
    prompt_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers"
        / "prompt.js"
    ).read_text(encoding="utf-8")
    assert (
        "export function renderPromptContentParts(targetEl, parts, options = {}) {"
        in prompt_script
    )
    assert (
        "enableWorkspaceImagePreview: options.enableWorkspaceImagePreview !== false,"
        in prompt_script
    )


def test_thinking_blocks_use_compact_summary_spacing() -> None:
    components_css = load_components_css()

    assert ".thinking-block {" in components_css
    assert "margin: 0 0 0.45rem;" in components_css
    assert "padding: 0.34rem 0.65rem;" in components_css
    assert "padding: 0 0.65rem 0.45rem;" in components_css
