from __future__ import annotations

from pathlib import Path


FRONTEND = Path("frontend/dist")


def test_connectors_styles_use_theme_variables_that_exist_in_dark_mode() -> None:
    css = (FRONTEND / "css" / "components" / "connectors.css").read_text(
        encoding="utf-8"
    )

    assert "--surface-panel" not in css
    assert "--surface-subtle" not in css
    assert "--text-muted" not in css
    assert "var(--bg-surface" in css
    assert "var(--bg-surface-muted" in css
    assert "var(--text-secondary" in css


def test_observability_styles_stay_plain_and_theme_compatible() -> None:
    css = (FRONTEND / "css" / "components" / "observability.css").read_text(
        encoding="utf-8"
    )

    assert "radial-gradient" not in css
    assert "filter: blur" not in css
    assert "border-radius: 999px" not in css
    assert "--tone-glow" not in css
    assert ".observability-scope-btn.active" in css
    assert "background: var(--button-primary-bg);" in css
    assert "color: var(--button-primary-text);" in css


def test_observability_charts_use_small_central_palette() -> None:
    source = (FRONTEND / "js" / "components" / "observability.js").read_text(
        encoding="utf-8"
    )

    assert "const OBSERVABILITY_CHART_COLORS = Object.freeze({" in source
    assert "const OBSERVABILITY_BREAKDOWN_PALETTE = Object.freeze([" in source
    assert "function chartColor(tone)" in source
    assert "function resolveChartTheme()" in source
    assert "'--text-primary'" in source
    assert "'--text-secondary'" in source
    assert "rgba(37, 99, 235" not in source
    assert "rgba(124, 58, 237" not in source
