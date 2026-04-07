# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import re
from pathlib import Path
from typing import Generator

from pydantic_ai import Agent

from relay_teams.paths import open_text_file, path_exists, path_is_dir
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool,
)
from relay_teams.tools.workspace_tools.edit_state import (
    assert_file_unchanged_since_read,
    record_file_read,
)
from relay_teams.tools.workspace_tools.write import (
    atomic_write,
    format_diff_summary,
    generate_diff,
)

MAX_DIFF_CHARS = 24_000
SINGLE_CANDIDATE_SIMILARITY_THRESHOLD = 0.0
MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD = 0.3
DESCRIPTION = load_tool_description(__file__)

Replacer = Generator[str, None, None]


def normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n")


def detect_line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def convert_to_line_ending(text: str, ending: str) -> str:
    if ending == "\n":
        return text
    return text.replace("\n", "\r\n")


def trim_diff(diff: str) -> str:
    lines = diff.split("\n")
    content_lines = [
        line
        for line in lines
        if line[:1] in {"+", "-", " "}
        and not line.startswith("---")
        and not line.startswith("+++")
    ]
    if not content_lines:
        return diff

    min_indent: int | None = None
    for line in content_lines:
        content = line[1:]
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip())
        if min_indent is None or indent < min_indent:
            min_indent = indent

    if not min_indent:
        return diff

    trimmed_lines = []
    for line in lines:
        if (
            line[:1] in {"+", "-", " "}
            and not line.startswith("---")
            and not line.startswith("+++")
        ):
            trimmed_lines.append(line[0] + line[1 + min_indent :])
        else:
            trimmed_lines.append(line)
    return "\n".join(trimmed_lines)


def levenshtein(a: str, b: str) -> int:
    if not a or not b:
        return max(len(a), len(b))
    matrix = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        matrix[i][0] = i
    for j in range(len(b) + 1):
        matrix[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            matrix[i][j] = min(
                matrix[i - 1][j] + 1,
                matrix[i][j - 1] + 1,
                matrix[i - 1][j - 1] + cost,
            )
    return matrix[-1][-1]


def simple_replacer(_content: str, find: str) -> Replacer:
    yield find


def line_trimmed_replacer(content: str, find: str) -> Replacer:
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    for i in range(len(original_lines) - len(search_lines) + 1):
        if all(
            original_lines[i + j].strip() == search_lines[j].strip()
            for j in range(len(search_lines))
        ):
            start = sum(len(original_lines[k]) + 1 for k in range(i))
            end = start
            for k in range(len(search_lines)):
                end += len(original_lines[i + k])
                if k < len(search_lines) - 1:
                    end += 1
            yield content[start:end]


def block_anchor_replacer(content: str, find: str) -> Replacer:
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if len(search_lines) < 3:
        return
    if search_lines and search_lines[-1] == "":
        search_lines.pop()

    first = search_lines[0].strip()
    last = search_lines[-1].strip()
    search_size = len(search_lines)
    candidates: list[tuple[int, int]] = []

    for i, line in enumerate(original_lines):
        if line.strip() != first:
            continue
        for j in range(i + 2, len(original_lines)):
            if original_lines[j].strip() == last:
                candidates.append((i, j))
                break
    if not candidates:
        return

    def candidate_similarity(start_line: int, end_line: int) -> float:
        actual_size = end_line - start_line + 1
        lines_to_check = min(search_size - 2, actual_size - 2)
        if lines_to_check <= 0:
            return 1.0
        similarity = 0.0
        for offset in range(1, lines_to_check + 1):
            original = original_lines[start_line + offset].strip()
            search = search_lines[offset].strip()
            max_len = max(len(original), len(search))
            if max_len == 0:
                continue
            similarity += 1 - (levenshtein(original, search) / max_len)
        return similarity / lines_to_check

    if len(candidates) == 1:
        start_line, end_line = candidates[0]
        if (
            candidate_similarity(start_line, end_line)
            >= SINGLE_CANDIDATE_SIMILARITY_THRESHOLD
        ):
            yield slice_block(content, original_lines, start_line, end_line)
        return

    best_match: tuple[int, int] | None = None
    best_similarity = -1.0
    for candidate in candidates:
        similarity = candidate_similarity(candidate[0], candidate[1])
        if similarity > best_similarity:
            best_similarity = similarity
            best_match = candidate
    if best_match and best_similarity >= MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD:
        yield slice_block(content, original_lines, best_match[0], best_match[1])


def whitespace_normalized_replacer(content: str, find: str) -> Replacer:
    def normalize_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    normalized_find = normalize_whitespace(find)
    lines = content.split("\n")

    for line in lines:
        if normalize_whitespace(line) == normalized_find:
            yield line
            continue
        normalized_line = normalize_whitespace(line)
        if normalized_find and normalized_find in normalized_line:
            words = find.strip().split()
            if words:
                pattern = r"\s+".join(re.escape(word) for word in words)
                match = re.search(pattern, line)
                if match:
                    yield match.group(0)

    find_lines = find.split("\n")
    if len(find_lines) > 1:
        for i in range(len(lines) - len(find_lines) + 1):
            block = "\n".join(lines[i : i + len(find_lines)])
            if normalize_whitespace(block) == normalized_find:
                yield block


def indentation_flexible_replacer(content: str, find: str) -> Replacer:
    def remove_indentation(text: str) -> str:
        lines = text.split("\n")
        non_empty = [line for line in lines if line.strip()]
        if not non_empty:
            return text
        min_indent = min(len(line) - len(line.lstrip()) for line in non_empty)
        return "\n".join(
            line if not line.strip() else line[min_indent:] for line in lines
        )

    normalized_find = remove_indentation(find)
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(len(content_lines) - len(find_lines) + 1):
        block = "\n".join(content_lines[i : i + len(find_lines)])
        if remove_indentation(block) == normalized_find:
            yield block


def escape_normalized_replacer(content: str, find: str) -> Replacer:
    def unescape_string(value: str) -> str:
        mapping = {
            "n": "\n",
            "t": "\t",
            "r": "\r",
            "'": "'",
            '"': '"',
            "`": "`",
            "\\": "\\",
            "$": "$",
            "\n": "\n",
        }

        def replace_match(match: re.Match[str]) -> str:
            token = match.group(1)
            return mapping.get(token, match.group(0))

        return re.sub(r"\\(n|t|r|'|\"|`|\\|\n|\$)", replace_match, value)

    unescaped_find = unescape_string(find)
    if unescaped_find in content:
        yield unescaped_find

    lines = content.split("\n")
    find_lines = unescaped_find.split("\n")
    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i : i + len(find_lines)])
        if unescape_string(block) == unescaped_find:
            yield block


def multi_occurrence_replacer(content: str, find: str) -> Replacer:
    start = 0
    while True:
        index = content.find(find, start)
        if index == -1:
            break
        yield find
        start = index + len(find)


def trimmed_boundary_replacer(content: str, find: str) -> Replacer:
    trimmed = find.strip()
    if trimmed == find:
        return
    if trimmed in content:
        yield trimmed

    lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i : i + len(find_lines)])
        if block.strip() == trimmed:
            yield block


def context_aware_replacer(content: str, find: str) -> Replacer:
    find_lines = find.split("\n")
    if len(find_lines) < 3:
        return
    if find_lines and find_lines[-1] == "":
        find_lines.pop()
    content_lines = content.split("\n")
    first = find_lines[0].strip()
    last = find_lines[-1].strip()

    for i, line in enumerate(content_lines):
        if line.strip() != first:
            continue
        for j in range(i + 2, len(content_lines)):
            if content_lines[j].strip() != last:
                continue
            block_lines = content_lines[i : j + 1]
            if len(block_lines) != len(find_lines):
                break
            matching = 0
            total_non_empty = 0
            for k in range(1, len(block_lines) - 1):
                block_line = block_lines[k].strip()
                find_line = find_lines[k].strip()
                if block_line or find_line:
                    total_non_empty += 1
                    if block_line == find_line:
                        matching += 1
            if total_non_empty == 0 or (matching / total_non_empty) >= 0.5:
                yield "\n".join(block_lines)
                break
            break


def slice_block(content: str, lines: list[str], start_line: int, end_line: int) -> str:
    start = sum(len(lines[k]) + 1 for k in range(start_line))
    end = start
    for line_index in range(start_line, end_line + 1):
        end += len(lines[line_index])
        if line_index < end_line:
            end += 1
    return content[start:end]


def replace_content(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    if old_string == new_string:
        raise ValueError(
            "No changes to apply: old_string and new_string are identical."
        )

    not_found = True
    replacers = (
        simple_replacer,
        line_trimmed_replacer,
        block_anchor_replacer,
        whitespace_normalized_replacer,
        indentation_flexible_replacer,
        escape_normalized_replacer,
        trimmed_boundary_replacer,
        context_aware_replacer,
        multi_occurrence_replacer,
    )
    for replacer in replacers:
        for search in replacer(content, old_string):
            index = content.find(search)
            if index == -1:
                continue
            not_found = False
            if replace_all:
                return content.replace(search, new_string)
            if index != content.rfind(search):
                continue
            return content[:index] + new_string + content[index + len(search) :]

    if not_found:
        raise ValueError(
            "Could not find old_string in the file. It must match exactly, including whitespace, indentation, and line endings."
        )
    raise ValueError(
        "Found multiple matches for old_string. Provide more surrounding context to make the match unique."
    )


def read_text_preserve_line_endings(file_path: Path) -> str:
    with open_text_file(file_path, newline="") as handle:
        return handle.read()


def apply_edit(
    *,
    file_path: Path,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict[str, str]:
    if old_string == new_string:
        raise ValueError(
            "No changes to apply: old_string and new_string are identical."
        )

    if old_string == "":
        if path_exists(file_path):
            raise ValueError(
                "Empty old_string is only allowed when creating a new file."
            )
        new_content = new_string
        atomic_write(file_path, new_content, encoding="utf-8", newline="")
        diff = trim_diff(
            generate_diff(str(file_path), "", normalize_line_endings(new_content))
        )
        return {
            "path": str(file_path),
            "output": "Edit applied successfully.\n\nDiff:\n"
            + format_diff_summary("", new_content),
            "diff": diff[:MAX_DIFF_CHARS],
            "diff_summary": format_diff_summary("", new_content),
        }

    if not path_exists(file_path):
        raise ValueError(f"File not found: {file_path}")
    if path_is_dir(file_path):
        raise ValueError(f"Path is a directory: {file_path}")

    old_content = read_text_preserve_line_endings(file_path)
    ending = detect_line_ending(old_content)
    normalized_old = convert_to_line_ending(normalize_line_endings(old_string), ending)
    normalized_new = convert_to_line_ending(normalize_line_endings(new_string), ending)
    new_content = replace_content(
        old_content, normalized_old, normalized_new, replace_all
    )

    atomic_write(file_path, new_content, encoding="utf-8", newline="")
    diff = trim_diff(
        generate_diff(
            str(file_path),
            normalize_line_endings(old_content),
            normalize_line_endings(new_content),
        )
    )
    diff_summary = format_diff_summary(old_content, new_content)
    return {
        "path": str(file_path),
        "output": "Edit applied successfully.\n\nDiff:\n" + diff_summary,
        "diff": diff[:MAX_DIFF_CHARS],
        "diff_summary": diff_summary,
    }


def edit_file_with_guard(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    file_path: Path,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict[str, str]:
    if old_string != "":
        if not path_exists(file_path):
            raise ValueError(f"File not found: {file_path}")
        if path_is_dir(file_path):
            raise ValueError(f"Path is a directory: {file_path}")
    if old_string != "":
        assert_file_unchanged_since_read(
            shared_store=shared_store,
            task_id=task_id,
            path=file_path,
        )
    result = apply_edit(
        file_path=file_path,
        old_string=old_string,
        new_string=new_string,
        replace_all=replace_all,
    )
    record_file_read(shared_store=shared_store, task_id=task_id, path=file_path)
    return result


def _project_edit_result(result: dict[str, str]) -> ToolResultProjection:
    internal_data: dict[str, JsonValue] = {key: value for key, value in result.items()}
    return ToolResultProjection(
        visible_data={
            "output": result["output"],
        },
        internal_data=internal_data,
    )


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def edit(
        ctx: ToolContext,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, JsonValue]:
        async def _action() -> ToolResultProjection:
            file_path = ctx.deps.workspace.resolve_path(path, write=True)
            result = edit_file_with_guard(
                shared_store=ctx.deps.shared_store,
                task_id=ctx.deps.task_id,
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all,
            )
            return _project_edit_result(result)

        return await execute_tool(
            ctx,
            tool_name="edit",
            args_summary={
                "path": path,
                "old_len": len(old_string),
                "new_len": len(new_string),
                "replace_all": replace_all,
            },
            action=_action,
        )
