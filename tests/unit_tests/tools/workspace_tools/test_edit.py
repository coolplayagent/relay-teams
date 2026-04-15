# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.tools.workspace_tools.edit import (
    _project_edit_result,
    apply_edit,
    edit_file_with_guard,
    replace_content,
)
from relay_teams.tools.workspace_tools.edit_state import (
    assert_file_was_read,
    load_file_read_state,
    record_file_read,
)


def test_replace_content_replaces_unique_exact_match() -> None:
    result = replace_content("alpha beta gamma", "beta", "delta")
    assert result == "alpha delta gamma"


def test_replace_content_replace_all_replaces_every_occurrence() -> None:
    result = replace_content("foo bar foo baz foo", "foo", "qux", replace_all=True)
    assert result == "qux bar qux baz qux"


def test_replace_content_supports_indentation_flexible_match() -> None:
    content = "def demo():\n    if True:\n        return 1\n"
    old = "if True:\n    return 1"
    new = "if False:\n    return 2"
    result = replace_content(content, old, new)
    assert "if False:" in result
    assert "return 2" in result


def test_replace_content_rejects_identical_old_and_new() -> None:
    with pytest.raises(ValueError, match="identical"):
        replace_content("alpha", "beta", "beta")


def test_replace_content_rejects_missing_match() -> None:
    with pytest.raises(ValueError, match="Could not find"):
        replace_content("alpha beta", "gamma", "delta")


def test_replace_content_rejects_ambiguous_match_without_replace_all() -> None:
    with pytest.raises(ValueError, match="multiple matches"):
        replace_content("foo foo", "foo", "bar")


def test_apply_edit_creates_new_file_when_old_string_is_empty(tmp_path: Path) -> None:
    file_path = tmp_path / "nested" / "new.txt"
    result = apply_edit(file_path=file_path, old_string="", new_string="hello")
    assert file_path.read_text(encoding="utf-8") == "hello"
    assert result["diff_summary"] == "  + 1: 1 line(s) added"


def test_apply_edit_preserves_lf_when_inputs_use_crlf(tmp_path: Path) -> None:
    file_path = tmp_path / "lf.txt"
    with file_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("alpha\nbeta\ngamma\n")

    apply_edit(
        file_path=file_path,
        old_string="alpha\r\nbeta\r\ngamma",
        new_string="alpha\r\nbeta-updated\r\ngamma",
    )

    with file_path.open("r", encoding="utf-8", newline="") as handle:
        content = handle.read()
    assert content == "alpha\nbeta-updated\ngamma\n"
    assert "\r\n" not in content


def test_apply_edit_preserves_crlf_when_inputs_use_lf(tmp_path: Path) -> None:
    file_path = tmp_path / "crlf.txt"
    with file_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("alpha\r\nbeta\r\ngamma\r\n")

    apply_edit(
        file_path=file_path,
        old_string="alpha\nbeta\ngamma",
        new_string="alpha\nbeta-updated\ngamma",
    )

    with file_path.open("r", encoding="utf-8", newline="") as handle:
        content = handle.read()
    assert content == "alpha\r\nbeta-updated\r\ngamma\r\n"


def test_edit_file_with_guard_requires_read_first(tmp_path: Path) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")

    with pytest.raises(ValueError, match="You must read file"):
        edit_file_with_guard(
            shared_store=shared_store,
            task_id="task-1",
            file_path=file_path,
            old_string="content",
            new_string="updated",
        )


def test_edit_file_with_guard_succeeds_after_read(tmp_path: Path) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    record_file_read(shared_store=shared_store, task_id="task-1", path=file_path)

    result = edit_file_with_guard(
        shared_store=shared_store,
        task_id="task-1",
        file_path=file_path,
        old_string="content",
        new_string="updated",
    )

    assert file_path.read_text(encoding="utf-8") == "updated"
    assert "Edit applied successfully." in result["output"]
    state = load_file_read_state(
        shared_store=shared_store, task_id="task-1", path=file_path
    )
    assert state is not None


def test_edit_file_with_guard_rejects_external_change_after_read(
    tmp_path: Path,
) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    record_file_read(shared_store=shared_store, task_id="task-1", path=file_path)
    file_path.write_text("changed externally", encoding="utf-8")

    with pytest.raises(ValueError, match="modified since it was last read"):
        edit_file_with_guard(
            shared_store=shared_store,
            task_id="task-1",
            file_path=file_path,
            old_string="changed externally",
            new_string="updated",
        )


def test_edit_file_with_guard_allows_new_file_without_prior_read(
    tmp_path: Path,
) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "new.txt"

    edit_file_with_guard(
        shared_store=shared_store,
        task_id="task-1",
        file_path=file_path,
        old_string="",
        new_string="created",
    )

    assert file_path.read_text(encoding="utf-8") == "created"
    assert_file_was_read(shared_store=shared_store, task_id="task-1", path=file_path)


def test_project_edit_result_keeps_only_output_visible() -> None:
    projected = _project_edit_result(
        {
            "path": "demo.txt",
            "output": "Edit applied successfully.",
            "diff": "@@ -1 +1 @@",
            "diff_summary": "  ~ 1: 1 line(s) changed",
        }
    )

    assert projected.visible_data == {"output": "Edit applied successfully."}
    assert projected.internal_data == {
        "path": "demo.txt",
        "output": "Edit applied successfully.",
        "diff": "@@ -1 +1 @@",
        "diff_summary": "  ~ 1: 1 line(s) changed",
    }


def test_apply_edit_rejects_existing_notebook_file(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.ipynb"
    file_path.write_text(
        '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Use notebook_edit"):
        apply_edit(
            file_path=file_path,
            old_string="[]",
            new_string="[{}]",
        )
