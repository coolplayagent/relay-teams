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
    hostFirstChildId: document.getElementById('chat-messages').firstElementChild?.id || null,
    nodeCount: nav.querySelectorAll('.round-nav-node').length,
    activeRunId: nav.querySelector('.round-nav-item.active')?.dataset?.runId || null,
    run2HasTodoClass: run2Node?.className.includes('has-todo') || false,
    run1HasTodo: run1Node?.querySelector('.round-nav-todo-branch') !== null,
    run2HasTodo: run2Node?.querySelector('.round-nav-todo-branch') !== null,
    todoCardOpen: run2Node?.querySelector('.round-todo-card')?.open || false,
    todoItemCount: run2Node?.querySelectorAll('.round-todo-item').length || 0,
}));
""".strip(),
    )

    assert payload == {
        "hostFirstChildId": "round-nav-float",
        "nodeCount": 2,
        "activeRunId": "run-2",
        "run2HasTodoClass": True,
        "run1HasTodo": False,
        "run2HasTodo": True,
        "todoCardOpen": True,
        "todoItemCount": 2,
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


def test_collapsed_round_nav_hides_list(tmp_path: Path) -> None:
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


def test_round_nav_rerender_preserves_single_inline_container(tmp_path: Path) -> None:
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

const { renderRoundNavigator } = await import('./navigator.mjs');
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-1' });
renderRoundNavigator(rounds, () => undefined, { activeRunId: 'run-2' });

const host = document.getElementById('chat-messages');
console.log(JSON.stringify({
    navCount: host.querySelectorAll('#round-nav-float').length,
    activeRunId: host.querySelector('.round-nav-item.active')?.dataset?.runId || null,
}));
""".strip(),
    )

    assert payload == {
        "navCount": 1,
        "activeRunId": "run-2",
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
        f"""
globalThis.window = globalThis;
const storage = new Map();
globalThis.localStorage = {{
    getItem(key) {{
        return storage.has(key) ? storage.get(key) : null;
    }},
    setItem(key, value) {{
        storage.set(key, String(value));
    }},
    removeItem(key) {{
        storage.delete(key);
    }},
}};

class FakeClassList {{
    constructor(owner) {{
        this.owner = owner;
    }}

    add(...classes) {{
        const next = new Set(String(this.owner.className || '').split(/\\s+/).filter(Boolean));
        classes.forEach(cls => next.add(cls));
        this.owner.className = Array.from(next).join(' ');
    }}

    toggle(cls, force) {{
        const has = this.contains(cls);
        const shouldAdd = typeof force === 'boolean' ? force : !has;
        if (shouldAdd) {{
            this.add(cls);
            return;
        }}
        this.owner.className = String(this.owner.className || '')
            .split(/\\s+/)
            .filter(item => item && item !== cls)
            .join(' ');
    }}

    contains(cls) {{
        return String(this.owner.className || '').split(/\\s+/).includes(cls);
    }}
}}

class FakeElement {{
    constructor(tagName = 'div') {{
        this.tagName = tagName.toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.className = '';
        this.dataset = {{}};
        this.attributes = new Map();
        this.listeners = new Map();
        this.id = '';
        this.title = '';
        this.type = '';
        this.open = false;
        this.textContent = '';
        this.classList = new FakeClassList(this);
    }}

    set innerHTML(value) {{
        this._innerHTML = String(value || '');
        this.replaceChildren();

        if (this.id === 'round-nav-float' && this._innerHTML.includes('round-nav-header')) {{
            const header = new FakeElement('div');
            header.className = 'round-nav-header';
            const title = new FakeElement('div');
            title.className = 'round-nav-title';
            title.textContent = 'Rounds';
            const toggle = new FakeElement('button');
            toggle.className = 'round-nav-toggle';
            toggle.type = 'button';
            header.appendChild(title);
            header.appendChild(toggle);

            const list = new FakeElement('div');
            list.className = 'round-nav-list';

            this.appendChild(header);
            this.appendChild(list);
            return;
        }}

        if (String(this.className || '').includes('round-nav-item')) {{
            const idx = new FakeElement('span');
            idx.className = 'idx';
            idx.textContent = /<span class="idx">([^<]*)</.exec(this._innerHTML)?.[1] || '';
            const copy = new FakeElement('span');
            copy.className = 'round-nav-copy';
            const txt = new FakeElement('span');
            txt.className = 'txt';
            txt.textContent = /<span class="txt">([^<]*)</.exec(this._innerHTML)?.[1] || '';
            const meta = new FakeElement('span');
            meta.className = 'round-nav-meta';
            copy.appendChild(txt);
            copy.appendChild(meta);
            this.appendChild(idx);
            this.appendChild(copy);
            return;
        }}

        if (String(this.className || '').includes('round-todo-card')) {{
            const summary = new FakeElement('summary');
            summary.className = 'round-todo-summary';
            const toggle = new FakeElement('span');
            toggle.className = 'round-todo-toggle';
            toggle.textContent = /<span class="round-todo-toggle">([^<]*)</.exec(this._innerHTML)?.[1] || '';
            summary.appendChild(toggle);

            const body = new FakeElement('div');
            body.className = 'round-todo-body';
            const list = new FakeElement('ul');
            list.className = 'round-todo-list';

            const itemMatches = Array.from(this._innerHTML.matchAll(/<li class="round-todo-item"[\\s\\S]*?<\\/li>/g));
            itemMatches.forEach(match => {{
                const block = String(match[0] || '');
                const itemStatus = /data-status="([^"]+)"/.exec(block)?.[1] || 'pending';
                const item = new FakeElement('li');
                item.className = 'round-todo-item';
                item.dataset.status = itemStatus;

                const text = new FakeElement('span');
                text.className = 'round-todo-item-text';
                text.title = /class="round-todo-item-text" title="([^"]+)"/.exec(block)?.[1] || '';
                text.textContent = /class="round-todo-item-text" title="[^"]*">([^<]*)</.exec(block)?.[1] || '';

                const status = new FakeElement('span');
                status.className = block.includes('round-todo-status-simple')
                    ? 'round-todo-status round-todo-status-simple'
                    : 'round-todo-status';
                status.dataset.status = itemStatus;
                status.textContent = /class="round-todo-status[^"]*" data-status="[^"]+">([^<]*)</.exec(block)?.[1] || '';

                item.appendChild(text);
                item.appendChild(status);
                list.appendChild(item);
            }});

            body.appendChild(list);
            this.appendChild(summary);
            this.appendChild(body);
        }}
    }}

    get innerHTML() {{
        return this._innerHTML || '';
    }}

    appendChild(node) {{
        if (!node) {{
            return node;
        }}
        node.parentNode = this;
        this.children.push(node);
        return node;
    }}

    prepend(node) {{
        if (!node) {{
            return node;
        }}
        node.parentNode = this;
        this.children = [node, ...this.children.filter(child => child !== node)];
        return node;
    }}

    replaceChildren(...nodes) {{
        this.children = [];
        nodes.forEach(node => this.appendChild(node));
    }}

    addEventListener(type, handler) {{
        const handlers = this.listeners.get(type) || [];
        handlers.push(handler);
        this.listeners.set(type, handlers);
    }}

    setAttribute(name, value) {{
        this.attributes.set(String(name), String(value));
        if (name === 'id') {{
            this.id = String(value);
        }}
    }}

    getAttribute(name) {{
        return this.attributes.get(String(name)) ?? null;
    }}

    querySelector(selector) {{
        return this.querySelectorAll(selector)[0] || null;
    }}

    querySelectorAll(selector) {{
        const results = [];
        traverse(this, child => {{
            if (matchesSelector(child, selector)) {{
                results.push(child);
            }}
        }});
        return results;
    }}

    scrollIntoView() {{
        return undefined;
    }}

    get firstElementChild() {{
        return this.children[0] || null;
    }}
}}

function traverse(root, visit) {{
    for (const child of root.children || []) {{
        visit(child);
        traverse(child, visit);
    }}
}}

function matchesSelector(node, selector) {{
    if (!node || typeof selector !== 'string') {{
        return false;
    }}
    if (selector.startsWith('#')) {{
        return String(node.id || '') === selector.slice(1);
    }}
    const classRunId = selector.match(/^\\.([a-zA-Z0-9_-]+)\\[data-run-id="(.+)"\\]$/);
    if (classRunId) {{
        return hasClass(node, classRunId[1]) && String(node.dataset?.runId || '') === classRunId[2];
    }}
    if (selector.startsWith('.')) {{
        return selector
            .slice(1)
            .split('.')
            .filter(Boolean)
            .every(cls => hasClass(node, cls));
    }}
    return false;
}}

function hasClass(node, className) {{
    return String(node?.className || '')
        .split(/\\s+/)
        .filter(Boolean)
        .includes(className);
}}

const body = new FakeElement('body');
const chatMessages = new FakeElement('div');
chatMessages.id = 'chat-messages';
body.appendChild(chatMessages);

globalThis.document = {{
    body,
    createElement(tagName) {{
        return new FakeElement(tagName);
    }},
    getElementById(id) {{
        return body.querySelector(`#${{String(id || '')}}`);
    }},
    querySelector(selector) {{
        return body.querySelector(selector);
    }},
}};

{runner_source}
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
        timeout=5,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
