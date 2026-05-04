# -*- coding: utf-8 -*-
from __future__ import annotations


from relay_teams.agents.execution.context_editing import render_compact_section


class TestRenderCompactSection:
    def test_basic_render(self) -> None:
        result = render_compact_section(
            title="Objective",
            content="Build the feature.",
        )
        assert "Objective:" in result
        assert "Build the feature." in result

    def test_empty_content(self) -> None:
        result = render_compact_section(
            title="Notes",
            content="",
        )
        assert result == "Notes: (empty)"

    def test_whitespace_content(self) -> None:
        result = render_compact_section(
            title="Notes",
            content="   ",
        )
        assert result == "Notes: (empty)"

    def test_truncation(self) -> None:
        long_content = "x" * 500
        result = render_compact_section(
            title="Content",
            content=long_content,
            max_length=100,
        )
        assert "..." in result
        assert len(result.split("\n", 1)[1]) < 500

    def test_no_truncation_for_short(self) -> None:
        result = render_compact_section(
            title="Short",
            content="hello",
            max_length=300,
        )
        assert "..." not in result
