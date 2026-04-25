"""Helpers for loading frontend CSS in runtime import order."""

from __future__ import annotations

import re
from pathlib import Path


def _parse_style_imports(style_path: Path) -> list[str]:
    """Return component filenames in the order imported by style.css."""
    text = style_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'@import\s+url\(["\']?\.?/css/components/([^"\'\)]+)["\']?\)\s*;'
    )
    return pattern.findall(text)


def load_components_css(repo_root: Path | None = None) -> str:
    """Return all component CSS concatenated in runtime import order."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    style_path = repo_root / "frontend" / "dist" / "style.css"
    components_dir = repo_root / "frontend" / "dist" / "css" / "components"
    import_order = _parse_style_imports(style_path)
    parts: list[str] = []
    for filename in import_order:
        css_file = components_dir / filename
        if css_file.exists():
            parts.append(_read_css_with_nested_imports(css_file))
    return "\n".join(parts)


def _read_css_with_nested_imports(css_file: Path) -> str:
    text = css_file.read_text(encoding="utf-8")
    nested_pattern = re.compile(r'@import\s+url\(["\']?\.?/([^"\'\)]+)["\']?\)\s*;')
    parts: list[str] = []
    last_index = 0
    for match in nested_pattern.finditer(text):
        parts.append(text[last_index : match.start()])
        nested_path = (css_file.parent / match.group(1)).resolve()
        if nested_path.exists() and nested_path.is_file():
            parts.append(_read_css_with_nested_imports(nested_path))
        last_index = match.end()
    parts.append(text[last_index:])
    return "\n".join(parts)
