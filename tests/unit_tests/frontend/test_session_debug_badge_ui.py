# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_session_debug_badge_shows_current_database_session_id(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sessionDebugBadge.js"
    )
    interface_css = (
        repo_root / "frontend" / "dist" / "css" / "components" / "interface.css"
    ).read_text(encoding="utf-8")
    badge_css = interface_css[
        interface_css.index(".current-session-id-badge {") : interface_css.index(
            ".current-session-id-badge[hidden]"
        )
    ]
    assert "bottom: 0.45rem;" in badge_css
    assert "right: 0.85rem;" in badge_css
    assert "background:" not in badge_css
    assert "border:" not in badge_css
    assert "pointer-events: auto;" in badge_css
    assert "cursor: text;" in badge_css
    assert "user-select: text;" in badge_css
    module_under_test_path = tmp_path / "sessionDebugBadge.mjs"
    runner_path = tmp_path / "runner-session-debug-badge.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: 'session-from-state',
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const badge = {
    hidden: true,
    textContent: '',
    title: '',
    removedAttributes: [],
    removeAttribute(name) {
        this.removedAttributes.push(name);
        if (name === 'title') {
            this.title = '';
        }
    },
};
export const els = {
    currentSessionIdBadge: badge,
};
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.ResizeObserver = class {
    observe() {}
};

const {
    initializeSessionDebugBadge,
    syncSessionDebugBadge,
} = await import('./sessionDebugBadge.mjs');
const { badge } = await import('./mockDom.mjs');

initializeSessionDebugBadge();
const initial = { hidden: badge.hidden, text: badge.textContent, title: badge.title };
syncSessionDebugBadge('session-db-123');
const shown = { hidden: badge.hidden, text: badge.textContent, title: badge.title };
syncSessionDebugBadge('');
const hidden = { hidden: badge.hidden, text: badge.textContent, title: badge.title };

console.log(JSON.stringify({
    initial,
    shown,
    hidden,
    removedAttributes: badge.removedAttributes,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert payload == {
        "initial": {
            "hidden": False,
            "text": "session-from-state",
            "title": "",
        },
        "shown": {
            "hidden": False,
            "text": "session-db-123",
            "title": "",
        },
        "hidden": {"hidden": True, "text": "", "title": ""},
        "removedAttributes": ["title", "title", "title"],
    }
