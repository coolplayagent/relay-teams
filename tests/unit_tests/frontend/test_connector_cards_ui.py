from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_runtime_tools_render_as_separate_group_and_modal_only_list() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "connectors"
        / "connectorCards.js"
    )

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.document = { "
                "getElementById() { return null; }, "
                "querySelector() { return null; }, "
                "querySelectorAll() { return []; }, "
                "body: null "
                "}; "
                f"const mod = await import({module_path.as_uri()!r}); "
                "const connectorsResponse = { "
                "summary: { connected: 0, needs_config: 2, disabled: 0, error: 0, total: 2 }, "
                "items: ["
                "{ connector_id: 'github', provider: 'github', status: 'needs_config', account_count: 0, capabilities: [] },"
                "{ connector_id: 'feishu', provider: 'feishu', status: 'needs_config', account_count: 0, capabilities: [] }"
                "]}; "
                "const runtimeToolsResponse = { items: [{ tool_id: 'rg', display_name: 'ripgrep', status: 'missing' }] }; "
                "const pageHtml = mod.renderConnectorsCardPageMarkup({ connectorsResponse, runtimeToolsResponse }); "
                "const modalHtml = mod.renderRuntimeToolsModalMarkup({ runtimeToolsResponse }); "
                "console.log(JSON.stringify({ pageHtml, modalHtml }));"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout.strip())
    page_html = str(payload["pageHtml"])
    modal_html = str(payload["modalHtml"])

    assert 'data-connector-card="feishu"' in page_html
    assert "data-runtime-tools-card" in page_html
    assert page_html.index('data-connector-card="feishu"') < page_html.index(
        "data-runtime-tools-card"
    )
    assert 'data-runtime-tool="rg"' not in page_html
    assert 'data-runtime-tool="rg"' in modal_html


def test_runtime_tools_card_renders_before_items_load() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "connectors"
        / "connectorCards.js"
    )

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.document = { "
                "getElementById() { return null; }, "
                "querySelector() { return null; }, "
                "querySelectorAll() { return []; }, "
                "body: null "
                "}; "
                f"const mod = await import({module_path.as_uri()!r}); "
                "const connectorsResponse = { summary: {}, items: [] }; "
                "const pageHtml = mod.renderConnectorsCardPageMarkup({ "
                "connectorsResponse, runtimeToolsResponse: null "
                "}); "
                "console.log(JSON.stringify({ pageHtml }));"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    page_html = str(json.loads(completed.stdout.strip())["pageHtml"])

    assert "data-runtime-tools-card" in page_html
    assert 'data-runtime-tool="' not in page_html
