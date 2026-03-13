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
