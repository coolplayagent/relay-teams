/**
 * components/memoryView.js
 * Global Memory Bank browser.
 */
import {
    applyMemorySkillDraft,
    fetchMemories,
    fetchMemorySkillDrafts,
    fetchWorkspaces,
    generateMemorySkillDrafts,
    getMemory,
    getMemorySkillDraft,
    searchMemories,
    updateMemorySkillDraft,
    validateMemorySkillDraft,
} from '../core/api.js';
import { state } from '../core/state.js';
import { els } from '../utils/dom.js';
import { formatMessage, t } from '../utils/i18n.js';
import { showToast } from '../utils/feedback.js';
import { sysLog } from '../utils/logger.js';
import { clearAllPanels } from './agentPanel.js';
import { clearNewSessionDraft } from './newSessionDraft.js';
import { hideProjectView, prepareExternalFeatureView } from './projectView.js';
import { hideRoundNavigator } from './rounds/navigator.js';
import { setSubagentRailExpanded } from './subagentRail.js';

const MEMORY_FEATURE_ID = 'memory';
const MEMORY_LIMIT = 40;
const TIER_OPTIONS = ['', 'working', 'medium_term', 'persistent'];
const SCOPE_OPTIONS = ['', 'workspace', 'session', 'role'];
const STATUS_OPTIONS = ['active', '', 'superseded', 'expired'];
const DRAFT_STATUS_OPTIONS = ['', 'draft', 'validated', 'applying', 'applied', 'rejected'];
const DRAFT_KIND_OPTIONS = ['auto', 'skill', 'sop_skill'];

let memoryRequestToken = 0;
let languageBound = false;
let memoryState = createInitialMemoryState();

function createInitialMemoryState() {
    return {
        query: '',
        activeTab: 'entries',
        workspaceId: '',
        tier: '',
        scope: '',
        status: 'active',
        workspaces: [],
        rows: [],
        hitMeta: new Map(),
        totalCount: 0,
        selectedId: '',
        selectedEntry: null,
        selectedLoadingId: '',
        draftScopeKind: 'workspace',
        draftWorkspaceId: '',
        draftKind: 'auto',
        draftStatus: '',
        draftRows: [],
        draftTotalCount: 0,
        selectedDraftId: '',
        selectedDraft: null,
        selectedDraftLoadingId: '',
        generatingDrafts: false,
        savingDraft: false,
        loading: false,
        errorMessage: '',
    };
}

export async function openMemoryFeatureView() {
    enterMemoryFeatureView();
    const token = ++memoryRequestToken;
    memoryState = {
        ...memoryState,
        loading: true,
        errorMessage: '',
    };
    renderMemoryToolbar();
    renderMemoryContent();
    try {
        const workspaces = await fetchWorkspaces();
        if (!isCurrentMemoryToken(token)) {
            return;
        }
        memoryState = {
            ...memoryState,
            workspaces: Array.isArray(workspaces) ? workspaces : [],
        };
        if (memoryState.activeTab === 'skill-drafts') {
            await loadSkillDraftRows({ token });
        } else {
            await loadMemoryRows({ token });
        }
    } catch (error) {
        if (!isCurrentMemoryToken(token)) {
            return;
        }
        memoryState = {
            ...memoryState,
            loading: false,
            errorMessage: String(error?.message || error || ''),
        };
        renderMemoryToolbar();
        renderMemoryContent();
        sysLog(`Failed to load Memory Bank: ${error?.message || error}`, 'log-error');
    }
}

function enterMemoryFeatureView() {
    prepareExternalFeatureView(MEMORY_FEATURE_ID);
    state.activeSubagentSession = null;
    clearNewSessionDraft();
    clearAllPanels();
    hideRoundNavigator();
    setSubagentRailExpanded(false);
    bindLanguageRefresh();
}

function bindLanguageRefresh() {
    if (languageBound || typeof document?.addEventListener !== 'function') {
        return;
    }
    languageBound = true;
    document.addEventListener('agent-teams-language-changed', () => {
        if (state.currentFeatureViewId === MEMORY_FEATURE_ID) {
            void openMemoryFeatureView();
        }
    });
}

function isCurrentMemoryToken(token) {
    return (
        token === memoryRequestToken
        && state.currentFeatureViewId === MEMORY_FEATURE_ID
        && state.currentMainView === 'project'
    );
}

async function loadMemoryRows({ token = ++memoryRequestToken } = {}) {
    memoryState = {
        ...memoryState,
        loading: true,
        errorMessage: '',
    };
    renderMemoryToolbar();
    renderMemoryContent();
    try {
        const result = memoryState.query
            ? await searchMemoryRows()
            : await fetchMemoryRows();
        if (!isCurrentMemoryToken(token)) {
            return;
        }
        const rows = normalizeMemoryRows(result);
        const selectedId = rows.some(row => row.id === memoryState.selectedId)
            ? memoryState.selectedId
            : String(rows[0]?.id || '').trim();
        memoryState = {
            ...memoryState,
            rows,
            hitMeta: normalizeHitMeta(result),
            totalCount: Number(result?.total_count || rows.length || 0),
            selectedId,
            selectedEntry: selectedId === memoryState.selectedId ? memoryState.selectedEntry : null,
            selectedLoadingId: '',
            loading: false,
            errorMessage: '',
        };
        renderMemoryToolbar();
        renderMemoryContent();
        if (selectedId) {
            await loadSelectedMemory(selectedId, token);
        }
    } catch (error) {
        if (!isCurrentMemoryToken(token)) {
            return;
        }
        memoryState = {
            ...memoryState,
            loading: false,
            errorMessage: String(error?.message || error || ''),
        };
        renderMemoryToolbar();
        renderMemoryContent();
        sysLog(`Failed to query Memory Bank: ${error?.message || error}`, 'log-error');
    }
}

async function loadSkillDraftRows({ token = ++memoryRequestToken } = {}) {
    memoryState = {
        ...memoryState,
        loading: true,
        errorMessage: '',
    };
    renderMemoryToolbar();
    renderMemoryContent();
    try {
        const result = await fetchMemorySkillDrafts({
            scopeKind: memoryState.draftScopeKind,
            workspaceId: memoryState.draftWorkspaceId,
            status: memoryState.draftStatus,
            draftKind: memoryState.draftKind === 'auto' ? '' : memoryState.draftKind,
            limit: MEMORY_LIMIT,
        });
        if (!isCurrentMemoryToken(token)) {
            return;
        }
        const rows = normalizeSkillDraftRows(result);
        const selectedDraftId = rows.some(row => row.id === memoryState.selectedDraftId)
            ? memoryState.selectedDraftId
            : String(rows[0]?.id || '').trim();
        memoryState = {
            ...memoryState,
            draftRows: rows,
            draftTotalCount: Number(result?.total_count || rows.length || 0),
            selectedDraftId,
            selectedDraft: selectedDraftId === memoryState.selectedDraftId ? memoryState.selectedDraft : null,
            selectedDraftLoadingId: '',
            loading: false,
            errorMessage: '',
        };
        renderMemoryToolbar();
        renderMemoryContent();
        if (selectedDraftId) {
            await loadSelectedSkillDraft(selectedDraftId, token);
        }
    } catch (error) {
        if (!isCurrentMemoryToken(token)) {
            return;
        }
        memoryState = {
            ...memoryState,
            loading: false,
            errorMessage: String(error?.message || error || ''),
        };
        renderMemoryToolbar();
        renderMemoryContent();
        sysLog(`Failed to load memory skill drafts: ${error?.message || error}`, 'log-error');
    }
}

async function fetchMemoryRows() {
    return await fetchMemories({
        workspaceId: memoryState.workspaceId,
        tier: memoryState.tier,
        scope: memoryState.scope,
        status: memoryState.status,
        limit: MEMORY_LIMIT,
    });
}

async function searchMemoryRows() {
    const payload = {
        text_query: memoryState.query,
        limit: MEMORY_LIMIT,
        min_confidence: 0,
    };
    if (memoryState.workspaceId) {
        payload.workspace_id = memoryState.workspaceId;
    }
    if (memoryState.tier) {
        payload.tier = memoryState.tier;
    }
    if (memoryState.scope) {
        payload.scope = memoryState.scope;
    }
    payload.status = memoryState.status || null;
    return await searchMemories(payload);
}

function normalizeMemoryRows(result) {
    if (Array.isArray(result?.items)) {
        if (result.items.length > 0 && result.items[0]?.entry) {
            return result.items.map(hit => hit.entry).filter(Boolean);
        }
        return result.items.filter(Boolean);
    }
    return [];
}

function normalizeHitMeta(result) {
    const hitMeta = new Map();
    if (!Array.isArray(result?.items)) {
        return hitMeta;
    }
    for (const hit of result.items) {
        const id = String(hit?.entry?.id || '').trim();
        if (id) {
            hitMeta.set(id, {
                score: Number(hit?.score || 0),
                snippet: String(hit?.snippet || '').trim(),
            });
        }
    }
    return hitMeta;
}

function normalizeSkillDraftRows(result) {
    if (!Array.isArray(result?.items)) {
        return [];
    }
    return result.items.filter(Boolean);
}

async function loadSelectedMemory(memoryId, token = memoryRequestToken) {
    const summary = memoryState.rows.find(row => row.id === memoryId);
    if (!summary) {
        return;
    }
    memoryState = {
        ...memoryState,
        selectedId: memoryId,
        selectedLoadingId: memoryId,
        selectedEntry: memoryState.selectedEntry?.id === memoryId
            ? memoryState.selectedEntry
            : null,
    };
    renderMemoryContent();
    try {
        const entry = await getMemory(summary.workspace_id, memoryId);
        if (!isCurrentMemoryToken(token) || memoryState.selectedId !== memoryId) {
            return;
        }
        memoryState = {
            ...memoryState,
            selectedEntry: entry,
            selectedLoadingId: '',
        };
        renderMemoryContent();
    } catch (error) {
        if (!isCurrentMemoryToken(token) || memoryState.selectedId !== memoryId) {
            return;
        }
        memoryState = {
            ...memoryState,
            selectedLoadingId: '',
            selectedEntry: null,
        };
        renderMemoryContent();
        sysLog(`Failed to load memory entry: ${error?.message || error}`, 'log-error');
    }
}

async function loadSelectedSkillDraft(draftId, token = memoryRequestToken) {
    const summary = memoryState.draftRows.find(row => row.id === draftId);
    if (!summary) {
        return;
    }
    memoryState = {
        ...memoryState,
        selectedDraftId: draftId,
        selectedDraftLoadingId: draftId,
        selectedDraft: memoryState.selectedDraft?.id === draftId
            ? memoryState.selectedDraft
            : null,
    };
    renderMemoryContent();
    try {
        const draft = await getMemorySkillDraft(draftId);
        if (!isCurrentMemoryToken(token) || memoryState.selectedDraftId !== draftId) {
            return;
        }
        memoryState = {
            ...memoryState,
            selectedDraft: draft,
            selectedDraftLoadingId: '',
        };
        renderMemoryContent();
    } catch (error) {
        if (!isCurrentMemoryToken(token) || memoryState.selectedDraftId !== draftId) {
            return;
        }
        memoryState = {
            ...memoryState,
            selectedDraftLoadingId: '',
            selectedDraft: null,
        };
        renderMemoryContent();
        sysLog(`Failed to load memory skill draft: ${error?.message || error}`, 'log-error');
    }
}

function renderMemoryToolbar() {
    els.projectViewTitle?.closest?.('.project-view-toolbar')?.classList?.remove('is-hidden');
    if (els.projectViewTitle) {
        els.projectViewTitle.textContent = t('feature.memory.title');
    }
    if (els.projectViewSummary) {
        els.projectViewSummary.textContent = memoryState.loading
            ? t('feature.memory.loading')
            : formatMessage('feature.memory.summary', {
                count: String(
                    memoryState.activeTab === 'skill-drafts'
                        ? memoryState.draftTotalCount
                        : memoryState.totalCount,
                ),
            });
    }
    if (!els.projectViewToolbarActions) {
        return;
    }
    els.projectViewToolbarActions.innerHTML = `
        <div class="memory-toolbar-controls">
            <div class="memory-tabs" role="tablist" aria-label="${escapeAttribute(t('feature.memory.tabs'))}">
                <button class="memory-tab${memoryState.activeTab === 'entries' ? ' is-active' : ''}" type="button" data-memory-tab="entries">${escapeHtml(t('feature.memory.entries_tab'))}</button>
                <button class="memory-tab${memoryState.activeTab === 'skill-drafts' ? ' is-active' : ''}" type="button" data-memory-tab="skill-drafts">${escapeHtml(t('feature.memory.skill_drafts_tab'))}</button>
            </div>
            ${memoryState.activeTab === 'skill-drafts' ? renderSkillDraftToolbarControls() : renderEntryToolbarControls()}
            <button class="icon-btn memory-refresh-btn" type="button" title="${escapeAttribute(t('workspace_view.reload'))}" aria-label="${escapeAttribute(t('workspace_view.reload'))}" data-memory-refresh>
                <svg viewBox="0 0 24 24" fill="none" class="icon" aria-hidden="true">
                    <path d="M20 12a8 8 0 1 1-2.34-5.66" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                    <path d="M20 4.5v5h-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </button>
        </div>
        <button id="project-view-close" class="icon-btn" type="button" title="${escapeAttribute(t('workspace_view.back'))}" aria-label="${escapeAttribute(t('workspace_view.back'))}" data-project-view-close>
            <svg viewBox="0 0 24 24" fill="none" class="icon" aria-hidden="true">
                <path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        </button>
    `;
    bindMemoryToolbar();
}

function renderEntryToolbarControls() {
    return `
            <input
                class="memory-search-input"
                type="search"
                value="${escapeAttribute(memoryState.query)}"
                placeholder="${escapeAttribute(t('feature.memory.search_placeholder'))}"
                aria-label="${escapeAttribute(t('feature.memory.search_placeholder'))}"
                data-memory-search
            />
            <select class="memory-filter-select" data-memory-workspace aria-label="${escapeAttribute(t('feature.memory.workspace'))}">
                <option value="">${escapeHtml(t('feature.memory.all_workspaces'))}</option>
                ${memoryState.workspaces.map(renderWorkspaceOption).join('')}
            </select>
            <select class="memory-filter-select" data-memory-tier aria-label="${escapeAttribute(t('feature.memory.tier'))}">
                ${TIER_OPTIONS.map(value => renderFilterOption(value, 'tier')).join('')}
            </select>
            <select class="memory-filter-select" data-memory-scope aria-label="${escapeAttribute(t('feature.memory.scope'))}">
                ${SCOPE_OPTIONS.map(value => renderFilterOption(value, 'scope')).join('')}
            </select>
            <select class="memory-filter-select" data-memory-status aria-label="${escapeAttribute(t('feature.memory.status'))}">
                ${STATUS_OPTIONS.map(value => renderFilterOption(value, 'status')).join('')}
            </select>
    `;
}

function renderSkillDraftToolbarControls() {
    return `
        <select class="memory-filter-select" data-draft-scope aria-label="${escapeAttribute(t('feature.memory.drafts.scope'))}">
            <option value="workspace"${memoryState.draftScopeKind === 'workspace' ? ' selected' : ''}>${escapeHtml(t('feature.memory.drafts.workspace_scope'))}</option>
            <option value="cross_workspace"${memoryState.draftScopeKind === 'cross_workspace' ? ' selected' : ''}>${escapeHtml(t('feature.memory.drafts.cross_workspace_scope'))}</option>
        </select>
        <select class="memory-filter-select" data-draft-workspace aria-label="${escapeAttribute(t('feature.memory.workspace'))}">
            <option value="">${escapeHtml(t('feature.memory.all_workspaces'))}</option>
            ${memoryState.workspaces.map(renderDraftWorkspaceOption).join('')}
        </select>
        <select class="memory-filter-select" data-draft-kind aria-label="${escapeAttribute(t('feature.memory.drafts.kind'))}">
            ${DRAFT_KIND_OPTIONS.map(value => renderDraftKindOption(value)).join('')}
        </select>
        <select class="memory-filter-select" data-draft-status aria-label="${escapeAttribute(t('feature.memory.status'))}">
            ${DRAFT_STATUS_OPTIONS.map(value => renderDraftStatusOption(value)).join('')}
        </select>
        <button class="secondary-btn memory-generate-btn" type="button" data-draft-generate ${memoryState.generatingDrafts ? 'disabled' : ''}>${escapeHtml(t('feature.memory.drafts.generate'))}</button>
    `;
}

function renderWorkspaceOption(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    if (!workspaceId) {
        return '';
    }
    const selected = workspaceId === memoryState.workspaceId ? ' selected' : '';
    const label = formatWorkspaceLabel(workspace);
    return `<option value="${escapeAttribute(workspaceId)}"${selected}>${escapeHtml(label)}</option>`;
}

function renderDraftWorkspaceOption(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    if (!workspaceId) {
        return '';
    }
    const selected = workspaceId === memoryState.draftWorkspaceId ? ' selected' : '';
    return `<option value="${escapeAttribute(workspaceId)}"${selected}>${escapeHtml(formatWorkspaceLabel(workspace))}</option>`;
}

function renderFilterOption(value, field) {
    const safeValue = String(value || '').trim();
    const selected = safeValue === String(memoryState[field] || '').trim()
        ? ' selected'
        : '';
    const label = safeValue ? formatEnumLabel(safeValue) : t('feature.memory.any');
    return `<option value="${escapeAttribute(safeValue)}"${selected}>${escapeHtml(label)}</option>`;
}

function renderDraftKindOption(value) {
    const selected = value === memoryState.draftKind ? ' selected' : '';
    const label = value === 'auto' ? t('feature.memory.any') : formatEnumLabel(value);
    return `<option value="${escapeAttribute(value)}"${selected}>${escapeHtml(label)}</option>`;
}

function renderDraftStatusOption(value) {
    const selected = value === memoryState.draftStatus ? ' selected' : '';
    const label = value ? formatEnumLabel(value) : t('feature.memory.any');
    return `<option value="${escapeAttribute(value)}"${selected}>${escapeHtml(label)}</option>`;
}

function bindMemoryToolbar() {
    const controls = els.projectViewToolbarActions;
    controls.querySelector('[data-project-view-close]')?.addEventListener('click', () => {
        hideProjectView();
    });
    controls.querySelector('[data-memory-refresh]')?.addEventListener('click', () => {
        if (memoryState.activeTab === 'skill-drafts') {
            void loadSkillDraftRows();
        } else {
            void openMemoryFeatureView();
        }
    });
    for (const button of controls.querySelectorAll('[data-memory-tab]')) {
        button.addEventListener('click', () => {
            const tab = String(button.getAttribute('data-memory-tab') || '').trim();
            if (!tab || tab === memoryState.activeTab) {
                return;
            }
            memoryState = {
                ...memoryState,
                activeTab: tab,
                selectedId: tab === 'entries' ? memoryState.selectedId : '',
                selectedDraftId: tab === 'skill-drafts' ? memoryState.selectedDraftId : '',
                selectedEntry: tab === 'entries' ? memoryState.selectedEntry : null,
                selectedDraft: tab === 'skill-drafts' ? memoryState.selectedDraft : null,
            };
            if (tab === 'skill-drafts') {
                void loadSkillDraftRows();
            } else {
                void loadMemoryRows();
            }
        });
    }
    if (memoryState.activeTab === 'skill-drafts') {
        bindSkillDraftToolbar(controls);
        return;
    }
    controls.querySelector('[data-memory-search]')?.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            applyToolbarFilters();
        }
    });
    for (const selector of [
        '[data-memory-workspace]',
        '[data-memory-tier]',
        '[data-memory-scope]',
        '[data-memory-status]',
    ]) {
        controls.querySelector(selector)?.addEventListener('change', () => {
            applyToolbarFilters();
        });
    }
}

function bindSkillDraftToolbar(controls) {
    for (const selector of [
        '[data-draft-scope]',
        '[data-draft-workspace]',
        '[data-draft-kind]',
        '[data-draft-status]',
    ]) {
        controls.querySelector(selector)?.addEventListener('change', () => {
            applySkillDraftToolbarFilters();
        });
    }
    controls.querySelector('[data-draft-generate]')?.addEventListener('click', () => {
        void handleGenerateSkillDrafts();
    });
}

function applyToolbarFilters() {
    const controls = els.projectViewToolbarActions;
    memoryState = {
        ...memoryState,
        query: String(controls.querySelector('[data-memory-search]')?.value || '').trim(),
        workspaceId: String(controls.querySelector('[data-memory-workspace]')?.value || '').trim(),
        tier: String(controls.querySelector('[data-memory-tier]')?.value || '').trim(),
        scope: String(controls.querySelector('[data-memory-scope]')?.value || '').trim(),
        status: String(controls.querySelector('[data-memory-status]')?.value || '').trim(),
        selectedId: '',
        selectedEntry: null,
    };
    void loadMemoryRows();
}

function applySkillDraftToolbarFilters() {
    const controls = els.projectViewToolbarActions;
    memoryState = {
        ...memoryState,
        draftScopeKind: String(controls.querySelector('[data-draft-scope]')?.value || 'workspace').trim(),
        draftWorkspaceId: String(controls.querySelector('[data-draft-workspace]')?.value || '').trim(),
        draftKind: String(controls.querySelector('[data-draft-kind]')?.value || 'auto').trim(),
        draftStatus: String(controls.querySelector('[data-draft-status]')?.value || '').trim(),
        selectedDraftId: '',
        selectedDraft: null,
    };
    void loadSkillDraftRows();
}

async function handleGenerateSkillDrafts() {
    const workspaceId = String(memoryState.draftWorkspaceId || '').trim();
    const payload = {
        scope_kind: memoryState.draftScopeKind,
        draft_kind: memoryState.draftKind,
    };
    if (workspaceId) {
        payload.workspace_id = workspaceId;
        if (memoryState.draftScopeKind === 'cross_workspace') {
            payload.workspace_ids = [workspaceId];
        }
    }
    memoryState = {
        ...memoryState,
        generatingDrafts: true,
    };
    renderMemoryToolbar();
    try {
        const result = await generateMemorySkillDrafts(payload);
        const rows = normalizeSkillDraftRows(result);
        memoryState = {
            ...memoryState,
            generatingDrafts: false,
            selectedDraftId: String(rows[0]?.id || memoryState.selectedDraftId || '').trim(),
        };
        if (result?.error_message) {
            showToast({
                title: t('feature.memory.drafts.generate_failed'),
                message: String(result.error_message),
                tone: 'warning',
            });
        }
        await reloadSkillDraftRowsIfActive();
    } catch (error) {
        memoryState = {
            ...memoryState,
            generatingDrafts: false,
        };
        renderMemoryToolbar();
        showToast({
            title: t('feature.memory.drafts.generate_failed'),
            message: String(error?.message || error || ''),
            tone: 'error',
        });
    }
}

async function reloadSkillDraftRowsIfActive() {
    if (memoryState.activeTab !== 'skill-drafts') {
        return;
    }
    await loadSkillDraftRows();
}

function renderMemoryContent() {
    if (!els.projectViewContent) {
        return;
    }
    if (memoryState.errorMessage) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-empty-state is-error">
                <p>${escapeHtml(t('workspace_view.load_failed'))}</p>
                <p>${escapeHtml(memoryState.errorMessage)}</p>
            </div>
        `;
        return;
    }
    if (memoryState.activeTab === 'skill-drafts') {
        renderSkillDraftContent();
        return;
    }
    if (memoryState.loading && memoryState.rows.length === 0) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-empty-state is-feature-loading-state">
                <p>${escapeHtml(t('feature.memory.loading'))}</p>
            </div>
        `;
        return;
    }
    if (memoryState.rows.length === 0) {
        els.projectViewContent.innerHTML = `
            ${renderMemoryArchitectureMap()}
            <div class="workspace-view-empty-state">
                <p>${escapeHtml(t('feature.memory.empty'))}</p>
            </div>
        `;
        return;
    }
    els.projectViewContent.innerHTML = `
        ${renderMemoryArchitectureMap()}
        <section class="memory-view-shell" aria-label="${escapeAttribute(t('feature.memory.title'))}">
            <div class="memory-list" role="listbox" aria-label="${escapeAttribute(t('feature.memory.entries'))}">
                ${memoryState.rows.map(renderMemoryRow).join('')}
            </div>
            <div class="memory-detail" aria-live="polite">
                ${renderMemoryDetail()}
            </div>
        </section>
    `;
    bindMemoryRows();
}

function renderSkillDraftContent() {
    if (memoryState.loading && memoryState.draftRows.length === 0) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-empty-state is-feature-loading-state">
                <p>${escapeHtml(t('feature.memory.drafts.loading'))}</p>
            </div>
        `;
        return;
    }
    if (memoryState.draftRows.length === 0) {
        els.projectViewContent.innerHTML = `
            <section class="memory-draft-empty">
                <p>${escapeHtml(t('feature.memory.drafts.empty'))}</p>
            </section>
        `;
        return;
    }
    els.projectViewContent.innerHTML = `
        <section class="memory-draft-shell" aria-label="${escapeAttribute(t('feature.memory.skill_drafts_tab'))}">
            <div class="memory-draft-list" role="listbox" aria-label="${escapeAttribute(t('feature.memory.skill_drafts_tab'))}">
                ${memoryState.draftRows.map(renderSkillDraftRow).join('')}
            </div>
            <div class="memory-draft-detail" aria-live="polite">
                ${renderSkillDraftDetail()}
            </div>
        </section>
    `;
    bindSkillDraftRows();
    bindSkillDraftDetail();
}

function renderMemoryArchitectureMap() {
    const tiers = [
        {
            key: 'working',
            title: t('feature.memory.arch.working.title'),
            scope: t('feature.memory.arch.working.scope'),
            ttl: t('feature.memory.arch.working.ttl'),
            copy: t('feature.memory.arch.working.copy'),
        },
        {
            key: 'medium',
            title: t('feature.memory.arch.medium.title'),
            scope: t('feature.memory.arch.medium.scope'),
            ttl: t('feature.memory.arch.medium.ttl'),
            copy: t('feature.memory.arch.medium.copy'),
        },
        {
            key: 'persistent',
            title: t('feature.memory.arch.persistent.title'),
            scope: t('feature.memory.arch.persistent.scope'),
            ttl: t('feature.memory.arch.persistent.ttl'),
            copy: t('feature.memory.arch.persistent.copy'),
        },
    ];
    return `
        <section class="memory-architecture-map" aria-label="${escapeAttribute(t('feature.memory.arch.title'))}">
            <div class="memory-architecture-edge memory-architecture-source">
                <strong>${escapeHtml(t('feature.memory.arch.capture'))}</strong>
                <span>${escapeHtml(t('feature.memory.arch.capture_sources'))}</span>
            </div>
            <div class="memory-architecture-flow" aria-label="${escapeAttribute(t('feature.memory.arch.layers'))}">
                ${tiers.map((tier, index) => `
                    ${index > 0 ? renderArchitectureLink() : ''}
                    ${renderArchitectureTier(tier)}
                `).join('')}
            </div>
            <div class="memory-architecture-edge memory-architecture-output">
                <strong>${escapeHtml(t('feature.memory.arch.reuse'))}</strong>
                <span>${escapeHtml(t('feature.memory.arch.reuse_targets'))}</span>
            </div>
        </section>
    `;
}

function renderArchitectureTier(tier) {
    return `
        <article class="memory-architecture-tier is-${escapeAttribute(tier.key)}">
            <div class="memory-architecture-tier-head">
                <h3>${escapeHtml(tier.title)}</h3>
                <span>${escapeHtml(tier.ttl)}</span>
            </div>
            <p>${escapeHtml(tier.scope)}</p>
            <div class="memory-architecture-tier-copy">${escapeHtml(tier.copy)}</div>
        </article>
    `;
}

function renderArchitectureLink() {
    return `
        <div class="memory-architecture-link" aria-hidden="true">
            <span>${escapeHtml(t('feature.memory.arch.consolidation'))}</span>
        </div>
    `;
}

function renderMemoryRow(row) {
    const selected = row.id === memoryState.selectedId;
    const hit = memoryState.hitMeta.get(row.id);
    const preview = String(hit?.snippet || row.content_body_preview || '').trim();
    const tags = Array.isArray(row.tags) ? row.tags : [];
    return `
        <button
            class="memory-row${selected ? ' is-selected' : ''}"
            type="button"
            role="option"
            aria-selected="${selected ? 'true' : 'false'}"
            data-memory-id="${escapeAttribute(row.id)}"
        >
            <span class="memory-row-head">
                <strong>${escapeHtml(row.content_title || row.id)}</strong>
                <span>${escapeHtml(formatTimestamp(row.updated_at))}</span>
            </span>
            <span class="memory-row-preview">${escapeHtml(preview)}</span>
            <span class="memory-row-meta">
                <span>${escapeHtml(formatEnumLabel(row.tier))}</span>
                <span>${escapeHtml(formatEnumLabel(row.scope))}</span>
                <span>${escapeHtml(row.role_id || row.workspace_id)}</span>
            </span>
            <span class="memory-row-tags">
                ${tags.slice(0, 3).map(tag => `<span>${escapeHtml(tag)}</span>`).join('')}
            </span>
        </button>
    `;
}

function bindMemoryRows() {
    for (const button of els.projectViewContent.querySelectorAll('[data-memory-id]')) {
        button.addEventListener('click', () => {
            const memoryId = String(button.getAttribute('data-memory-id') || '').trim();
            if (!memoryId) {
                return;
            }
            void loadSelectedMemory(memoryId);
        });
    }
}

function renderSkillDraftRow(row) {
    const selected = row.id === memoryState.selectedDraftId;
    return `
        <button
            class="memory-draft-row${selected ? ' is-selected' : ''}"
            type="button"
            role="option"
            aria-selected="${selected ? 'true' : 'false'}"
            data-draft-id="${escapeAttribute(row.id)}"
        >
            <span class="memory-row-head">
                <strong>${escapeHtml(row.runtime_name || row.id)}</strong>
                <span>${escapeHtml(formatEnumLabel(row.status))}</span>
            </span>
            <span class="memory-row-preview">${escapeHtml(row.description || '')}</span>
            <span class="memory-row-meta">
                <span>${escapeHtml(formatEnumLabel(row.draft_kind))}</span>
                <span>${escapeHtml(formatEnumLabel(row.scope_kind))}</span>
                <span>${escapeHtml(formatMessage('feature.memory.drafts.sources', {
                    count: String(row.source_memory_count || 0),
                }))}</span>
            </span>
        </button>
    `;
}

function bindSkillDraftRows() {
    for (const button of els.projectViewContent.querySelectorAll('[data-draft-id]')) {
        button.addEventListener('click', () => {
            const draftId = String(button.getAttribute('data-draft-id') || '').trim();
            if (!draftId) {
                return;
            }
            void loadSelectedSkillDraft(draftId);
        });
    }
}

function renderMemoryDetail() {
    if (!memoryState.selectedId) {
        return `<div class="memory-detail-empty">${escapeHtml(t('feature.memory.select_entry'))}</div>`;
    }
    if (memoryState.selectedLoadingId === memoryState.selectedId && !memoryState.selectedEntry) {
        return `<div class="memory-detail-empty">${escapeHtml(t('feature.memory.loading_detail'))}</div>`;
    }
    const entry = memoryState.selectedEntry;
    const summary = memoryState.rows.find(row => row.id === memoryState.selectedId);
    if (!entry && !summary) {
        return `<div class="memory-detail-empty">${escapeHtml(t('feature.memory.select_entry'))}</div>`;
    }
    const content = entry?.content || {
        title: summary?.content_title || '',
        body: summary?.content_body_preview || '',
        context: '',
        outcome: '',
    };
    const tags = Array.isArray(entry?.tags)
        ? entry.tags
        : Array.isArray(summary?.tags)
            ? summary.tags
            : [];
    return `
        <article class="memory-detail-body">
            <header class="memory-detail-header">
                <div>
                    <h4>${escapeHtml(content.title || summary?.id || '')}</h4>
                    <p>${escapeHtml(summary?.id || entry?.id || '')}</p>
                </div>
                <span>${escapeHtml(formatPercent(entry?.confidence_score ?? summary?.confidence_score))}</span>
            </header>
            <dl class="memory-detail-grid">
                ${renderDetailItem(t('feature.memory.workspace'), entry?.workspace_id || summary?.workspace_id)}
                ${renderDetailItem(t('feature.memory.tier'), formatEnumLabel(entry?.tier || summary?.tier))}
                ${renderDetailItem(t('feature.memory.scope'), formatEnumLabel(entry?.scope || summary?.scope))}
                ${renderDetailItem(t('feature.memory.kind'), formatEnumLabel(entry?.kind || summary?.kind))}
                ${renderDetailItem(t('feature.memory.status'), formatEnumLabel(entry?.status || summary?.status))}
                ${renderDetailItem(t('feature.memory.source'), formatEnumLabel(entry?.source || summary?.source))}
                ${renderDetailItem(t('feature.memory.role'), entry?.role_id || summary?.role_id || '')}
                ${renderDetailItem(t('feature.memory.updated'), formatTimestamp(entry?.updated_at || summary?.updated_at))}
                ${renderDetailItem(t('feature.memory.expires'), formatExpiry(entry?.expires_at || summary?.expires_at))}
            </dl>
            <div class="memory-detail-text">${escapeHtml(content.body || '')}</div>
            ${content.context ? `<div class="memory-detail-note">${escapeHtml(content.context)}</div>` : ''}
            ${content.outcome ? `<div class="memory-detail-note">${escapeHtml(content.outcome)}</div>` : ''}
            <div class="memory-detail-tags">
                ${tags.map(tag => `<span>${escapeHtml(tag)}</span>`).join('')}
            </div>
        </article>
    `;
}

function renderSkillDraftDetail() {
    if (!memoryState.selectedDraftId) {
        return `<div class="memory-detail-empty">${escapeHtml(t('feature.memory.drafts.select'))}</div>`;
    }
    if (memoryState.selectedDraftLoadingId === memoryState.selectedDraftId && !memoryState.selectedDraft) {
        return `<div class="memory-detail-empty">${escapeHtml(t('feature.memory.loading_detail'))}</div>`;
    }
    const draft = memoryState.selectedDraft;
    if (!draft) {
        return `<div class="memory-detail-empty">${escapeHtml(t('feature.memory.drafts.select'))}</div>`;
    }
    const canApply = draft.status === 'validated';
    const canEdit = draft.status !== 'applied' && draft.status !== 'applying';
    return `
        <form class="memory-draft-editor" data-draft-editor>
            <div class="memory-draft-editor-head">
                <div>
                    <h4>${escapeHtml(draft.runtime_name || draft.id)}</h4>
                    <p>${escapeHtml(draft.id || '')}</p>
                </div>
                <span>${escapeHtml(formatEnumLabel(draft.status))}</span>
            </div>
            <div class="memory-draft-form-grid">
                <label>
                    <span>${escapeHtml(t('feature.memory.drafts.runtime_name'))}</span>
                    <input type="text" value="${escapeAttribute(draft.runtime_name || '')}" data-draft-runtime-name ${canEdit ? '' : 'disabled'}>
                </label>
                <label>
                    <span>${escapeHtml(t('feature.memory.drafts.kind'))}</span>
                    <input type="text" value="${escapeAttribute(formatEnumLabel(draft.draft_kind))}" disabled>
                </label>
            </div>
            <label class="memory-draft-field">
                <span>${escapeHtml(t('feature.memory.drafts.description'))}</span>
                <input type="text" value="${escapeAttribute(draft.description || '')}" data-draft-description ${canEdit ? '' : 'disabled'}>
            </label>
            <label class="memory-draft-field">
                <span>${escapeHtml(t('feature.memory.drafts.instructions'))}</span>
                <textarea data-draft-instructions ${canEdit ? '' : 'disabled'}>${escapeHtml(draft.instructions || '')}</textarea>
            </label>
            ${renderSkillDraftValidationMessages(draft)}
            <div class="memory-draft-actions">
                <button class="secondary-btn" type="submit" ${canEdit && !memoryState.savingDraft ? '' : 'disabled'}>${escapeHtml(t('settings.action.save'))}</button>
                <button class="secondary-btn" type="button" data-draft-validate ${canEdit ? '' : 'disabled'}>${escapeHtml(t('feature.memory.drafts.validate'))}</button>
                <button class="primary-btn" type="button" data-draft-apply ${canApply ? '' : 'disabled'}>${escapeHtml(t('feature.memory.drafts.apply'))}</button>
                <button class="secondary-btn" type="button" data-draft-reject ${canEdit ? '' : 'disabled'}>${escapeHtml(t('feature.memory.drafts.reject'))}</button>
            </div>
        </form>
    `;
}

function renderSkillDraftValidationMessages(draft) {
    const messages = Array.isArray(draft.validation_messages)
        ? draft.validation_messages
        : [];
    if (messages.length === 0) {
        return `<div class="memory-draft-validation is-empty">${escapeHtml(t('feature.memory.drafts.no_validation'))}</div>`;
    }
    return `
        <div class="memory-draft-validation">
            ${messages.map(message => `
                <div class="memory-draft-validation-row is-${escapeAttribute(message.severity || 'warning')}">
                    <strong>${escapeHtml(message.code || '')}</strong>
                    <span>${escapeHtml(message.message || '')}</span>
                </div>
            `).join('')}
        </div>
    `;
}

function bindSkillDraftDetail() {
    const form = els.projectViewContent.querySelector('[data-draft-editor]');
    if (!form) {
        return;
    }
    form.addEventListener('submit', event => {
        event.preventDefault();
        void handleSaveSkillDraft();
    });
    form.querySelector('[data-draft-validate]')?.addEventListener('click', () => {
        void handleValidateSkillDraft();
    });
    form.querySelector('[data-draft-apply]')?.addEventListener('click', () => {
        void handleApplySkillDraft();
    });
    form.querySelector('[data-draft-reject]')?.addEventListener('click', () => {
        void handleRejectSkillDraft();
    });
}

async function handleSaveSkillDraft() {
    const draftId = memoryState.selectedDraftId;
    const form = els.projectViewContent.querySelector('[data-draft-editor]');
    if (!draftId || !form) {
        return;
    }
    const payload = {
        runtime_name: String(form.querySelector('[data-draft-runtime-name]')?.value || '').trim(),
        description: String(form.querySelector('[data-draft-description]')?.value || '').trim(),
        instructions: String(form.querySelector('[data-draft-instructions]')?.value || '').trimEnd(),
    };
    memoryState = {
        ...memoryState,
        savingDraft: true,
    };
    renderMemoryContent();
    try {
        const updated = await updateMemorySkillDraft(draftId, payload);
        memoryState = {
            ...memoryState,
            selectedDraft: updated,
            savingDraft: false,
        };
        await reloadSkillDraftRowsIfActive();
    } catch (error) {
        memoryState = {
            ...memoryState,
            savingDraft: false,
        };
        renderMemoryContent();
        showToast({
            title: t('feature.memory.drafts.save_failed'),
            message: String(error?.message || error || ''),
            tone: 'error',
        });
    }
}

async function handleValidateSkillDraft() {
    const draftId = memoryState.selectedDraftId;
    if (!draftId) {
        return;
    }
    try {
        const updated = await validateMemorySkillDraft(draftId);
        memoryState = {
            ...memoryState,
            selectedDraft: updated,
        };
        await reloadSkillDraftRowsIfActive();
    } catch (error) {
        showToast({
            title: t('feature.memory.drafts.validate_failed'),
            message: String(error?.message || error || ''),
            tone: 'error',
        });
    }
}

async function handleApplySkillDraft() {
    const draftId = memoryState.selectedDraftId;
    if (!draftId) {
        return;
    }
    try {
        const result = await applyMemorySkillDraft(draftId);
        showToast({
            title: t('feature.memory.drafts.applied'),
            message: String(result?.ref || result?.skill_id || ''),
            tone: 'success',
        });
        await reloadSkillDraftRowsIfActive();
    } catch (error) {
        showToast({
            title: t('feature.memory.drafts.apply_failed'),
            message: String(error?.message || error || ''),
            tone: 'error',
        });
    }
}

async function handleRejectSkillDraft() {
    const draftId = memoryState.selectedDraftId;
    if (!draftId) {
        return;
    }
    try {
        const updated = await updateMemorySkillDraft(draftId, { status: 'rejected' });
        memoryState = {
            ...memoryState,
            selectedDraft: updated,
        };
        await reloadSkillDraftRowsIfActive();
    } catch (error) {
        showToast({
            title: t('feature.memory.drafts.reject_failed'),
            message: String(error?.message || error || ''),
            tone: 'error',
        });
    }
}

function renderDetailItem(label, value) {
    return `
        <div>
            <dt>${escapeHtml(label)}</dt>
            <dd>${escapeHtml(value || '-')}</dd>
        </div>
    `;
}

function formatWorkspaceLabel(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    const rootPath = String(workspace?.root_path || '').trim();
    if (!rootPath) {
        return workspaceId;
    }
    const parts = rootPath.split(/[\/\\]/).filter(Boolean);
    const name = parts.at(-1) || workspaceId;
    return `${name} (${workspaceId})`;
}

function formatEnumLabel(value) {
    return String(value || '')
        .replaceAll('_', ' ')
        .replace(/\b\w/g, char => char.toUpperCase());
}

function formatTimestamp(value) {
    const safeValue = String(value || '').trim();
    if (!safeValue) {
        return '';
    }
    const parsed = new Date(safeValue);
    if (Number.isNaN(parsed.getTime())) {
        return safeValue;
    }
    return parsed.toLocaleString();
}

function formatExpiry(value) {
    const formatted = formatTimestamp(value);
    return formatted || t('feature.memory.no_expiry');
}

function formatPercent(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
        return '';
    }
    return `${Math.round(numeric * 100)}%`;
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function escapeAttribute(value) {
    return escapeHtml(value).replaceAll('`', '&#96;');
}
