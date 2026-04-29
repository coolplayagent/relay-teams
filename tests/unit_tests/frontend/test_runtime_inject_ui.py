from __future__ import annotations

import json
from pathlib import Path
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "frontend" / "dist"


def test_prompt_runtime_inject_queues_then_promotes_from_queue_card() -> None:
    prompt_script = (FRONTEND / "js" / "app" / "prompt.js").read_text(encoding="utf-8")
    api_script = (FRONTEND / "js" / "core" / "api" / "runs.js").read_text(
        encoding="utf-8"
    )

    assert "async function handleRuntimeInject" in prompt_script
    assert "clientMessageId" in prompt_script
    assert "client_message_id: clientMessageId" in prompt_script
    assert 'mode: "queued"' in prompt_script
    assert "upsertRuntimeInjectMessage(runId, localMessage)" in prompt_script
    assert "message_id: result?.message_id || localMessage.message_id" in prompt_script
    assert "hasActiveForegroundSubmission()" in prompt_script
    assert "upsertRoundInjectionMessage?.(runId, localMessage" not in prompt_script
    assert "upsertRoundInjectionMessage?.(runId, queuedMessage" not in prompt_script
    assert "upsertRoundInjectionMessage?.(runId, failedMessage" not in prompt_script
    assert "export async function handleRuntimeForceInject" in prompt_script
    assert "await forceQueuedInject(sourceRunId)" in prompt_script
    assert (
        "removeRuntimeInjectMessage(sourceRunId, result, { render: false })"
        in prompt_script
    )
    assert "replaceRuntimeInjectMessages(sourceRunId" not in prompt_script
    assert "attachRunStream(" not in prompt_script
    assert "startIntentStream(" in prompt_script
    assert "client_message_id" in api_script
    assert "body: JSON.stringify(payload)" in api_script
    assert "export async function forceQueuedInject" in api_script
    assert "/inject:force" in api_script


def test_runtime_inject_queue_card_action_is_wired_and_styled() -> None:
    index_html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    bootstrap_script = (FRONTEND / "js" / "app" / "bootstrap.js").read_text(
        encoding="utf-8"
    )
    stream_script = (FRONTEND / "js" / "core" / "stream.js").read_text(encoding="utf-8")
    interface_css = (FRONTEND / "css" / "components" / "interface.css").read_text(
        encoding="utf-8"
    )

    assert 'id="runtime-inject-queue"' in index_html
    assert 'id="interrupt-inject-btn"' not in index_html
    assert 'handleSend({ mode: "interrupt" })' not in bootstrap_script
    assert "agent-teams-force-inject-requested" in bootstrap_script
    assert "is-runtime-inject-mode" not in stream_script
    assert "renderRuntimeInjectQueue" in stream_script
    assert "els.sendBtn.disabled = isBusy && !runtimeInjectEnabled" in stream_script
    assert "#input-container.is-runtime-inject-mode #prompt-input" not in interface_css
    assert "#input-container .runtime-inject-queue" in interface_css
    assert "#input-container .runtime-inject-flush" in interface_css
    assert "#input-container #interrupt-inject-btn" not in interface_css


def test_event_router_projects_injection_events_to_composer_inject_queue() -> None:
    router_script = (FRONTEND / "js" / "core" / "eventRouter" / "index.js").read_text(
        encoding="utf-8"
    )
    message_renderer_facade = (
        FRONTEND / "js" / "components" / "messageRenderer.js"
    ).read_text(encoding="utf-8")
    inject_queue_script = (
        FRONTEND / "js" / "components" / "runtimeInjectQueue.js"
    ).read_text(encoding="utf-8")

    assert "handleInjection" in router_script
    assert "evType === 'injection_enqueued'" in router_script
    assert "upsertRuntimeInjectMessage" in router_script
    assert "upsertRoundInjectionMessage" not in router_script
    assert "appendStreamInjectionMarker" in router_script
    assert "appendStreamInjectionMarker" in message_renderer_facade
    assert "applyStreamOverlayEvent('injection_applied'" in router_script
    assert "renderCurrentSessionTimeline" not in router_script
    assert "if (evType === 'injection_applied')" in router_script
    assert "removeRuntimeInjectMessage(runId, projectedMessage)" in router_script
    assert (
        "removeRuntimeInjectMessage(runId, projectedMessage, { render: false })"
        in router_script
    )
    assert "if (source === 'user')" not in router_script
    assert "clearRuntimeInjectMessages(runId)" not in router_script
    assert "export function upsertRuntimeInjectMessage" in inject_queue_script
    assert "export function removeRuntimeInjectMessage" in inject_queue_script
    assert "applied_injection_ids" in inject_queue_script
    assert "superseded_injection_ids" in inject_queue_script
    assert "client_message_id" in inject_queue_script
    assert "superseded_client_message_ids" in inject_queue_script
    assert "normalizeAppliedInjectionIds" in inject_queue_script
    assert "normalizeSupersededClientMessageIds" in inject_queue_script
    assert "export function renderRuntimeInjectQueue" in inject_queue_script
    assert "mergeQueuedUserMessagesForDisplay" in inject_queue_script
    assert "data-inject-force" in inject_queue_script
    assert "lastForceableMessageIndex" in inject_queue_script
    assert "findFallbackRemovalIndex" in inject_queue_script
    assert "agent-teams-force-inject-requested" in inject_queue_script


def test_runtime_inject_queue_reconciles_local_and_server_messages() -> None:
    runner = textwrap.dedent(
        f"""
        class Element {{
            constructor() {{
                this.children = [];
                this.dataset = {{}};
                this.classList = {{ toggle() {{}} }};
                this.hidden = false;
                this.title = '';
                this._innerHTML = '';
            }}
            set innerHTML(value) {{
                this._innerHTML = String(value || '');
                if (!this._innerHTML) this.children = [];
            }}
            get innerHTML() {{
                return this._innerHTML;
            }}
            appendChild(child) {{
                this.children.push(child);
            }}
            addEventListener() {{}}
        }}
        const host = new Element();
        globalThis.document = {{
            querySelector(selector) {{
                return selector === '#runtime-inject-queue' ? host : null;
            }},
            querySelectorAll() {{
                return [];
            }},
            getElementById() {{
                return null;
            }},
            createElement() {{
                return new Element();
            }},
        }};
        globalThis.localStorage = {{
            getItem() {{ return null; }},
            setItem() {{}},
            removeItem() {{}},
        }};
        const stateModule = await import({json.dumps((FRONTEND / "js" / "core" / "state.js").as_uri())});
        const queue = await import({json.dumps((FRONTEND / "js" / "components" / "runtimeInjectQueue.js").as_uri())});
        stateModule.state.activeRunId = 'run-1';
        queue.upsertRuntimeInjectMessage('run-1', {{
            message_id: 'client-1',
            client_message_id: 'client-1',
            run_id: 'run-1',
            source: 'user',
            mode: 'queued',
            status: 'sending',
            content: '上一级的',
            queued_at: '2026-04-30T01:00:00Z',
        }});
        queue.upsertRuntimeInjectMessage('run-1', {{
            injection_id: 'inj-1',
            client_message_id: 'client-1',
            run_id: 'run-1',
            source: 'user',
            mode: 'queued',
            status: 'queued',
            content: '上一级的',
            queued_at: '2026-04-30T01:00:01Z',
        }});
        if (host.children.length !== 1) {{
            throw new Error(`expected one queue card, got ${{host.children.length}}`);
        }}
        const rendered = host.children[0].innerHTML;
        const count = (rendered.match(/上一级的/g) || []).length;
        if (count !== 1) {{
            throw new Error(`expected content once, got ${{count}}: ${{rendered}}`);
        }}
        queue.upsertRuntimeInjectMessage('run-2', {{
            message_id: 'other-run-message',
            run_id: 'run-2',
            source: 'user',
            mode: 'queued',
            status: 'queued',
            content: 'other run',
            queued_at: '2026-04-30T01:00:02Z',
        }});
        if (host.hidden || host.children.length !== 1) {{
            throw new Error('expected inactive run upsert to preserve active queue');
        }}
        queue.replaceRuntimeInjectMessages('run-2', [{{
            message_id: 'other-run-replaced',
            run_id: 'run-2',
            source: 'user',
            mode: 'queued',
            status: 'queued',
            content: 'other run replaced',
            queued_at: '2026-04-30T01:00:03Z',
        }}]);
        if (host.hidden || host.children.length !== 1) {{
            throw new Error('expected inactive run replace to preserve active queue');
        }}
        queue.removeRuntimeInjectMessage('run-1', {{
            superseded_client_message_ids: ['client-1'],
        }});
        if (!host.hidden || host.children.length !== 0) {{
            throw new Error('expected superseded client message id to clear the queue');
        }}
        """
    )

    subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        check=True,
        cwd=FRONTEND,
    )


def test_runtime_inject_messages_render_in_round_timeline() -> None:
    timeline_script = (
        FRONTEND / "js" / "components" / "rounds" / "timeline.js"
    ).read_text(encoding="utf-8")
    history_script = (
        FRONTEND / "js" / "components" / "messageRenderer" / "history.js"
    ).read_text(encoding="utf-8")
    history_css = (
        FRONTEND / "css" / "components" / "rounds" / "history.css"
    ).read_text(encoding="utf-8")
    stream_script = (
        FRONTEND / "js" / "components" / "messageRenderer" / "stream.js"
    ).read_text(encoding="utf-8")
    timeline_renderer = (
        FRONTEND / "js" / "components" / "messageTimeline" / "renderer.js"
    ).read_text(encoding="utf-8")

    assert "mergeRoundMessagesAndInjectionMessages" in timeline_script
    assert "injectionMessageToHistoryMessage" in timeline_script
    assert "entry_type: 'injection'" in timeline_script
    assert "renderInjectionMarker(container, msgItem)" in history_script
    assert "message-inject-marker" in history_script
    assert "isCollapsibleIntermediateNode" in history_script
    assert "message-inject-marker')" in history_script
    assert "function injectionSortAt" in timeline_script
    assert "function injectionQueuedAt" in timeline_script
    assert "message?.applied_at" in timeline_script
    assert (
        "rawMessage.applied_at\n        || rawMessage.queued_at" not in timeline_script
    )
    assert "message-inject" in history_script
    assert ".message.message-inject" not in history_css
    assert ".message-inject-marker" in history_css
    assert "message-inject-icon" in history_css
    assert "message-inject-label" not in history_script
    assert "message-inject-label" not in stream_script
    assert "message-inject-label" not in timeline_renderer
    assert "message-inject-label" not in history_css


def test_runtime_inject_i18n_keys_exist() -> None:
    i18n_script = (FRONTEND / "js" / "utils" / "i18n.js").read_text(encoding="utf-8")

    for key in (
        "inject.queue.placeholder",
        "inject.queue.status.queued",
        "inject.queue.status.inserting",
        "inject.queue.action.stop_insert",
        "inject.queue.error.insert_failed",
        "inject.message.label",
        "inject.message.subagent_label",
    ):
        assert i18n_script.count(key) >= 2
