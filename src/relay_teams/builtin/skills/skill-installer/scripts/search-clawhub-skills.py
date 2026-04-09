# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys

from relay_teams.skills.clawhub_search_service import search_clawhub_skills


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="+")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    result = search_clawhub_skills(
        query=" ".join(part for part in args.query if part.strip()),
        limit=args.limit,
    )
    if not result.ok:
        print(
            result.error_message or "ClawHub skill search failed.",
            file=sys.stderr,
        )
        return 1

    if args.format == "json":
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
    else:
        print(_render_search_text(result.model_dump(mode="json")))
    return 0


def _render_search_text(payload: dict[str, object]) -> str:
    query = str(payload.get("query") or "").strip()
    lines = [f'ClawHub search results for "{query}":', ""]
    raw_items = payload.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    if not items:
        lines.append("<none>")
    else:
        for item in items:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or "").strip()
            title = str(item.get("title") or "").strip()
            if not slug and not title:
                continue
            lines.append(f"{slug} - {title}".strip(" -"))
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
