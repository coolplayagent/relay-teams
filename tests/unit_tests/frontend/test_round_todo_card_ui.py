# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_round_nav_renders_timeline_without_numeric_indices(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    {
        run_id: 'run-1',
        intent: 'Inspect issue',
        created_at: '2026-04-25T02:35:38',
    },
    {
        run_id: 'run-2',
        intent: 'Implement feature',
        created_at: '2026-04-25T02:36:26',
        run_status: 'completed',
    },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-2' });

const nav = document.getElementById('round-nav-float');
const run2Node = nav.querySelector('.round-nav-node[data-run-id="run-2"]');

console.log(JSON.stringify({
    hostClass: document.querySelector('.chat-container').className,
    navParentClass: nav.parentNode?.className || null,
    nodeCount: nav.querySelectorAll('.round-nav-node').length,
    activeRunId: nav.querySelector('.round-nav-item.active')?.dataset?.runId || null,
    hasNumericIndex: nav.querySelector('.idx') !== null,
    hasTimelineDot: run2Node?.querySelector('.round-nav-dot') !== null,
    stateTone: run2Node?.dataset?.stateTone || null,
    dotTitle: run2Node?.querySelector('.round-nav-dot')?.title || null,
    dotAriaLabel: run2Node?.querySelector('.round-nav-dot')?.getAttribute('aria-label') || null,
    hasOrdinaryStateMeta: run2Node?.querySelector('.round-nav-meta') !== null,
    timeText: run2Node?.querySelector('.round-nav-time')?.textContent || null,
    previewText: run2Node?.querySelector('.txt')?.textContent || null,
    inlineDetailCount: nav.querySelectorAll('.round-nav-detail').length,
    activeDetail: run2Node?.querySelector('.round-nav-detail') !== null,
}));""".strip(),
    )

    assert payload == {
        "hostClass": "chat-container rounds-timeline-visible",
        "navParentClass": "chat-container rounds-timeline-visible",
        "nodeCount": 2,
        "activeRunId": "run-2",
        "hasNumericIndex": False,
        "hasTimelineDot": True,
        "stateTone": "success",
        "dotTitle": "rounds.state.completed",
        "dotAriaLabel": "rounds.state.completed",
        "hasOrdinaryStateMeta": False,
        "timeText": "02:36:26",
        "previewText": "Implement feature",
        "inlineDetailCount": 2,
        "activeDetail": True,
    }


def test_round_nav_renders_todo_in_timeline_detail(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    {
        run_id: 'run-1',
        intent: 'Inspect issue',
        todo: {
            run_id: 'run-1',
            items: [{ content: 'First task', status: 'pending' }],
        },
    },
    {
        run_id: 'run-2',
        intent: 'Implement feature',
        todo: {
            run_id: 'run-2',
            items: [
                { content: 'Second task', status: 'in_progress' },
                { content: 'Verify branch', status: 'completed' },
            ],
        },
    },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-2' });

const nav = document.getElementById('round-nav-float');
const run1Node = nav.querySelector('.round-nav-node[data-run-id="run-1"]');
const run2Node = nav.querySelector('.round-nav-node[data-run-id="run-2"]');

console.log(JSON.stringify({
    oldTodoBranchCount: nav.querySelectorAll('.round-nav-todo-branch').length,
    oldTodoCardCount: nav.querySelectorAll('.round-todo-card').length,
    run1HasTodo: run1Node?.querySelector('.round-nav-todo') !== null,
    run2HasTodo: run2Node?.querySelector('.round-nav-todo') !== null,
    run2HasTodoClass: run2Node?.className.includes('has-todo') || false,
    todoItemCount: run2Node?.querySelectorAll('.round-nav-todo-item').length || 0,
    firstTodoTitle: run2Node?.querySelector('.round-nav-todo-text')?.title || null,
    firstTodoStatus: run2Node?.querySelector('.round-nav-todo-status')?.textContent || null,
}));""".strip(),
    )

    assert payload == {
        "oldTodoBranchCount": 0,
        "oldTodoCardCount": 0,
        "run1HasTodo": True,
        "run2HasTodo": True,
        "run2HasTodoClass": True,
        "todoItemCount": 2,
        "firstTodoTitle": "Second task",
        "firstTodoStatus": "rounds.todo.status.in_progress",
    }


def test_round_nav_patches_todo_without_rebuilding_list(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    {
        run_id: 'run-1',
        intent: 'Inspect issue',
        todo: {
            run_id: 'run-1',
            items: [
                { content: 'Initial task', status: 'pending' },
                { content: 'Verify branch', status: 'pending' },
            ],
        },
    },
    {
        run_id: 'run-2',
        intent: 'Implement feature',
    },
];

const { patchRoundNavigatorTodo, renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const list = nav.querySelector('.round-nav-list');
const run1Node = nav.querySelector('.round-nav-node[data-run-id="run-1"]');
const detail = run1Node.querySelector('.round-nav-detail');
const todoEl = run1Node.querySelector('.round-nav-todo');
const beforeTransform = run1Node.style.transform;

const patched = patchRoundNavigatorTodo('run-1', {
    run_id: 'run-1',
    items: [
        { content: 'Updated task', status: 'completed' },
        { content: 'Verify branch', status: 'pending' },
    ],
});

const nextList = nav.querySelector('.round-nav-list');
const nextRun1Node = nav.querySelector('.round-nav-node[data-run-id="run-1"]');

console.log(JSON.stringify({
    patched,
    listPreserved: nextList === list,
    nodePreserved: nextRun1Node === run1Node,
    detailPreserved: nextRun1Node?.querySelector('.round-nav-detail') === detail,
    todoPreserved: nextRun1Node?.querySelector('.round-nav-todo') === todoEl,
    transformStable: (nextRun1Node?.style?.transform || '') === (beforeTransform || ''),
    hasTodoClass: nextRun1Node?.className.includes('has-todo') || false,
    todoItemCount: nextRun1Node?.querySelectorAll('.round-nav-todo-item').length || 0,
    firstTodoTitle: nextRun1Node?.querySelector('.round-nav-todo-text')?.title || null,
    firstTodoStatus: nextRun1Node?.querySelector('.round-nav-todo-status')?.textContent || null,
}));""".strip(),
    )

    assert payload == {
        "patched": True,
        "listPreserved": True,
        "nodePreserved": True,
        "detailPreserved": True,
        "todoPreserved": True,
        "transformStable": True,
        "hasTodoClass": True,
        "todoItemCount": 2,
        "firstTodoTitle": "Updated task",
        "firstTodoStatus": "rounds.todo.status.completed",
    }


def test_round_nav_todo_patch_returns_false_when_node_missing(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    { run_id: 'run-1', intent: 'Inspect issue' },
];

const { patchRoundNavigatorTodo, renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

console.log(JSON.stringify({
    patched: patchRoundNavigatorTodo('missing-run', {
        run_id: 'missing-run',
        items: [{ content: 'Should not render', status: 'pending' }],
    }),
    nodeCount: document.getElementById('round-nav-float')?.querySelectorAll('.round-nav-node').length || 0,
}));""".strip(),
    )

    assert payload == {
        "patched": False,
        "nodeCount": 1,
    }


def test_set_active_round_nav_keeps_todo_details_available(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    {
        run_id: 'run-1',
        intent: 'Inspect issue',
        todo: {
            run_id: 'run-1',
            items: [{ content: 'First task', status: 'pending' }],
        },
    },
    {
        run_id: 'run-2',
        intent: 'Implement feature',
        todo: {
            run_id: 'run-2',
            items: [{ content: 'Second task', status: 'in_progress' }],
        },
    },
];

const { renderRoundNavigator, setActiveRoundNav } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-2' });
setActiveRoundNav('run-1');

const nav = document.getElementById('round-nav-float');
const run1Node = nav.querySelector('.round-nav-node[data-run-id="run-1"]');
const run2Node = nav.querySelector('.round-nav-node[data-run-id="run-2"]');

console.log(JSON.stringify({
    activeRunId: nav.querySelector('.round-nav-item.active')?.dataset?.runId || null,
    run1Active: run1Node?.className.includes('active') || false,
    run2Active: run2Node?.className.includes('active') || false,
    run1HasDetail: run1Node?.querySelector('.round-nav-detail') !== null,
    run2HasDetail: run2Node?.querySelector('.round-nav-detail') !== null,
    run1HasTodo: run1Node?.querySelector('.round-nav-todo') !== null,
    run2HasTodo: run2Node?.querySelector('.round-nav-todo') !== null,
}));""".strip(),
    )

    assert payload == {
        "activeRunId": "run-1",
        "run1Active": True,
        "run2Active": False,
        "run1HasDetail": True,
        "run2HasDetail": True,
        "run1HasTodo": True,
        "run2HasTodo": True,
    }


def test_round_nav_click_selects_round(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    { run_id: 'run-1', intent: 'Inspect issue' },
    { run_id: 'run-2', intent: 'Implement feature' },
];
let selectedRunId = null;

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, round => {
    selectedRunId = round.run_id;
}, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const item = nav.querySelector('.round-nav-item[data-run-id="run-2"]');
item.onclick();

console.log(JSON.stringify({
    selectedRunId,
    activeRunId: nav.querySelector('.round-nav-item.active')?.dataset?.runId || null,
}));""".strip(),
    )

    assert payload == {
        "selectedRunId": "run-2",
        "activeRunId": "run-2",
    }


def test_round_nav_rerender_preserves_list_scroll_top(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    { run_id: 'run-1', intent: 'Inspect issue' },
    { run_id: 'run-2', intent: 'Implement feature' },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const list = nav.querySelector('.round-nav-list');
list.scrollTop = 96;

renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

console.log(JSON.stringify({
    scrollTop: nav.querySelector('.round-nav-list')?.scrollTop || 0,
}));""".strip(),
    )

    assert payload == {
        "scrollTop": 96,
    }


def test_round_nav_nodes_keep_order_while_tracking_anchor_state(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const section1 = document.createElement('section');
section1.className = 'session-round-section';
section1.dataset.runId = 'run-1';
section1.__rect = { left: 0, top: -180, width: 900, height: 120, right: 900, bottom: -60 };
const header1 = document.createElement('div');
header1.className = 'round-detail-header';
header1.__rect = { left: 320, top: -180, width: 900, height: 70, right: 1220, bottom: -110 };
section1.appendChild(header1);

const section2 = document.createElement('section');
section2.className = 'session-round-section';
section2.dataset.runId = 'run-2';
section2.__rect = { left: 0, top: 260, width: 900, height: 120, right: 900, bottom: 380 };
const header2 = document.createElement('div');
header2.className = 'round-detail-header';
header2.__rect = { left: 320, top: 260, width: 900, height: 70, right: 1220, bottom: 330 };
section2.appendChild(header2);

const section3 = document.createElement('section');
section3.className = 'session-round-section';
section3.dataset.runId = 'run-3';
section3.__rect = { left: 0, top: 980, width: 900, height: 120, right: 900, bottom: 1100 };
const header3 = document.createElement('div');
header3.className = 'round-detail-header';
header3.__rect = { left: 320, top: 980, width: 900, height: 70, right: 1220, bottom: 1050 };
section3.appendChild(header3);

chatScroll.appendChild(section1);
chatScroll.appendChild(section2);
chatScroll.appendChild(section3);

const rounds = [
    { run_id: 'run-1', intent: 'Already above' },
    { run_id: 'run-2', intent: 'Visible now' },
    { run_id: 'run-3', intent: 'Below later' },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-2' });

const nav = document.getElementById('round-nav-float');
const run1Node = nav.querySelector('.round-nav-node[data-run-id="run-1"]');
const run2Node = nav.querySelector('.round-nav-node[data-run-id="run-2"]');
const run3Node = nav.querySelector('.round-nav-node[data-run-id="run-3"]');

console.log(JSON.stringify({
    run1State: run1Node?.dataset?.anchorState || null,
    run2State: run2Node?.dataset?.anchorState || null,
    run3State: run3Node?.dataset?.anchorState || null,
    visibleRunIds: Array.from(nav.querySelectorAll('.round-nav-node'))
        .filter(node => node.dataset.roundNavHidden !== 'true')
        .map(node => node.dataset.runId),
    usesTransform: Array.from(nav.querySelectorAll('.round-nav-node'))
        .some(node => Boolean(node.style.transform)),
}));""".strip(),
    )

    assert payload == {
        "run1State": "above",
        "run2State": "visible",
        "run3State": "below",
        "visibleRunIds": ["run-1", "run-2", "run-3"],
        "usesTransform": False,
    }


def test_round_nav_hover_detail_keeps_window_stable(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
['run-1', 'run-2', 'run-3'].forEach((runId, index) => {
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const top = 100 + (index * 40);
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    header.__rect = { left: 320, top, width: 900, height: 24, right: 1220, bottom: top + 24 };
    section.appendChild(header);
    chatScroll.appendChild(section);
});

const rounds = [
    { run_id: 'run-1', intent: 'First' },
    { run_id: 'run-2', intent: 'Second' },
    { run_id: 'run-3', intent: 'Third' },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const run2Node = nav.querySelector('.round-nav-node[data-run-id="run-2"]');
const beforeVisible = Array.from(nav.querySelectorAll('.round-nav-node'))
    .filter(node => node.dataset.roundNavHidden !== 'true')
    .map(node => node.dataset.runId);

run2Node.__rect = { left: 1048, top: 22, width: 216, height: 120, right: 1264, bottom: 142 };
run2Node.offsetHeight = 120;
run2Node.dispatch('pointerenter');

console.log(JSON.stringify({
    beforeVisible,
    afterVisible: Array.from(nav.querySelectorAll('.round-nav-node'))
        .filter(node => node.dataset.roundNavHidden !== 'true')
        .map(node => node.dataset.runId),
    usesTransform: Array.from(nav.querySelectorAll('.round-nav-node'))
        .some(node => Boolean(node.style.transform)),
}));""".strip(),
    )

    assert payload == {
        "beforeVisible": ["run-1", "run-2", "run-3"],
        "afterVisible": ["run-1", "run-2", "run-3"],
        "usesTransform": False,
    }


def test_round_nav_hover_detail_collapse_restores_same_window(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
['run-1', 'run-2', 'run-3'].forEach((runId, index) => {
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const top = 100 + (index * 40);
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    header.__rect = { left: 320, top, width: 900, height: 24, right: 1220, bottom: top + 24 };
    section.appendChild(header);
    chatScroll.appendChild(section);
});

const rounds = [
    { run_id: 'run-1', intent: 'First' },
    { run_id: 'run-2', intent: 'Second' },
    { run_id: 'run-3', intent: 'Third' },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const run2Node = nav.querySelector('.round-nav-node[data-run-id="run-2"]');
const before = Array.from(nav.querySelectorAll('.round-nav-node'))
    .filter(node => node.dataset.roundNavHidden !== 'true')
    .map(node => node.dataset.runId);

run2Node.__rect = { left: 1048, top: 22, width: 216, height: 120, right: 1264, bottom: 142 };
run2Node.offsetHeight = 120;
run2Node.dispatch('pointerenter');
const expanded = Array.from(nav.querySelectorAll('.round-nav-node'))
    .filter(node => node.dataset.roundNavHidden !== 'true')
    .map(node => node.dataset.runId);

delete run2Node.__rect;
run2Node.offsetHeight = 32;
run2Node.dispatch('pointerleave');

console.log(JSON.stringify({
    before,
    expanded,
    restored: Array.from(nav.querySelectorAll('.round-nav-node'))
        .filter(node => node.dataset.roundNavHidden !== 'true')
        .map(node => node.dataset.runId),
}));""".strip(),
    )

    assert payload == {
        "before": ["run-1", "run-2", "run-3"],
        "expanded": ["run-1", "run-2", "run-3"],
        "restored": ["run-1", "run-2", "run-3"],
    }


def test_round_nav_missing_anchor_rounds_use_ordered_positions(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
['run-4', 'run-5'].forEach((runId, index) => {
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const top = 260 + (index * 80);
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    header.__rect = { left: 320, top, width: 900, height: 30, right: 1220, bottom: top + 30 };
    section.appendChild(header);
    chatScroll.appendChild(section);
});

const rounds = [
    { run_id: 'run-1', intent: 'Unloaded first' },
    { run_id: 'run-2', intent: 'Unloaded second' },
    { run_id: 'run-3', intent: 'Unloaded third' },
    { run_id: 'run-4', intent: 'Loaded fourth' },
    { run_id: 'run-5', intent: 'Loaded fifth' },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-4' });

const nav = document.getElementById('round-nav-float');
const states = {};
['run-1', 'run-2', 'run-3', 'run-4', 'run-5'].forEach(runId => {
    const node = nav.querySelector(`.round-nav-node[data-run-id="${runId}"]`);
    states[runId] = node.dataset.anchorState;
});

console.log(JSON.stringify({
    states,
    visibleRunIds: Array.from(nav.querySelectorAll('.round-nav-node'))
        .filter(node => node.dataset.roundNavHidden !== 'true')
        .map(node => node.dataset.runId),
}));""".strip(),
    )

    assert payload == {
        "states": {
            "run-1": "unloaded",
            "run-2": "unloaded",
            "run-3": "unloaded",
            "run-4": "visible",
            "run-5": "visible",
        },
        "visibleRunIds": ["run-1", "run-2", "run-3", "run-4", "run-5"],
    }


def test_round_nav_all_missing_anchor_rounds_start_at_top(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    { run_id: 'run-1', intent: 'Unloaded first' },
    { run_id: 'run-2', intent: 'Unloaded second' },
    { run_id: 'run-3', intent: 'Unloaded third' },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const states = {};
['run-1', 'run-2', 'run-3'].forEach(runId => {
    const node = nav.querySelector(`.round-nav-node[data-run-id="${runId}"]`);
    states[runId] = node.dataset.anchorState;
});

console.log(JSON.stringify({
    states,
    visibleRunIds: Array.from(nav.querySelectorAll('.round-nav-node'))
        .filter(node => node.dataset.roundNavHidden !== 'true')
        .map(node => node.dataset.runId),
}));""".strip(),
    )

    assert payload == {
        "states": {
            "run-1": "unloaded",
            "run-2": "unloaded",
            "run-3": "unloaded",
        },
        "visibleRunIds": ["run-1", "run-2", "run-3"],
    }


def test_round_nav_below_rounds_keep_ordered_single_page_positions(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
['run-1', 'run-2', 'run-3', 'run-4', 'run-5'].forEach(runId => {
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    header.__rect = { left: 320, top: 980, width: 900, height: 30, right: 1220, bottom: 1010 };
    section.appendChild(header);
    chatScroll.appendChild(section);
});

const rounds = [
    { run_id: 'run-1', intent: 'Below first' },
    { run_id: 'run-2', intent: 'Below second' },
    { run_id: 'run-3', intent: 'Below third' },
    { run_id: 'run-4', intent: 'Below fourth' },
    { run_id: 'run-5', intent: 'Below fifth' },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-5' });

const nav = document.getElementById('round-nav-float');
console.log(JSON.stringify({
    visibleRunIds: Array.from(nav.querySelectorAll('.round-nav-node'))
        .filter(node => node.dataset.roundNavHidden !== 'true')
        .map(node => node.dataset.runId),
    usesTransform: Array.from(nav.querySelectorAll('.round-nav-node'))
        .some(node => Boolean(node.style.transform)),
}));""".strip(),
    )

    assert payload == {
        "visibleRunIds": ["run-1", "run-2", "run-3", "run-4", "run-5"],
        "usesTransform": False,
    }


def test_round_nav_overflowing_below_rounds_scroll_list_without_hiding_nodes(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [];
for (let index = 1; index <= 20; index += 1) {
    const runId = `run-${index}`;
    rounds.push({ run_id: runId, intent: `Below ${index}` });
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    header.__rect = { left: 320, top: 980, width: 900, height: 30, right: 1220, bottom: 1010 };
    section.appendChild(header);
    chatScroll.appendChild(section);
}

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, {
    activeRunId: 'run-20',
    layoutReason: 'new-latest',
});

const nav = document.getElementById('round-nav-float');
const list = nav.querySelector('.round-nav-list');
const visibleRunIds = [];
for (let index = 1; index <= 20; index += 1) {
    const runId = `run-${index}`;
    const node = nav.querySelector(`.round-nav-node[data-run-id="${runId}"]`);
    if (node?.dataset?.roundNavHidden !== 'true') {
        visibleRunIds.push(runId);
    }
}

console.log(JSON.stringify({
    scrollTop: list.scrollTop,
    visibleRunIds,
    firstHidden: nav.querySelector('.round-nav-node[data-run-id="run-1"]').dataset.roundNavHidden,
    lastHidden: nav.querySelector('.round-nav-node[data-run-id="run-20"]').dataset.roundNavHidden,
}));""".strip(),
    )

    visible_run_ids = payload["visibleRunIds"]
    assert isinstance(visible_run_ids, list)
    scroll_top = payload["scrollTop"]
    assert isinstance(scroll_top, int | float)
    assert scroll_top > 0
    assert visible_run_ids == sorted(
        visible_run_ids,
        key=lambda run_id: int(str(run_id).removeprefix("run-")),
    )
    assert "run-20" in visible_run_ids
    assert "run-1" in visible_run_ids
    assert len(visible_run_ids) == 20
    assert payload["firstHidden"] == "false"
    assert payload["lastHidden"] == "false"


def test_set_active_round_nav_reuses_nodes_and_scrolls_active_into_view(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    { run_id: 'run-1', intent: 'First' },
    { run_id: 'run-2', intent: 'Second' },
    { run_id: 'run-3', intent: 'Third' },
];

const { renderRoundNavigator, setActiveRoundNav } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const list = nav.querySelector('.round-nav-list');
const track = nav.querySelector('.round-nav-track');
const run1Node = nav.querySelector('.round-nav-node[data-run-id="run-1"]');
const run2Node = nav.querySelector('.round-nav-node[data-run-id="run-2"]');
list.scrollTop = 96;

setActiveRoundNav('run-2');

console.log(JSON.stringify({
    trackPreserved: nav.querySelector('.round-nav-track') === track,
    run1Preserved: nav.querySelector('.round-nav-node[data-run-id="run-1"]') === run1Node,
    run2Preserved: nav.querySelector('.round-nav-node[data-run-id="run-2"]') === run2Node,
    scrollTop: list.scrollTop,
    activeRunId: nav.querySelector('.round-nav-item.active')?.dataset?.runId || null,
}));""".strip(),
    )

    assert payload == {
        "trackPreserved": True,
        "run1Preserved": True,
        "run2Preserved": True,
        "scrollTop": 0,
        "activeRunId": "run-2",
    }


def test_round_nav_ignores_programmatic_scroll_until_user_scrolls(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const section1 = document.createElement('section');
section1.className = 'session-round-section';
section1.dataset.runId = 'run-1';
section1.__rect = { left: 0, top: -180, width: 900, height: 120, right: 900, bottom: -60 };
const header1 = document.createElement('div');
header1.className = 'round-detail-header';
header1.__rect = { left: 320, top: -180, width: 900, height: 70, right: 1220, bottom: -110 };
section1.appendChild(header1);

const section2 = document.createElement('section');
section2.className = 'session-round-section';
section2.dataset.runId = 'run-2';
section2.__rect = { left: 0, top: 260, width: 900, height: 120, right: 900, bottom: 380 };
const header2 = document.createElement('div');
header2.className = 'round-detail-header';
header2.__rect = { left: 320, top: 260, width: 900, height: 70, right: 1220, bottom: 330 };
section2.appendChild(header2);

const section3 = document.createElement('section');
section3.className = 'session-round-section';
section3.dataset.runId = 'run-3';
section3.__rect = { left: 0, top: 980, width: 900, height: 120, right: 900, bottom: 1100 };
const header3 = document.createElement('div');
header3.className = 'round-detail-header';
header3.__rect = { left: 320, top: 980, width: 900, height: 70, right: 1220, bottom: 1050 };
section3.appendChild(header3);

chatScroll.appendChild(section1);
chatScroll.appendChild(section2);
chatScroll.appendChild(section3);

const rounds = [
    { run_id: 'run-1', intent: 'Already above' },
    { run_id: 'run-2', intent: 'Visible now' },
    { run_id: 'run-3', intent: 'Below later' },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-2' });

const nav = document.getElementById('round-nav-float');
const run2Node = nav.querySelector('.round-nav-node[data-run-id="run-2"]');
const beforeActive = nav.querySelector('.round-nav-item.active')?.dataset?.runId || null;
const beforeVisible = Array.from(nav.querySelectorAll('.round-nav-node'))
    .filter(node => node.dataset.roundNavHidden !== 'true')
    .map(node => node.dataset.runId);

header2.__rect = { left: 320, top: 360, width: 900, height: 70, right: 1220, bottom: 430 };
chatScroll.dispatch('scroll');
const afterProgrammaticScrollActive = nav.querySelector('.round-nav-item.active')?.dataset?.runId || null;

chatScroll.dispatch('wheel');
chatScroll.dispatch('scroll');
const afterUserVisible = Array.from(nav.querySelectorAll('.round-nav-node'))
    .filter(node => node.dataset.roundNavHidden !== 'true')
    .map(node => node.dataset.runId);

console.log(JSON.stringify({
    beforeActive,
    afterProgrammaticScrollActive,
    beforeVisible,
    afterUserVisible,
    usesTransform: Boolean(run2Node.style.transform),
}));""".strip(),
    )

    assert payload == {
        "beforeActive": "run-2",
        "afterProgrammaticScrollActive": "run-2",
        "beforeVisible": ["run-1", "run-2", "run-3"],
        "afterUserVisible": ["run-1", "run-2", "run-3"],
        "usesTransform": False,
    }


def test_round_nav_visible_active_round_scrolls_timeline_back_to_item(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [];
for (let index = 1; index <= 12; index += 1) {
    const runId = `run-${index}`;
    rounds.push({ run_id: runId, intent: `Round ${index}` });
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    const top = index === 1 ? 120 : 900 + (index * 30);
    header.__rect = { left: 320, top, width: 900, height: 30, right: 1220, bottom: top + 30 };
    section.appendChild(header);
    chatScroll.appendChild(section);
}

const { renderRoundNavigator, setActiveRoundNav } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-12', layoutReason: 'new-latest' });

const nav = document.getElementById('round-nav-float');
const list = nav.querySelector('.round-nav-list');
const beforeScrollTop = list.scrollTop;
setActiveRoundNav('run-1');

console.log(JSON.stringify({
    beforeScrollTop,
    afterScrollTop: list.scrollTop,
    firstHidden: nav.querySelector('.round-nav-node[data-run-id="run-1"]').dataset.roundNavHidden,
    lastHidden: nav.querySelector('.round-nav-node[data-run-id="run-12"]').dataset.roundNavHidden,
    nodeCount: nav.querySelectorAll('.round-nav-node').length,
}));""".strip(),
    )

    assert payload == {
        "beforeScrollTop": 0,
        "afterScrollTop": 0,
        "firstHidden": "false",
        "lastHidden": "false",
        "nodeCount": 12,
    }


def test_round_nav_active_change_scrolls_list_without_entry_animation(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [];
for (let index = 1; index <= 24; index += 1) {
    const runId = `run-${index}`;
    rounds.push({ run_id: runId, intent: `Round ${index}` });
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    const top = index === 24 ? 520 : 980 + (index * 28);
    header.__rect = { left: 320, top, width: 900, height: 30, right: 1220, bottom: top + 30 };
    section.appendChild(header);
    chatScroll.appendChild(section);
}

const { renderRoundNavigator, setActiveRoundNav } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-24', layoutReason: 'new-latest' });

const nav = document.getElementById('round-nav-float');
setActiveRoundNav('run-1', { layoutReason: 'sync-visible-active' });

console.log(JSON.stringify({
    scrollTop: nav.querySelector('.round-nav-list')?.scrollTop || 0,
    activeRunId: nav.querySelector('.round-nav-item.active')?.dataset?.runId || null,
    enteringRunIds: Array.from(nav.querySelectorAll('.round-nav-node'))
        .filter(node => node.dataset.roundNavEntering === 'true')
        .map(node => node.dataset.runId),
}));""".strip(),
    )

    entering = payload["enteringRunIds"]
    assert isinstance(entering, list)
    assert entering == []
    assert payload["activeRunId"] == "run-1"
    assert payload["scrollTop"] == 0


def test_round_nav_follow_active_packs_following_context_into_view(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [];
for (let index = 1; index <= 12; index += 1) {
    const runId = `run-${index}`;
    rounds.push({ run_id: runId, intent: `Round ${index}` });
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    const top = index === 1 ? 120 : 980;
    header.__rect = { left: 320, top, width: 900, height: 30, right: 1220, bottom: top + 30 };
    section.appendChild(header);
    chatScroll.appendChild(section);
}

const { renderRoundNavigator, setActiveRoundNav } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-12', layoutReason: 'new-latest' });

const nav = document.getElementById('round-nav-float');
const list = nav.querySelector('.round-nav-list');
setActiveRoundNav('run-1');

const visibleRunIds = [];
for (let index = 1; index <= 12; index += 1) {
    const runId = `run-${index}`;
    const node = nav.querySelector(`.round-nav-node[data-run-id="${runId}"]`);
    if (node?.dataset?.roundNavHidden === 'true') {
        continue;
    }
    visibleRunIds.push(runId);
}

console.log(JSON.stringify({
    scrollTop: list.scrollTop,
    visibleRunIds,
    firstHidden: nav.querySelector('.round-nav-node[data-run-id="run-1"]').dataset.roundNavHidden,
    lastHidden: nav.querySelector('.round-nav-node[data-run-id="run-12"]').dataset.roundNavHidden,
}));""".strip(),
    )

    assert payload == {
        "scrollTop": 0,
        "visibleRunIds": [
            "run-1",
            "run-2",
            "run-3",
            "run-4",
            "run-5",
            "run-6",
            "run-7",
            "run-8",
            "run-9",
            "run-10",
            "run-11",
            "run-12",
        ],
        "firstHidden": "false",
        "lastHidden": "false",
    }


def test_round_nav_near_bottom_prioritizes_latest_loaded_round(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
chatScroll.scrollHeight = 2400;
chatScroll.clientHeight = 760;
chatScroll.scrollTop = 2400 - 760 - 12;

const rounds = [];
for (let index = 1; index <= 20; index += 1) {
    const runId = `run-${index}`;
    rounds.push({ run_id: runId, intent: `Round ${index}` });
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    const top = index === 20 ? 520 : -900 + (index * 28);
    header.__rect = { left: 320, top, width: 900, height: 30, right: 1220, bottom: top + 30 };
    section.appendChild(header);
    chatScroll.appendChild(section);
}

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-3', layoutReason: 'structure' });

const nav = document.getElementById('round-nav-float');
const visibleRunIds = [];
for (let index = 1; index <= 20; index += 1) {
    const runId = `run-${index}`;
    const node = nav.querySelector(`.round-nav-node[data-run-id="${runId}"]`);
    if (node?.dataset?.roundNavHidden !== 'true') {
        visibleRunIds.push(runId);
    }
}

console.log(JSON.stringify({
    visibleRunIds,
    staleActiveHidden: nav.querySelector('.round-nav-node[data-run-id="run-3"]').dataset.roundNavHidden,
    latestHidden: nav.querySelector('.round-nav-node[data-run-id="run-20"]').dataset.roundNavHidden,
}));""".strip(),
    )

    visible_run_ids = payload["visibleRunIds"]
    assert isinstance(visible_run_ids, list)
    assert "run-20" in visible_run_ids
    assert payload["latestHidden"] == "false"
    assert payload["staleActiveHidden"] == "false"


def test_round_nav_visible_viewport_window_does_not_pin_older_items(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [];
for (let index = 1; index <= 24; index += 1) {
    const runId = `run-${index}`;
    rounds.push({ run_id: runId, intent: `Round ${index}` });
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    let top = -900 + (index * 24);
    if (index === 18) top = 80;
    if (index === 19) top = 360;
    if (index === 20) top = 620;
    if (index > 20) top = 980 + (index * 18);
    header.__rect = { left: 320, top, width: 900, height: 30, right: 1220, bottom: top + 30 };
    section.appendChild(header);
    chatScroll.appendChild(section);
}

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-3', layoutReason: 'structure' });

const nav = document.getElementById('round-nav-float');
const visibleRunIds = [];
for (let index = 1; index <= 24; index += 1) {
    const runId = `run-${index}`;
    const node = nav.querySelector(`.round-nav-node[data-run-id="${runId}"]`);
    if (node?.dataset?.roundNavHidden !== 'true') {
        visibleRunIds.push(runId);
    }
}

console.log(JSON.stringify({
    visibleRunIds,
    oldActiveHidden: nav.querySelector('.round-nav-node[data-run-id="run-3"]').dataset.roundNavHidden,
    visible18Hidden: nav.querySelector('.round-nav-node[data-run-id="run-18"]').dataset.roundNavHidden,
    visible20Hidden: nav.querySelector('.round-nav-node[data-run-id="run-20"]').dataset.roundNavHidden,
}));""".strip(),
    )

    visible_run_ids = payload["visibleRunIds"]
    assert isinstance(visible_run_ids, list)
    assert "run-18" in visible_run_ids
    assert "run-20" in visible_run_ids
    assert "run-3" in visible_run_ids
    assert payload["oldActiveHidden"] == "false"
    assert payload["visible18Hidden"] == "false"
    assert payload["visible20Hidden"] == "false"


def test_round_nav_tall_active_detail_still_shows_multiple_following_rounds(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [];
for (let index = 1; index <= 14; index += 1) {
    const runId = `run-${index}`;
    rounds.push({ run_id: runId, intent: `Round ${index}` });
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    const top = index === 6 ? 120 : 980;
    header.__rect = { left: 320, top, width: 900, height: 30, right: 1220, bottom: top + 30 };
    section.appendChild(header);
    chatScroll.appendChild(section);
}

const { renderRoundNavigator, setActiveRoundNav } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-6' });

const nav = document.getElementById('round-nav-float');
const activeNode = nav.querySelector('.round-nav-node[data-run-id="run-6"]');
activeNode.__rect = { left: 1048, top: 22, width: 216, height: 180, right: 1264, bottom: 202 };
activeNode.offsetHeight = 180;
setActiveRoundNav('run-6');

const visibleRunIds = [];
for (let index = 1; index <= 14; index += 1) {
    const runId = `run-${index}`;
    const node = nav.querySelector(`.round-nav-node[data-run-id="${runId}"]`);
    if (node?.dataset?.roundNavHidden !== 'true') {
        visibleRunIds.push(runId);
    }
}

console.log(JSON.stringify({
    visibleRunIds,
    activeHidden: activeNode.dataset.roundNavHidden,
    run7Hidden: nav.querySelector('.round-nav-node[data-run-id="run-7"]').dataset.roundNavHidden,
    run12Hidden: nav.querySelector('.round-nav-node[data-run-id="run-12"]').dataset.roundNavHidden,
    run13Hidden: nav.querySelector('.round-nav-node[data-run-id="run-13"]').dataset.roundNavHidden,
}));""".strip(),
    )

    visible_run_ids = payload["visibleRunIds"]
    assert isinstance(visible_run_ids, list)
    assert visible_run_ids == sorted(
        visible_run_ids,
        key=lambda run_id: int(str(run_id).removeprefix("run-")),
    )
    assert "run-6" in visible_run_ids
    assert "run-7" in visible_run_ids
    assert len(visible_run_ids) >= 8
    assert payload["activeHidden"] == "false"
    assert payload["run7Hidden"] == "false"
    assert payload["run12Hidden"] == "false"
    assert payload["run13Hidden"] == "false"


def test_round_nav_lower_active_keeps_following_context_visible(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [];
for (let index = 1; index <= 18; index += 1) {
    const runId = `run-${index}`;
    rounds.push({
        run_id: runId,
        intent: `Round ${index}`,
        todo: index === 12
            ? { run_id: runId, items: [
                { content: 'First task', status: 'completed' },
                { content: 'Second task', status: 'completed' },
                { content: 'Third task', status: 'completed' },
            ] }
            : null,
    });
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    const top = index === 12 ? 680 : -900 + (index * 34);
    header.__rect = { left: 320, top, width: 900, height: 30, right: 1220, bottom: top + 30 };
    section.appendChild(header);
    chatScroll.appendChild(section);
}

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-12', layoutReason: 'sync-visible-active' });

const nav = document.getElementById('round-nav-float');
const activeNode = nav.querySelector('.round-nav-node[data-run-id="run-12"]');
const visibleRunIds = [];
for (let index = 1; index <= 18; index += 1) {
    const runId = `run-${index}`;
    const node = nav.querySelector(`.round-nav-node[data-run-id="${runId}"]`);
    if (node?.dataset?.roundNavHidden !== 'true') {
        visibleRunIds.push(runId);
    }
}

console.log(JSON.stringify({
    visibleRunIds,
    anchorZone: activeNode.dataset.anchorZone,
    detailAfterItem: activeNode.children.indexOf(activeNode.querySelector('.round-nav-detail'))
        > activeNode.children.indexOf(activeNode.querySelector('.round-nav-item')),
    run13Hidden: nav.querySelector('.round-nav-node[data-run-id="run-13"]').dataset.roundNavHidden,
    run14Hidden: nav.querySelector('.round-nav-node[data-run-id="run-14"]').dataset.roundNavHidden,
}));""".strip(),
    )

    visible_run_ids = payload["visibleRunIds"]
    assert isinstance(visible_run_ids, list)
    assert payload["anchorZone"] == "bottom"
    assert payload["detailAfterItem"] is True
    assert "run-12" in visible_run_ids
    assert "run-13" in visible_run_ids
    assert "run-14" in visible_run_ids
    assert payload["run13Hidden"] == "false"
    assert payload["run14Hidden"] == "false"


def test_round_nav_todo_hover_opens_left_popover_without_repacking_window(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [];
for (let index = 1; index <= 24; index += 1) {
    const runId = `run-${index}`;
    rounds.push({
        run_id: runId,
        intent: `Round ${index}`,
        todo: index === 12
            ? { run_id: runId, items: [
                { content: 'Requirement research', status: 'completed' },
                { content: 'Prototype design', status: 'completed' },
                { content: 'System build', status: 'completed' },
                { content: 'Validation', status: 'completed' },
                { content: 'Release', status: 'completed' },
            ] }
            : null,
    });
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = runId;
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    const top = index === 12 ? 470 : -900 + (index * 34);
    header.__rect = { left: 320, top, width: 900, height: 30, right: 1220, bottom: top + 30 };
    section.appendChild(header);
    chatScroll.appendChild(section);
}

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-12', layoutReason: 'sync-visible-active' });

const nav = document.getElementById('round-nav-float');
const activeNode = nav.querySelector('.round-nav-node[data-run-id="run-12"]');
const detail = activeNode.querySelector('.round-nav-detail');
const visibleRunIds = [];
for (let index = 1; index <= 24; index += 1) {
    const runId = `run-${index}`;
    const node = nav.querySelector(`.round-nav-node[data-run-id="${runId}"]`);
    if (node?.dataset?.roundNavHidden !== 'true') {
        visibleRunIds.push(runId);
    }
}
activeNode.dispatch('mouseenter');
const afterHoverVisibleRunIds = [];
for (let index = 1; index <= 24; index += 1) {
    const runId = `run-${index}`;
    const node = nav.querySelector(`.round-nav-node[data-run-id="${runId}"]`);
    if (node?.dataset?.roundNavHidden !== 'true') {
        afterHoverVisibleRunIds.push(runId);
    }
}

console.log(JSON.stringify({
    visibleRunIds,
    afterHoverVisibleRunIds,
    popoverOpen: activeNode.dataset.popoverOpen || null,
    popoverLeft: detail.style.left || null,
    popoverTop: detail.style.top || null,
    todoItemCount: detail.querySelectorAll('.round-nav-todo-item').length,
    hasMeta: detail.querySelector('.round-nav-meta') !== null,
    run18Hidden: nav.querySelector('.round-nav-node[data-run-id="run-18"]').dataset.roundNavHidden,
}));""".strip(),
    )

    visible_run_ids = payload["visibleRunIds"]
    assert isinstance(visible_run_ids, list)
    assert payload["afterHoverVisibleRunIds"] == visible_run_ids
    assert payload["popoverOpen"] == "true"
    assert str(payload["popoverLeft"]).endswith("px")
    assert str(payload["popoverTop"]).endswith("px")
    assert payload["todoItemCount"] == 5
    assert payload["hasMeta"] is False
    assert payload["run18Hidden"] == "false"


def test_round_nav_popover_opens_for_live_round_and_patches_status(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    {
        run_id: 'run-1',
        intent: 'Live round',
        run_status: 'running',
        run_phase: 'running',
        todo: {
            run_id: 'run-1',
            items: [{ content: 'One', status: 'pending' }],
        },
    },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
const { patchRoundNavigatorTodo } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const node = nav.querySelector('.round-nav-node[data-run-id="run-1"]');
const detail = node.querySelector('.round-nav-detail');
node.dispatch('mouseenter');
const beforeStatus = detail.querySelector('.round-nav-todo-status')?.textContent || null;
patchRoundNavigatorTodo('run-1', {
    run_id: 'run-1',
    items: [{ content: 'One', status: 'completed' }],
});

console.log(JSON.stringify({
    popoverOpen: node.dataset.popoverOpen || null,
    hasTodo: node.querySelector('.round-nav-todo') !== null,
    beforeStatus,
    afterStatus: detail.querySelector('.round-nav-todo-status')?.textContent || null,
}));""".strip(),
    )

    assert payload == {
        "popoverOpen": "true",
        "hasTodo": True,
        "beforeStatus": "rounds.todo.status.pending",
        "afterStatus": "rounds.todo.status.completed",
    }


def test_round_nav_todo_refresh_preserves_timeline_scroll_position(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [];
for (let index = 1; index <= 16; index += 1) {
    rounds.push({
        run_id: `run-${index}`,
        intent: `Round ${index}`,
        todo: {
            run_id: `run-${index}`,
            items: [{ content: `Task ${index}`, status: 'pending' }],
        },
    });
}

const { patchRoundNavigatorTodo, renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-16', layoutReason: 'new-latest' });

const nav = document.getElementById('round-nav-float');
const list = nav.querySelector('.round-nav-list');
const beforeScrollTop = list.scrollTop;
patchRoundNavigatorTodo('run-16', {
    run_id: 'run-16',
    items: [{ content: 'Task 16', status: 'completed' }],
});

console.log(JSON.stringify({
    beforeScrollTop,
    afterScrollTop: list.scrollTop,
    nodeCount: nav.querySelectorAll('.round-nav-node').length,
    status: nav.querySelector('.round-nav-node[data-run-id="run-16"] .round-nav-todo-status')?.textContent || null,
}));""".strip(),
    )

    assert payload == {
        "beforeScrollTop": 72,
        "afterScrollTop": 72,
        "nodeCount": 16,
        "status": "rounds.todo.status.completed",
    }


def test_round_nav_css_keeps_todo_status_readable_and_focus_subtle() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    css = (
        repo_root
        / "frontend"
        / "dist"
        / "css"
        / "components"
        / "rounds"
        / "navigator.css"
    ).read_text(encoding="utf-8")

    assert ".round-nav-item:focus-visible" in css
    assert "outline: none;" in css
    assert "min-width: 4.75rem;" in css
    assert "max-width: 5.4rem;" not in css
    assert "white-space: nowrap;" in css


def test_round_nav_pending_approval_uses_warning_dot(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    {
        run_id: 'run-1',
        intent: 'Approve command',
        run_status: 'completed',
        pending_tool_approval_count: 2,
    },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const node = nav.querySelector('.round-nav-node[data-run-id="run-1"]');

console.log(JSON.stringify({
    stateTone: node?.dataset?.stateTone || null,
    dotTitle: node?.querySelector('.round-nav-dot')?.title || null,
    hasWarningMeta: node?.querySelector('.round-nav-state-warning') !== null,
}));""".strip(),
    )

    assert payload == {
        "stateTone": "warning",
        "dotTitle": "rounds.pending_approvals",
        "hasWarningMeta": False,
    }


def test_round_nav_density_degrades_before_overlap(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
const rounds = [
    { run_id: 'run-1', intent: 'Inspect issue' },
];
const { renderRoundNavigator } = await import('./navigator.mjs');

function renderAtWidth(width) {
    chatContainer.__rect = { left: 0, top: 0, width, height: 900, right: width, bottom: 900 };
    renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });
    const nav = document.getElementById('round-nav-float');
    return {
        navDensity: nav?.dataset?.density || null,
        chatDensity: chatContainer.dataset.roundTimelineDensity || null,
    };
}

console.log(JSON.stringify({
    full: renderAtWidth(1900),
    compact: renderAtWidth(1100),
    dot: renderAtWidth(820),
    hidden: renderAtWidth(700),
}));""".strip(),
    )

    assert payload == {
        "full": {"navDensity": "full", "chatDensity": "full"},
        "compact": {"navDensity": "full", "chatDensity": "full"},
        "dot": {"navDensity": "compact", "chatDensity": "compact"},
        "hidden": {"navDensity": "dot", "chatDensity": "dot"},
    }


def _run_round_nav_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "rounds"
        / "navigator.js"
    )
    todo_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "todo.js"
    )

    navigator_path = tmp_path / "navigator.mjs"
    todo_module_path = tmp_path / "todo.mjs"
    runner_path = tmp_path / "runner-round-nav.mjs"

    replacements = {
        "./utils.js": "./mockRoundUtils.mjs",
        "../../utils/i18n.js": "./mockI18n.mjs",
        "./todo.js": "./todo.mjs",
    }
    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in replacements.items():
        source_text = source_text.replace(original, replacement)
    navigator_path.write_text(source_text, encoding="utf-8")
    todo_module_path.write_text(todo_path.read_text(encoding="utf-8"), encoding="utf-8")

    (tmp_path / "mockRoundUtils.mjs").write_text(
        """
export function esc(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

export function roundStateLabel(round) {
    const status = String(round?.run_status || '');
    if (status === 'completed') return 'rounds.state.completed';
    if (status === 'running') return 'rounds.state.running';
    if (status === 'failed') return 'rounds.state.failed';
    return '';
}

export function roundStateTone(round) {
    const status = String(round?.run_status || '');
    if (status === 'completed') return 'success';
    if (status === 'running') return 'running';
    if (status === 'failed') return 'danger';
    return 'idle';
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function formatMessage(message, values = {}) {
    return Object.entries(values).reduce(
        (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)),
        String(message || ''),
    );
}

export function t(key) {
    return String(key || '');
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        (
            """
globalThis.HTMLElement = class HTMLElement {};
globalThis.window = globalThis;
window.requestAnimationFrame = callback => {
    callback();
    return 1;
};
window.cancelAnimationFrame = () => undefined;
window.addEventListener = () => undefined;
window.matchMedia = () => ({ matches: false });

class FakeClassList {
    constructor(owner) {
        this.owner = owner;
    }

    add(...classes) {
        const next = new Set(String(this.owner.className || '').split(/\\s+/).filter(Boolean));
        classes.forEach(cls => next.add(cls));
        this.owner.className = Array.from(next).join(' ');
    }

    remove(...classes) {
        const blocked = new Set(classes);
        this.owner.className = String(this.owner.className || '')
            .split(/\\s+/)
            .filter(cls => cls && !blocked.has(cls))
            .join(' ');
    }

    toggle(cls, force) {
        const has = this.contains(cls);
        const shouldAdd = typeof force === 'boolean' ? force : !has;
        if (shouldAdd) {
            this.add(cls);
        } else {
            this.remove(cls);
        }
    }

    contains(cls) {
        return String(this.owner.className || '').split(/\\s+/).includes(cls);
    }
}

class FakeElement extends HTMLElement {
    constructor(tagName = 'div') {
        super();
        this.tagName = tagName.toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.className = '';
        this.dataset = {};
        this.attributes = new Map();
        this.listeners = new Map();
        this.id = '';
        this.title = '';
        this.type = '';
        this.scrollTop = 0;
        this.offsetHeight = 32;
        this.offsetWidth = 228;
        this.textContent = '';
        this.classList = new FakeClassList(this);
        this.style = {
            setProperty(name, value) {
                this[name] = String(value);
            },
        };
    }

    set innerHTML(value) {
        this._innerHTML = String(value || '');
        this.replaceChildren();

        if (this.id === 'round-nav-float' && this._innerHTML.includes('round-nav-list')) {
            const list = new FakeElement('div');
            list.className = 'round-nav-list';
            this.appendChild(list);
            return;
        }

        if (hasClass(this, 'round-nav-item')) {
            const marker = new FakeElement('span');
            marker.className = 'round-nav-marker';
            const dot = new FakeElement('span');
            dot.className = 'round-nav-dot';
            dot.title = /class="round-nav-dot" title="([^"]*)"/.exec(this._innerHTML)?.[1] || '';
            const ariaLabel = /aria-label="([^"]*)"/.exec(this._innerHTML)?.[1] || '';
            if (ariaLabel) {
                dot.setAttribute('aria-label', ariaLabel);
            }
            marker.appendChild(dot);

            const copy = new FakeElement('span');
            copy.className = 'round-nav-copy';
            const time = new FakeElement('span');
            time.className = 'round-nav-time';
            time.textContent = /class="round-nav-time">([^<]*)</.exec(this._innerHTML)?.[1] || '';
            const txt = new FakeElement('span');
            txt.className = 'txt';
            txt.textContent = /class="txt">([^<]*)</.exec(this._innerHTML)?.[1] || '';
            copy.appendChild(time);
            copy.appendChild(txt);

            this.appendChild(marker);
            this.appendChild(copy);
            return;
        }

        if (hasClass(this, 'round-nav-detail')) {
            if (this._innerHTML.includes('round-nav-meta')) {
                const meta = new FakeElement('div');
                meta.className = 'round-nav-meta';
                const stateMatches = Array.from(
                    this._innerHTML.matchAll(/<span class="round-nav-state ([^"]+)">([^<]*)<\\/span>/g),
                );
                stateMatches.forEach(match => {
                    const state = new FakeElement('span');
                    state.className = `round-nav-state ${String(match[1] || '')}`;
                    state.textContent = String(match[2] || '');
                    meta.appendChild(state);
                });
                this.appendChild(meta);
            }
            if (this._innerHTML.includes('round-nav-todo')) {
                const todo = new FakeElement('div');
                todo.className = 'round-nav-todo';
                const list = new FakeElement('ul');
                list.className = 'round-nav-todo-list';

                const itemMatches = Array.from(
                    this._innerHTML.matchAll(/<li class="round-nav-todo-item"[\\s\\S]*?<\\/li>/g),
                );
                itemMatches.forEach(match => {
                    const block = String(match[0] || '');
                    const itemStatus = /data-status="([^"]+)"/.exec(block)?.[1] || 'pending';
                    const itemTitle = /class="round-nav-todo-text" title="([^"]+)"/.exec(block)?.[1] || '';
                    const itemText = /class="round-nav-todo-text" title="[^"]*">([^<]*)</.exec(block)?.[1] || '';
                    const statusText = /class="round-nav-todo-status" data-status="[^"]+">([^<]*)</.exec(block)?.[1] || '';
                    const item = new FakeElement('li');
                    item.className = 'round-nav-todo-item';
                    item.dataset.status = itemStatus;

                    const text = new FakeElement('span');
                    text.className = 'round-nav-todo-text';
                    text.title = itemTitle;
                    text.textContent = itemText;

                    const status = new FakeElement('span');
                    status.className = 'round-nav-todo-status';
                    status.dataset.status = itemStatus;
                    status.textContent = statusText;

                    item.appendChild(text);
                    item.appendChild(status);
                    list.appendChild(item);
                });
                todo.appendChild(list);
                this.appendChild(todo);
            }
        }
    }

    get innerHTML() {
        return this._innerHTML || '';
    }

    appendChild(node) {
        if (!node) return node;
        node.parentNode = this;
        this.children.push(node);
        return node;
    }

    replaceChildren(...nodes) {
        this.children.forEach(child => {
            child.parentNode = null;
        });
        this.children = [];
        nodes.forEach(node => this.appendChild(node));
    }

    replaceWith(node) {
        const parent = this.parentNode;
        if (!parent) return;
        const index = parent.children.indexOf(this);
        if (index < 0) return;
        this.parentNode = null;
        if (node) {
            node.parentNode = parent;
            parent.children[index] = node;
        } else {
            parent.children.splice(index, 1);
        }
    }

    remove() {
        if (!this.parentNode) return;
        this.replaceWith();
    }

    addEventListener(type, handler) {
        const key = String(type || '');
        const handlers = this.listeners.get(key) || [];
        handlers.push(handler);
        this.listeners.set(key, handlers);
    }

    removeEventListener(type, handler) {
        const key = String(type || '');
        const handlers = this.listeners.get(key) || [];
        this.listeners.set(key, handlers.filter(item => item !== handler));
    }

    dispatch(type, event = {}) {
        const key = String(type || '');
        const handlers = this.listeners.get(key) || [];
        handlers.forEach(handler => {
            handler({
                target: this,
                currentTarget: this,
                ...event,
            });
        });
    }

    setAttribute(name, value) {
        this.attributes.set(String(name), String(value));
        if (name === 'id') {
            this.id = String(value);
        }
        if (name === 'class') {
            this.className = String(value);
        }
    }

    getAttribute(name) {
        return this.attributes.get(String(name)) ?? null;
    }

    contains(node) {
        if (node === this) return true;
        return this.children.some(child => child.contains(node));
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const segments = String(selector || '').trim().split(/\\s+/).filter(Boolean);
        let current = [this];
        segments.forEach(segment => {
            const next = [];
            current.forEach(node => {
                traverse(node, child => {
                    if (matchesSelector(child, segment)) {
                        next.push(child);
                    }
                });
            });
            current = next;
        });
        return current;
    }

    scrollIntoView() {}

    getBoundingClientRect() {
        if (this.__rect) {
            return this.__rect;
        }
        if (hasClass(this, 'chat-container')) {
            return { left: 0, top: 0, width: 1280, height: 900, right: 1280, bottom: 900 };
        }
        if (hasClass(this, 'chat-scroll')) {
            return { left: 0, top: 0, width: 1280, height: 760, right: 1280, bottom: 760 };
        }
        if (this.id === 'input-container') {
            return { left: 0, top: 760, width: 1280, height: 140, right: 1280, bottom: 900 };
        }
        if (this.id === 'round-nav-float') {
            return { left: 1036, top: 12, width: 228, height: 600, right: 1264, bottom: 612 };
        }
        if (hasClass(this, 'round-nav-list')) {
            return { left: 1048, top: 22, width: 216, height: 600, right: 1264, bottom: 622 };
        }
        if (hasClass(this, 'round-nav-node')) {
            return { left: 1048, top: 22, width: 216, height: 42, right: 1264, bottom: 64 };
        }
        return { left: 0, top: 0, width: this.offsetWidth, height: this.offsetHeight, right: this.offsetWidth, bottom: this.offsetHeight };
    }
}

function traverse(root, visit) {
    for (const child of root.children || []) {
        visit(child);
        traverse(child, visit);
    }
}

function hasClass(node, className) {
    return String(node?.className || '')
        .split(/\\s+/)
        .filter(Boolean)
        .includes(className);
}

function matchesSelector(node, selector) {
    if (!node || typeof selector !== 'string') return false;
    if (selector.startsWith('#')) {
        return String(node.id || '') === selector.slice(1);
    }
    const datasetRunIdMatch = selector.match(/^\\.([a-zA-Z0-9_-]+)\\[data-run-id="(.+)"\\]$/);
    if (datasetRunIdMatch) {
        return hasClass(node, datasetRunIdMatch[1]) && String(node.dataset?.runId || '') === datasetRunIdMatch[2];
    }
    if (selector.startsWith('.')) {
        return selector
            .slice(1)
            .split('.')
            .filter(Boolean)
            .every(cls => hasClass(node, cls));
    }
    return false;
}

const body = new FakeElement('body');
const chatContainer = new FakeElement('div');
chatContainer.className = 'chat-container';
const chatScroll = new FakeElement('div');
chatScroll.className = 'chat-scroll';
chatContainer.appendChild(chatScroll);
const inputContainer = new FakeElement('div');
inputContainer.id = 'input-container';
chatContainer.appendChild(inputContainer);
body.appendChild(chatContainer);

globalThis.document = {
    body,
    createElement(tagName) {
        return new FakeElement(tagName);
    },
    getElementById(id) {
        return body.querySelector(`#${String(id || '')}`);
    },
    querySelector(selector) {
        if (selector === '.chat-scroll') {
            return chatScroll;
        }
        if (selector === '.chat-container') {
            return chatContainer;
        }
        return body.querySelector(selector);
    },
};

"""
            + runner_source
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=5,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
