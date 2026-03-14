# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_workspace_shell_hides_execution_mode_selector() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    index_html = (repo_root / "frontend" / "dist" / "index.html").read_text(
        encoding="utf-8"
    )
    prompt_script = (
        repo_root / "frontend" / "dist" / "js" / "app" / "prompt.js"
    ).read_text(encoding="utf-8")
    timeline_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "timeline.js"
    ).read_text(encoding="utf-8")
    navigator_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "rounds"
        / "navigator.js"
    ).read_text(encoding="utf-8")
    model_profiles_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "modelProfiles.js"
    ).read_text(encoding="utf-8")
    system_status_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "systemStatus.js"
    ).read_text(encoding="utf-8")
    sidebar_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sidebar.js"
    ).read_text(encoding="utf-8")
    feedback_script = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "feedback.js"
    ).read_text(encoding="utf-8")
    navbar_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "navbar.js"
    ).read_text(encoding="utf-8")
    bootstrap_script = (
        repo_root / "frontend" / "dist" / "js" / "app" / "bootstrap.js"
    ).read_text(encoding="utf-8")
    state_script = (
        repo_root / "frontend" / "dist" / "js" / "core" / "state.js"
    ).read_text(encoding="utf-8")
    request_script = (
        repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    ).read_text(encoding="utf-8")
    backend_status_script = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "backendStatus.js"
    ).read_text(encoding="utf-8")
    markdown_script = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "markdown.js"
    ).read_text(encoding="utf-8")
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")

    assert "execution-mode-select" not in index_html
    assert "Execution mode" not in index_html
    assert "AI orchestration" not in index_html
    assert "Manual" not in index_html
    assert "execution-mode-select" not in prompt_script
    assert "No session selected" not in index_html
    assert "Start a session from the left sidebar" not in index_html
    assert 'id="new-project-btn"' in index_html
    assert 'id="project-sort-btn"' in index_html
    assert 'id="projects-list"' in index_html
    assert "<h2>agent-teams</h2>" in index_html
    assert "<h2>Workspace</h2>" in index_html
    assert 'id="backend-status"' in index_html
    assert 'id="backend-status-label"' in index_html
    assert "Checking backend..." in index_html
    assert "No session selected" not in timeline_script
    assert "Start a session from the left sidebar" not in timeline_script
    assert "Sessions</p>" not in index_html
    assert "Coordinator output and run history" not in index_html
    assert "Intent:" not in timeline_script
    assert "round-detail-token-host" in timeline_script
    assert "round-detail-meta" in timeline_script
    assert "pendingScrollTargetRunId" in timeline_script
    assert "syncPendingRoundSelection" in timeline_script
    assert "clearPendingRoundSelection" in timeline_script
    assert "emphasizeRoundSection" in timeline_script
    assert "round-nav-toggle" in navigator_script
    assert "ROUND_NAV_COLLAPSED_KEY" in navigator_script
    assert "item.title = String(round.intent || 'No intent');" in navigator_script
    assert "alert(" not in model_profiles_script
    assert "confirm(" not in model_profiles_script
    assert "alert(" not in system_status_script
    assert "confirm(" not in sidebar_script
    assert "showToast" in feedback_script
    assert "showConfirmDialog" in feedback_script
    assert "requestAnimationFrame" in navbar_script
    assert "initBackendStatusMonitor" in bootstrap_script
    assert "is-resizing-rails" in navbar_script
    assert "marked.setOptions" not in state_script
    assert "fetch('/api/system/health'" in backend_status_script
    assert "markBackendOnline" in request_script
    assert "markBackendOffline" in request_script
    assert ".status-indicator > span:last-child" in components_css
    assert "flex: 1 1 auto;" in components_css
    assert "white-space: nowrap;" in components_css
    assert ".status-indicator.online span {" not in components_css
    assert ".status-indicator.offline > span:first-child" in components_css
    assert ".status-indicator.checking > span:first-child" in components_css
    assert (
        ".session-round-section.round-section-emphasis .round-detail-header"
        in components_css
    )
    assert "@keyframes roundSectionEmphasis" in components_css
    assert "margin-left: -0.35rem;" in components_css
    assert "margin-right: -0.35rem;" in components_css
    assert "export function parseMarkdown" in markdown_script
    assert "markdown-table-wrap" in markdown_script
    assert "markdown-code-block" in markdown_script
    assert "formatCodeLanguage" in markdown_script
    assert "markdown-code-copy" in markdown_script
    assert "navigator.clipboard.writeText" in markdown_script
    assert "Code Copied" in markdown_script
    assert ".msg-content blockquote," in components_css
    assert ".markdown-table-wrap {" in components_css
    assert ".markdown-code-block {" in components_css
    assert ".markdown-code-header {" in components_css
    assert ".markdown-code-copy {" in components_css
    assert ".markdown-code-copy.is-copied {" in components_css
    assert ".msg-content table," in components_css
    assert ".msg-content :is(h1, h2, h3, h4)," in components_css


def test_button_theme_tokens_are_distinct_between_dark_and_light_modes() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    base_css = (repo_root / "frontend" / "dist" / "css" / "base.css").read_text(
        encoding="utf-8"
    )
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")

    assert "--button-primary-bg: #494641;" in base_css
    assert "--button-primary-bg: #4d6259;" in base_css
    assert "--button-secondary-bg: #232629;" in base_css
    assert "--button-secondary-bg: #ffffff;" in base_css
    assert "--settings-shell-bg: #ffffff;" in base_css
    assert "background: var(--button-primary-bg);" in components_css
    assert "background: var(--button-secondary-bg);" in components_css


def test_light_theme_workspace_avoids_legacy_beige_overrides() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")
    layout_css = (repo_root / "frontend" / "dist" / "css" / "layout.css").read_text(
        encoding="utf-8"
    )

    assert "background: #ece4d8;" not in components_css
    assert "background: #e8dfd1;" not in components_css
    assert "background: #efe7db;" not in components_css
    assert "background: #fbf7f0;" not in components_css
    assert "background: #fbf7f0;" not in layout_css
    assert "color-mix(in srgb, var(--primary) 8%, transparent)" in components_css
    assert "background: var(--bg-surface-muted);" in components_css
    assert "background: var(--bg-surface);" in layout_css


def test_light_theme_workspace_uses_shared_surface_hierarchy() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")
    layout_css = (repo_root / "frontend" / "dist" / "css" / "layout.css").read_text(
        encoding="utf-8"
    )

    assert "body.light-theme .agent-panel-scroll," in layout_css
    assert "background: var(--bg-surface);" in layout_css
    assert "body.light-theme .sidebar," in layout_css
    assert "box-shadow: none;" in layout_css
    assert (
        "background: color-mix(in srgb, var(--primary) 6%, transparent);"
        in components_css
    )
    assert (
        "border-color: color-mix(in srgb, var(--primary) 22%, var(--border-color) 78%);"
        in components_css
    )
    assert "body.light-theme .round-state-pill," in components_css
    assert "background: transparent;" in components_css
    assert "body.light-theme .tool-block," in components_css
    assert "background: var(--bg-tool-block);" in components_css
    assert "background: var(--bg-surface-glass);" in layout_css
    assert "--bg-surface-glass: #f3f4f4;" in (
        repo_root / "frontend" / "dist" / "css" / "base.css"
    ).read_text(encoding="utf-8")
    assert "body.light-theme .sessions-list," in layout_css
    assert "body.light-theme .session-item {" in components_css
    assert "background: var(--bg-surface-muted);" in components_css


def test_side_rails_use_transition_based_collapse_rules() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    layout_css = (repo_root / "frontend" / "dist" / "css" / "layout.css").read_text(
        encoding="utf-8"
    )

    assert ".sidebar > * {" in layout_css
    assert ".sidebar.collapsed > * {" in layout_css
    assert ".right-rail > * {" in layout_css
    assert ".right-rail.collapsed > * {" in layout_css
    assert ".right-rail-resizer.hidden {" in layout_css
    assert "body.is-resizing-rails .sidebar," in layout_css
    assert (
        "display: none;"
        not in layout_css[
            layout_css.index(".right-rail-resizer.hidden {") : layout_css.index(
                "}", layout_css.index(".right-rail-resizer.hidden {")
            )
        ]
    )
