from __future__ import annotations

import json

from agent_teams_evals.loaders.swebench_loader import (
    SWEBenchLoader,
    build_swebench_intent,
)


def test_build_swebench_intent_preserves_content_but_normalizes_formatting() -> None:
    intent = build_swebench_intent(
        problem_statement=(
            "<!-- hidden template -->\r\n"
            "### Description\r\n"
            "Keep this section.\r\n"
            "\r\n"
            "\r\n"
            "### System Details\r\n"
            "Windows 11\r\n"
        ),
        hints_text="  try the transform graph first  \n\n",
    )

    assert "<!--" not in intent
    assert intent.startswith("Consider the following PR description:")
    assert "<pr_description>" in intent
    assert "</pr_description>" in intent
    assert "### Description" in intent
    assert "### System Details" in intent
    assert "Windows 11" in intent
    assert "Additional context:" in intent
    assert "try the transform graph first" in intent
    assert "Required task flow:" in intent
    assert "dispatch Explorer" in intent
    assert "dispatch Crafter" in intent
    assert "dispatch Gater" in intent
    assert "FAIL_TO_PASS Tests" not in intent
    assert "PASS_TO_PASS Tests" not in intent
    assert "\r" not in intent
    assert "\n\n\n" not in intent


def test_loader_injects_structured_swebench_intent(tmp_path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text(
        json.dumps(
            {
                "instance_id": "demo-1",
                "repo": "org/repo",
                "base_commit": "abc123",
                "problem_statement": "<!-- note -->\n### Description\nFix it.\n",
                "patch": "diff --git a/a.py b/a.py\n",
                "hints_text": "look at parser.py",
                "FAIL_TO_PASS": ["tests.test_fix"],
                "PASS_TO_PASS": ["tests.test_keep"],
            }
        ),
        encoding="utf-8",
    )

    [item] = SWEBenchLoader().load(dataset_path)

    assert item.item_id == "demo-1"
    assert item.repo_url == "https://github.com/org/repo"
    assert item.intent.startswith("Consider the following PR description:")
    assert "### Description" in item.intent
    assert "<pr_description>" in item.intent
    assert "Additional context:" in item.intent
    assert "look at parser.py" in item.intent
    assert "FAIL_TO_PASS Tests" not in item.intent
    assert "PASS_TO_PASS Tests" not in item.intent
    assert "<!--" not in item.intent
    assert item.fail_to_pass == ("tests.test_fix",)
    assert item.pass_to_pass == ("tests.test_keep",)


def test_loader_extracts_test_patch(tmp_path) -> None:
    tp = "diff --git a/tests/t.py b/tests/t.py\n+new test\n"
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text(
        json.dumps(
            {
                "instance_id": "demo-tp",
                "repo": "org/repo",
                "base_commit": "abc123",
                "problem_statement": "Fix it.",
                "patch": "diff --git a/a.py b/a.py\n",
                "test_patch": tp,
                "FAIL_TO_PASS": ["tests.test_fix"],
                "PASS_TO_PASS": [],
            }
        ),
        encoding="utf-8",
    )

    [item] = SWEBenchLoader().load(dataset_path)

    assert item.test_patch == tp


def test_loader_test_patch_absent_yields_none(tmp_path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text(
        json.dumps(
            {
                "instance_id": "demo-no-tp",
                "repo": "org/repo",
                "base_commit": "abc123",
                "problem_statement": "Fix it.",
                "patch": "diff --git a/a.py b/a.py\n",
                "FAIL_TO_PASS": ["tests.test_fix"],
                "PASS_TO_PASS": [],
            }
        ),
        encoding="utf-8",
    )

    [item] = SWEBenchLoader().load(dataset_path)

    assert item.test_patch is None
