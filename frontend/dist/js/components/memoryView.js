/**
 * components/memoryView.js
 * Global Memory Bank browser.
 */
import {
    fetchMemories,
    fetchWorkspaces,
    getMemory,
    searchMemories,
} from '../core/api.js';
import { state } from '../core/state.js';
import { els } from '../utils/dom.js';
import { formatMessage, t } from '../utils/i18n.js';
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

let memoryRequestToken = 0;
let languageBound = false;
let memoryState = createInitialMemoryState();

function createInitialMemoryState() {
    return {
        query: '',
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
        await loadMemoryRows({ token });
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
    const result = await searchMemories(payload);
    if (!memoryState.status) {
        return result;
    }
    return {
        ...result,
        items: (Array.isArray(result?.items) ? result.items : [])
            .filter(hit => String(hit?.entry?.status || '') === memoryState.status),
    };
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

function renderMemoryToolbar() {
    if (els.projectViewTitle) {
        els.projectViewTitle.textContent = t('feature.memory.title');
    }
    if (els.projectViewSummary) {
        els.projectViewSummary.textContent = memoryState.loading
            ? t('feature.memory.loading')
            : formatMessage('feature.memory.summary', {
                count: String(memoryState.totalCount),
            });
    }
    if (!els.projectViewToolbarActions) {
        return;
    }
    els.projectViewToolbarActions.innerHTML = `
        <div class="memory-toolbar-controls">
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

function renderWorkspaceOption(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    if (!workspaceId) {
        return '';
    }
    const selected = workspaceId === memoryState.workspaceId ? ' selected' : '';
    const label = formatWorkspaceLabel(workspace);
    return `<option value="${escapeAttribute(workspaceId)}"${selected}>${escapeHtml(label)}</option>`;
}

function renderFilterOption(value, field) {
    const safeValue = String(value || '').trim();
    const selected = safeValue === String(memoryState[field] || '').trim()
        ? ' selected'
        : '';
    const label = safeValue ? formatEnumLabel(safeValue) : t('feature.memory.any');
    return `<option value="${escapeAttribute(safeValue)}"${selected}>${escapeHtml(label)}</option>`;
}

function bindMemoryToolbar() {
    const controls = els.projectViewToolbarActions;
    controls.querySelector('[data-project-view-close]')?.addEventListener('click', () => {
        hideProjectView();
    });
    controls.querySelector('[data-memory-refresh]')?.addEventListener('click', () => {
        void openMemoryFeatureView();
    });
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
            <div class="workspace-view-empty-state">
                <p>${escapeHtml(t('feature.memory.empty'))}</p>
            </div>
        `;
        return;
    }
    els.projectViewContent.innerHTML = `
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
                ${renderDetailItem(t('feature.memory.role'), entry?.role_id || summary?.role_id || '')}
                ${renderDetailItem(t('feature.memory.updated'), formatTimestamp(entry?.updated_at || summary?.updated_at))}
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
