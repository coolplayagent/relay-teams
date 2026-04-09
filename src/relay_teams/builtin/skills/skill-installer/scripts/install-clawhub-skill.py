# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys

from relay_teams.skills.clawhub_install_service import install_clawhub_skill


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--version")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    result = install_clawhub_skill(
        slug=args.slug,
        version=args.version,
        force=args.force,
    )
    if not result.ok:
        print(
            result.error_message or "ClawHub skill install failed.",
            file=sys.stderr,
        )
        return 1

    if args.format == "json":
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
    else:
        print(_render_install_text(result.model_dump(mode="json")))
    return 0


def _render_install_text(payload: dict[str, object]) -> str:
    slug = str(payload.get("slug") or "").strip()
    lines = [f"Installed ClawHub skill {slug}."]
    installed_skill = payload.get("installed_skill")
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
