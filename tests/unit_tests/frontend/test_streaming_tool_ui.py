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
        "const { container } = resolveToolEventTarget(instanceId, roleId, eventMeta);"
        in tool_events_script
    )
    assert "container," in tool_events_script


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
    assert ".tool-input-value {" in tools_css
    assert ".tool-detail-footer {" not in tools_css
    assert ".tool-result-status {" not in tools_css
