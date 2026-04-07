from __future__ import annotations

from pathlib import Path


def load_tool_description(module_file: str) -> str:
    description_path = Path(module_file).with_suffix(".txt")
    if not description_path.is_file():
        raise FileNotFoundError(f"Tool description file not found: {description_path}")

    description = description_path.read_text(encoding="utf-8").strip()
    if not description:
        raise ValueError(f"Tool description file is empty: {description_path}")
    return description
