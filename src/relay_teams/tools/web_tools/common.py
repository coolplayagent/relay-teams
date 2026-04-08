# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify

from relay_teams.env.web_config_models import WebConfig
from relay_teams.env.web_config_service import WebConfigService
from relay_teams.paths import get_app_config_dir

MAX_TEXT_OUTPUT_CHARS = 32_000
WEBFETCH_SUBDIR = "webfetch"
STRIP_HTML_TAGS = ("script", "style", "noscript", "iframe", "object", "embed")


def load_runtime_web_config() -> WebConfig:
    return WebConfigService(config_dir=get_app_config_dir()).resolve_runtime_config()


def resolve_webfetch_output_dir(workspace_dir: Path) -> Path:
    output_dir = workspace_dir / "tmp" / WEBFETCH_SUBDIR
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def extract_text_from_html(content: str) -> str:
    soup = build_clean_html_soup(content)
    text = soup.get_text("\n", strip=True)
    return text.strip()


def convert_html_to_markdown(content: str, *, base_url: str | None = None) -> str:
    soup = build_clean_html_soup(content, base_url=base_url)
    body = soup.body if soup.body is not None else soup
    rendered = markdownify(
        str(body),
        heading_style="ATX",
        bullets="-",
        strong_em_symbol="*",
    ).strip()
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


def build_clean_html_soup(
    content: str,
    *,
    base_url: str | None = None,
) -> BeautifulSoup:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(STRIP_HTML_TAGS):
        tag.decompose()
    if base_url:
        absolutize_html_urls(soup, base_url=base_url)
    return soup


def absolutize_html_urls(soup: BeautifulSoup, *, base_url: str) -> None:
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if isinstance(href, str) and href.strip():
            anchor["href"] = urljoin(base_url, href)
    for image in soup.find_all("img", src=True):
        src = image.get("src")
        if isinstance(src, str) and src.strip():
            image["src"] = urljoin(base_url, src)


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
