# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast


def test_round_completion_scroll_policy_follows_only_when_latest_intent_visible(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "rounds"
        / "scrollController.js"
    )
    module_path = tmp_path / "scrollController.mjs"
    module_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")

    runner_path = tmp_path / "runner.mjs"
    runner_path.write_text(
        """
class FakeElement {
    constructor(rect = { top: 0, bottom: 0, height: 0 }) {
        this.rect = rect;
        this.children = [];
        this.dataset = {};
        this.scrollHeight = 1000;
        this.scrollTop = 0;
        this.clientHeight = 400;
    }

    appendChild(child) {
        this.children.push(child);
        return child;
    }

    querySelector(selector) {
        const runMatch = String(selector || '').match(/data-run-id="([^"]+)"/);
        if (runMatch) {
            return this.children.find(child => child.dataset.runId === runMatch[1]) || null;
        }
        if (selector === '.round-detail-intent' || selector === '.round-detail-header') {
            return this.children.find(child => child.kind === selector.slice(1)) || null;
        }
        return null;
    }

    getBoundingClientRect() {
        return this.rect;
    }
}

const {
    captureChatScrollAnchor,
    restoreChatScrollAnchor,
    shouldFollowLatestRoundAfterCompletion,
} = await import('./scrollController.mjs');

const container = new FakeElement({ top: 0, bottom: 400, height: 400 });
container.scrollTop = 300;
container.scrollHeight = 1200;
container.clientHeight = 400;

const visibleSection = new FakeElement({ top: 120, bottom: 260, height: 140 });
visibleSection.dataset.runId = 'run-visible';
const visibleIntent = new FakeElement({ top: 130, bottom: 170, height: 40 });
visibleIntent.kind = 'round-detail-intent';
visibleSection.appendChild(visibleIntent);
container.appendChild(visibleSection);

const hiddenSection = new FakeElement({ top: -500, bottom: -360, height: 140 });
hiddenSection.dataset.runId = 'run-hidden';
const hiddenIntent = new FakeElement({ top: -490, bottom: -450, height: 40 });
hiddenIntent.kind = 'round-detail-intent';
hiddenSection.appendChild(hiddenIntent);
container.appendChild(hiddenSection);

const anchor = captureChatScrollAnchor(container, () => [visibleSection, hiddenSection]);
container.scrollTop = 200;
visibleSection.rect = { top: 80, bottom: 220, height: 140 };
restoreChatScrollAnchor(container, anchor);

console.log(JSON.stringify({
    followsVisible: shouldFollowLatestRoundAfterCompletion(container, 'run-visible'),
    followsHidden: shouldFollowLatestRoundAfterCompletion(container, 'run-hidden'),
    restoredScrollTop: container.scrollTop,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    payload = cast(dict[str, object], json.loads(completed.stdout))

    assert payload == {
        "followsVisible": True,
        "followsHidden": False,
        "restoredScrollTop": 160,
    }


def test_round_timeline_active_state_uses_finite_lock() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    timeline_source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "timeline.js"
    ).read_text(encoding="utf-8")
    navigator_source = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "rounds"
        / "navigator.js"
    ).read_text(encoding="utf-8")

    assert "Number.POSITIVE_INFINITY" not in timeline_source
    assert "activeLockUntil" in timeline_source
    assert "'sync-visible-active'" in navigator_source
