/**
 * components/newSessionDraft.js
 * Draft state for starting a conversation without creating an empty session.
 */
import {
    fetchWorkspaces,
    pickWorkspace,
    startNewSession,
    updateSessionTopology,
} from '../core/api.js';
import {
    applyCurrentSessionRecord,
    resetCurrentSessionTopology,
    state,
} from '../core/state.js';
import { clearAllPanels } from './agentPanel.js';
import { clearContextIndicators } from './contextIndicators.js';
import { clearAllStreamState } from './messageRenderer.js';
import { clearSessionTimeline } from './rounds/timeline.js';
import { clearSessionTokenUsage } from './sessionTokenUsage.js';
import { clearActiveSubagentSession } from './subagentSessions.js';
import { renderNewSessionDraftView as renderNewSessionDraftMarkup } from './newSessionDraftView.js';
import { els } from '../utils/dom.js';
import { t } from '../utils/i18n.js';
import { showTextInputDialog } from '../utils/feedback.js';

let composerHomeParent = null;
let composerHomeNextSibling = null;
let composerHomePlaceholder = null;
let languageListenerBound = false;
let draftWorkspaces = [];
let draftWorkspaceLoadState = 'idle';
let draftWorkspaceError = '';
let draftWorkspaceBusy = false;
let draftWorkspaceMenuOpen = false;
let mentionHintInput = null;

export function isNewSessionDraftActive() {
    return state.pendingNewSessionActive === true;
}

export function openNewSessionDraft(workspaceId) {
    const safeWorkspaceId = String(workspaceId || '').trim();

    if (state.activeEventSource) {
        return;
    }

    bindLanguageRefresh();
    rememberComposerHome();
    draftWorkspaceError = '';
    draftWorkspaceLoadState = draftWorkspaces.length > 0 ? 'ready' : 'loading';

    state.pendingNewSessionActive = true;
    state.pendingNewSessionWorkspaceId = safeWorkspaceId;
    if (safeWorkspaceId) {
        state.currentWorkspaceId = safeWorkspaceId;
    }
    state.currentSessionId = null;
    state.currentSessionCanSwitchMode = false;
    state.currentMainView = 'new-session-draft';
    state.currentProjectViewWorkspaceId = null;
    state.currentFeatureViewId = null;
    resetCurrentSessionTopology();
    clearActiveSubagentSession();
    clearAllPanels();
    clearContextIndicators();
    clearSessionTokenUsage();
    clearAllStreamState();
    clearSessionTimeline();
    clearObservabilityMode();

    document.querySelectorAll('.session-item.active').forEach(item => {
        item.classList.remove('active');
    });

    const projectView = els.projectView;
    if (projectView) {
        projectView.style.display = 'none';
    }
    if (els.chatContainer) {
        els.chatContainer.style.display = 'flex';
        els.chatContainer.classList.add('is-new-session-draft');
    }
    if (els.chatMessages) {
        els.chatMessages.innerHTML = renderNewSessionDraftMarkup(resolveRecentSession());
    }
    moveComposerIntoDraft();
    if (els.promptInput) {
        els.promptInput.disabled = false;
        els.promptInput.value = '';
        els.promptInput.style.height = 'auto';
        els.promptInput.focus?.();
    }
    if (els.sendBtn) {
        els.sendBtn.disabled = false;
    }

    bindNewSessionDraftInteractions();
    void refreshDraftWorkspaces({ preferredWorkspaceId: safeWorkspaceId });
    document.dispatchEvent(new CustomEvent('agent-teams-new-session-draft-opened', {
        detail: { workspaceId: safeWorkspaceId },
    }));
}

export function clearNewSessionDraft() {
    restoreComposerPlacement();
    clearDraftPageMarkup();
    state.pendingNewSessionActive = false;
    state.pendingNewSessionWorkspaceId = null;
    if (state.currentMainView === 'new-session-draft') {
        state.currentMainView = 'session';
    }
}

export function applyDraftSessionTopology(sessionMode, {
    normalRootRoleId = null,
    orchestrationPresetId = null,
} = {}) {
    const normalizedMode = sessionMode === 'orchestration' ? 'orchestration' : 'normal';
    state.currentSessionMode = normalizedMode;
    state.currentSessionCanSwitchMode = false;
    state.currentNormalRootRoleId = normalizedMode === 'normal'
        ? String(normalRootRoleId || '').trim() || null
        : null;
    state.currentOrchestrationPresetId = normalizedMode === 'orchestration'
        ? String(orchestrationPresetId || '').trim() || null
        : null;
}

export async function ensureSessionForNewSessionDraft() {
    if (!isNewSessionDraftActive()) {
        return state.currentSessionId || '';
    }

    const workspaceId = String(state.pendingNewSessionWorkspaceId || '').trim();
    if (!workspaceId) {
        draftWorkspaceError = t('new_session_draft.workspace_required');
        draftWorkspaceMenuOpen = true;
        renderWorkspaceSelector();
        throw new Error(t('new_session_draft.workspace_required'));
    }
    const sessionMode = state.currentSessionMode === 'orchestration'
        ? 'orchestration'
        : 'normal';
    const normalRootRoleId = String(state.currentNormalRootRoleId || '').trim();
    const orchestrationPresetId = String(state.currentOrchestrationPresetId || '').trim();

    const created = await startNewSession(workspaceId);
    const sessionId = String(created?.session_id || '').trim();
    if (!sessionId) {
        throw new Error('Session creation did not return a session id.');
    }

    state.currentWorkspaceId = workspaceId;
    state.currentSessionId = sessionId;
    applyCurrentSessionRecord(created);

    let record = created;
    try {
        if (sessionMode === 'orchestration' || normalRootRoleId) {
            record = await updateSessionTopology(sessionId, {
                session_mode: sessionMode,
                normal_root_role_id: sessionMode === 'normal' ? normalRootRoleId : undefined,
                orchestration_preset_id: sessionMode === 'orchestration'
                    ? orchestrationPresetId
                    : null,
            });
            applyCurrentSessionRecord(record);
        }
    } catch (error) {
        finalizeCreatedDraftSession(sessionId, workspaceId, created);
        throw error;
    }

    finalizeCreatedDraftSession(sessionId, workspaceId, record);
    return sessionId;
}

function finalizeCreatedDraftSession(sessionId, workspaceId, record) {
    clearNewSessionDraft();
    if (els.chatMessages) {
        els.chatMessages.innerHTML = '';
    }
    document.dispatchEvent(new CustomEvent('agent-teams-new-session-draft-created', {
        detail: { sessionId, workspaceId, session: record },
    }));
}

function bindLanguageRefresh() {
    if (
        languageListenerBound
        || typeof document === 'undefined'
        || typeof document.addEventListener !== 'function'
    ) {
        return;
    }
    languageListenerBound = true;
    document.addEventListener('agent-teams-language-changed', () => {
        if (!isNewSessionDraftActive() || !els.chatMessages) {
            return;
        }
        els.chatMessages.innerHTML = renderNewSessionDraftMarkup(resolveRecentSession());
        moveComposerIntoDraft();
        bindNewSessionDraftInteractions();
        void refreshDraftWorkspaces({
            preferredWorkspaceId: String(state.pendingNewSessionWorkspaceId || '').trim(),
        });
    });
}

async function refreshDraftWorkspaces({ preferredWorkspaceId = '' } = {}) {
    draftWorkspaceLoadState = 'loading';
    draftWorkspaceError = '';
    renderWorkspaceSelector();
    try {
        const fetched = await fetchWorkspaces();
        draftWorkspaces = Array.isArray(fetched) ? fetched : [];
        const preferred = String(preferredWorkspaceId || '').trim();
        const selected = resolveSelectedWorkspaceId(preferred);
        state.pendingNewSessionWorkspaceId = selected;
        if (selected) {
            state.currentWorkspaceId = selected;
        }
        draftWorkspaceLoadState = 'ready';
        renderWorkspaceSelector();
    } catch (error) {
        draftWorkspaceLoadState = 'error';
        draftWorkspaceError = error?.message || String(error);
        renderWorkspaceSelector();
    }
}

function resolveSelectedWorkspaceId(preferredWorkspaceId = '') {
    const ids = new Set(draftWorkspaces
        .map(workspace => String(workspace?.workspace_id || '').trim())
        .filter(Boolean));
    const currentDraftId = String(state.pendingNewSessionWorkspaceId || '').trim();
    const currentStateId = String(state.currentWorkspaceId || '').trim();
    const preferred = String(preferredWorkspaceId || '').trim();
    if (currentDraftId && ids.has(currentDraftId)) return currentDraftId;
    if (preferred && ids.has(preferred)) return preferred;
    if (currentStateId && ids.has(currentStateId)) return currentStateId;
    if (draftWorkspaces.length === 1) {
        return String(draftWorkspaces[0]?.workspace_id || '').trim();
    }
    return '';
}

function renderWorkspaceSelector() {
    renderDraftComposerActionRow();
}

function renderDraftComposerActionRow() {
    const host = els.inputContainer?.querySelector?.('.new-session-draft-action-row') || null;
    if (!host) {
        return;
    }
    host.innerHTML = renderDraftComposerActionRowContent();
    bindWorkspaceSelectorInteractions(host);
}

function bindWorkspaceSelectorInteractions(host) {
    host.querySelector('[data-draft-workspace-menu]')?.addEventListener('click', () => {
        draftWorkspaceMenuOpen = !draftWorkspaceMenuOpen;
        renderWorkspaceSelector();
    });
    host.querySelectorAll('[data-draft-workspace-option]')?.forEach(button => {
        button.addEventListener('click', () => {
            const workspaceId = String(button.getAttribute('data-workspace-id') || '').trim();
            if (!workspaceId) {
                return;
            }
            state.pendingNewSessionWorkspaceId = workspaceId;
            state.currentWorkspaceId = workspaceId;
            draftWorkspaceError = '';
            draftWorkspaceMenuOpen = false;
            renderWorkspaceSelector();
        });
    });
    host.querySelector('[data-draft-workspace-clear]')?.addEventListener('click', () => {
        state.pendingNewSessionWorkspaceId = '';
        draftWorkspaceError = '';
        renderWorkspaceSelector();
    });
    host.querySelector('[data-draft-add-workspace]')?.addEventListener('click', () => {
        void addDraftWorkspace();
    });
}

async function addDraftWorkspace() {
    if (draftWorkspaceBusy) {
        return;
    }
    draftWorkspaceBusy = true;
    draftWorkspaceError = '';
    draftWorkspaceMenuOpen = false;
    renderWorkspaceSelector();
    try {
        let response = null;
        try {
            response = await pickWorkspace();
        } catch (error) {
            if (!isNativeDirectoryPickerUnavailable(error)) throw error;
            const rootPath = await requestWorkspaceRootPath();
            if (!rootPath) return;
            response = await pickWorkspace(rootPath);
        }
        const workspace = response?.workspace || null;
        const workspaceId = String(workspace?.workspace_id || '').trim();
        if (!workspaceId) {
            return;
        }
        state.pendingNewSessionWorkspaceId = workspaceId;
        state.currentWorkspaceId = workspaceId;
        draftWorkspaceMenuOpen = false;
        document.dispatchEvent(new CustomEvent('agent-teams-draft-workspace-added', {
            detail: { workspaceId, workspace },
        }));
        await refreshDraftWorkspaces({ preferredWorkspaceId: workspaceId });
    } catch (error) {
        draftWorkspaceError = error?.message || String(error);
        draftWorkspaceMenuOpen = false;
        renderWorkspaceSelector();
    } finally {
        draftWorkspaceBusy = false;
        renderWorkspaceSelector();
    }
}

function isNativeDirectoryPickerUnavailable(error) {
    return error?.status === 503 && error?.detail === 'Native directory picker is unavailable';
}

async function requestWorkspaceRootPath() {
    const enteredPath = await showTextInputDialog({
        title: t('sidebar.enter_project_path_title'),
        message: t('sidebar.enter_project_path_message'),
        tone: 'info',
        confirmLabel: t('sidebar.new_project'),
        cancelLabel: t('settings.action.cancel'),
        placeholder: '/path/to/project',
    });
    const rootPath = String(enteredPath || '').trim();
    return rootPath || null;
}

function rememberComposerHome() {
    if (!els.inputContainer || composerHomeParent) {
        return;
    }
    composerHomeParent = els.inputContainer.parentNode || null;
    composerHomeNextSibling = els.inputContainer.nextSibling || null;
}

function moveComposerIntoDraft() {
    if (!els.inputContainer) {
        return;
    }
    const slot = document.getElementById('new-session-draft-composer-slot');
    if (!slot) {
        return;
    }
    slot.appendChild(els.inputContainer);
    els.inputContainer.classList.add('is-new-session-draft-composer');
    els.inputContainer.setAttribute('data-draft-composer', 'true');
    ensureDraftComposerMentionHint();
    bindDraftMentionHintVisibility();
    ensureDraftComposerActionRow();
    if (els.promptInput) {
        if (composerHomePlaceholder === null) {
            composerHomePlaceholder = String(els.promptInput.getAttribute?.('placeholder') || '');
        }
        els.promptInput.setAttribute('placeholder', t('new_session_draft.input_placeholder'));
    }
    if (els.sendBtn) {
        els.sendBtn.title = t('new_session_draft.start_button');
        els.sendBtn.setAttribute('aria-label', t('new_session_draft.start_button'));
    }
}

function restoreComposerPlacement() {
    if (els.chatContainer) {
        els.chatContainer.classList.remove('is-new-session-draft');
    }
    if (!els.inputContainer) {
        return;
    }
    els.inputContainer.classList.remove('is-new-session-draft-composer');
    els.inputContainer.removeAttribute('data-draft-composer');
    removeDraftComposerActionRow();
    removeDraftComposerMentionHint();
    unbindDraftMentionHintVisibility();
    if (els.promptInput && composerHomePlaceholder !== null) {
        els.promptInput.setAttribute('placeholder', composerHomePlaceholder);
        composerHomePlaceholder = null;
    }
    if (composerHomeParent && els.inputContainer.parentNode !== composerHomeParent) {
        const nextSibling = composerHomeNextSibling
            && composerHomeNextSibling.parentNode === composerHomeParent
            ? composerHomeNextSibling
            : null;
        composerHomeParent.insertBefore(els.inputContainer, nextSibling);
    }
    if (els.sendBtn) {
        els.sendBtn.title = t('composer.send_title');
        els.sendBtn.setAttribute('aria-label', t('composer.send_title'));
    }
}

function clearDraftPageMarkup() {
    if (!els.chatMessages) {
        return;
    }
    const hasDraftPage = Boolean(
        els.chatMessages.querySelector?.('.new-session-draft-page'),
    ) || String(els.chatMessages.innerHTML || '').includes('new-session-draft-page');
    if (hasDraftPage) {
        els.chatMessages.innerHTML = '';
    }
}

function clearObservabilityMode() {
    const observabilityView = document.getElementById('observability-view');
    if (observabilityView) {
        observabilityView.style.display = 'none';
    }
    const observabilityButton = document.getElementById('observability-btn');
    observabilityButton?.classList?.remove?.('active');
    document.body?.classList?.remove?.('observability-mode');
}

function bindNewSessionDraftInteractions() {
    const root = els.chatMessages?.querySelector?.('.new-session-draft-page');
    if (!root) {
        return;
    }
    root.querySelectorAll('[data-draft-prompt]').forEach(button => {
        button.addEventListener('click', () => {
            setDraftPrompt(button.getAttribute('data-draft-prompt') || '');
        });
    });
    root.querySelector('[data-draft-select-session]')?.addEventListener('click', event => {
        const sessionId = String(event.currentTarget?.getAttribute('data-session-id') || '').trim();
        if (!sessionId) {
            return;
        }
        document.dispatchEvent(new CustomEvent('agent-teams-select-session', {
            detail: { sessionId },
        }));
    });
    root.querySelector('[data-draft-open-gateway]')?.addEventListener('click', () => {
        const gatewayButton = document.querySelector('.home-feature-item[data-feature-id="gateway"]');
        if (gatewayButton) {
            gatewayButton.click();
            return;
        }
        setDraftPrompt(t('new_session_draft.recent.im_prompt'));
    });
}

function setDraftPrompt(prompt) {
    if (!els.promptInput) {
        return;
    }
    els.promptInput.value = String(prompt || '').trim();
    els.promptInput.style.height = 'auto';
    els.promptInput.style.height = `${els.promptInput.scrollHeight}px`;
    els.promptInput.focus?.();
    els.promptInput.dispatchEvent(new Event('input', { bubbles: true }));
}

function ensureDraftComposerActionRow() {
    if (!els.inputContainer) {
        return;
    }
    let row = els.inputContainer.querySelector?.('.new-session-draft-action-row') || null;
    if (!row) {
        row = document.createElement('div');
        row.className = 'new-session-draft-action-row';
        const controls = els.inputContainer.querySelector?.('.input-controls') || null;
        if (controls?.parentNode === els.inputContainer) {
            els.inputContainer.insertBefore(row, controls);
        } else {
            els.inputContainer.appendChild(row);
        }
    }
    row.innerHTML = renderDraftComposerActionRowContent();
    bindWorkspaceSelectorInteractions(row);
}

function ensureDraftComposerMentionHint() {
    const wrapper = els.inputContainer?.querySelector?.('.input-wrapper') || null;
    if (!wrapper) {
        return;
    }
    let hint = wrapper.querySelector?.('.new-session-draft-mention-hint') || null;
    if (!hint) {
        hint = document.createElement('div');
        hint.className = 'new-session-draft-mention-hint';
        wrapper.appendChild(hint);
    }
    hint.innerHTML = renderDraftComposerMentionHintContent();
    syncDraftMentionHintVisibility();
}

function removeDraftComposerActionRow() {
    const row = els.inputContainer?.querySelector?.('.new-session-draft-action-row') || null;
    row?.remove?.();
}

function removeDraftComposerMentionHint() {
    const hint = els.inputContainer?.querySelector?.('.new-session-draft-mention-hint') || null;
    hint?.remove?.();
}

function bindDraftMentionHintVisibility() {
    if (!els.promptInput || mentionHintInput === els.promptInput) {
        return;
    }
    unbindDraftMentionHintVisibility();
    mentionHintInput = els.promptInput;
    mentionHintInput.addEventListener('input', syncDraftMentionHintVisibility);
    syncDraftMentionHintVisibility();
}

function unbindDraftMentionHintVisibility() {
    if (!mentionHintInput) {
        return;
    }
    mentionHintInput.removeEventListener?.('input', syncDraftMentionHintVisibility);
    mentionHintInput = null;
}

function syncDraftMentionHintVisibility() {
    const hint = els.inputContainer?.querySelector?.('.new-session-draft-mention-hint') || null;
    if (!hint) {
        return;
    }
    const hasText = String(els.promptInput?.value || '').trim().length > 0;
    hint.classList.toggle('is-hidden', hasText);
}

function renderDraftComposerActionRowContent() {
    const selectedWorkspaceId = String(state.pendingNewSessionWorkspaceId || '').trim();
    const menu = draftWorkspaceMenuOpen ? renderWorkspaceMenu(selectedWorkspaceId) : '';
    const workspaceBar = renderWorkspaceBar(selectedWorkspaceId);
    return `
        ${workspaceBar}
        ${menu}
        ${draftWorkspaceError ? `
            <div class="new-session-workspace-status is-error">${escapeHtml(draftWorkspaceError)}</div>
        ` : ''}
    `;
}

function renderDraftComposerMentionHintContent() {
    return `
        <span class="new-session-mention-chip">
            <span>${escapeHtml(t('new_session_draft.mention.prefix'))}</span>
            <span class="new-session-mention-action">${escapeHtml(t('new_session_draft.mention.repository'))}</span>
            <span class="new-session-mention-separator" aria-hidden="true">/</span>
            <span>${escapeHtml(t('new_session_draft.mention.files'))}</span>
            <span class="new-session-mention-separator" aria-hidden="true">/</span>
            <span>${escapeHtml(t('new_session_draft.mention.skills'))}</span>
        </span>
        <span class="new-session-collab-chip">${escapeHtml(t('new_session_draft.mention.collaboration'))}</span>
    `;
}

function renderWorkspaceBar(selectedWorkspaceId) {
    const selectedWorkspace = findDraftWorkspaceById(selectedWorkspaceId);
    const title = selectedWorkspace
        ? formatWorkspaceDirectoryName(selectedWorkspace)
        : t('new_session_draft.workspace.placeholder');
    const description = selectedWorkspace
        ? formatWorkspaceDescription(selectedWorkspace)
        : t('new_session_draft.workspace.required_hint');
    return `
        <div class="new-session-workspace-bar">
            <span class="new-session-workspace-label">${escapeHtml(t('new_session_draft.workspace.label'))}:</span>
            <button
                class="new-session-workspace-select${selectedWorkspace ? ' is-selected' : ''}"
                type="button"
                data-draft-workspace-menu
                aria-haspopup="listbox"
                aria-expanded="${draftWorkspaceMenuOpen ? 'true' : 'false'}"
                ${draftWorkspaceLoadState === 'loading' ? 'disabled' : ''}
            >
                <span class="new-session-workspace-select-copy">
                    <span class="new-session-workspace-select-title">${escapeHtml(title)}</span>
                    <span class="new-session-workspace-select-path">${escapeHtml(description)}</span>
                </span>
                <span class="new-session-workspace-select-chevron" aria-hidden="true">
                    <svg viewBox="0 0 16 16" focusable="false">
                        <path d="M4.5 6.5 8 10l3.5-3.5" />
                    </svg>
                </span>
            </button>
            <button class="new-session-workspace-create" type="button" data-draft-add-workspace ${draftWorkspaceBusy ? 'disabled' : ''}>
                <span aria-hidden="true">+</span>
                ${escapeHtml(draftWorkspaceBusy ? t('new_session_draft.workspace.adding') : t('new_session_draft.workspace.add'))}
            </button>
        </div>
    `;
}

function renderWorkspaceMenu(selectedWorkspaceId) {
    const workspaceOptions = draftWorkspaces
        .map(workspace => {
            const workspaceId = String(workspace?.workspace_id || '').trim();
            if (!workspaceId) {
                return '';
            }
            const selected = workspaceId === selectedWorkspaceId;
            return `
                <button class="new-session-workspace-option${selected ? ' is-selected' : ''}" type="button" role="option" aria-selected="${selected ? 'true' : 'false'}" data-draft-workspace-option data-workspace-id="${escapeHtml(workspaceId)}">
                    <span>${escapeHtml(formatWorkspaceDirectoryName(workspace))}</span>
                    <span class="new-session-workspace-option-path">${escapeHtml(formatWorkspaceDescription(workspace))}</span>
                </button>
            `;
        })
        .join('');
    const body = workspaceOptions || `
        <div class="new-session-workspace-empty">${escapeHtml(t('new_session_draft.workspace.none'))}</div>
    `;
    const status = draftWorkspaceLoadState === 'loading'
        ? `<div class="new-session-workspace-status">${escapeHtml(t('new_session_draft.workspace.loading'))}</div>`
        : '';
    return `
        <div class="new-session-workspace-popover" role="listbox" aria-label="${escapeHtml(t('new_session_draft.workspace.label'))}">
            <div class="new-session-workspace-popover-head">
                <strong>${escapeHtml(t('new_session_draft.workspace.label'))}</strong>
                ${selectedWorkspaceId ? `
                    <button type="button" data-draft-workspace-clear>${escapeHtml(t('new_session_draft.workspace.clear'))}</button>
                ` : ''}
            </div>
            <div class="new-session-workspace-options">
                ${body}
            </div>
            ${status}
        </div>
    `;
}

function findDraftWorkspaceById(workspaceId) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    if (!safeWorkspaceId) {
        return null;
    }
    return draftWorkspaces.find(
        workspace => String(workspace?.workspace_id || '').trim() === safeWorkspaceId,
    ) || null;
}

function formatWorkspaceDirectoryName(workspace) {
    const rootPath = String(workspace?.root_path || '').trim();
    if (rootPath) {
        const parts = rootPath.split(/[\/\\]/).filter(Boolean);
        return parts.at(-1) || rootPath;
    }
    const name = String(workspace?.name || workspace?.display_name || '').trim();
    const workspaceId = String(workspace?.workspace_id || '').trim();
    return name || workspaceId || t('sidebar.workspace');
}

function formatWorkspaceDescription(workspace) {
    const rootPath = String(workspace?.root_path || '').trim();
    const workspaceId = String(workspace?.workspace_id || '').trim();
    return rootPath || workspaceId || t('new_session_draft.workspace.selected');
}

function resolveRecentSession() {
    const sessionItems = Array.from(document.querySelectorAll('.session-item[data-session-id]'));
    for (const item of sessionItems) {
        const sessionId = String(item.getAttribute('data-session-id') || '').trim();
        if (!sessionId) {
            continue;
        }
        const label = String(item.querySelector?.('.session-label-text')?.textContent || '').trim();
        return {
            sessionId,
            label: label || sessionId,
        };
    }
    return null;
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
