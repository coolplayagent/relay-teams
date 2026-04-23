from __future__ import annotations

from pathlib import Path

import subprocess


def test_subagent_rail_module_loads_with_real_frontend_graph() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentRail.js"
    )

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "const noop = () => undefined; "
                "const createClassList = () => ({ add: noop, remove: noop, toggle: noop, contains: () => false }); "
                "const createElement = () => ({"
                "innerHTML: '', textContent: '', value: '', hidden: false, disabled: false, style: {}, dataset: {}, "
                "classList: createClassList(), appendChild: noop, insertBefore: noop, remove: noop, "
                "setAttribute: noop, removeAttribute: noop, addEventListener: noop, removeEventListener: noop, "
                "querySelector: () => null, querySelectorAll: () => [], scrollIntoView: noop, focus: noop"
                "}); "
                "globalThis.window = globalThis; "
                "Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { language: 'en-US', clipboard: { writeText: async () => undefined } } }); "
                "Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://127.0.0.1:8000' } }); "
                "globalThis.matchMedia = () => ({ matches: false, addEventListener: noop, removeEventListener: noop }); "
                "globalThis.ResizeObserver = class ResizeObserver { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } }; "
                "globalThis.MutationObserver = class MutationObserver { observe() { return undefined; } disconnect() { return undefined; } }; "
                "globalThis.CustomEvent = class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail ?? null; } }; "
                "globalThis.EventSource = class EventSource { constructor() { this.readyState = 1; } close() { return undefined; } addEventListener() { return undefined; } removeEventListener() { return undefined; } }; "
                "globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => '' }); "
                "globalThis.localStorage = { getItem: () => null, setItem: noop, removeItem: noop }; "
                "globalThis.sessionStorage = { getItem: () => null, setItem: noop, removeItem: noop }; "
                "const listeners = new Map(); "
                "globalThis.document = { "
                "body: createElement(), documentElement: createElement(), visibilityState: 'visible', "
                "getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], "
                "createElement, addEventListener(type, listener) { "
                "if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(listener); "
                "}, "
                "removeEventListener(type, listener) { "
                "const next = (listeners.get(type) || []).filter(item => item !== listener); listeners.set(type, next); "
                "}, "
                "dispatchEvent(event) { (listeners.get(event.type) || []).forEach(listener => listener(event)); return true; } "
                "}; "
                f"const mod = await import({module_path.as_uri()!r}); "
                "console.log(typeof mod.initializeSubagentRail);"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert completed.stdout.strip() == "function"


def test_new_session_draft_opens_without_creating_session() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "newSessionDraft.js"
    )
    state_path = repo_root / "frontend" / "dist" / "js" / "core" / "state.js"

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "const noop = () => undefined; "
                "const createClassList = () => { const names = new Set(); return { add: (...items) => items.forEach(item => names.add(item)), remove: (...items) => items.forEach(item => names.delete(item)), toggle: (item, force) => { const next = force ?? !names.has(item); if (next) names.add(item); else names.delete(item); return next; }, contains: item => names.has(item) }; }; "
                "const createElement = () => { const element = {"
                "innerHTML: '', textContent: '', value: 'stale prompt', hidden: false, disabled: true, style: {}, dataset: {}, childNodes: [], parentNode: null, nextSibling: null, scrollHeight: 32, className: '', "
                "classList: createClassList(), _listeners: new Map(), setAttribute(name, value) { this[name] = value; }, removeAttribute(name) { delete this[name]; }, "
                "addEventListener(name, handler) { if (!this._listeners.has(name)) this._listeners.set(name, []); this._listeners.get(name).push(handler); }, "
                "removeEventListener(name, handler) { const next = (this._listeners.get(name) || []).filter(item => item !== handler); this._listeners.set(name, next); }, "
                "dispatchEvent(event) { (this._listeners.get(event.type) || []).forEach(handler => handler(event)); return true; }, "
                "appendChild(child) { child.parentNode = this; this.childNodes.push(child); return child; }, "
                "insertBefore(child, before) { child.parentNode = this; const index = before ? this.childNodes.indexOf(before) : -1; if (index >= 0) this.childNodes.splice(index, 0, child); else this.childNodes.push(child); return child; }, "
                "remove() { if (!this.parentNode) return; this.parentNode.childNodes = this.parentNode.childNodes.filter(item => item !== this); this.parentNode = null; }, "
                "querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }, "
                "querySelectorAll(selector) { const matches = []; const visit = node => { const classMatch = selector.startsWith('.') && String(node.className || '').split(' ').includes(selector.slice(1)); const idMatch = selector.startsWith('#') && node.id === selector.slice(1); if (classMatch || idMatch) matches.push(node); (node.childNodes || []).forEach(visit); }; this.childNodes.forEach(visit); return matches; }, scrollIntoView: noop, focus() { this.focused = true; }"
                "}; return element; }; "
                "globalThis.window = globalThis; "
                "Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { language: 'en-US', clipboard: { writeText: async () => undefined } } }); "
                "Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://127.0.0.1:8000' } }); "
                "globalThis.matchMedia = () => ({ matches: false, addEventListener: noop, removeEventListener: noop }); "
                "globalThis.ResizeObserver = class ResizeObserver { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } }; "
                "globalThis.MutationObserver = class MutationObserver { observe() { return undefined; } disconnect() { return undefined; } }; "
                "globalThis.CustomEvent = class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail ?? null; } }; "
                "globalThis.EventSource = class EventSource { constructor() { this.readyState = 1; } close() { return undefined; } addEventListener() { return undefined; } removeEventListener() { return undefined; } }; "
                "globalThis.fetch = async () => { throw new Error('draft open must not create a session'); }; "
                "globalThis.localStorage = { getItem: () => null, setItem: noop, removeItem: noop }; "
                "globalThis.sessionStorage = { getItem: () => null, setItem: noop, removeItem: noop }; "
                "const listeners = new Map(); "
                "const elements = new Map(); "
                "const chatMessages = createElement(); "
                "const chatContainer = createElement(); "
                "const projectView = createElement(); "
                "const observabilityView = createElement(); "
                "const observabilityButton = createElement(); "
                "const inputContainer = createElement(); "
                "const chatForm = createElement(); "
                "const inputWrapper = createElement(); "
                "const inputControls = createElement(); "
                "const composerSlot = createElement(); "
                "const promptInput = createElement(); "
                "const sendBtn = createElement(); "
                "const tokenUsage = createElement(); "
                "chatForm.id = 'chat-form'; "
                "inputWrapper.className = 'input-wrapper'; "
                "inputControls.className = 'input-controls'; "
                "observabilityView.style.display = 'block'; "
                "observabilityButton.classList.add('active'); "
                "chatContainer.appendChild(chatMessages); "
                "chatContainer.appendChild(inputContainer); "
                "inputContainer.appendChild(chatForm); "
                "inputContainer.appendChild(inputWrapper); "
                "inputContainer.appendChild(inputControls); "
                "elements.set('#chat-messages', chatMessages); "
                "elements.set('.chat-container', chatContainer); "
                "elements.set('#project-view', projectView); "
                "elements.set('#observability-view', observabilityView); "
                "elements.set('#observability-btn', observabilityButton); "
                "elements.set('#input-container', inputContainer); "
                "elements.set('#prompt-input', promptInput); "
                "elements.set('#send-btn', sendBtn); "
                "elements.set('#session-token-usage', tokenUsage); "
                "globalThis.document = { "
                "body: createElement(), documentElement: createElement(), visibilityState: 'visible', "
                "getElementById: (id) => id === 'new-session-draft-composer-slot' ? composerSlot : elements.get(`#${id}`) ?? null, "
                "querySelector: (selector) => elements.get(selector) ?? null, querySelectorAll: () => [], "
                "createElement, addEventListener(type, listener) { "
                "if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(listener); "
                "}, "
                "removeEventListener(type, listener) { "
                "const next = (listeners.get(type) || []).filter(item => item !== listener); listeners.set(type, next); "
                "}, "
                "dispatchEvent(event) { (listeners.get(event.type) || []).forEach(listener => listener(event)); return true; } "
                "}; "
                "document.body.classList.add('observability-mode'); "
                f"const mod = await import({module_path.as_uri()!r}); "
                f"const stateMod = await import({state_path.as_uri()!r}); "
                "mod.openNewSessionDraft(''); "
                "if (stateMod.state.currentSessionId !== null) throw new Error('draft created a current session'); "
                "if (stateMod.state.pendingNewSessionActive !== true) throw new Error('draft state was not activated'); "
                "if (stateMod.state.pendingNewSessionWorkspaceId !== '') throw new Error('draft required a workspace before opening'); "
                "if (!chatMessages.innerHTML.includes('new-session-draft-page')) throw new Error('draft page was not rendered'); "
                "if (observabilityView.style.display !== 'none') throw new Error('observability view was not hidden'); "
                "if (observabilityButton.classList.contains('active')) throw new Error('observability button stayed active'); "
                "if (document.body.classList.contains('observability-mode')) throw new Error('observability mode class stayed active'); "
                "if (chatMessages.innerHTML.includes('new-session-workspace-selector')) throw new Error('workspace selector should not be a standalone card'); "
                "if (!chatMessages.innerHTML.includes('new-session-quick-grid')) throw new Error('quick start grid was not rendered'); "
                "if (!chatMessages.innerHTML.includes('常用能力')) throw new Error('common capabilities heading was not rendered'); "
                "if (!chatMessages.innerHTML.includes('快捷操作')) throw new Error('quick actions heading was not rendered'); "
                "if (chatMessages.innerHTML.includes('查看全部模板')) throw new Error('template link should not be rendered'); "
                "if (chatMessages.innerHTML.includes('查看帮助文档')) throw new Error('help docs link should not be rendered'); "
                "if (!chatMessages.innerHTML.includes('new-session-draft-aside')) throw new Error('setup guide was not rendered'); "
                "if (!chatMessages.innerHTML.includes('定时任务')) throw new Error('scheduled task card was not rendered'); "
                "if (chatMessages.innerHTML.includes('从仓库开始')) throw new Error('repository card should be replaced'); "
                "const actionRow = inputContainer.childNodes.find(child => child.className === 'new-session-draft-action-row'); "
                "if (!actionRow) throw new Error('composer mention action row was not inserted'); "
                "if (!actionRow.innerHTML.includes('data-draft-workspace-menu')) throw new Error('workspace dropdown trigger was not rendered between input and mode controls'); "
                "if (!actionRow.innerHTML.includes('new-session-workspace-select-title')) throw new Error('workspace trigger title was not rendered'); "
                "if (!actionRow.innerHTML.includes('new-session-workspace-select-path')) throw new Error('workspace trigger path detail was not rendered'); "
                "if (!actionRow.innerHTML.includes('data-draft-add-workspace')) throw new Error('new workspace action was not rendered'); "
                "if (actionRow.innerHTML.includes('new-session-workspace-popover')) throw new Error('workspace popover should not open by default or when add action renders'); "
                "const mentionHint = inputWrapper.childNodes.find(child => child.className === 'new-session-draft-mention-hint'); "
                "if (!mentionHint) throw new Error('mention hint was not inserted inside the input wrapper'); "
                "if (!mentionHint.innerHTML.includes('new-session-collab-chip')) throw new Error('multi-agent hint was not rendered inside input wrapper'); "
                "promptInput.value = 'hello'; "
                "promptInput.dispatchEvent(new Event('input')); "
                "if (!mentionHint.classList.contains('is-hidden')) throw new Error('mention hint should hide while typing'); "
                "promptInput.value = ''; "
                "promptInput.dispatchEvent(new Event('input')); "
                "if (mentionHint.classList.contains('is-hidden')) throw new Error('mention hint should return when the prompt is empty'); "
                "if (inputContainer.parentNode !== composerSlot) throw new Error('composer was not moved into the draft page'); "
                "if (promptInput.value !== '') throw new Error('prompt was not cleared'); "
                "if (!String(promptInput.placeholder || '').includes('PR')) throw new Error('draft placeholder was not applied'); "
                "if (sendBtn.disabled !== false) throw new Error('send button was not enabled'); "
                "mod.clearNewSessionDraft(); "
                "if (inputContainer.parentNode !== chatContainer) throw new Error('composer was not restored'); "
                "if (chatMessages.innerHTML.includes('new-session-draft-page')) throw new Error('draft page markup was not cleared'); "
                "console.log('draft-opened');"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node draft import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert completed.stdout.strip() == "draft-opened"
    css = (
        repo_root / "frontend" / "dist" / "css" / "components" / "interface.css"
    ).read_text(encoding="utf-8")
    draft_js = module_path.read_text(encoding="utf-8")
    assert ".chat-container.is-new-session-draft .chat-scroll" in css
    assert "overflow-y: auto;" in css
    assert ".new-session-draft-mention-hint .new-session-mention-action" in css
    assert "color: inherit;" in css
    assert "#input-container .composer-preset-select" in css
    assert "appearance: none;" in css
    assert "#input-container .composer-mode-inline-select:focus" in css
    assert (
        "bindWorkspaceSelectorInteractions(els.inputContainer || root);" not in draft_js
    )


def test_new_session_draft_creation_does_not_reuse_previous_session() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "newSessionDraft.js"
    )
    state_path = repo_root / "frontend" / "dist" / "js" / "core" / "state.js"

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "const noop = () => undefined; "
                "const createClassList = () => { const names = new Set(); return { add: (...items) => items.forEach(item => names.add(item)), remove: (...items) => items.forEach(item => names.delete(item)), toggle: (item, force) => { const next = force ?? !names.has(item); if (next) names.add(item); else names.delete(item); return next; }, contains: item => names.has(item) }; }; "
                "const createElement = () => { const element = {"
                "innerHTML: '', textContent: '', value: '', hidden: false, disabled: false, style: {}, dataset: {}, childNodes: [], parentNode: null, nextSibling: null, scrollHeight: 32, className: '', "
                "classList: createClassList(), setAttribute(name, value) { this[name] = value; }, getAttribute(name) { return this[name] ?? ''; }, removeAttribute(name) { delete this[name]; }, addEventListener: noop, removeEventListener: noop, "
                "appendChild(child) { child.parentNode = this; this.childNodes.push(child); return child; }, "
                "insertBefore(child, before) { child.parentNode = this; const index = before ? this.childNodes.indexOf(before) : -1; if (index >= 0) this.childNodes.splice(index, 0, child); else this.childNodes.push(child); return child; }, "
                "remove() { if (!this.parentNode) return; this.parentNode.childNodes = this.parentNode.childNodes.filter(item => item !== this); this.parentNode = null; }, "
                "querySelector(selector) { if (selector.startsWith('.')) return this.childNodes.find(child => String(child.className || '').split(' ').includes(selector.slice(1))) ?? null; if (selector.startsWith('#')) return this.childNodes.find(child => child.id === selector.slice(1)) ?? null; return null; }, "
                "querySelectorAll(selector) { const item = this.querySelector(selector); return item ? [item] : []; }, scrollIntoView: noop, focus() { this.focused = true; }"
                "}; return element; }; "
                "globalThis.window = globalThis; "
                "Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { language: 'en-US', clipboard: { writeText: async () => undefined } } }); "
                "Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://127.0.0.1:8000' } }); "
                "globalThis.matchMedia = () => ({ matches: false, addEventListener: noop, removeEventListener: noop }); "
                "globalThis.ResizeObserver = class ResizeObserver { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } }; "
                "globalThis.MutationObserver = class MutationObserver { observe() { return undefined; } disconnect() { return undefined; } }; "
                "globalThis.CustomEvent = class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail ?? null; } }; "
                "globalThis.localStorage = { getItem: () => null, setItem: noop, removeItem: noop }; "
                "globalThis.sessionStorage = { getItem: () => null, setItem: noop, removeItem: noop }; "
                "const fetchCalls = []; "
                "let sessionCounter = 0; "
                "let failTopology = false; "
                "globalThis.fetch = async (url, options = {}) => { "
                "fetchCalls.push({ url: String(url), method: String(options.method || 'GET').toUpperCase(), body: options.body || '' }); "
                "if (String(url) === '/api/workspaces') return { ok: true, json: async () => [{ workspace_id: 'workspace-1', root_path: 'C:\\\\Users\\\\yex\\\\Documents\\\\workspace\\\\agent-teams' }] }; "
                "if (String(url) === '/api/sessions' && String(options.method || 'GET').toUpperCase() === 'POST') { sessionCounter += 1; return { ok: true, json: async () => ({ session_id: `new-session-${sessionCounter}`, workspace_id: 'workspace-1' }) }; } "
                "if (String(url).endsWith('/topology') && String(options.method || 'GET').toUpperCase() === 'PATCH') { if (failTopology) return { ok: false, status: 500, json: async () => ({ detail: 'topology failed' }) }; return { ok: true, json: async () => ({ session_id: 'new-session-1', workspace_id: 'workspace-1' }) }; } "
                "throw new Error(`unexpected fetch ${url}`); "
                "}; "
                "const listeners = new Map(); "
                "const elements = new Map(); "
                "const chatMessages = createElement(); "
                "const chatContainer = createElement(); "
                "const projectView = createElement(); "
                "const inputContainer = createElement(); "
                "const chatForm = createElement(); "
                "const inputWrapper = createElement(); "
                "const inputControls = createElement(); "
                "const composerSlot = createElement(); "
                "const promptInput = createElement(); "
                "const sendBtn = createElement(); "
                "chatForm.id = 'chat-form'; "
                "inputWrapper.className = 'input-wrapper'; "
                "inputControls.className = 'input-controls'; "
                "chatContainer.appendChild(chatMessages); "
                "chatContainer.appendChild(inputContainer); "
                "inputContainer.appendChild(chatForm); "
                "inputContainer.appendChild(inputWrapper); "
                "inputContainer.appendChild(inputControls); "
                "elements.set('#chat-messages', chatMessages); "
                "elements.set('.chat-container', chatContainer); "
                "elements.set('#project-view', projectView); "
                "elements.set('#input-container', inputContainer); "
                "elements.set('#prompt-input', promptInput); "
                "elements.set('#send-btn', sendBtn); "
                "globalThis.document = { "
                "body: createElement(), documentElement: createElement(), visibilityState: 'visible', "
                "getElementById: (id) => id === 'new-session-draft-composer-slot' ? composerSlot : elements.get(`#${id}`) ?? null, "
                "querySelector: (selector) => elements.get(selector) ?? null, querySelectorAll: () => [], createElement, "
                "addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(listener); }, "
                "removeEventListener(type, listener) { const next = (listeners.get(type) || []).filter(item => item !== listener); listeners.set(type, next); }, "
                "dispatchEvent(event) { (listeners.get(event.type) || []).forEach(listener => listener(event)); return true; } "
                "}; "
                f"const mod = await import({module_path.as_uri()!r}); "
                f"const stateMod = await import({state_path.as_uri()!r}); "
                "mod.openNewSessionDraft('workspace-1'); "
                "await new Promise(resolve => setTimeout(resolve, 0)); "
                "const actionRow = inputContainer.childNodes.find(child => child.className === 'new-session-draft-action-row'); "
                "if (!actionRow.innerHTML.includes('agent-teams')) throw new Error('workspace directory name was not rendered'); "
                "if (!actionRow.innerHTML.includes('C:\\\\Users\\\\yex\\\\Documents\\\\workspace\\\\agent-teams')) throw new Error('workspace absolute path was not rendered'); "
                "chatMessages.innerHTML = 'old session history'; "
                "stateMod.state.currentSessionId = 'old-session'; "
                "stateMod.state.pendingNewSessionWorkspaceId = 'workspace-1'; "
                "const sessionId = await mod.ensureSessionForNewSessionDraft(); "
                "if (sessionId !== 'new-session-1') throw new Error(`expected new session, got ${sessionId}`); "
                "if (stateMod.state.currentSessionId !== 'new-session-1') throw new Error('current session did not switch to the newly created session'); "
                "if (chatMessages.innerHTML !== '') throw new Error('old session history was not cleared before starting the new session'); "
                "const postCalls = fetchCalls.filter(call => call.url === '/api/sessions' && call.method === 'POST'); "
                "if (postCalls.length !== 1) throw new Error('draft creation did not POST exactly one new session'); "
                "mod.openNewSessionDraft('workspace-1'); "
                "await new Promise(resolve => setTimeout(resolve, 0)); "
                "chatMessages.innerHTML = 'draft page with old content'; "
                "stateMod.state.pendingNewSessionWorkspaceId = 'workspace-1'; "
                "mod.applyDraftSessionTopology('normal', { normalRootRoleId: 'Main Agent' }); "
                "failTopology = true; "
                "let topologyError = ''; "
                "try { await mod.ensureSessionForNewSessionDraft(); } catch (error) { topologyError = error.message || String(error); } "
                "if (topologyError !== 'topology failed') throw new Error(`unexpected topology error ${topologyError}`); "
                "if (stateMod.state.pendingNewSessionActive !== false) throw new Error('draft state stayed active after topology failure'); "
                "if (stateMod.state.currentSessionId !== 'new-session-2') throw new Error('created session was not kept active after topology failure'); "
                "if (chatMessages.innerHTML !== '') throw new Error('draft history was not cleared after topology failure'); "
                "const finalPostCalls = fetchCalls.filter(call => call.url === '/api/sessions' && call.method === 'POST'); "
                "if (finalPostCalls.length !== 2) throw new Error('topology failure test should create exactly one additional session'); "
                "console.log('draft-created-new-session');"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node draft creation failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert completed.stdout.strip() == "draft-created-new-session"


def test_new_session_draft_clears_previous_round_timeline() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    draft_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "newSessionDraft.js"
    )
    rounds_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "timeline.js"
    )

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "const noop = () => undefined; "
                "const createClassList = () => { const names = new Set(); return { add: (...items) => items.forEach(item => names.add(item)), remove: (...items) => items.forEach(item => names.delete(item)), toggle: (item, force) => { const next = force ?? !names.has(item); if (next) names.add(item); else names.delete(item); return next; }, contains: item => names.has(item) }; }; "
                "const createElement = () => { const element = {"
                "id: '', innerHTML: '', textContent: '', value: '', hidden: false, disabled: false, style: {}, dataset: {}, childNodes: [], parentNode: null, nextSibling: null, scrollHeight: 32, scrollTop: 0, clientHeight: 800, offsetHeight: 120, offsetWidth: 270, className: '', "
                "classList: createClassList(), setAttribute(name, value) { this[name] = value; }, getAttribute(name) { return this[name] ?? ''; }, removeAttribute(name) { delete this[name]; }, addEventListener: noop, removeEventListener: noop, "
                "appendChild(child) { child.parentNode = this; this.childNodes.push(child); return child; }, "
                "insertBefore(child, before) { child.parentNode = this; const index = before ? this.childNodes.indexOf(before) : -1; if (index >= 0) this.childNodes.splice(index, 0, child); else this.childNodes.push(child); return child; }, "
                "remove() { if (!this.parentNode) return; this.parentNode.childNodes = this.parentNode.childNodes.filter(item => item !== this); this.parentNode = null; }, "
                "querySelector(selector) { return findElement(this, selector); }, querySelectorAll(selector) { const found = []; collectElements(this, selector, found); return found; }, "
                "getBoundingClientRect() { return { left: 0, top: 0, right: 1400, bottom: 900, width: 1400, height: 900 }; }, "
                "scrollIntoView: noop, focus() { this.focused = true; }"
                "}; return element; }; "
                "function matchesSelector(element, selector) { if (!element) return false; if (selector.startsWith('.')) return String(element.className || '').split(' ').includes(selector.slice(1)); if (selector.startsWith('#')) return element.id === selector.slice(1); return false; } "
                "function findElement(root, selector) { for (const child of root.childNodes || []) { if (matchesSelector(child, selector)) return child; const nested = findElement(child, selector); if (nested) return nested; } return null; } "
                "function collectElements(root, selector, found) { for (const child of root.childNodes || []) { if (matchesSelector(child, selector)) found.push(child); collectElements(child, selector, found); } } "
                "globalThis.window = globalThis; "
                "globalThis.requestAnimationFrame = (callback) => { callback(); return 1; }; "
                "globalThis.cancelAnimationFrame = noop; "
                "Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { language: 'en-US', clipboard: { writeText: async () => undefined } } }); "
                "Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://127.0.0.1:8000' } }); "
                "globalThis.matchMedia = () => ({ matches: false, addEventListener: noop, removeEventListener: noop }); "
                "globalThis.ResizeObserver = class ResizeObserver { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } }; "
                "globalThis.MutationObserver = class MutationObserver { observe() { return undefined; } disconnect() { return undefined; } }; "
                "globalThis.CustomEvent = class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail ?? null; } }; "
                "globalThis.fetch = async (url) => { if (String(url) === '/api/workspaces') return { ok: true, json: async () => [{ workspace_id: 'workspace-1', root_path: 'C:\\\\Users\\\\yex\\\\Documents\\\\workspace\\\\agent-teams' }] }; throw new Error(`unexpected fetch ${url}`); }; "
                "globalThis.localStorage = { getItem: () => null, setItem: noop, removeItem: noop }; "
                "globalThis.sessionStorage = { getItem: () => null, setItem: noop, removeItem: noop }; "
                "const listeners = new Map(); "
                "const elements = new Map(); "
                "const body = createElement(); "
                "const chatMessages = createElement(); chatMessages.id = 'chat-messages'; "
                "const chatContainer = createElement(); chatContainer.className = 'chat-container'; "
                "const projectView = createElement(); projectView.id = 'project-view'; "
                "const inputContainer = createElement(); inputContainer.id = 'input-container'; "
                "const chatForm = createElement(); chatForm.id = 'chat-form'; "
                "const inputWrapper = createElement(); inputWrapper.className = 'input-wrapper'; "
                "const inputControls = createElement(); inputControls.className = 'input-controls'; "
                "const composerSlot = createElement(); composerSlot.id = 'new-session-draft-composer-slot'; "
                "const promptInput = createElement(); promptInput.id = 'prompt-input'; "
                "const sendBtn = createElement(); sendBtn.id = 'send-btn'; "
                "const nav = createElement(); nav.id = 'round-nav-float'; nav.style.display = 'flex'; nav.innerHTML = 'old rounds'; "
                "body.appendChild(chatContainer); body.appendChild(nav); chatContainer.appendChild(chatMessages); chatContainer.appendChild(inputContainer); inputContainer.appendChild(chatForm); inputContainer.appendChild(inputWrapper); inputContainer.appendChild(inputControls); "
                "elements.set('#chat-messages', chatMessages); elements.set('.chat-container', chatContainer); elements.set('#project-view', projectView); elements.set('#input-container', inputContainer); elements.set('#prompt-input', promptInput); elements.set('#send-btn', sendBtn); elements.set('#round-nav-float', nav); "
                "globalThis.document = { "
                "body, documentElement: createElement(), visibilityState: 'visible', "
                "getElementById: (id) => id === 'new-session-draft-composer-slot' ? composerSlot : elements.get(`#${id}`) ?? findElement(body, `#${id}`), "
                "querySelector: (selector) => elements.get(selector) ?? findElement(body, selector), querySelectorAll: (selector) => { const found = []; collectElements(body, selector, found); return found; }, createElement, "
                "addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(listener); }, "
                "removeEventListener(type, listener) { const next = (listeners.get(type) || []).filter(item => item !== listener); listeners.set(type, next); }, "
                "dispatchEvent(event) { (listeners.get(event.type) || []).forEach(listener => listener(event)); return true; } "
                "}; "
                f"const rounds = await import({rounds_path.as_uri()!r}); "
                f"const draft = await import({draft_path.as_uri()!r}); "
                "rounds.createLiveRound('old-run', 'old prompt'); "
                "if (rounds.currentRounds.length !== 1) throw new Error('test setup did not create an old round'); "
                "draft.openNewSessionDraft('workspace-1'); "
                "if (rounds.currentRounds.length !== 0) throw new Error('draft page did not clear old round state'); "
                "if (nav.style.display !== 'none') throw new Error('draft page did not hide the previous round navigator'); "
                "if (String(nav.innerHTML || '') !== '') throw new Error('draft page did not clear previous round navigator markup'); "
                "console.log('draft-cleared-rounds');"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node draft round clearing failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert completed.stdout.strip() == "draft-cleared-rounds"
