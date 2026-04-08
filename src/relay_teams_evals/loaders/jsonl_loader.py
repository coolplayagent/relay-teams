from __future__ import annotations

import json
from pathlib import Path

from relay_teams_evals.loaders.base import DatasetLoader
from relay_teams_evals.models import EvalItem

_KNOWN_FIELDS = frozenset(
    {
        "item_id",
        "dataset",
        "intent",
        "expected_keywords",
        "expected_patterns",
        "reference_patch",
        "fail_to_pass",
        "pass_to_pass",
        "repo_url",
        "base_commit",
    }
)


class JsonlLoader(DatasetLoader):
    def __init__(self, dataset_name: str = "jsonl") -> None:
        self._dataset_name = dataset_name

    def load(self, path: Path) -> list[EvalItem]:
        items: list[EvalItem] = []
        with path.open(encoding="utf-8") as fh:
            for line_num, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                if "intent" not in raw:
                    raise ValueError(
                        f"Line {line_num}: required field 'intent' is missing"
                    )
                extra_fields: dict[str, str] = {
                    k: str(v) for k, v in raw.items() if k not in _KNOWN_FIELDS
                }
                item = EvalItem(
                    item_id=str(raw.get("item_id", f"item-{line_num}")),
                    dataset=str(raw.get("dataset", self._dataset_name)),
                    intent=str(raw["intent"]),
                    expected_keywords=tuple(raw.get("expected_keywords", [])),
                    expected_patterns=tuple(raw.get("expected_patterns", [])),
                    reference_patch=raw.get("reference_patch"),
                    fail_to_pass=tuple(raw.get("fail_to_pass", [])),
                    pass_to_pass=tuple(raw.get("pass_to_pass", [])),
                    repo_url=raw.get("repo_url"),
                    base_commit=raw.get("base_commit"),
                    extra_fields=extra_fields,
                )
                items.append(item)
        return items
