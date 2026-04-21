# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_stream_overlay_uses_run_primary_role_for_primary_key(tmp_path: Path) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/stream.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "stream_overlay"
    temp_dir.mkdir()

    (temp_dir / "stream.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId(runId) {
    return runId === "run-acp" ? "external-role" : "";
}

export function isPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
export function applyToolReturn() {}
export function appendStructuredContentPart() {}
export function appendThinkingText() { return {}; }
export function buildPendingToolBlock() { return { querySelector() { return null; } }; }
export function findToolBlock() { return null; }
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function renderMessageBlock() {
    return {
        wrapper: {
            dataset: {},
            querySelector() { return null; },
            closest() { return null; },
        },
        contentEl: {
            appendChild() {},
            querySelector() { return null; },
            querySelectorAll() { return []; },
        },
    };
}
export function resolvePendingToolBlock() { return null; }
export function scrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor() {}
export function updateThinkingText() {}
export function updateMessageText() {}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(_key, values = {}) {
    return JSON.stringify(values);
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import {
  applyStreamOverlayEvent,
  getRunStreamOverlaySnapshot,
} from "./stream.js";

applyStreamOverlayEvent(
  "text_delta",
  { text: "streamed from external ACP" },
  {
    runId: "run-acp",
    instanceId: "external-instance",
    roleId: "external-role",
    label: "External ACP",
  },
);

console.log(JSON.stringify(getRunStreamOverlaySnapshot("run-acp")));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload["coordinator"] is not None
    assert payload["coordinator"]["roleId"] == "external-role"
    assert payload["coordinator"]["label"] == "External ACP"
    assert payload["coordinator"]["textStreaming"] is True
    assert payload["coordinator"]["parts"] == [
        {"kind": "text", "content": "streamed from external ACP"}
    ]
    assert payload["byInstance"] == {}


def test_history_overlay_renders_live_cursor_placeholder_for_stream_tail(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/history.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "history_overlay"
    temp_dir.mkdir()

    (temp_dir / "history.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function isRunPrimaryRoleId(roleId, runId) {
    return roleId === "external-role" && runId === "run-1";
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(key, values = {}) {
    return `${key}:${JSON.stringify(values)}`;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
function createContentEl() {
  return {
    children: [],
    appendChild(child) {
      this.children.push(child);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
}

export function applyToolReturn() {}
export function appendStructuredContentPart() { return null; }
export function appendThinkingText() {}
export function buildToolBlock() {
  return { dataset: {}, querySelector() { return null; } };
}
export function decoratePendingApprovalBlock() {}
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function labelFromRole(_role, roleId, instanceId) {
  return roleId || instanceId || "Agent";
}
export function parseApprovalArgsPreview() { return {}; }
export function renderMessageBlock(container, _role, label, _parts = [], options = {}) {
  const contentEl = createContentEl();
  const wrapper = {
    dataset: {
      runId: String(options.runId || ""),
      roleId: String(options.roleId || ""),
      instanceId: String(options.instanceId || ""),
      streamKey: String(options.streamKey || ""),
    },
    querySelector(selector) {
      if (selector === ".msg-role") {
        return { textContent: String(label || "").toUpperCase() };
      }
      if (selector === ".msg-content") {
        return contentEl;
      }
      return null;
    },
  };
  container.messages.push(wrapper);
  return { wrapper, contentEl };
}
export function renderParts() {}
export function resolvePendingToolBlock() { return null; }
export function forceScrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}

export function appendMessageText(contentEl, text, options = {}) {
  globalThis.__appendCalls.push({
    text,
    streaming: options.streaming === true,
  });
  const block = {
    type: "msg-text",
    text,
    streaming: options.streaming === true,
  };
  contentEl.appendChild(block);
  return block;
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import { renderHistoricalMessageList } from "./history.js";

globalThis.__appendCalls = [];

const container = {
  dataset: {},
  messages: [],
  appendChild(child) {
    this.messages.push(child);
  },
  querySelectorAll() {
    return [];
  },
  querySelector() {
    return null;
  },
};

renderHistoricalMessageList(container, [], {
  runId: "run-1",
  pendingToolApprovals: [],
  streamOverlayEntry: {
    roleId: "external-role",
    instanceId: "external-instance",
    label: "External ACP",
    parts: [],
    textStreaming: true,
  },
});

console.log(JSON.stringify(globalThis.__appendCalls));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload == [{"text": "", "streaming": True}]


def test_history_overlay_renders_live_cursor_placeholder_for_idle_gap(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/history.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "history_overlay_idle_gap"
    temp_dir.mkdir()

    (temp_dir / "history.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function isRunPrimaryRoleId(roleId, runId) {
    return roleId === "external-role" && runId === "run-1";
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(key, values = {}) {
    return `${key}:${JSON.stringify(values)}`;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
function createContentEl() {
  return {
    children: [],
    appendChild(child) {
      this.children.push(child);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
}

export function applyToolReturn() {}
export function appendStructuredContentPart() { return null; }
export function appendThinkingText() {}
export function buildToolBlock() {
  return { dataset: {}, querySelector() { return null; } };
}
export function decoratePendingApprovalBlock() {}
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function labelFromRole(_role, roleId, instanceId) {
  return roleId || instanceId || "Agent";
}
export function parseApprovalArgsPreview() { return {}; }
export function renderMessageBlock(container, _role, label, _parts = [], options = {}) {
  const contentEl = createContentEl();
  const wrapper = {
    dataset: {
      runId: String(options.runId || ""),
      roleId: String(options.roleId || ""),
      instanceId: String(options.instanceId || ""),
      streamKey: String(options.streamKey || ""),
    },
    querySelector(selector) {
      if (selector === ".msg-role") {
        return { textContent: String(label || "").toUpperCase() };
      }
      if (selector === ".msg-content") {
        return contentEl;
      }
      return null;
    },
  };
  container.messages.push(wrapper);
  return { wrapper, contentEl };
}
export function renderParts() {}
export function resolvePendingToolBlock() { return null; }
export function forceScrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}

export function appendMessageText(contentEl, text, options = {}) {
  globalThis.__appendCalls.push({
    text,
    streaming: options.streaming === true,
  });
  const block = {
    type: "msg-text",
    text,
    streaming: options.streaming === true,
  };
  contentEl.appendChild(block);
  return block;
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import { renderHistoricalMessageList } from "./history.js";

globalThis.__appendCalls = [];

const container = {
  dataset: {},
  messages: [],
  appendChild(child) {
    this.messages.push(child);
  },
  querySelectorAll() {
    return [];
  },
  querySelector() {
    return null;
  },
};

renderHistoricalMessageList(container, [], {
  runId: "run-1",
  pendingToolApprovals: [],
  streamOverlayEntry: {
    roleId: "external-role",
    instanceId: "external-instance",
    label: "External ACP",
    parts: [],
    textStreaming: false,
    idleCursor: true,
  },
});

console.log(JSON.stringify(globalThis.__appendCalls));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload == [{"text": "", "streaming": True}]


def test_history_overlay_renders_media_refs_from_stream_overlay(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/history.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "history_overlay_media_ref"
    temp_dir.mkdir()

    (temp_dir / "history.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function isRunPrimaryRoleId(roleId, runId) {
    return roleId === "external-role" && runId === "run-1";
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(key, values = {}) {
    return `${key}:${JSON.stringify(values)}`;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
function createContentEl() {
  return {
    children: [],
    appendChild(child) {
      this.children.push(child);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
}

export function applyToolReturn() {}
export function appendStructuredContentPart(contentEl, part) {
  globalThis.__structuredCalls.push(part);
  const block = {
    type: "structured",
    part,
  };
  contentEl.appendChild(block);
  return block;
}
export function appendThinkingText() {}
export function buildToolBlock() {
  return { dataset: {}, querySelector() { return null; } };
}
export function decoratePendingApprovalBlock() {}
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function labelFromRole(_role, roleId, instanceId) {
  return roleId || instanceId || "Agent";
}
export function parseApprovalArgsPreview() { return {}; }
export function renderMessageBlock(container, _role, label, _parts = [], options = {}) {
  const contentEl = createContentEl();
  const wrapper = {
    dataset: {
      runId: String(options.runId || ""),
      roleId: String(options.roleId || ""),
      instanceId: String(options.instanceId || ""),
      streamKey: String(options.streamKey || ""),
    },
    querySelector(selector) {
      if (selector === ".msg-role") {
        return { textContent: String(label || "").toUpperCase() };
      }
      if (selector === ".msg-content") {
        return contentEl;
      }
      return null;
    },
  };
  container.messages.push({ wrapper, contentEl });
  return { wrapper, contentEl };
}
export function renderParts() {}
export function resolvePendingToolBlock() { return null; }
export function forceScrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function appendMessageText(contentEl, text, options = {}) {
  const block = {
    type: "msg-text",
    text,
    streaming: options.streaming === true,
  };
  contentEl.appendChild(block);
  return block;
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import { renderHistoricalMessageList } from "./history.js";

globalThis.__structuredCalls = [];

const container = {
  dataset: {},
  messages: [],
  appendChild(child) {
    this.messages.push(child);
  },
  querySelectorAll() {
    return [];
  },
  querySelector() {
    return null;
  },
};

renderHistoricalMessageList(container, [], {
  runId: "run-1",
  pendingToolApprovals: [],
  streamOverlayEntry: {
    roleId: "external-role",
    instanceId: "external-instance",
    label: "External ACP",
    parts: [
      {
        kind: "media_ref",
        modality: "image",
        mime_type: "image/png",
        url: "data:image/png;base64,AAA",
        name: "image.png",
      },
    ],
    textStreaming: false,
  },
});

console.log(JSON.stringify({
  structuredCalls: globalThis.__structuredCalls,
  wrapperCount: container.messages.length,
}));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload == {
        "structuredCalls": [
            {
                "kind": "media_ref",
                "modality": "image",
                "mime_type": "image/png",
                "url": "data:image/png;base64,AAA",
                "name": "image.png",
            }
        ],
        "wrapperCount": 1,
    }


def test_history_overlay_can_render_as_separate_live_message(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/history.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "history_overlay_separate"
    temp_dir.mkdir()

    (temp_dir / "history.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function isRunPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(key, values = {}) {
    return `${key}:${JSON.stringify(values)}`;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
function createContentEl(wrapperId) {
  return {
    wrapperId,
    children: [],
    appendChild(child) {
      this.children.push(child);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
}

export function applyToolReturn() {}
export function appendStructuredContentPart() { return null; }
export function appendThinkingText(contentEl, text, options = {}) {
  contentEl.children.push({
    type: "thinking",
    text,
    streaming: options.streaming === true,
  });
}
export function buildToolBlock() {
  return { dataset: {}, querySelector() { return null; } };
}
export function decoratePendingApprovalBlock() {}
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function labelFromRole(_role, roleId, instanceId) {
  return roleId || instanceId || "Agent";
}
export function parseApprovalArgsPreview() { return {}; }
export function renderMessageBlock(container, _role, label, _parts = [], options = {}) {
  const wrapperId = `wrapper-${container.messages.length + 1}`;
  const contentEl = createContentEl(wrapperId);
  const wrapper = {
    id: wrapperId,
    dataset: {
      runId: String(options.runId || ""),
      roleId: String(options.roleId || ""),
      instanceId: String(options.instanceId || ""),
      streamKey: String(options.streamKey || ""),
    },
    querySelector(selector) {
      if (selector === ".msg-role") {
        return { textContent: String(label || "").toUpperCase() };
      }
      if (selector === ".msg-content") {
        return contentEl;
      }
      return null;
    },
  };
  container.messages.push({ wrapper, contentEl });
  return { wrapper, contentEl };
}
export function renderParts(contentEl, parts) {
  contentEl.children.push({
    type: "history-parts",
    parts,
  });
}
export function resolvePendingToolBlock() { return null; }
export function forceScrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function appendMessageText(contentEl, text, options = {}) {
  contentEl.children.push({
    type: "text",
    text,
    streaming: options.streaming === true,
  });
  return {
    closest() { return null; },
  };
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import { renderHistoricalMessageList } from "./history.js";

const container = {
  dataset: {},
  messages: [],
  appendChild() {},
  querySelectorAll() {
    return this.messages.map(item => item.wrapper);
  },
  querySelector() {
    return null;
  },
};

renderHistoricalMessageList(container, [
  {
    role: "assistant",
    role_id: "Writer",
    instance_id: "inst-1",
    message: {
      parts: [{ part_kind: "text", content: "persisted" }],
    },
  },
], {
  runId: "subagent_run_1",
  streamOverlayEntry: {
    roleId: "Writer",
    instanceId: "inst-1",
    label: "Writer",
    parts: [{ kind: "thinking", content: "live thought", finished: false, part_index: 0, _key: "0:0" }],
    textStreaming: true,
  },
  separateOverlayMessage: true,
});

console.log(JSON.stringify({
  wrapperCount: container.messages.length,
  firstWrapperChildren: container.messages[0].contentEl.children,
  secondWrapperChildren: container.messages[1].contentEl.children,
}));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload["wrapperCount"] == 2
    assert payload["firstWrapperChildren"] == [
        {
            "type": "history-parts",
            "parts": [{"part_kind": "text", "content": "persisted"}],
        }
    ]
    assert payload["secondWrapperChildren"] == [
        {
            "type": "thinking",
            "text": "live thought",
            "streaming": True,
        },
        {
            "type": "text",
            "text": "",
            "streaming": True,
        },
    ]


def test_finalize_stream_clears_overlay_without_live_stream_state(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/stream.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "stream_finalize_overlay"
    temp_dir.mkdir()

    (temp_dir / "stream.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId() {
    return "";
}

export function isPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
export function applyToolReturn() {}
export function appendStructuredContentPart() {}
export function appendThinkingText() { return {}; }
export function buildPendingToolBlock() { return { querySelector() { return null; } }; }
export function findToolBlock() { return null; }
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function renderMessageBlock() {
    return {
        wrapper: {
            dataset: {},
            querySelector() { return null; },
            closest() { return null; },
        },
        contentEl: {
            appendChild() {},
            querySelector() { return null; },
            querySelectorAll() { return []; },
        },
    };
}
export function resolvePendingToolBlock() { return null; }
export function scrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor() {}
export function updateThinkingText() {}
export function updateMessageText() {}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(_key, values = {}) {
    return JSON.stringify(values);
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import {
  applyStreamOverlayEvent,
  finalizeStream,
  getRunStreamOverlaySnapshot,
} from "./stream.js";

applyStreamOverlayEvent(
  "text_delta",
  { text: "stale overlay" },
  {
    runId: "subagent_run_1",
    instanceId: "inst-sub-1",
    roleId: "Crafter",
    label: "Crafter",
  },
);

const before = getRunStreamOverlaySnapshot("subagent_run_1");
finalizeStream("inst-sub-1", "Crafter", { runId: "subagent_run_1" });
const after = getRunStreamOverlaySnapshot("subagent_run_1");

console.log(JSON.stringify({ before, after }));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload["before"]["byInstance"] == {
        "inst-sub-1": {
            "instanceId": "inst-sub-1",
            "roleId": "Crafter",
            "label": "Crafter",
            "parts": [{"kind": "text", "content": "stale overlay"}],
            "textStreaming": True,
            "idleCursor": False,
        }
    }
    assert payload["after"] == {"coordinator": None, "byInstance": {}}


def test_finalize_stream_turns_off_streaming_cursor_for_live_subagent_text(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/stream.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "stream_finalize_cursor"
    temp_dir.mkdir()

    (temp_dir / "stream.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId() {
    return "";
}

export function isPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
export function applyToolReturn() {}
export function appendStructuredContentPart() {}
export function appendThinkingText() { return {}; }
export function buildPendingToolBlock() { return { querySelector() { return null; } }; }
export function findToolBlock() { return null; }
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function renderMessageBlock() {
    return {
        wrapper: {
            dataset: {},
            querySelector() { return null; },
            closest() { return null; },
        },
        contentEl: {
            childNodes: [],
            appendChild(child) { this.childNodes.push(child); },
            querySelector() { return null; },
            querySelectorAll() { return []; },
        },
    };
}
export function resolvePendingToolBlock() { return null; }
export function scrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor(_textEl, active) {
    globalThis.__cursorStates.push(active === true);
}
export function updateThinkingText() {}
export function updateMessageText(_textEl, text, options = {}) {
    globalThis.__textUpdates.push({
        text: String(text || ""),
        streaming: options.streaming === true,
    });
    syncStreamingCursor(_textEl, options.streaming === true);
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(_key, values = {}) {
    return JSON.stringify(values);
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
globalThis.__textUpdates = [];
globalThis.__cursorStates = [];
globalThis.document = {
  createElement() {
    return {
      className: "",
      childNodes: [],
      appendChild(child) { this.childNodes.push(child); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      closest() { return null; },
    };
  },
};

import {
  appendStreamChunk,
  finalizeStream,
  getOrCreateStreamBlock,
} from "./stream.js";

const container = {
  appendChild() {},
  querySelectorAll() { return []; },
};

getOrCreateStreamBlock(
  container,
  "inst-sub-1",
  "Crafter",
  "Crafter",
  "subagent_run_1",
);
appendStreamChunk(
  "inst-sub-1",
  "50159495496",
  "subagent_run_1",
  "Crafter",
  "Crafter",
);
finalizeStream("inst-sub-1", "Crafter", { runId: "subagent_run_1" });

console.log(JSON.stringify({
  textUpdates: globalThis.__textUpdates,
  cursorStates: globalThis.__cursorStates,
}));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload["textUpdates"] == [
        {"text": "50159495496", "streaming": True},
        {"text": "50159495496", "streaming": False},
    ]
    assert payload["cursorStates"] == [True, False]


def test_tool_result_materializes_overlay_tool_block_into_visible_container(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/stream.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "stream_tool_result_materialize"
    temp_dir.mkdir()

    (temp_dir / "stream.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId() {
    return "";
}

export function isPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(_key, values = {}) {
    return JSON.stringify(values);
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
function toolMatches(block, toolName, toolCallId) {
  const safeToolCallId = String(toolCallId || "");
  if (safeToolCallId) {
    return String(block?.dataset?.toolCallId || "") === safeToolCallId;
  }
  return String(block?.dataset?.toolName || "") === String(toolName || "");
}

function findInContent(contentEl, toolName, toolCallId) {
  if (!contentEl || !Array.isArray(contentEl.children)) {
    return null;
  }
  for (let index = contentEl.children.length - 1; index >= 0; index -= 1) {
    const child = contentEl.children[index];
    if (toolMatches(child, toolName, toolCallId)) {
      return child;
    }
  }
  return null;
}

export function applyToolReturn(toolBlock, content) {
  toolBlock.__result = content;
}

export function appendStructuredContentPart() {}
export function appendThinkingText() { return {}; }

export function buildPendingToolBlock(toolName, args, toolCallId = null) {
  const outputEl = { classList: { add() {}, remove() {} }, innerHTML: "", textContent: "" };
  return {
    dataset: {
      toolName: String(toolName || ""),
      toolCallId: String(toolCallId || ""),
      status: "running",
    },
    __args: args || {},
    __result: null,
    querySelector(selector) {
      if (selector === ".tool-output") {
        return outputEl;
      }
      return null;
    },
    closest() { return null; },
  };
}

export function findToolBlock(contentEl, toolName, toolCallId) {
  return findInContent(contentEl, toolName, toolCallId);
}

export function findToolBlockInContainer(container, toolName, toolCallId) {
  const messages = Array.isArray(container?.__messages) ? container.__messages : [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const found = findInContent(messages[index].contentEl, toolName, toolCallId);
    if (found) {
      return found;
    }
  }
  return null;
}

export function indexPendingToolBlock(pendingToolBlocks, toolBlock, toolName, toolCallId) {
  const key = `${toolName || ""}::${toolCallId || ""}`;
  pendingToolBlocks[key] = toolBlock;
  if (toolName) {
    pendingToolBlocks[`${toolName}::`] = toolBlock;
  }
}

export function renderMessageBlock(container, _role, label, _parts = [], options = {}) {
  const contentEl = {
    children: [],
    appendChild(child) {
      this.children.push(child);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
  const wrapper = {
    dataset: {
      runId: String(options.runId || ""),
      roleId: String(options.roleId || ""),
      instanceId: String(options.instanceId || ""),
      streamKey: String(options.streamKey || ""),
    },
    querySelector(selector) {
      if (selector === ".msg-role") {
        return { textContent: String(label || "").toUpperCase() };
      }
      if (selector === ".msg-content") {
        return contentEl;
      }
      return null;
    },
    closest() { return null; },
  };
  container.__messages.push({ wrapper, contentEl });
  return { wrapper, contentEl };
}

export function resolvePendingToolBlock(pendingToolBlocks, toolName, toolCallId) {
  const byId = pendingToolBlocks[`${toolName || ""}::${toolCallId || ""}`];
  if (byId) {
    return byId;
  }
  return pendingToolBlocks[`${toolName || ""}::`] || null;
}

export function scrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor() {}
export function updateThinkingText() {}
export function updateMessageText() {}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import {
  applyStreamOverlayEvent,
  getRunStreamOverlaySnapshot,
  updateToolResult,
} from "./stream.js";

globalThis.document = {
  createElement() {
    return {
      className: "",
      dataset: {},
      children: [],
      appendChild(child) { this.children.push(child); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      closest() { return null; },
    };
  },
};

const container = {
  __messages: [],
  querySelectorAll() {
    return this.__messages.map(item => item.wrapper);
  },
};

applyStreamOverlayEvent(
  "tool_call",
  {
    tool_name: "shell",
    tool_call_id: "call-1",
    args: { command: "echo hi" },
  },
  {
    runId: "run-1",
    instanceId: "inst-1",
    roleId: "Writer",
    label: "Writer",
  },
);

updateToolResult(
  "inst-1",
  "shell",
  {
    ok: true,
    data: { text: "done" },
  },
  false,
  "call-1",
  {
    runId: "run-1",
    roleId: "Writer",
    label: "Writer",
    container,
  },
);

const snapshot = getRunStreamOverlaySnapshot("run-1");
const block = container.__messages[0].contentEl.children[0];

console.log(JSON.stringify({
  messageCount: container.__messages.length,
  blockArgs: block.__args,
  blockResult: block.__result,
  overlay: snapshot.byInstance["inst-1"],
}));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload["messageCount"] == 1
    assert payload["blockArgs"] == {"command": "echo hi"}
    assert payload["blockResult"] == {"ok": True, "data": {"text": "done"}}
    assert payload["overlay"] == {
        "instanceId": "inst-1",
        "roleId": "Writer",
        "label": "Writer",
        "parts": [
            {
                "kind": "tool",
                "tool_call_id": "call-1",
                "tool_name": "shell",
                "args": {"command": "echo hi"},
                "status": "completed",
                "result": {"ok": True, "data": {"text": "done"}},
            }
        ],
        "textStreaming": False,
        "idleCursor": True,
    }


def test_tool_result_event_synthesizes_overlay_part_without_prior_tool_call(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/stream.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "stream_tool_result_overlay_only"
    temp_dir.mkdir()

    (temp_dir / "stream.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId() {
    return "";
}

export function isPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(_key, values = {}) {
    return JSON.stringify(values);
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
export function applyToolReturn() {}
export function appendStructuredContentPart() {}
export function appendThinkingText() { return {}; }
export function buildPendingToolBlock() { return { querySelector() { return null; } }; }
export function findToolBlock() { return null; }
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function renderMessageBlock() {
  return {
    wrapper: {
      dataset: {},
      querySelector() { return null; },
      closest() { return null; },
    },
    contentEl: {
      appendChild() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
    },
  };
}
export function resolvePendingToolBlock() { return null; }
export function scrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor() {}
export function updateThinkingText() {}
export function updateMessageText() {}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import {
  applyStreamOverlayEvent,
  getRunStreamOverlaySnapshot,
} from "./stream.js";

applyStreamOverlayEvent(
  "tool_result",
  {
    tool_name: "read",
    tool_call_id: "call-9",
    result: {
      ok: false,
      error: { message: "boom" },
    },
  },
  {
    runId: "run-2",
    instanceId: "inst-2",
    roleId: "Researcher",
    label: "Researcher",
  },
);

console.log(JSON.stringify(getRunStreamOverlaySnapshot("run-2")));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload == {
        "coordinator": None,
        "byInstance": {
            "inst-2": {
                "instanceId": "inst-2",
                "roleId": "Researcher",
                "label": "Researcher",
                "parts": [
                    {
                        "kind": "tool",
                        "tool_call_id": "call-9",
                        "tool_name": "read",
                        "args": {},
                        "status": "error",
                        "result": {
                            "ok": False,
                            "error": {"message": "boom"},
                        },
                    }
                ],
                "textStreaming": False,
                "idleCursor": True,
            }
        },
    }


def test_overlay_snapshot_preserves_idle_cursor_state(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/stream.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "stream_idle_cursor_snapshot"
    temp_dir.mkdir()

    (temp_dir / "stream.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId() {
    return "";
}

export function isPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(_key, values = {}) {
    return JSON.stringify(values);
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
export function applyToolReturn() {}
export function appendStructuredContentPart() {}
export function appendThinkingText() { return {}; }
export function buildPendingToolBlock() { return { querySelector() { return null; } }; }
export function findToolBlock() { return null; }
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function renderMessageBlock() {
  return {
    wrapper: {
      dataset: {},
      querySelector() { return null; },
      closest() { return null; },
    },
    contentEl: {
      appendChild() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
    },
  };
}
export function resolvePendingToolBlock() { return null; }
export function scrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor() {}
export function updateThinkingText() {}
export function updateMessageText() {}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import {
  applyStreamOverlayEvent,
  getRunStreamOverlaySnapshot,
} from "./stream.js";

applyStreamOverlayEvent(
  "thinking_finished",
  { part_index: 0 },
  {
    runId: "run-3",
    instanceId: "inst-3",
    roleId: "Writer",
    label: "Writer",
  },
);

console.log(JSON.stringify(getRunStreamOverlaySnapshot("run-3")));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload == {
        "coordinator": None,
        "byInstance": {
            "inst-3": {
                "instanceId": "inst-3",
                "roleId": "Writer",
                "label": "Writer",
                "parts": [],
                "textStreaming": False,
                "idleCursor": True,
            }
        },
    }


def test_finalize_thinking_restores_idle_streaming_cursor_until_finalize(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/stream.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "stream_idle_cursor_after_thinking"
    temp_dir.mkdir()

    (temp_dir / "stream.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId() {
    return "";
}

export function isPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(_key, values = {}) {
    return JSON.stringify(values);
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
export function applyToolReturn() {}
export function appendStructuredContentPart() {}
export function buildPendingToolBlock() { return { querySelector() { return null; } }; }
export function findToolBlock() { return null; }
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function renderMessageBlock() {
  return {
    wrapper: {
      dataset: {},
      querySelector() { return null; },
      closest() { return null; },
    },
    contentEl: {
      children: [],
      appendChild(child) { this.children.push(child); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
    },
  };
}
export function resolvePendingToolBlock() { return null; }
export function scrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor(_textEl, active) {
  globalThis.__cursorStates.push(active === true);
}
export function updateMessageText(_textEl, text, options = {}) {
  globalThis.__messageUpdates.push({
    text: String(text || ""),
    streaming: options.streaming === true,
  });
  syncStreamingCursor(_textEl, options.streaming === true);
}
export function appendThinkingText(contentEl, _text, options = {}) {
  const thinkingBlock = {
    dataset: {},
    open: true,
    querySelector() {
      return { style: {} };
    },
  };
  const textEl = {
    __partIndex: String(options.partIndex || ""),
    closest() {
      return thinkingBlock;
    },
  };
  contentEl.appendChild(textEl);
  return textEl;
}
export function updateThinkingText(_textEl, text, options = {}) {
  globalThis.__thinkingUpdates.push({
    text: String(text || ""),
    streaming: options.streaming === true,
  });
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
globalThis.__cursorStates = [];
globalThis.__messageUpdates = [];
globalThis.__thinkingUpdates = [];
globalThis.document = {
  createElement() {
    return {
      className: "",
      childNodes: [],
      appendChild(child) { this.childNodes.push(child); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      closest() { return null; },
      remove() { this.__removed = true; },
    };
  },
};

import {
  appendThinkingChunk,
  finalizeStream,
  finalizeThinking,
  getOrCreateStreamBlock,
  startThinkingBlock,
} from "./stream.js";

const container = {
  appendChild() {},
  querySelectorAll() { return []; },
};

getOrCreateStreamBlock(container, "inst-1", "Crafter", "Crafter", "run-1");
startThinkingBlock("inst-1", 0, {
  container,
  runId: "run-1",
  roleId: "Crafter",
  label: "Crafter",
});
appendThinkingChunk("inst-1", 0, "working", {
  container,
  runId: "run-1",
  roleId: "Crafter",
  label: "Crafter",
});
finalizeThinking("inst-1", 0, { runId: "run-1", roleId: "Crafter" });
const beforeFinalize = globalThis.__cursorStates.slice();
finalizeStream("inst-1", "Crafter", { runId: "run-1" });

console.log(JSON.stringify({
  thinkingUpdates: globalThis.__thinkingUpdates,
  messageUpdates: globalThis.__messageUpdates,
  cursorStates: globalThis.__cursorStates,
  beforeFinalize,
}));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload["thinkingUpdates"] == [
        {"text": "working", "streaming": True},
        {"text": "working", "streaming": False},
        {"text": "working", "streaming": False},
    ]
    assert payload["messageUpdates"] == [{"text": "", "streaming": True}]
    assert payload["beforeFinalize"] == [True]
    assert payload["cursorStates"] == [True, False]


def test_tool_result_restores_idle_streaming_cursor_until_next_segment(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/stream.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "stream_idle_cursor_after_tool"
    temp_dir.mkdir()

    (temp_dir / "stream.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId() {
    return "";
}

export function isPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(_key, values = {}) {
    return JSON.stringify(values);
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
export function appendStructuredContentPart() {}
export function appendThinkingText() { return {}; }
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function scrollBottom() {}

export function buildPendingToolBlock(toolName, args, toolCallId = null) {
  return {
    dataset: {
      toolName: String(toolName || ""),
      toolCallId: String(toolCallId || ""),
    },
    __args: args || {},
    __result: null,
    querySelector() { return null; },
    closest() { return null; },
  };
}

export function applyToolReturn(toolBlock, content) {
  toolBlock.__result = content;
}

export function findToolBlock(contentEl, toolName, toolCallId) {
  return contentEl.children.find(child =>
    String(child?.dataset?.toolName || "") === String(toolName || "")
      && String(child?.dataset?.toolCallId || "") === String(toolCallId || "")
  ) || null;
}

export function findToolBlockInContainer(container, toolName, toolCallId) {
  return container.__messages
    .flatMap(item => item.contentEl.children)
    .find(child =>
      String(child?.dataset?.toolName || "") === String(toolName || "")
        && String(child?.dataset?.toolCallId || "") === String(toolCallId || "")
    ) || null;
}

export function indexPendingToolBlock(pendingToolBlocks, toolBlock, toolName, toolCallId) {
  pendingToolBlocks[`${toolName || ""}::${toolCallId || ""}`] = toolBlock;
  pendingToolBlocks[`${toolName || ""}::`] = toolBlock;
}

export function renderMessageBlock(container, _role, label, _parts = [], options = {}) {
  const contentEl = {
    children: [],
    appendChild(child) { this.children.push(child); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const wrapper = {
    dataset: {
      runId: String(options.runId || ""),
      roleId: String(options.roleId || ""),
      instanceId: String(options.instanceId || ""),
      streamKey: String(options.streamKey || ""),
    },
    querySelector(selector) {
      if (selector === ".msg-role") return { textContent: String(label || "").toUpperCase() };
      if (selector === ".msg-content") return contentEl;
      return null;
    },
    closest() { return null; },
  };
  container.__messages.push({ wrapper, contentEl });
  return { wrapper, contentEl };
}

export function resolvePendingToolBlock(pendingToolBlocks, toolName, toolCallId) {
  return pendingToolBlocks[`${toolName || ""}::${toolCallId || ""}`]
    || pendingToolBlocks[`${toolName || ""}::`]
    || null;
}

export function syncStreamingCursor(_textEl, active) {
  globalThis.__cursorStates.push(active === true);
}

export function updateMessageText(_textEl, text, options = {}) {
  globalThis.__messageUpdates.push({
    text: String(text || ""),
    streaming: options.streaming === true,
  });
  syncStreamingCursor(_textEl, options.streaming === true);
}

export function updateThinkingText() {}
""".strip(),
        encoding="utf-8",
    )

    runner = """
globalThis.__cursorStates = [];
globalThis.__messageUpdates = [];
globalThis.document = {
  createElement() {
    return {
      className: "",
      childNodes: [],
      appendChild(child) { this.childNodes.push(child); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      closest() { return null; },
      remove() { this.__removed = true; },
    };
  },
};

import {
  appendToolCallBlock,
  getOrCreateStreamBlock,
  startThinkingBlock,
  updateToolResult,
} from "./stream.js";

const container = {
  __messages: [],
  appendChild() {},
  querySelectorAll() { return this.__messages.map(item => item.wrapper); },
};

getOrCreateStreamBlock(container, "inst-2", "Crafter", "Crafter", "run-2");
appendToolCallBlock(container, "inst-2", "shell", { command: "echo ok" }, "call-1", {
  runId: "run-2",
  roleId: "Crafter",
  label: "Crafter",
});
updateToolResult("inst-2", "shell", { ok: true, data: "ok" }, false, "call-1", {
  runId: "run-2",
  roleId: "Crafter",
  label: "Crafter",
  container,
});
const afterToolResult = globalThis.__cursorStates.slice();
startThinkingBlock("inst-2", 1, {
  container,
  runId: "run-2",
  roleId: "Crafter",
  label: "Crafter",
});

console.log(JSON.stringify({
  messageUpdates: globalThis.__messageUpdates,
  cursorStates: globalThis.__cursorStates,
  afterToolResult,
}));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload["messageUpdates"] == [{"text": "", "streaming": True}]
    assert payload["afterToolResult"] == [True]
    assert payload["cursorStates"] == [True, False]


def test_rebind_then_tool_result_keeps_existing_text_and_appends_idle_placeholder(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/stream.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "stream_rebind_tool_result_idle_tail"
    temp_dir.mkdir()

    (temp_dir / "stream.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId() {
    return "";
}

export function isPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(_key, values = {}) {
    return JSON.stringify(values);
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
function removeFromParent(node) {
  const parent = node?.__parent || null;
  if (!parent || !Array.isArray(parent.children)) {
    return;
  }
  const index = parent.children.indexOf(node);
  if (index >= 0) {
    parent.children.splice(index, 1);
  }
}

function createTextNode() {
  return {
    className: "msg-text",
    dataset: {},
    __text: "",
    __streaming: false,
    __parent: null,
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    remove() { removeFromParent(this); },
  };
}

function toolMatches(block, toolName, toolCallId) {
  const safeToolCallId = String(toolCallId || "");
  if (safeToolCallId) {
    return String(block?.dataset?.toolCallId || "") === safeToolCallId;
  }
  return String(block?.dataset?.toolName || "") === String(toolName || "");
}

function findInContent(contentEl, toolName, toolCallId) {
  if (!contentEl || !Array.isArray(contentEl.children)) {
    return null;
  }
  for (let index = contentEl.children.length - 1; index >= 0; index -= 1) {
    const child = contentEl.children[index];
    if (toolMatches(child, toolName, toolCallId)) {
      return child;
    }
  }
  return null;
}

export function applyToolReturn(toolBlock, content) {
  toolBlock.__result = content;
}

export function appendStructuredContentPart() {}
export function appendThinkingText() { return {}; }

export function buildPendingToolBlock(toolName, args, toolCallId = null) {
  return {
    dataset: {
      toolName: String(toolName || ""),
      toolCallId: String(toolCallId || ""),
      status: "running",
    },
    __args: args || {},
    __result: null,
    __parent: null,
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    remove() { removeFromParent(this); },
  };
}

export function findToolBlock(contentEl, toolName, toolCallId) {
  return findInContent(contentEl, toolName, toolCallId);
}

export function findToolBlockInContainer(container, toolName, toolCallId) {
  const messages = Array.isArray(container?.__messages) ? container.__messages : [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const found = findInContent(messages[index].contentEl, toolName, toolCallId);
    if (found) {
      return found;
    }
  }
  return null;
}

export function indexPendingToolBlock(pendingToolBlocks, toolBlock, toolName, toolCallId) {
  pendingToolBlocks[`${toolName || ""}::${toolCallId || ""}`] = toolBlock;
  if (toolName) {
    pendingToolBlocks[`${toolName}::`] = toolBlock;
  }
}

export function renderMessageBlock(container, _role, label, _parts = [], options = {}) {
  const contentEl = {
    children: [],
    appendChild(child) {
      child.__parent = this;
      this.children.push(child);
    },
    querySelector() {
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".msg-text") {
        return this.children.filter(child => child?.className === "msg-text");
      }
      return [];
    },
  };
  const wrapper = {
    dataset: {
      runId: String(options.runId || ""),
      roleId: String(options.roleId || ""),
      instanceId: String(options.instanceId || ""),
      streamKey: String(options.streamKey || ""),
    },
    querySelector(selector) {
      if (selector === ".msg-role") {
        return { textContent: String(label || "").toUpperCase() };
      }
      if (selector === ".msg-content") {
        return contentEl;
      }
      return null;
    },
    closest() { return null; },
  };
  container.__messages.push({ wrapper, contentEl });
  return { wrapper, contentEl };
}

export function resolvePendingToolBlock(pendingToolBlocks, toolName, toolCallId) {
  return pendingToolBlocks[`${toolName || ""}::${toolCallId || ""}`]
    || pendingToolBlocks[`${toolName || ""}::`]
    || null;
}

export function scrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor(_textEl, active) {
  globalThis.__cursorStates.push(active === true);
}
export function updateThinkingText() {}
export function updateMessageText(textEl, text, options = {}) {
  textEl.__text = String(text || "");
  textEl.__streaming = options.streaming === true;
  syncStreamingCursor(textEl, options.streaming === true);
}

globalThis.__createTextNode = createTextNode;
""".strip(),
        encoding="utf-8",
    )

    runner = """
globalThis.__cursorStates = [];
globalThis.document = {
  createElement() {
    return globalThis.__createTextNode();
  },
};

import {
  appendStreamChunk,
  appendToolCallBlock,
  bindStreamOverlayToContainer,
  clearRenderedStreamState,
  getOrCreateStreamBlock,
  updateToolResult,
} from "./stream.js";

const container = {
  __messages: [],
  appendChild() {},
  querySelectorAll() {
    return this.__messages.map(item => item.wrapper);
  },
};

getOrCreateStreamBlock(container, "inst-1", "Writer", "Writer", "run-1");
appendStreamChunk("inst-1", "hello", "run-1", "Writer", "Writer");
appendToolCallBlock(container, "inst-1", "shell", { command: "echo hi" }, "call-1", {
  runId: "run-1",
  roleId: "Writer",
  label: "Writer",
});
clearRenderedStreamState();
bindStreamOverlayToContainer(container, {
  instanceId: "inst-1",
  roleId: "Writer",
  label: "Writer",
  runId: "run-1",
});
updateToolResult("inst-1", "shell", { ok: true, data: "done" }, false, "call-1", {
  runId: "run-1",
  roleId: "Writer",
  label: "Writer",
  container,
});

const children = container.__messages[0].contentEl.children.map(child => ({
  className: String(child.className || ""),
  text: String(child.__text || ""),
  toolName: String(child?.dataset?.toolName || ""),
  toolCallId: String(child?.dataset?.toolCallId || ""),
  idleCursor: String(child?.dataset?.idleCursor || ""),
}));

console.log(JSON.stringify(children));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload == [
        {
            "className": "msg-text",
            "text": "hello",
            "toolName": "",
            "toolCallId": "",
            "idleCursor": "",
        },
        {
            "className": "",
            "text": "",
            "toolName": "shell",
            "toolCallId": "call-1",
            "idleCursor": "",
        },
        {
            "className": "msg-text",
            "text": "",
            "toolName": "",
            "toolCallId": "",
            "idleCursor": "true",
        },
    ]


def test_idle_cursor_rebind_resets_live_buffer_before_next_text_delta(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/stream.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "stream_idle_rebind_text_delta"
    temp_dir.mkdir()

    (temp_dir / "stream.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId() {
    return "";
}

export function isPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(_key, values = {}) {
    return JSON.stringify(values);
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
function createTextNode(text = "", idle = false) {
  return {
    className: "msg-text",
    dataset: idle ? { idleCursor: "true" } : {},
    __idleCursor: idle,
    __text: String(text || ""),
    __streaming: false,
    __parent: null,
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    remove() {
      const parent = this.__parent || null;
      if (!parent || !Array.isArray(parent.children)) return;
      const index = parent.children.indexOf(this);
      if (index >= 0) parent.children.splice(index, 1);
    },
  };
}

export function applyToolReturn() {}
export function appendStructuredContentPart() {}
export function appendThinkingText() { return {}; }
export function buildPendingToolBlock() { return { querySelector() { return null; } }; }
export function findToolBlock() { return null; }
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function renderMessageBlock() {
  return {
    wrapper: {
      dataset: {},
      querySelector() { return null; },
      closest() { return null; },
    },
    contentEl: {
      children: [],
      appendChild(child) {
        child.__parent = this;
        this.children.push(child);
      },
      querySelector() { return null; },
      querySelectorAll(selector) {
        if (selector === ".msg-text") {
          return this.children.filter(child => child?.className === "msg-text");
        }
        return [];
      },
    },
  };
}
export function resolvePendingToolBlock() { return null; }
export function scrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor() {}
export function updateThinkingText() {}
export function updateMessageText(textEl, text, options = {}) {
  textEl.__text = String(text || "");
  textEl.__streaming = options.streaming === true;
}

globalThis.__createTextNode = createTextNode;
""".strip(),
        encoding="utf-8",
    )

    runner = """
globalThis.document = {
  createElement() {
    return globalThis.__createTextNode();
  },
};

import {
  applyStreamOverlayEvent,
  appendStreamChunk,
  bindStreamOverlayToContainer,
} from "./stream.js";

const contentEl = {
  children: [
    globalThis.__createTextNode("hello", false),
    globalThis.__createTextNode("", true),
  ],
  appendChild(child) {
    child.__parent = this;
    this.children.push(child);
  },
  querySelector() { return null; },
  querySelectorAll(selector) {
    if (selector === ".msg-text") {
      return this.children.filter(child => child?.className === "msg-text");
    }
    return [];
  },
};
contentEl.children.forEach(child => {
  child.__parent = contentEl;
});

const wrapper = {
  dataset: {
    runId: "run-2",
    roleId: "Writer",
    instanceId: "inst-2",
    streamKey: "inst-2",
  },
  querySelector(selector) {
    if (selector === ".msg-role") {
      return { textContent: "WRITER" };
    }
    if (selector === ".msg-content") {
      return contentEl;
    }
    return null;
  },
  closest() { return null; },
};

const container = {
  __messages: [{ wrapper, contentEl }],
  querySelectorAll() {
    return this.__messages.map(item => item.wrapper);
  },
};

applyStreamOverlayEvent(
  "text_delta",
  { text: "hello" },
  {
    runId: "run-2",
    instanceId: "inst-2",
    roleId: "Writer",
    label: "Writer",
  },
);
applyStreamOverlayEvent(
  "thinking_started",
  { part_index: 0 },
  {
    runId: "run-2",
    instanceId: "inst-2",
    roleId: "Writer",
    label: "Writer",
  },
);
applyStreamOverlayEvent(
  "thinking_finished",
  { part_index: 0 },
  {
    runId: "run-2",
    instanceId: "inst-2",
    roleId: "Writer",
    label: "Writer",
  },
);

bindStreamOverlayToContainer(container, {
  instanceId: "inst-2",
  roleId: "Writer",
  label: "Writer",
  runId: "run-2",
});
appendStreamChunk("inst-2", " world", "run-2", "Writer", "Writer");

console.log(JSON.stringify(contentEl.children.map(child => ({
  text: String(child.__text || ""),
  idleCursor: String(child?.dataset?.idleCursor || ""),
  idleFlag: child.__idleCursor === true,
}))));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload == [
        {
            "text": "hello",
            "idleCursor": "",
            "idleFlag": False,
        },
        {
            "text": " world",
            "idleCursor": "",
            "idleFlag": False,
        },
    ]


def test_finalize_stream_keeps_real_text_tail_when_overlay_idle_cursor_drifts(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/messageRenderer/stream.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "stream_finalize_real_text_tail"
    temp_dir.mkdir()

    (temp_dir / "stream.js").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId() {
    return "";
}

export function isPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function formatMessage(_key, values = {}) {
    return JSON.stringify(values);
}

export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockHelpers.mjs").write_text(
        """
function createTextNode(text = "") {
  return {
    className: "msg-text",
    dataset: {},
    __text: String(text || ""),
    __streaming: false,
    __idleCursor: false,
    __parent: null,
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    remove() {
      const parent = this.__parent || null;
      if (!parent || !Array.isArray(parent.children)) return;
      const index = parent.children.indexOf(this);
      if (index >= 0) parent.children.splice(index, 1);
    },
  };
}

export function applyToolReturn() {}
export function appendStructuredContentPart() {}
export function appendThinkingText() { return {}; }
export function buildPendingToolBlock() { return { querySelector() { return null; } }; }
export function findToolBlock() { return null; }
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function renderMessageBlock() {
  return {
    wrapper: {
      dataset: {},
      querySelector() { return null; },
      closest() { return null; },
    },
    contentEl: {
      children: [],
      appendChild(child) {
        child.__parent = this;
        this.children.push(child);
      },
      querySelector() { return null; },
      querySelectorAll(selector) {
        if (selector === ".msg-text") {
          return this.children.filter(child => child?.className === "msg-text");
        }
        return [];
      },
    },
  };
}
export function resolvePendingToolBlock() { return null; }
export function scrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor(_textEl, active) {
  globalThis.__cursorStates.push(active === true);
}
export function updateThinkingText() {}
export function updateMessageText(textEl, text, options = {}) {
  textEl.__text = String(text || "");
  textEl.__streaming = options.streaming === true;
}

globalThis.__createTextNode = createTextNode;
""".strip(),
        encoding="utf-8",
    )

    runner = """
globalThis.__cursorStates = [];
globalThis.document = {
  createElement() {
    return globalThis.__createTextNode();
  },
};

import {
  applyStreamOverlayEvent,
  bindStreamOverlayToContainer,
  finalizeStream,
} from "./stream.js";

const textNode = globalThis.__createTextNode("hello");
const contentEl = {
  children: [textNode],
  appendChild(child) {
    child.__parent = this;
    this.children.push(child);
  },
  querySelector() { return null; },
  querySelectorAll(selector) {
    if (selector === ".msg-text") {
      return this.children.filter(child => child?.className === "msg-text");
    }
    return [];
  },
};
textNode.__parent = contentEl;

const wrapper = {
  dataset: {
    runId: "run-3",
    roleId: "Writer",
    instanceId: "inst-3",
    streamKey: "inst-3",
  },
  querySelector(selector) {
    if (selector === ".msg-role") {
      return { textContent: "WRITER" };
    }
    if (selector === ".msg-content") {
      return contentEl;
    }
    return null;
  },
  closest() { return null; },
};

const container = {
  __messages: [{ wrapper, contentEl }],
  querySelectorAll() {
    return this.__messages.map(item => item.wrapper);
  },
};

applyStreamOverlayEvent(
  "text_delta",
  { text: "hello" },
  {
    runId: "run-3",
    instanceId: "inst-3",
    roleId: "Writer",
    label: "Writer",
  },
);
applyStreamOverlayEvent(
  "tool_result",
  {
    tool_name: "shell",
    tool_call_id: "call-3",
    result: { ok: true, data: "done" },
  },
  {
    runId: "run-3",
    instanceId: "inst-3",
    roleId: "Writer",
    label: "Writer",
  },
);

bindStreamOverlayToContainer(container, {
  instanceId: "inst-3",
  roleId: "Writer",
  label: "Writer",
  runId: "run-3",
});
finalizeStream("inst-3", "Writer", { runId: "run-3" });

console.log(JSON.stringify({
  children: contentEl.children.map(child => ({
    text: String(child.__text || ""),
    idleCursor: String(child?.dataset?.idleCursor || ""),
  })),
  cursorStates: globalThis.__cursorStates,
}));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload == {
        "children": [
            {
                "text": "hello",
                "idleCursor": "",
            }
        ],
        "cursorStates": [True],
    }
