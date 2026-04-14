# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys

from relay_teams.skills.clawhub_install_service import install_clawhub_skill
from relay_teams.skills.clawhub_search_support import run_clawhub_search


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--search-limit", type=int, default=10)
    parser.add_argument("--version")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    search_result = run_clawhub_search(
        query=args.query,
        limit=args.search_limit,
    )
    if not bool(search_result.get("ok")):
        print(
            str(search_result.get("error_message") or "ClawHub skill search failed."),
            file=sys.stderr,
        )
        return 1

    install_result = install_clawhub_skill(
        slug=args.slug,
        version=args.version,
        force=args.force,
    )
    if not install_result.ok:
        print(
            install_result.error_message or "ClawHub skill install failed.",
            file=sys.stderr,
        )
        return 1

    payload: dict[str, object] = {
        "search": search_result,
        "install": install_result.model_dump(mode="json"),
    }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(_render_text(payload))
    return 0


def _render_text(payload: dict[str, object]) -> str:
    search_payload = payload.get("search")
    install_payload = payload.get("install")
    lines: list[str] = []
    if isinstance(search_payload, dict):
        query = str(search_payload.get("query") or "").strip()
        lines.append(f'ClawHub search results for "{query}":')
        lines.append("")
        raw_items = search_payload.get("items")
        items = raw_items if isinstance(raw_items, list) else []
        if not items:
            lines.append("<none>")
        else:
            for item in items:
                if not isinstance(item, dict):
                    continue
                slug = str(item.get("slug") or "").strip()
                title = str(item.get("title") or "").strip()
                if slug or title:
                    lines.append(f"{slug} - {title}".strip(" -"))
        lines.append("")
    if isinstance(install_payload, dict):
        slug = str(install_payload.get("slug") or "").strip()
        lines.append(f"Installed ClawHub skill {slug}.")
        installed_skill = install_payload.get("installed_skill")
        if isinstance(installed_skill, dict):
            directory = str(installed_skill.get("directory") or "").strip()
            runtime_name = str(installed_skill.get("runtime_name") or "").strip()
            runtime_ref = str(installed_skill.get("ref") or "").strip()
            if directory:
                lines.append(f"Directory: {directory}")
            if runtime_name:
                lines.append(f"Runtime name: {runtime_name}")
            if runtime_ref:
                lines.append(f"Runtime ref: {runtime_ref}")
        lines.append("Restart Agent Teams to pick up new skills.")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
