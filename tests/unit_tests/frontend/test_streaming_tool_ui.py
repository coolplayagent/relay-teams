# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_tool_result_updates_can_patch_dom_after_stream_finalize() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    stream_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "stream.js"
    ).read_text(encoding="utf-8")
    tool_events_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "core"
        / "eventRouter"
        / "toolEvents.js"
    ).read_text(encoding="utf-8")

    assert "findToolBlockInContainer" in stream_script
    assert "const container = options.container || null;" in stream_script
    assert (
        "resolveToolBlockTarget(st, container, toolName, toolCallId)" in stream_script
    )
    assert (
        "return findToolBlockInContainer(container, toolName, toolCallId);"
        in stream_script
    )
    assert (
        "const { container, isCoordinator } = resolveToolEventTarget(instanceId, roleId, eventMeta);"
        in tool_events_script
    )
    assert "container," in tool_events_script
    assert (
        "scheduleCurrentSessionSubagentDiscovery({ delayMs: 0 });" in tool_events_script
    )


def test_streaming_tool_calls_keep_indexed_dom_targets_and_message_metadata() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    stream_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "stream.js"
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

    assert (
        "indexPendingToolBlock(st.pendingToolBlocks, toolBlock, toolName, toolCallId);"
        in stream_script
    )
    assert "const indexed = resolvePendingToolBlock(" in stream_script
    assert (
        "const pendingToolBlocks = bindReusableToolBlocks(contentEl, overlayEntry);"
        in stream_script
    )
    assert "function bindReusableToolBlocks(contentEl, overlayEntry) {" in stream_script
    assert "wrapper.dataset.runId = runId;" in block_script
    assert "wrapper.dataset.instanceId = instanceId;" in block_script
    assert "wrapper.dataset.roleId = roleId;" in block_script
    assert "wrapper.dataset.streamKey = streamKey;" in block_script


def test_live_streaming_tool_overlay_skips_processed_group_summary() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    history_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "history.js"
    ).read_text(encoding="utf-8")

    assert (
        "if (shouldCollapseIntermediateMessages(streamOverlayEntry, options)) {"
        in history_script
    )
    assert (
        "function shouldCollapseIntermediateMessages(streamOverlayEntry, options = {}) {"
        in history_script
    )
    assert (
        "const runStatus = String(options.runStatus || '').trim().toLowerCase();"
        in history_script
    )
    assert "const isLatestRound = options.isLatestRound === true;" in history_script
    assert "if (isLatestRound && runStatus !== 'completed') {" in history_script
    assert "if (streamOverlayEntry.textStreaming === true) {" in history_script
    assert "status === 'pending'" in history_script
    assert "status === 'running'" in history_script
    assert "approvalStatus === 'requested'" in history_script
    assert "function isApprovedApprovalStatus(value)" in history_script
    assert "approvalStatus === 'approve_exact'" in history_script


def test_tool_blocks_extract_effective_inputs_instead_of_footer_status() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    tool_blocks_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers"
        / "toolBlocks.js"
    ).read_text(encoding="utf-8")
    tools_css = (
        repo_root / "frontend" / "dist" / "css" / "components" / "tools.css"
    ).read_text(encoding="utf-8")

    assert "fields: ['command', 'cmd']" in tool_blocks_script
    assert (
        "fields: ['path', 'file_path', 'filepath', 'target_path']" in tool_blocks_script
    )
    assert "fields: ['query', 'q', 'search_query']" in tool_blocks_script
    assert "fields: ['url', 'uri']" in tool_blocks_script
    assert "function normalizeToolArgs(args) {" in tool_blocks_script
    assert "return { __raw: raw };" in tool_blocks_script
    assert (
        'return `<div class="tool-input-value"><code>${escapeHtml(info.detailText)}</code></div>`;'
        in tool_blocks_script
    )
    assert (
        'return `<div class="tool-input-value"><code>${escapeHtml(info.detailText)}</code>${lineRangeHtml}</div>`;'
        in tool_blocks_script
    )
    assert ".tool-input-value {" in tools_css
    assert ".tool-detail-footer {" not in tools_css
    assert ".tool-result-status {" not in tools_css


def test_tool_blocks_parse_tagged_read_payloads_and_cap_large_diffs() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    tool_blocks_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers"
        / "toolBlocks.js"
    ).read_text(encoding="utf-8")
    tools_css = (
        repo_root / "frontend" / "dist" / "css" / "components" / "tools.css"
    ).read_text(encoding="utf-8")

    assert "function parseReadPayload(text) {" in tool_blocks_script
    assert "function extractTaggedSection(text, lines, tagName) {" in tool_blocks_script
    assert "const INLINE_READ_TAGS = new Set(['path', 'type']);" in tool_blocks_script
    assert "function extractInlineTaggedSection(lines, tagName) {" in tool_blocks_script
    assert "if (!INLINE_READ_TAGS.has(tagName)) {" in tool_blocks_script
    assert "function extractBlockTaggedSection(lines, tagName) {" in tool_blocks_script
    assert (
        "function renderTaggedLineContent(text, fallbackStartLine = 1) {"
        in tool_blocks_script
    )
    assert "const MAX_DIFF_DP_CELLS = 50000;" in tool_blocks_script
    assert "const MAX_DIFF_TOTAL_LINES = 600;" in tool_blocks_script
    assert "const MAX_WRITE_PREVIEW_LINES = 200;" in tool_blocks_script
    assert "const MAX_WRITE_PREVIEW_CHARS = 12000;" in tool_blocks_script
    assert (
        "function buildBoundedPreview(text, { maxLines, maxChars }) {"
        in tool_blocks_script
    )
    assert "Preview truncated. Showing first" in tool_blocks_script
    assert "return pairLinesByIndex(oldLines, newLines);" in tool_blocks_script
    assert 'class="tool-diff-no"' not in tool_blocks_script
    assert ".tool-diff-no {" not in tools_css
