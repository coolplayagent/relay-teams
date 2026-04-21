# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_round_nav_renders_todo_under_active_round(tmp_path: Path) -> None:
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
    nodeCount: nav.querySelectorAll('.round-nav-node').length,
    activeRunId: nav.querySelector('.round-nav-item.active')?.dataset?.runId || null,
    run2NodeActive: run2Node?.className.includes('active') || false,
    run2HasTodoClass: run2Node?.className.includes('has-todo') || false,
    run1HasTodo: run1Node?.querySelector('.round-nav-todo-branch') !== null,
    run2HasTodo: run2Node?.querySelector('.round-nav-todo-branch') !== null,
    todoCardOpen: run2Node?.querySelector('.round-todo-card')?.open || false,
    todoItemCount: run2Node?.querySelectorAll('.round-todo-item').length || 0,
    firstTodoTitle: run2Node?.querySelector('.round-todo-item-text')?.title || null,
    firstTodoStatus: run2Node?.querySelector('.round-todo-status')?.textContent || null,
}));
""".strip(),
    )

    assert payload == {
        "nodeCount": 2,
        "activeRunId": "run-2",
        "run2NodeActive": True,
        "run2HasTodoClass": True,
        "run1HasTodo": False,
        "run2HasTodo": True,
        "todoCardOpen": True,
        "todoItemCount": 2,
        "firstTodoTitle": "Second task",
        "firstTodoStatus": "rounds.todo.status.in_progress",
    }


def test_set_active_round_nav_moves_todo_branch(tmp_path: Path) -> None:
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
    run1HasTodo: run1Node?.querySelector('.round-nav-todo-branch') !== null,
    run2HasTodo: run2Node?.querySelector('.round-nav-todo-branch') !== null,
}));
""".strip(),
    )

    assert payload == {
        "activeRunId": "run-1",
        "run1HasTodo": True,
        "run2HasTodo": False,
    }


def test_collapsed_round_nav_hides_todo_branch(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
localStorage.setItem('agent_teams_round_nav_collapsed', '1');

const rounds = [
    {
        run_id: 'run-1',
        intent: 'Inspect issue',
        todo: {
            run_id: 'run-1',
            items: [{ content: 'First task', status: 'pending' }],
        },
    },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
console.log(JSON.stringify({
    collapsed: nav.className.includes('collapsed'),
    todoBranchCount: nav.querySelectorAll('.round-nav-todo-branch').length,
}));
""".strip(),
    )

    assert payload == {
        "collapsed": True,
        "todoBranchCount": 0,
    }


def test_completed_round_nav_todo_defaults_collapsed(tmp_path: Path) -> None:
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
                { content: 'First task', status: 'completed' },
                { content: 'Second task', status: 'completed' },
            ],
        },
    },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const todoCard = nav.querySelector('.round-todo-card');
console.log(JSON.stringify({
    open: todoCard?.open || false,
    itemCount: todoCard?.querySelectorAll('.round-todo-item').length || 0,
}));
""".strip(),
    )

    assert payload == {
        "open": False,
        "itemCount": 2,
    }


def test_round_nav_restores_persisted_width(tmp_path: Path) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
localStorage.setItem('agent_teams_round_nav_width', '460');

const rounds = [
    {
        run_id: 'run-1',
        intent: 'Inspect issue',
        todo: {
            run_id: 'run-1',
            items: [{ content: 'First task', status: 'pending' }],
        },
    },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
console.log(JSON.stringify({
    width: nav?.style?.width || null,
}));
""".strip(),
    )

    assert payload == {
        "width": "460px",
    }


def test_round_nav_resize_handle_updates_width_and_persists(tmp_path: Path) -> None:
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
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const resizer = nav.querySelector('.round-nav-resizer');

nav.dispatch('pointerdown', { target: resizer, clientX: 0, clientY: 0, pointerId: 1 });
nav.dispatch('pointermove', { target: resizer, clientX: 80, clientY: 0, pointerId: 1 });
nav.dispatch('pointerup', { target: resizer, clientX: 80, clientY: 0, pointerId: 1 });

console.log(JSON.stringify({
    width: nav?.style?.width || null,
    persistedWidth: localStorage.getItem('agent_teams_round_nav_width'),
    resizing: nav?.className.includes('resizing') || false,
}));
""".strip(),
    )

    assert payload == {
        "width": "350px",
        "persistedWidth": "350",
        "resizing": False,
    }


def test_round_nav_rerender_preserves_list_scroll_top(tmp_path: Path) -> None:
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
                { content: 'First task', status: 'in_progress' },
                { content: 'Second task', status: 'pending' },
                { content: 'Third task', status: 'pending' },
            ],
        },
    },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const list = nav.querySelector('.round-nav-list');
list.scrollTop = 96;

renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

console.log(JSON.stringify({
    scrollTop: nav.querySelector('.round-nav-list')?.scrollTop || 0,
}));
""".strip(),
    )

    assert payload == {
        "scrollTop": 96,
    }


def test_round_nav_collapsed_width_is_short_and_expand_restores_saved_width(
    tmp_path: Path,
) -> None:
    payload = _run_round_nav_script(
        tmp_path=tmp_path,
        runner_source="""
localStorage.setItem('agent_teams_round_nav_width', '460');

const rounds = [
    {
        run_id: 'run-1',
        intent: 'Inspect issue',
        todo: {
            run_id: 'run-1',
            items: [{ content: 'First task', status: 'pending' }],
        },
    },
];

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });

const nav = document.getElementById('round-nav-float');
const toggle = nav.querySelector('.round-nav-toggle');

toggle.onclick();
const collapsedWidth = nav?.style?.width || null;
const collapsed = nav?.className.includes('collapsed') || false;

const expandedToggle = nav.querySelector('.round-nav-toggle');
expandedToggle.onclick();

console.log(JSON.stringify({
    collapsed,
    collapsedWidth,
    expandedWidth: nav?.style?.width || null,
}));
""".strip(),
    )

    assert payload == {
        "collapsed": True,
        "collapsedWidth": "180px",
        "expandedWidth": "460px",
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

export function roundStateLabel() {
    return '';
}

export function roundStateTone() {
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
window.innerWidth = 1280;
window.innerHeight = 900;
window.requestAnimationFrame = callback => {
    callback();
    return 1;
};
window.cancelAnimationFrame = () => undefined;

globalThis.DOMRect = class DOMRect {
    constructor(x = 0, y = 0, width = 0, height = 0) {
        this.left = x;
        this.top = y;
        this.width = width;
        this.height = height;
        this.right = x + width;
        this.bottom = y + height;
    }
};

globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    disconnect() {}
};

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
        this.open = false;
        this.scrollTop = 0;
        this.offsetWidth = 270;
        this.offsetHeight = 280;
        this.textContent = '';
        this.classList = new FakeClassList(this);
        this.style = new Proxy(
            {},
            {
                set: (target, key, value) => {
                    target[key] = value;
                    if (key === 'width') {
                        const parsed = Number.parseInt(String(value), 10);
                        if (Number.isFinite(parsed)) {
                            this.offsetWidth = parsed;
                        }
                    }
                    if (key === 'height') {
                        const parsed = Number.parseInt(String(value), 10);
                        if (Number.isFinite(parsed)) {
                            this.offsetHeight = parsed;
                        }
                    }
                    return true;
                },
            },
        );
    }

    set innerHTML(value) {
        this._innerHTML = String(value || '');
        this.replaceChildren();

        if (this.id === 'round-nav-float' && this._innerHTML.includes('round-nav-header')) {
            const header = new FakeElement('div');
            header.className = 'round-nav-header';

            const title = new FakeElement('div');
            title.className = 'round-nav-title';
            title.textContent = 'Rounds';
            header.appendChild(title);

            const toggle = new FakeElement('button');
            toggle.className = 'round-nav-toggle';
            toggle.type = 'button';
            header.appendChild(toggle);

            const list = new FakeElement('div');
            list.className = 'round-nav-list';

            const resizer = new FakeElement('div');
            resizer.className = 'round-nav-resizer';

            this.appendChild(header);
            this.appendChild(list);
            this.appendChild(resizer);
            return;
        }

        if (hasClass(this, 'round-nav-item')) {
            const idx = new FakeElement('span');
            idx.className = 'idx';
            const copy = new FakeElement('span');
            copy.className = 'round-nav-copy';
            const txt = new FakeElement('span');
            txt.className = 'txt';
            const meta = new FakeElement('span');
            meta.className = 'round-nav-meta';
            copy.appendChild(txt);
            copy.appendChild(meta);
            this.appendChild(idx);
            this.appendChild(copy);
            return;
        }

        if (hasClass(this, 'round-todo-card')) {
            const summary = new FakeElement('summary');
            summary.className = 'round-todo-summary';

            const summaryCopy = new FakeElement('span');
            summaryCopy.className = 'round-todo-summary-copy';
            const titleRow = new FakeElement('span');
            titleRow.className = 'round-todo-title-row';
            const title = new FakeElement('span');
            title.className = 'round-todo-title';
            const count = new FakeElement('span');
            count.className = 'round-todo-count';
            titleRow.appendChild(title);
            titleRow.appendChild(count);
            if (this._innerHTML.includes('round-todo-count-progress')) {
                const progress = new FakeElement('span');
                progress.className = 'round-todo-count round-todo-count-progress';
                titleRow.appendChild(progress);
            }
            summaryCopy.appendChild(titleRow);

            const toggle = new FakeElement('span');
            toggle.className = 'round-todo-toggle';
            summary.appendChild(summaryCopy);
            summary.appendChild(toggle);

            const body = new FakeElement('div');
            body.className = 'round-todo-body';
            const list = new FakeElement('ul');
            list.className = 'round-todo-list';

            const itemMatches = Array.from(
                this._innerHTML.matchAll(/<li class="round-todo-item"[\\s\\S]*?<\\/li>/g),
            );
            itemMatches.forEach(match => {
                const block = String(match[0] || '');
                const itemStatus = /data-status="([^"]+)"/.exec(block)?.[1] || 'pending';
                const itemTitle = /class="round-todo-item-text" title="([^"]+)"/.exec(block)?.[1] || '';
                const itemText = /class="round-todo-item-text" title="[^"]*">([^<]*)</.exec(block)?.[1] || '';
                const statusText = /class="round-todo-status[^"]*" data-status="[^"]+">([^<]*)</.exec(block)?.[1] || '';
                const item = new FakeElement('li');
                item.className = 'round-todo-item';
                item.dataset.status = itemStatus;

                const index = new FakeElement('span');
                index.className = 'round-todo-index';

                const copy = new FakeElement('span');
                copy.className = 'round-todo-item-copy';

                const text = new FakeElement('span');
                text.className = 'round-todo-item-text';
                text.title = itemTitle;
                text.textContent = itemText;
                copy.appendChild(text);

                const status = new FakeElement('span');
                status.className = block.includes('round-todo-status-simple')
                    ? 'round-todo-status round-todo-status-simple'
                    : 'round-todo-status';
                status.dataset.status = itemStatus;
                status.textContent = statusText;

                item.appendChild(index);
                item.appendChild(copy);
                item.appendChild(status);
                list.appendChild(item);
            });

            body.appendChild(list);
            this.appendChild(summary);
            this.appendChild(body);
        }
    }

    get innerHTML() {
        return this._innerHTML || '';
    }

    appendChild(node) {
        if (!node) {
            return node;
        }
        node.parentNode = this;
        this.children.push(node);
        return node;
    }

    replaceChildren(...nodes) {
        this.children = [];
        nodes.forEach(node => this.appendChild(node));
    }

    addEventListener(type, handler) {
        const key = String(type || '');
        const handlers = this.listeners.get(key) || [];
        handlers.push(handler);
        this.listeners.set(key, handlers);
    }

    dispatch(type, event = {}) {
        const key = String(type || '');
        const handlers = this.listeners.get(key) || [];
        const payload = {
            target: this,
            currentTarget: this,
            preventDefault() {},
            ...event,
        };
        handlers.forEach(handler => handler(payload));
    }

    setPointerCapture() {}

    releasePointerCapture() {}

    remove() {
        if (!this.parentNode) {
            return;
        }
        this.parentNode.children = this.parentNode.children.filter(child => child !== this);
        this.parentNode = null;
    }

    setAttribute(name, value) {
        this.attributes.set(String(name), String(value));
        if (name === 'id') {
            this.id = String(value);
        }
    }

    getAttribute(name) {
        return this.attributes.get(String(name)) ?? null;
    }

    contains(node) {
        if (node === this) {
            return true;
        }
        return this.children.some(child => child.contains(node));
    }

    closest(selector) {
        let current = this;
        while (current) {
            if (matchesSelector(current, selector)) {
                return current;
            }
            current = current.parentNode;
        }
        return null;
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
        const left = Number.parseInt(String(this.style.left || 0), 10) || 0;
        const top = Number.parseInt(String(this.style.top || 0), 10) || 0;
        return new DOMRect(left, top, this.offsetWidth, this.offsetHeight);
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
    if (!node || typeof selector !== 'string') {
        return false;
    }
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

const storage = new Map();
globalThis.localStorage = {
    getItem(key) {
        return storage.has(key) ? storage.get(key) : null;
    },
    setItem(key, value) {
        storage.set(key, String(value));
    },
};

const body = new FakeElement('body');
globalThis.document = {
    body,
    createElement(tagName) {
        return new FakeElement(tagName);
    },
    getElementById(id) {
        return body.querySelector(`#${String(id || '')}`);
    },
    querySelector(selector) {
        if (selector === '.chat-container') {
            return null;
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
