# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_live_scroll_follow_uses_pre_mutation_bottom_intent(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageTimeline"
        / "scrollController.js"
    )
    module_path = tmp_path / "scrollController.mjs"
    module_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")

    runner_path = tmp_path / "runner.mjs"
    runner_path.write_text(
        """
const frames = [];
globalThis.window = {
  requestAnimationFrame(callback) {
    frames.push(callback);
    return frames.length;
  },
};
globalThis.performance = { now: () => 1000 };

class FakeContainer {
  constructor() {
    this.dataset = {};
    this.scrollHeight = 1000;
    this.clientHeight = 400;
    this.scrollTop = 600;
    this.listeners = {};
  }

  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }

  dispatch(type, event = {}) {
    this.listeners[type]?.(event);
  }
}

const {
  captureBottomIntent,
  forceFollowBottom,
  isNearBottom,
  scheduleFollowBottom,
} = await import('./scrollController.mjs');

const container = new FakeContainer();
const follow = captureBottomIntent(container);
container.scrollHeight = 1800;
scheduleFollowBottom(container, { follow });
while (frames.length) {
  frames.shift()(1000);
}
const followedLargeMutation = container.scrollTop;
const nearAfterLargeMutation = isNearBottom(container);

container.dispatch('wheel', { deltaY: -24 });
container.scrollTop = 900;
container.scrollHeight = 2100;
scheduleFollowBottom(container);
while (frames.length) {
  frames.shift()(1000);
}
const preservedAfterUserScroll = container.scrollTop;

forceFollowBottom(container);
while (frames.length) {
  frames.shift()(1000);
}

console.log(JSON.stringify({
  followedLargeMutation,
  nearAfterLargeMutation,
  preservedAfterUserScroll,
  forcedBottom: container.scrollTop,
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
        timeout=10,
    )

    assert json.loads(completed.stdout) == {
        "followedLargeMutation": 1400,
        "nearAfterLargeMutation": True,
        "preservedAfterUserScroll": 900,
        "forcedBottom": 1700,
    }
