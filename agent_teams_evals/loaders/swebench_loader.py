from __future__ import annotations

import json
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


class SWEBenchLoader(DatasetLoader):
    def load(self, path: Path) -> list[EvalItem]:
        items: list[EvalItem] = []
        with path.open(encoding="utf-8") as fh:
            for line_num, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)

                instance_id = str(raw.get("instance_id", f"item-{line_num}"))
                repo = str(raw.get("repo", ""))
                repo_url = f"https://github.com/{repo}" if repo else None
                base_commit = raw.get("base_commit")
                problem_statement = str(raw.get("problem_statement", ""))
                reference_patch = raw.get("patch")
                fail_to_pass = _parse_test_list(raw.get("FAIL_TO_PASS", []))
                pass_to_pass = _parse_test_list(raw.get("PASS_TO_PASS", []))

                extra_fields: dict[str, str] = {
                    k: str(v) for k, v in raw.items() if k not in _SWEBENCH_FIELDS
                }

                item = EvalItem(
                    item_id=instance_id,
                    dataset="swebench",
                    intent=problem_statement,
                    repo_url=repo_url,
                    base_commit=base_commit,
                    reference_patch=reference_patch,
                    fail_to_pass=fail_to_pass,
                    pass_to_pass=pass_to_pass,
                    extra_fields=extra_fields,
                )
                items.append(item)
        return items
