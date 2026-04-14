# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import subprocess

from relay_teams.env.clawhub_cli import resolve_existing_clawhub_path
from relay_teams.env.clawhub_env import build_clawhub_subprocess_env

_SEARCH_LINE_RE = re.compile(
    r"^(?P<slug>\S+)(?:\s+(?P<version>v?\d[^\s]*))?\s{2,}"
    r"(?P<title>.+?)\s+\((?P<score>-?\d+(?:\.\d+)?)\)\s*$"
)


def run_clawhub_search(*, query: str, limit: int) -> dict[str, object]:
    normalized_query = " ".join(part for part in query.split() if part.strip())
    if not normalized_query:
        return {
            "ok": False,
            "query": "",
            "items": [],
            "error_message": "ClawHub search query must not be empty.",
        }
    command = _build_command(normalized_query, limit)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=build_clawhub_subprocess_env(None, base_env=os.environ),
            check=False,
        )
    except OSError as exc:
        return {
            "ok": False,
            "query": normalized_query,
            "items": [],
            "error_message": str(exc) or "ClawHub CLI is not available on PATH.",
        }

    if completed.returncode != 0:
        return {
            "ok": False,
            "query": normalized_query,
            "items": [],
            "error_message": _first_meaningful_line(
                completed.stderr,
                completed.stdout,
            )
            or "ClawHub skill search failed.",
        }

    try:
        items = _parse_search_output(completed.stdout)
    except ValueError as exc:
        return {
            "ok": False,
            "query": normalized_query,
            "items": [],
            "error_message": str(exc),
        }
    return {"ok": True, "query": normalized_query, "items": items}


def _build_command(query: str, limit: int) -> list[str]:
    clawhub_path = resolve_existing_clawhub_path()
    executable = "clawhub" if clawhub_path is None else str(clawhub_path)
    return [executable, "search", query, "--limit", str(limit)]


def _parse_search_output(raw_output: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    saw_unparseable_result_line = False
    for raw_line in raw_output.splitlines():
        normalized_line = raw_line.strip()
        if not normalized_line or normalized_line.startswith("- Searching"):
            continue
        parsed = _parse_search_line(normalized_line)
        if parsed is None:
            saw_unparseable_result_line = True
            continue
        items.append(parsed)
    if items:
        return items
    if saw_unparseable_result_line:
        raise ValueError("ClawHub search returned an unexpected output format.")
    return []


def _parse_search_line(raw_line: str) -> dict[str, object] | None:
    match = _SEARCH_LINE_RE.match(raw_line)
    if match is None:
        return None
    score_text = match.group("score")
    score = float(score_text) if score_text else None
    version = match.group("version")
    return {
        "slug": match.group("slug"),
        "title": match.group("title"),
        "version": version,
        "score": score,
    }


def _first_meaningful_line(*chunks: str) -> str | None:
    for chunk in chunks:
        for line in chunk.splitlines():
            normalized_line = line.strip()
            if normalized_line:
                return normalized_line
    return None
