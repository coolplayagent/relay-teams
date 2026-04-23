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

const QUICK_START_ITEMS = [
    {
        key: 'code_review',
        icon: 'code',
        promptKey: 'new_session_draft.quick.code_review_prompt',
    },
    {
        key: 'pr_summary',
        icon: 'branch',
        promptKey: 'new_session_draft.quick.pr_summary_prompt',
    },
    {
        key: 'requirements',
        icon: 'flow',
        promptKey: 'new_session_draft.quick.requirements_prompt',
    },
    {
        key: 'tests',
        icon: 'flask',
        promptKey: 'new_session_draft.quick.tests_prompt',
    },
    {
        key: 'debug',
        icon: 'warning',
        promptKey: 'new_session_draft.quick.debug_prompt',
    },
    {
        key: 'automation',
        icon: 'bot',
        promptKey: 'new_session_draft.quick.automation_prompt',
    },
];

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
        els.chatMessages.innerHTML = renderNewSessionDraftView();
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
        els.chatMessages.innerHTML = renderNewSessionDraftView();
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

function renderNewSessionDraftView() {
    return `
        <section class="new-session-draft-page" aria-label="${escapeHtml(t('new_session_draft.title'))}">
            <div class="new-session-draft-main">
                <div class="new-session-draft-hero">
                    <div class="new-session-draft-spark" aria-hidden="true">
                        ${renderIcon('spark')}
                    </div>
                    <h1>${escapeHtml(t('new_session_draft.hero_title'))}</h1>
                    <p>${escapeHtml(t('new_session_draft.hero_copy'))}</p>
                </div>
                <div id="new-session-draft-composer-slot" class="new-session-draft-composer-slot"></div>
                <div class="new-session-section-head">
                    <h2>${escapeHtml(t('new_session_draft.quick_title'))}</h2>
                </div>
                <div class="new-session-quick-grid">
                    ${QUICK_START_ITEMS.map(renderQuickStartItem).join('')}
                </div>
                <div class="new-session-section-head new-session-section-head-recent">
                    <h2>${escapeHtml(t('new_session_draft.recent_title'))}</h2>
                </div>
                <div class="new-session-recent-grid">
                    ${renderRecentCards()}
                </div>
            </div>
            <aside class="new-session-draft-aside" aria-label="${escapeHtml(t('new_session_draft.suggestion_title'))}">
                ${renderSuggestionPanel()}
                ${renderTipsPanel()}
            </aside>
        </section>
    `;
}

function renderQuickStartItem(item) {
    const title = t(`new_session_draft.quick.${item.key}.title`);
    const copy = t(`new_session_draft.quick.${item.key}.copy`);
    const prompt = t(item.promptKey);
    return `
        <button class="new-session-quick-card new-session-quick-card-${escapeHtml(item.key)}" type="button" data-draft-prompt="${escapeHtml(prompt)}">
            <span class="new-session-card-icon" aria-hidden="true">${renderIcon(item.icon)}</span>
            <span class="new-session-card-copy">
                <strong>${escapeHtml(title)}</strong>
                <span>${escapeHtml(copy)}</span>
            </span>
            <span class="new-session-card-arrow" aria-hidden="true">→</span>
        </button>
    `;
}

function renderRecentCards() {
    const recentSession = resolveRecentSession();
    return `
        ${renderContinueSessionCard(recentSession)}
        <button class="new-session-recent-card" type="button" data-draft-prompt="${escapeHtml(t('new_session_draft.recent.schedule_prompt'))}">
            <span class="new-session-card-icon new-session-card-icon-schedule" aria-hidden="true">${renderIcon('calendar')}</span>
            <span class="new-session-card-copy">
                <strong>${escapeHtml(t('new_session_draft.recent.schedule_title'))}</strong>
                <span>${escapeHtml(t('new_session_draft.recent.schedule_copy'))}</span>
            </span>
            <span class="new-session-card-arrow" aria-hidden="true">›</span>
        </button>
        <button class="new-session-recent-card" type="button" data-draft-open-gateway>
            <span class="new-session-card-icon new-session-card-icon-chat" aria-hidden="true">${renderIcon('chat')}</span>
            <span class="new-session-card-copy">
                <strong>${escapeHtml(t('new_session_draft.recent.im_title'))}</strong>
                <span>${escapeHtml(t('new_session_draft.recent.im_copy'))}</span>
            </span>
            <span class="new-session-card-arrow" aria-hidden="true">›</span>
        </button>
    `;
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

function renderContinueSessionCard(session) {
    if (!session) {
        return `
            <button class="new-session-recent-card" type="button" data-draft-prompt="${escapeHtml(t('new_session_draft.recent.continue_prompt'))}">
                <span class="new-session-card-icon new-session-card-icon-clock" aria-hidden="true">${renderIcon('clock')}</span>
                <span class="new-session-card-copy">
                    <strong>${escapeHtml(t('new_session_draft.recent.continue_title'))}</strong>
                    <span>${escapeHtml(t('new_session_draft.recent.continue_empty'))}</span>
                </span>
                <span class="new-session-card-arrow" aria-hidden="true">›</span>
            </button>
        `;
    }
    return `
        <button class="new-session-recent-card" type="button" data-draft-select-session data-session-id="${escapeHtml(session.sessionId)}">
            <span class="new-session-card-icon new-session-card-icon-clock" aria-hidden="true">${renderIcon('clock')}</span>
            <span class="new-session-card-copy">
                <strong>${escapeHtml(t('new_session_draft.recent.continue_title'))}</strong>
                <span>${escapeHtml(session.label || session.sessionId)}</span>
            </span>
            <span class="new-session-card-arrow" aria-hidden="true">›</span>
        </button>
    `;
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

function renderSuggestionPanel() {
    const rows = [
        ['1', t('new_session_draft.suggestion.workspace_title'), t('new_session_draft.suggestion.workspace_copy')],
        ['2', t('new_session_draft.suggestion.mode_title'), t('new_session_draft.suggestion.mode_copy')],
        ['3', t('new_session_draft.suggestion.role_title'), t('new_session_draft.suggestion.role_copy')],
        ['4', t('new_session_draft.suggestion.yolo_title'), t('new_session_draft.suggestion.yolo_copy')],
        ['5', t('new_session_draft.suggestion.input_title'), t('new_session_draft.suggestion.input_copy')],
    ];
    return `
        <section class="new-session-side-panel new-session-suggestion-panel">
            <h2>
                <span aria-hidden="true">${renderIcon('bulb')}</span>
                ${escapeHtml(t('new_session_draft.suggestion_title'))}
            </h2>
            <ol class="new-session-suggestion-list">
                ${rows.map(([number, title, copy]) => `
                    <li>
                        <span class="new-session-suggestion-number">${escapeHtml(number)}</span>
                        <span class="new-session-suggestion-copy">
                            <strong>${escapeHtml(title)}</strong>
                            <span>${escapeHtml(copy)}</span>
                        </span>
                    </li>
                `).join('')}
            </ol>
        </section>
    `;
}

function renderTipsPanel() {
    return `
        <section class="new-session-side-panel new-session-tips-panel">
            <h2>
                <span aria-hidden="true">${renderIcon('book')}</span>
                ${escapeHtml(t('new_session_draft.tips_title'))}
            </h2>
            <ul>
                <li>${escapeHtml(t('new_session_draft.tip.complex'))}</li>
                <li>${escapeHtml(t('new_session_draft.tip.orchestration'))}</li>
                <li>${escapeHtml(t('new_session_draft.tip.mention'))}</li>
                <li>${escapeHtml(t('new_session_draft.tip.subagents'))}</li>
            </ul>
        </section>
    `;
}

function renderIcon(name) {
    const icons = {
        spark: '<svg viewBox="0 0 24 24" fill="none"><path d="M12 3.5l1.6 4.7 4.9 1.8-4.9 1.8L12 16.5l-1.6-4.7-4.9-1.8 4.9-1.8L12 3.5ZM18.5 15.5l.8 2.1 2.2.9-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.9.8-2.1Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
        code: '<svg viewBox="0 0 24 24" fill="none"><path d="M8.5 8l-4 4 4 4M15.5 8l4 4-4 4M13 6.5l-2 11" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        branch: '<svg viewBox="0 0 24 24" fill="none"><path d="M7 5v14M7 7.5h4.5a4 4 0 0 1 4 4V16" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="7" cy="5" r="2" stroke="currentColor" stroke-width="1.7"/><circle cx="7" cy="19" r="2" stroke="currentColor" stroke-width="1.7"/><circle cx="15.5" cy="18" r="2" stroke="currentColor" stroke-width="1.7"/></svg>',
        flow: '<svg viewBox="0 0 24 24" fill="none"><rect x="9" y="3.5" width="6" height="4.5" rx="1.2" stroke="currentColor" stroke-width="1.7"/><rect x="4" y="16" width="6" height="4.5" rx="1.2" stroke="currentColor" stroke-width="1.7"/><rect x="14" y="16" width="6" height="4.5" rx="1.2" stroke="currentColor" stroke-width="1.7"/><path d="M12 8v4.5M7 16v-3.5h10V16" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        flask: '<svg viewBox="0 0 24 24" fill="none"><path d="M9 4h6M10 4v5.4l-4.1 7.2A2.2 2.2 0 0 0 7.8 20h8.4a2.2 2.2 0 0 0 1.9-3.4L14 9.4V4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M8.4 15h7.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        warning: '<svg viewBox="0 0 24 24" fill="none"><path d="M10.5 4.9L3.8 17a2 2 0 0 0 1.8 3h12.8a2 2 0 0 0 1.8-3L13.5 4.9a1.7 1.7 0 0 0-3 0Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M12 9v4M12 16.8v.1" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>',
        bot: '<svg viewBox="0 0 24 24" fill="none"><rect x="5" y="8" width="14" height="10" rx="3" stroke="currentColor" stroke-width="1.7"/><path d="M12 8V4.5M8.7 12.5h.1M15.2 12.5h.1M9.5 16h5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M3.5 12.5v2M20.5 12.5v2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        clock: '<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="7.5" stroke="currentColor" stroke-width="1.7"/><path d="M12 8v4.4l2.8 1.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        calendar: '<svg viewBox="0 0 24 24" fill="none"><path d="M6.5 5.5h11A2.5 2.5 0 0 1 20 8v9.5a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5V8a2.5 2.5 0 0 1 2.5-2.5Z" stroke="currentColor" stroke-width="1.7"/><path d="M8 3.5v4M16 3.5v4M4.5 10h15M8 14h2.5M13.5 14H16" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        chat: '<svg viewBox="0 0 24 24" fill="none"><path d="M5 6.5h14a2 2 0 0 1 2 2V15a2 2 0 0 1-2 2h-6.5l-4 2.8V17H5a2 2 0 0 1-2-2V8.5a2 2 0 0 1 2-2Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M7.5 10h9M7.5 13h5.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        bulb: '<svg viewBox="0 0 24 24" fill="none"><path d="M9.5 18h5M10 21h4M8 13.5a6 6 0 1 1 8 0c-.9.75-1.4 1.75-1.55 3h-4.9c-.15-1.25-.65-2.25-1.55-3Z" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        book: '<svg viewBox="0 0 24 24" fill="none"><path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H20v16H7.5A2.5 2.5 0 0 0 5 21.5v-16Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M5 18.5A2.5 2.5 0 0 1 7.5 16H20M9 7h7M9 10h5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
    };
    return icons[name] || icons.spark;
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
