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
            }
        },
    }
