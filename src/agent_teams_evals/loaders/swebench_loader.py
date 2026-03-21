from __future__ import annotations

import json
import re
from pathlib import Path

from agent_teams_evals.loaders.base import DatasetLoader
from agent_teams_evals.models import EvalItem

_SWEBENCH_FIELDS = frozenset(
    {
        "instance_id",
        "repo",
        "base_commit",
        "problem_statement",
        "patch",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
        "environment_setup_commit",
        "hints_text",
        "created_at",
        "version",
        "test_patch",
    }
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _parse_test_list(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return tuple(str(v) for v in parsed)
        except json.JSONDecodeError:
            pass
        return (value,) if value else ()
    return ()


def _iter_objects(text: str) -> list[dict[str, object]]:
    """Parse one or more JSON objects from text, supporting both strict JSONL
    (one compact object per line) and pretty-printed multi-line objects."""
    decoder = json.JSONDecoder()
    pos = 0
    objs: list[dict[str, object]] = []
    while pos < len(text):
        stripped = text[pos:].lstrip()
        if not stripped:
            break
        skip = len(text[pos:]) - len(stripped)
        obj, end = decoder.raw_decode(stripped)
        if isinstance(obj, dict):
            objs.append(obj)
        pos += skip + end
    return objs


def _normalize_text_block(text: str) -> str:
    cleaned = _HTML_COMMENT_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in cleaned.split("\n")]
    compacted: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip():
            blank_run = 0
            compacted.append(line)
            continue
        blank_run += 1
        if blank_run <= 1:
            compacted.append("")
    return "\n".join(compacted).strip()


def build_swebench_intent(
    *,
    problem_statement: str,
    hints_text: str | None,
) -> str:
    pr_description = _normalize_text_block(problem_statement)
    normalized_hints = _normalize_text_block(str(hints_text or ""))

    hints_block = ""
    if normalized_hints:
        hints_block = f"<hints_text>\n{normalized_hints}\n</hints_text>\n\n"

    return (
        "Consider the following PR description:\n\n"
        "<pr_description>\n"
        f"{pr_description}\n"
        "</pr_description>\n\n"
        f"{hints_block}"
        "Help ensure that the requirements in <pr_description> are satisfied "
        "with the minimal necessary changes.\n\n"
        "All test-file changes described by the PR have already been handled. "
        "Do not modify any test files or testing logic.\n\n"
        "Only make the minimal non-test changes needed in the current directory "
        "to satisfy the <pr_description>.\n\n"
    )


class SWEBenchLoader(DatasetLoader):
    def load(self, path: Path) -> list[EvalItem]:
        items: list[EvalItem] = []
        text = path.read_text(encoding="utf-8")
        for line_num, raw in enumerate(_iter_objects(text), start=1):
            instance_id = str(raw.get("instance_id", f"item-{line_num}"))
            repo = str(raw.get("repo", ""))
            repo_url = f"https://github.com/{repo}" if repo else None
            base_commit_raw = raw.get("base_commit")
            base_commit = str(base_commit_raw) if base_commit_raw is not None else None
            problem_statement = str(raw.get("problem_statement", ""))
            reference_patch_raw = raw.get("patch")
            reference_patch = (
                str(reference_patch_raw) if reference_patch_raw is not None else None
            )
            test_patch_raw = raw.get("test_patch")
            test_patch = str(test_patch_raw) if test_patch_raw is not None else None
            fail_to_pass = _parse_test_list(raw.get("FAIL_TO_PASS", []))
            pass_to_pass = _parse_test_list(raw.get("PASS_TO_PASS", []))
            hints_text_raw = raw.get("hints_text")
            hints_text = (
                str(hints_text_raw).strip() if hints_text_raw is not None else ""
            )

            extra_fields: dict[str, str] = {
                k: str(v) for k, v in raw.items() if k not in _SWEBENCH_FIELDS
            }

            swebench_instance = {k: str(v) for k, v in raw.items()}

            item = EvalItem(
                item_id=instance_id,
                dataset="swebench",
                intent=build_swebench_intent(
                    problem_statement=problem_statement,
                    hints_text=hints_text,
                ),
                repo_url=repo_url,
                base_commit=base_commit,
                reference_patch=reference_patch,
                test_patch=test_patch,
                fail_to_pass=fail_to_pass,
                pass_to_pass=pass_to_pass,
                extra_fields=extra_fields,
                swebench_instance=swebench_instance,
            )
            items.append(item)
        return items
