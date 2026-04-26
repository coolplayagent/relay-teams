# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_message_timeline_scopes_and_deduplicates_stream_events() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    runner = """
import {
  applyRunEventToTimeline,
} from './frontend/dist/js/components/messageTimeline/actions.js';
import {
  clearTimelineState,
  getRunTimelineSnapshot,
} from './frontend/dist/js/components/messageTimeline/store.js';

clearTimelineState();

applyRunEventToTimeline(
  'text_delta',
  { text: 'hello', instance_id: 'inst-orch', role_id: 'writer' },
  { event_id: 1, run_id: 'run-parent' },
  { view: 'orchestration-panel' },
);
applyRunEventToTimeline(
  'text_delta',
  { text: 'hello', instance_id: 'inst-orch', role_id: 'writer' },
  { event_id: 1, run_id: 'run-parent' },
  { view: 'orchestration-panel' },
);
applyRunEventToTimeline(
  'tool_call',
  {
    tool_name: 'shell',
    tool_call_id: 'call-1',
    args: { command: 'echo ok' },
    instance_id: 'inst-child',
    role_id: 'runner',
  },
  { event_id: 2, run_id: 'subagent_run_1' },
  { view: 'normal-child-session' },
);

console.log(JSON.stringify({
  orchestration: getRunTimelineSnapshot('run-parent').byInstance['inst-orch'],
  normalChild: getRunTimelineSnapshot('subagent_run_1').byInstance['inst-child'],
}));
"""

    completed = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )

    payload = json.loads(completed.stdout)
    orchestration = payload["orchestration"]
    normal_child = payload["normalChild"]

    assert orchestration["scope"]["view"] == "orchestration-panel"
    assert orchestration["parts"] == [
        {
            "id": "session::run-parent::orchestration-panel::inst-orch::text::1",
            "kind": "text",
            "content": "hello",
            "streaming": True,
            "updatedAt": orchestration["parts"][0]["updatedAt"],
        }
    ]
    assert normal_child["scope"]["view"] == "normal-child-session"
    assert normal_child["parts"][0]["kind"] == "tool"
    assert normal_child["parts"][0]["tool_call_id"] == "call-1"


def test_message_timeline_gives_disconnected_text_without_event_ids_unique_ids() -> (
    None
):
    repo_root = Path(__file__).resolve().parents[3]
    runner = """
import {
  applyRunEventToTimeline,
} from './frontend/dist/js/components/messageTimeline/actions.js';
import {
  clearTimelineState,
  getRunTimelineSnapshot,
} from './frontend/dist/js/components/messageTimeline/store.js';

clearTimelineState();

applyRunEventToTimeline('text_delta', { text: 'first' }, { run_id: 'run-1' });
applyRunEventToTimeline(
  'tool_call',
  { tool_name: 'shell', tool_call_id: 'call-1', args: { command: 'pwd' } },
  { run_id: 'run-1' },
);
applyRunEventToTimeline('text_delta', { text: 'second' }, { run_id: 'run-1' });

console.log(JSON.stringify(getRunTimelineSnapshot('run-1').coordinator.parts));
""".strip()

    completed = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )

    parts = json.loads(completed.stdout)
    assert [part["kind"] for part in parts] == ["text", "tool", "text"]
    assert parts[0]["id"].endswith("::text::text-0")
    assert parts[2]["id"].endswith("::text::text-1")
    assert parts[0]["id"] != parts[2]["id"]


def test_message_timeline_gives_repeated_thinking_parts_unique_event_ids() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    runner = """
import {
  applyRunEventToTimeline,
} from './frontend/dist/js/components/messageTimeline/actions.js';
import {
  clearTimelineState,
  getRunTimelineSnapshot,
} from './frontend/dist/js/components/messageTimeline/store.js';

clearTimelineState();

applyRunEventToTimeline(
  'thinking_started',
  { part_index: 0 },
  { run_id: 'run-1', event_id: 1 },
);
applyRunEventToTimeline(
  'thinking_delta',
  { part_index: 0, text: 'first thought' },
  { run_id: 'run-1', event_id: 2 },
);
applyRunEventToTimeline(
  'thinking_finished',
  { part_index: 0 },
  { run_id: 'run-1', event_id: 3 },
);
applyRunEventToTimeline(
  'thinking_started',
  { part_index: 0 },
  { run_id: 'run-1', event_id: 4 },
);
applyRunEventToTimeline(
  'thinking_delta',
  { part_index: 0, text: 'second thought' },
  { run_id: 'run-1', event_id: 5 },
);
applyRunEventToTimeline(
  'thinking_finished',
  { part_index: 0 },
  { run_id: 'run-1', event_id: 6 },
);

console.log(JSON.stringify(getRunTimelineSnapshot('run-1').coordinator.parts));
""".strip()

    completed = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )

    parts = json.loads(completed.stdout)
    assert [part["kind"] for part in parts] == ["thinking", "thinking"]
    assert [part["content"] for part in parts] == ["first thought", "second thought"]
    assert parts[0]["id"] != parts[1]["id"]
    assert parts[0]["id"].endswith("::thinking::1")
    assert parts[1]["id"].endswith("::thinking::4")


def test_message_timeline_gives_media_parts_without_event_ids_unique_ids() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    runner = """
import {
  applyRunEventToTimeline,
} from './frontend/dist/js/components/messageTimeline/actions.js';
import {
  clearTimelineState,
  getRunTimelineSnapshot,
} from './frontend/dist/js/components/messageTimeline/store.js';

clearTimelineState();

applyRunEventToTimeline(
  'output_delta',
  { output: [{ kind: 'media_ref', url: '/first.png' }] },
  { run_id: 'run-1' },
);
applyRunEventToTimeline(
  'output_delta',
  { output: [{ kind: 'media_ref', url: '/second.png' }] },
  { run_id: 'run-1' },
);

console.log(JSON.stringify(getRunTimelineSnapshot('run-1').coordinator.parts));
""".strip()

    completed = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )

    parts = json.loads(completed.stdout)
    assert [part["kind"] for part in parts] == ["media_ref", "media_ref"]
    assert [part["url"] for part in parts] == ["/first.png", "/second.png"]
    assert parts[0]["id"].endswith("::media_ref::media-0")
    assert parts[1]["id"].endswith("::media_ref::media-1")
    assert parts[0]["id"] != parts[1]["id"]


def test_message_timeline_keeps_completed_tool_status_when_call_arrives_late() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    runner = """
import {
  applyRunEventToTimeline,
} from './frontend/dist/js/components/messageTimeline/actions.js';
import {
  clearTimelineState,
  getRunTimelineSnapshot,
} from './frontend/dist/js/components/messageTimeline/store.js';

clearTimelineState();

applyRunEventToTimeline(
  'tool_result',
  {
    tool_name: 'shell',
    tool_call_id: 'call-b',
    result: { ok: true, output: 'done' },
  },
  { run_id: 'run-1', event_id: 2 },
);
applyRunEventToTimeline(
  'tool_call',
  {
    tool_name: 'shell',
    tool_call_id: 'call-b',
    args: { command: 'echo b' },
  },
  { run_id: 'run-1', event_id: 1 },
);

console.log(JSON.stringify(getRunTimelineSnapshot('run-1').coordinator.parts));
""".strip()

    completed = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )

    parts = json.loads(completed.stdout)
    assert len(parts) == 1
    assert parts[0]["tool_call_id"] == "call-b"
    assert parts[0]["status"] == "completed"
    assert parts[0]["args"] == {"command": "echo b"}


def test_message_timeline_normalizes_string_tool_args_for_live_and_hydrated_parts() -> (
    None
):
    repo_root = Path(__file__).resolve().parents[3]
    runner = """
import {
  applyRunEventToTimeline,
} from './frontend/dist/js/components/messageTimeline/actions.js';
import {
  applyTimelineAction,
  clearTimelineState,
  getRunTimelineSnapshot,
} from './frontend/dist/js/components/messageTimeline/store.js';

clearTimelineState();

applyRunEventToTimeline(
  'tool_call',
  {
    tool_name: 'websearch',
    tool_call_id: 'call-live',
    args: '{"query":"Anthropic funding 2026"}',
  },
  { run_id: 'run-live', event_id: 1 },
);
applyRunEventToTimeline(
  'tool_result',
  {
    tool_name: 'websearch',
    tool_call_id: 'call-late',
    result: { ok: true, output: 'done' },
  },
  { run_id: 'run-late', event_id: 1 },
);
applyRunEventToTimeline(
  'tool_call',
  {
    tool_name: 'websearch',
    tool_call_id: 'call-late',
    args: '{"query":"Anthropic model release"}',
  },
  { run_id: 'run-late', event_id: 2 },
);
applyTimelineAction({
  type: 'hydrate_parts',
  scope: { runId: 'run-history', streamKey: 'primary', view: 'main' },
  parts: [{
    kind: 'tool',
    tool_name: 'websearch',
    tool_call_id: 'call-history',
    args: '{"query":"Anthropic safety policy"}',
    status: 'pending',
  }],
});
applyRunEventToTimeline(
  'tool_call',
  {
    tool_name: 'batch',
    tool_call_id: 'call-array',
    args: '["one","two"]',
  },
  { run_id: 'run-array', event_id: 1 },
);
applyRunEventToTimeline(
  'tool_call',
  {
    tool_name: 'raw',
    tool_call_id: 'call-raw',
    args: 'not json',
  },
  { run_id: 'run-raw', event_id: 1 },
);

console.log(JSON.stringify({
  live: getRunTimelineSnapshot('run-live').coordinator.parts[0],
  late: getRunTimelineSnapshot('run-late').coordinator.parts[0],
  hydrated: getRunTimelineSnapshot('run-history').coordinator.parts[0],
  arrayArgs: getRunTimelineSnapshot('run-array').coordinator.parts[0].args,
  rawArgs: getRunTimelineSnapshot('run-raw').coordinator.parts[0].args,
}));
""".strip()

    completed = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )

    payload = json.loads(completed.stdout)
    assert payload["live"]["args"] == {"query": "Anthropic funding 2026"}
    assert payload["late"]["status"] == "completed"
    assert payload["late"]["args"] == {"query": "Anthropic model release"}
    assert payload["hydrated"]["args"] == {"query": "Anthropic safety policy"}
    assert payload["arrayArgs"] == {"__items": ["one", "two"]}
    assert payload["rawArgs"] == {"__raw": "not json"}
