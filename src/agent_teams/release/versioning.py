# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_BASE_VERSION = "0.0.3"
DEFAULT_VERSION_FILE = Path("src/agent_teams/_version.py")


def build_timestamp_version(
    timestamp: str, base_version: str = DEFAULT_BASE_VERSION
) -> str:
    normalized_timestamp = timestamp.strip()
    if not normalized_timestamp.isdigit():
        raise ValueError("Timestamp must contain digits only.")
    return f"{base_version}.{normalized_timestamp}"


def generate_timestamp() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")


def render_version_file(version: str) -> str:
    return "\n".join(
        [
            "# -*- coding: utf-8 -*-",
            "from __future__ import annotations",
            "",
            '__all__ = ["__version__"]',
            "",
            f'__version__ = "{version}"',
            "",
        ]
    )


def write_version_file(version: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_version_file(version), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the package version file used by release builds."
    )
    parser.add_argument(
        "--timestamp",
        help="Timestamp digits to append to the base version. Defaults to current UTC time.",
    )
    parser.add_argument(
        "--base-version",
        default=DEFAULT_BASE_VERSION,
        help="Base version prefix to use before the timestamp.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_VERSION_FILE,
        help="Version module path to overwrite.",
    )
    args = parser.parse_args()

    timestamp = args.timestamp or generate_timestamp()
    version = build_timestamp_version(
        timestamp=timestamp, base_version=args.base_version
    )
    write_version_file(version=version, output_path=args.output)
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
