# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_markdown_fallback_renders_without_marked_or_highlight(
    tmp_path: Path,
) -> None:
    payload = _run_markdown_script(
        tmp_path=tmp_path,
        runner_source="""
import { renderMarkdownToHtml } from "./markdown.mjs";

const source = [
    "# Release Notes",
    "",
    "- Added offline markdown rendering",
    "- Removed CDN hard dependency",
    "",
    "> Works without external scripts.",
    "",
    "| Name | Status |",
    "| --- | --- |",
    "| markdown | local |",
    "",
    "```python",
    "print(\\"ok\\")",
    "```",
    "",
    "Open the [docs](/docs).",
].join("\\n");

const html = renderMarkdownToHtml(source);

console.log(JSON.stringify({ html }));
""".strip(),
    )

    html = payload["html"]
    assert "<h1>Release Notes</h1>" in html
    assert "<ul><li>Added offline markdown rendering</li>" in html
    assert "<blockquote><p>Works without external scripts.</p></blockquote>" in html
    assert "<table><thead><tr><th>Name</th><th>Status</th></tr></thead>" in html
    assert '<code class="language-python">print(&quot;ok&quot;)\n</code>' in html
    assert '<a href="/docs" target="_blank" rel="noreferrer">docs</a>' in html


def test_frontend_index_avoids_external_markdown_and_font_cdns() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    index_html = (repo_root / "frontend" / "dist" / "index.html").read_text(
        encoding="utf-8"
    )

    assert "fonts.googleapis.com" not in index_html
    assert "cdn.jsdelivr.net/npm/marked" not in index_html
    assert "cdnjs.cloudflare.com/ajax/libs/highlight.js" not in index_html


def _run_markdown_script(tmp_path: Path, runner_source: str) -> dict[str, str]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "utils" / "markdown.js"

    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    module_under_test_path = tmp_path / "markdown.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_feedback_path.write_text(
        """
export function showToast() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
const translations = {
    "composer.thinking": "Thinking",
    "thinking.live": "Live",
    "markdown.copy": "Copy",
    "markdown.copied": "Copied",
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("./feedback.js", "./mockFeedback.mjs")
        .replace("./i18n.js", "./mockI18n.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    runner_path.write_text(runner_source, encoding="utf-8")

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)
