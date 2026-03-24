# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from agent_teams.env.web_config_models import WebConfig
from agent_teams.env.web_config_service import WebConfigService
from agent_teams.paths import get_app_config_dir

MAX_TEXT_OUTPUT_CHARS = 32_000
WEBFETCH_SUBDIR = "webfetch"


def load_runtime_web_config() -> WebConfig:
    return WebConfigService(config_dir=get_app_config_dir()).resolve_runtime_config()


def resolve_webfetch_output_dir(workspace_dir: Path) -> Path:
    output_dir = workspace_dir / "tmp" / WEBFETCH_SUBDIR
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def extract_text_from_html(content: str) -> str:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "object", "embed"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return text.strip()


def convert_html_to_markdown(content: str) -> str:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "object", "embed"]):
        tag.decompose()
    body = soup.body if soup.body is not None else soup
    rendered = _render_markdown_node(body).strip()
    lines = [line.rstrip() for line in rendered.splitlines()]
    deduped_lines: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        deduped_lines.append(line)
        previous_blank = is_blank
    return "\n".join(deduped_lines).strip()


def sanitize_file_extension(url: str, content_type: str) -> str:
    path = Path(urlparse(url).path)
    suffix = path.suffix.strip().lower()
    if suffix:
        if suffix.startswith("."):
            return suffix
        return f".{suffix}"
    if content_type.startswith("image/"):
        image_suffix = content_type.split("/", 1)[1].split(";", 1)[0].strip().lower()
        if image_suffix == "jpeg":
            return ".jpg"
        if image_suffix:
            return f".{image_suffix}"
    if content_type == "application/pdf":
        return ".pdf"
    if content_type.startswith("text/"):
        return ".txt"
    return ".bin"


def _render_markdown_node(node: Tag | NavigableString) -> str:
    if isinstance(node, NavigableString):
        return str(node)

    if node.name == "br":
        return "\n"
    if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(node.name[1])
        return f"{'#' * level} {node.get_text(' ', strip=True)}\n\n"
    if node.name == "a":
        text = node.get_text(" ", strip=True)
        href_value = node.get("href")
        href = href_value if isinstance(href_value, str) else None
        if href:
            return f"[{text or href}]({href})"
        return text
    if node.name == "img":
        alt = _string_attr(node.get("alt")) or "image"
        src = _string_attr(node.get("src")) or ""
        return f"![{alt}]({src})" if src else alt
    if node.name == "code" and node.parent is not None and node.parent.name == "pre":
        return node.get_text("", strip=False)
    if node.name == "pre":
        code_text = node.get_text("", strip=False).strip("\n")
        return f"```\n{code_text}\n```\n\n"
    if node.name == "li":
        return f"- {node.get_text(' ', strip=True)}\n"

    rendered_children = "".join(
        _render_markdown_node(child)
        for child in node.children
        if isinstance(child, (Tag, NavigableString))
    )
    if node.name in {"p", "div", "section", "article", "header", "footer"}:
        return f"{rendered_children.strip()}\n\n"
    if node.name in {"ul", "ol"}:
        return f"{rendered_children}\n"
    return rendered_children


def _string_attr(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None
