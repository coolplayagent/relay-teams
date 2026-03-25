# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys

from agent_teams.skills.installer_support import (
    SkillInstallerError,
    build_listing_payload,
    render_listing_text,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="openai/skills")
    parser.add_argument("--path", default="skills/.curated")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    try:
        payload = build_listing_payload(
            repo=args.repo,
            ref=args.ref,
            path=args.path,
        )
    except SkillInstallerError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(render_listing_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
