from __future__ import annotations

from agent_teams_evals.models import EvalItem
from agent_teams_evals.workspace.patch_filter import (
    collect_benchmark_test_files,
    filter_patch_for_swebench,
)


def _make_item() -> EvalItem:
    return EvalItem(
        item_id="demo",
        dataset="swebench",
        intent="demo",
        test_patch=(
            "diff --git a/tests/test_feature.py b/tests/test_feature.py\n"
            "--- a/tests/test_feature.py\n"
            "+++ b/tests/test_feature.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
        fail_to_pass=("tests/test_feature.py::test_fix",),
        pass_to_pass=("tests/test_keep.py::test_keep",),
    )


def test_collect_benchmark_test_files_combines_patch_and_test_ids() -> None:
    files = collect_benchmark_test_files(_make_item())

    assert files == ("tests/test_feature.py", "tests/test_keep.py")


def test_filter_patch_for_swebench_filters_only_benchmark_test_files() -> None:
    raw_patch = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/tests/test_feature.py b/tests/test_feature.py\n"
        "--- a/tests/test_feature.py\n"
        "+++ b/tests/test_feature.py\n"
        "@@ -1 +1 @@\n"
        "-old test\n"
        "+new test\n"
    )

    result = filter_patch_for_swebench(_make_item(), raw_patch)

    assert result.raw_patch == raw_patch
    assert "src/app.py" in result.scored_patch
    assert "tests/test_feature.py" not in result.scored_patch
    assert result.filtered_files == ("tests/test_feature.py",)


def test_filter_patch_for_swebench_all_filtered_returns_empty_scored_patch() -> None:
    raw_patch = (
        "diff --git a/tests/test_keep.py b/tests/test_keep.py\n"
        "--- a/tests/test_keep.py\n"
        "+++ b/tests/test_keep.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    result = filter_patch_for_swebench(_make_item(), raw_patch)

    assert result.scored_patch == ""
    assert result.filtered_files == ("tests/test_keep.py",)
