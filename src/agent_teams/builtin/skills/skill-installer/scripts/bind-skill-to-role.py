# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sys

from agent_teams.skills.installer_support import (
    SkillInstallerError,
    mount_skills_to_roles,
    render_mount_results_text,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", action="append", default=[])
    parser.add_argument("--role", action="append", default=[])
    args = parser.parse_args()

    try:
        if not args.skill:
            raise SkillInstallerError("Provide at least one --skill value")
        mounted_roles = mount_skills_to_roles(
            role_ids=tuple(args.role),
            skill_names=tuple(args.skill),
        )
    except SkillInstallerError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        render_mount_results_text(
            skill_names=tuple(args.skill),
            role_ids=mounted_roles,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
