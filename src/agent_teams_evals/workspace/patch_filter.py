from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from agent_teams_evals.models import EvalItem

_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")


class FilteredPatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_patch: str = ""
    scored_patch: str = ""
    filtered_files: tuple[str, ...] = Field(default_factory=tuple)


def collect_benchmark_test_files(item: EvalItem) -> tuple[str, ...]:
    files: set[str] = set()
    files.update(_collect_patch_files(item.test_patch or ""))
    files.update(_collect_test_case_files(item.fail_to_pass))
    files.update(_collect_test_case_files(item.pass_to_pass))
    return tuple(sorted(files))


def filter_patch_for_swebench(
    item: EvalItem,
    raw_patch: str,
) -> FilteredPatchResult:
    benchmark_test_files = set(collect_benchmark_test_files(item))
    if not raw_patch or not benchmark_test_files:
        return FilteredPatchResult(
            raw_patch=raw_patch,
            scored_patch=raw_patch,
        )

    scored_blocks: list[str] = []
    filtered_files: list[str] = []
    for block in _split_patch_blocks(raw_patch):
        block_path = _block_path(block)
        if block_path in benchmark_test_files:
            filtered_files.append(block_path)
            continue
        scored_blocks.append(block)

    return FilteredPatchResult(
        raw_patch=raw_patch,
        scored_patch="".join(scored_blocks),
        filtered_files=tuple(sorted(set(filtered_files))),
    )


def _collect_patch_files(patch: str) -> set[str]:
    files: set[str] = set()
    for block in _split_patch_blocks(patch):
        block_path = _block_path(block)
        if block_path:
            files.add(block_path)
    return files


def _collect_test_case_files(test_ids: tuple[str, ...]) -> set[str]:
    files: set[str] = set()
    for test_id in test_ids:
        file_path, _, _ = test_id.partition("::")
        normalized = _normalize_path(file_path)
        if normalized:
            files.add(normalized)
    return files


def _split_patch_blocks(patch: str) -> list[str]:
    if not patch:
        return []

    blocks: list[str] = []
    current: list[str] = []
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current:
                blocks.append("".join(current))
            current = [line]
            continue
        if current:
            current.append(line)
    if current:
        blocks.append("".join(current))
    if blocks:
        return blocks
    return [patch]


def _block_path(block: str) -> str:
    first_line = block.splitlines()[0] if block else ""
    match = _DIFF_HEADER_RE.match(first_line)
    if not match:
        return ""
    old_path = _normalize_path(match.group(1))
    new_path = _normalize_path(match.group(2))
    return new_path or old_path


def _normalize_path(path: str) -> str:
    stripped = path.strip()
    if not stripped or stripped == "/dev/null":
        return ""
    return stripped.replace("\\", "/")
