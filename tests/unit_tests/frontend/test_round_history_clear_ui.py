# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_round_history_clear_ui_renders_segment_dividers_and_collapsed_history() -> (
    None
):
    repo_root = Path(__file__).resolve().parents[3]
    timeline_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "timeline.js"
    ).read_text(encoding="utf-8")
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")

    assert "const expandedHistorySegments = new Set();" in timeline_script
    assert "splitRoundsByHistoryMarkers(rounds)" in timeline_script
    assert "renderClearDivider(segment, isExpanded)" in timeline_script
    assert "toggleHistorySegment(segment.segmentId);" in timeline_script
    assert "expandHistorySegmentForRun(round.run_id);" in timeline_script
    assert "round-history-segment" in timeline_script
    assert "round-clear-divider" in timeline_script
    assert "round-history-divider" in timeline_script
    assert "History cleared" in timeline_script
    assert "History compacted" in timeline_script
    assert "Show ${roundLabel}" in timeline_script
    assert "Hide ${roundLabel}" in timeline_script

    assert ".round-history-segment {" in components_css
    assert ".round-history-segment-body[hidden] {" in components_css
    assert ".round-clear-divider {" in components_css
    assert ".round-history-divider," in components_css
    assert ".message-history-divider {" in components_css
    assert ".round-clear-divider-chip {" in components_css
    assert ".round-history-divider-chip," in components_css
    assert ".round-clear-divider-title {" in components_css
    assert "body.light-theme .round-clear-divider-chip," in components_css
    assert "body.light-theme .round-history-divider-chip," in components_css
