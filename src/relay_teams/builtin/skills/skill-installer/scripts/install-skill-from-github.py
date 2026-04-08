# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sys

from relay_teams.skills.installer_support import (
    InstallMethod,
    SkillInstallerError,
    install_from_repo_paths,
    install_from_url,
    render_install_results_text,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--url")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--dest")
    parser.add_argument("--name")
    parser.add_argument(
        "--method",
        choices=tuple(method.value for method in InstallMethod),
        default=InstallMethod.AUTO.value,
    )
    args = parser.parse_args()

    try:
        if args.url:
            if args.repo or args.path:
                raise SkillInstallerError("--url cannot be combined with --repo/--path")
            results = install_from_url(
                url=args.url,
                dest_root=args.dest,
                name=args.name,
                method=InstallMethod(args.method),
            )
        else:
            if not args.repo or not args.path:
                raise SkillInstallerError(
                    "Provide either --url or both --repo and at least one --path"
                )
            results = install_from_repo_paths(
                repo=args.repo,
                ref=args.ref,
                paths=tuple(args.path),
                dest_root=args.dest,
                name=args.name,
                method=InstallMethod(args.method),
            )
    except SkillInstallerError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(render_install_results_text(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
