/**
 * components/boards/todoBoard.js
 * Workspace TODO board feature page.
 */
import {
    archiveBoardTodo,
    createBoardTodo,
    fetchBoardTodoChanges,
    fetchBoardTodos,
    linkBoardTodoPullRequest,
    requestBoardTodoChanges,
    restoreBoardTodo,
    startBoardTodo,
    syncBoardTodoChanges,
    syncBoardTodos,
    fetchWorkspaces,
} from '../../core/api.js';
import { state } from '../../core/state.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { showConfirmDialog, showFormDialog, showToast } from '../../utils/feedback.js';
import { escapeHtml } from '../newSessionDraftIcons.js';

const ROOT_ID = 'board-todo-root';
const CACHE_STORAGE_KEY = 'agent_teams_board_todo_cache_v3';
const AUTO_SYNC_INTERVAL_MS = 60 * 60 * 1000;
const PROGRESSIVE_BATCH_SIZE = 8;
const PROGRESSIVE_FRAME_MS = 120;
const DETAIL_BODY_PAGE_CHARS = 2800;
const STATUSES = ['todo', 'in_progress', 'review', 'done'];
const STATUS_CONFIG = {
    todo: { titleKey: 'board_todos.status.todo', tone: 'todo' },
    in_progress: { titleKey: 'board_todos.status.in_progress', tone: 'progress' },
    review: { titleKey: 'board_todos.status.review', tone: 'review' },
    done: { titleKey: 'board_todos.status.done', tone: 'done' },
    archived: { titleKey: 'board_todos.status.archived', tone: 'archived' },
};
const SORT_OPTIONS = [
    { value: 'updated_desc', labelKey: 'board_todos.sort.updated_desc' },
    { value: 'updated_asc', labelKey: 'board_todos.sort.updated_asc' },
    { value: 'title_asc', labelKey: 'board_todos.sort.title_asc' },
    { value: 'title_desc', labelKey: 'board_todos.sort.title_desc' },
];

const boardCache = new Map();
const stagedCounts = new Map();
const columnViews = new Map();
const enteringTodoIds = new Set();
const boardLoadRequests = new Map();
const boardSyncModes = new Map();
const autoSyncTimes = new Map();
let boardWorkspaces = [];
let selectedWorkspaceId = '';
let includeArchived = false;
let loadState = 'idle';
let loadError = '';
let listenersBound = false;
let cacheHydrated = false;
let detailTodoId = '';
let detailBodyPage = 1;
let progressiveRenderToken = 0;
let boardMountToken = 0;

export function mountBoardTodoBoard({ preferredWorkspaceId = '' } = {}) {
    const mountToken = ++boardMountToken;
    const root = getRoot();
    if (!root) {
        return;
    }
    bindListeners();
    hydrateBoardCache();
    const preferred = String(preferredWorkspaceId || state.pendingNewSessionWorkspaceId || state.currentWorkspaceId || '').trim();
    if (preferred) {
        selectedWorkspaceId = preferred;
    }
    renderBoard(root);
    void loadBoardWorkspaces({ preferredWorkspaceId: preferred, mountToken });
}

export function unmountBoardTodoBoard() {
    boardMountToken += 1;
    cancelProgressiveRender();
    boardLoadRequests.clear();
    boardSyncModes.clear();
    loadState = 'idle';
    loadError = '';
    detailTodoId = '';
    detailBodyPage = 1;
}

function bindListeners() {
    if (listenersBound) {
        return;
    }
    listenersBound = true;
    document.addEventListener('click', event => {
        const root = getRoot();
        if (!root || !root.contains(event.target)) {
            return;
        }
        const actionElement = event.target?.closest?.('[data-board-todo-action]');
        if (!actionElement) {
            return;
        }
        if (actionElement.classList?.contains('board-todos-detail-backdrop') && event.target !== actionElement) {
            return;
        }
        void handleAction(actionElement);
    });
    document.addEventListener('change', event => {
        const root = getRoot();
        if (!root || !root.contains(event.target)) {
            return;
        }
        const workspaceSelect = event.target?.closest?.('[data-board-todo-workspace]');
        if (workspaceSelect) {
            cancelProgressiveRender();
            selectedWorkspaceId = String(workspaceSelect.value || '').trim();
            renderBoard(root);
            void loadBoard({ workspaceId: selectedWorkspaceId, sync: true });
            return;
        }
        const archivedToggle = event.target?.closest?.('[data-board-todo-archived]');
        if (archivedToggle) {
            cancelProgressiveRender();
            includeArchived = archivedToggle.checked === true;
            renderBoard(root);
            void loadBoard({ workspaceId: selectedWorkspaceId, sync: false });
            return;
        }
        const sortSelect = event.target?.closest?.('[data-board-todo-sort]');
        if (sortSelect) {
            const status = String(sortSelect.getAttribute('data-status') || '').trim();
            updateColumnView(status, { sort: String(sortSelect.value || 'updated_desc') });
            cancelProgressiveRender();
            refreshBoardTodoColumn(status);
        }
    });
    document.addEventListener('input', event => {
        const root = getRoot();
        if (!root || !root.contains(event.target)) {
            return;
        }
        const searchInput = event.target?.closest?.('[data-board-todo-search]');
        if (!searchInput) {
            return;
        }
        const status = String(searchInput.getAttribute('data-status') || '').trim();
        updateColumnView(status, { query: String(searchInput.value || '') });
        cancelProgressiveRender();
        refreshBoardTodoColumn(status);
    });
}

async function loadBoardWorkspaces({ preferredWorkspaceId = '', mountToken = boardMountToken } = {}) {
    const root = getRoot();
    if (!root || !isCurrentBoardMount(mountToken)) {
        return;
    }
    loadState = 'loading';
    loadError = '';
    renderBoard(root);
    try {
        const response = await fetchWorkspaces();
        if (!isCurrentBoardMount(mountToken)) {
            return;
        }
        boardWorkspaces = Array.isArray(response) ? response : [];
        selectedWorkspaceId = resolveWorkspaceId(preferredWorkspaceId);
        renderBoard(root);
        if (selectedWorkspaceId) {
            await loadBoard({ workspaceId: selectedWorkspaceId, sync: true, mountToken });
        } else {
            loadState = 'ready';
            renderBoard(root);
        }
    } catch (error) {
        if (!isCurrentBoardMount(mountToken)) {
            return;
        }
        loadState = 'error';
        loadError = error?.message || String(error);
        renderBoard(root);
    }
}

async function loadBoard({ workspaceId, sync = false, mountToken = boardMountToken } = {}) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    const shouldSync = shouldRunAutoSync({ workspaceId: safeWorkspaceId, sync });
    const forceFull = shouldSync && shouldRunAutoFullSync(safeWorkspaceId);
    if (shouldSync) {
        markAutoSyncAttempt(safeWorkspaceId);
    }
    return loadBoardInternal({
        workspaceId: safeWorkspaceId,
        sync: shouldSync,
        forceFull,
        mountToken,
    });
}

async function forceSyncBoard({ workspaceId, mountToken = boardMountToken } = {}) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    if (safeWorkspaceId) {
        markAutoSyncAttempt(safeWorkspaceId);
    }
    return loadBoardInternal({
        workspaceId: safeWorkspaceId,
        sync: true,
        forceFull: true,
        mountToken,
    });
}

async function loadBoardInternal({
    workspaceId,
    sync = false,
    forceFull = false,
    mountToken = boardMountToken,
} = {}) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    const root = getRoot();
    if (!root || !safeWorkspaceId || !isCurrentBoardMount(mountToken)) {
        return;
    }
    const key = cacheKey(safeWorkspaceId, includeArchived);
    const existingRequest = boardLoadRequests.get(key);
    if (existingRequest) {
        return existingRequest.promise;
    }
    const cached = boardCache.get(key);
    const syncMode = sync ? (forceFull ? 'full' : 'incremental') : '';
    if (syncMode) {
        boardSyncModes.set(key, syncMode);
    }
    if (cached) {
        loadState = 'ready';
        updateSyncButton(root);
    } else {
        loadState = 'loading';
        loadError = '';
        renderBoard(root);
    }
    const request = (async () => {
        const response = await fetchBoardPayload({
            workspaceId: safeWorkspaceId,
            includeArchived,
            sync,
            cached,
            forceFull,
        });
        const nextBoard = isDeltaResponse(response)
            ? mergeBoardDelta(cached, response)
            : normalizeBoardResponse(response);
        boardCache.set(key, nextBoard);
        persistBoardCache();
        if (!isCurrentBoardMount(mountToken)) {
            return;
        }
        loadState = 'ready';
        loadError = '';
        if (cached) {
            refreshBoardContent(root);
        } else {
            startProgressiveRender(key, mountToken);
        }
    })();
    const requestEntry = { promise: request };
    boardLoadRequests.set(key, requestEntry);
    try {
        await request;
    } catch (error) {
        if (!isCurrentBoardMount(mountToken)) {
            return;
        }
        if (sync) {
            clearAutoSyncAttempt(safeWorkspaceId);
        }
        loadState = cached ? 'ready' : 'error';
        loadError = error?.message || String(error);
        if (cached) {
            updateSyncButton(root);
            renderDiagnosticsInto(root);
        } else {
            renderBoard(root);
        }
        showToast({
            tone: 'danger',
            message: formatMessage('board_todos.toast.load_failed', { error: loadError }),
        });
    } finally {
        if (boardLoadRequests.get(key) === requestEntry) {
            boardLoadRequests.delete(key);
        }
        if (
            syncMode
            && isCurrentBoardMount(mountToken)
            && boardSyncModes.get(key) === syncMode
        ) {
            boardSyncModes.delete(key);
        }
        if (isCurrentBoardMount(mountToken)) {
            updateSyncButton(getRoot());
        }
    }
}

function shouldRunAutoSync({ workspaceId, sync }) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    if (!sync || !safeWorkspaceId) {
        return false;
    }
    const lastSyncedAt = Number(autoSyncTimes.get(safeWorkspaceId) || 0);
    return !lastSyncedAt || Date.now() - lastSyncedAt >= AUTO_SYNC_INTERVAL_MS;
}

function shouldRunAutoFullSync(workspaceId) {
    return !boardCache.has(cacheKey(workspaceId, false));
}

function markAutoSyncAttempt(workspaceId) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    if (safeWorkspaceId) {
        autoSyncTimes.set(safeWorkspaceId, Date.now());
        persistBoardCache();
    }
}

function clearAutoSyncAttempt(workspaceId) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    if (safeWorkspaceId) {
        autoSyncTimes.delete(safeWorkspaceId);
        persistBoardCache();
    }
}

function renderBoard(root) {
    if (!root) {
        return;
    }
    const board = selectedWorkspaceId ? boardCache.get(currentCacheKey()) : null;
    root.innerHTML = `
        <section class="board-todos" aria-label="${escapeHtml(t('board_todos.title'))}">
            <div class="board-todos-toolbar">
                <div class="board-todos-heading">
                    <h2>${escapeHtml(t('board_todos.title'))}</h2>
                    <span>${escapeHtml(board?.repository_full_name || t('board_todos.no_repo'))}</span>
                </div>
                <div class="board-todos-actions">
                    <select class="board-todos-workspace" data-board-todo-workspace aria-label="${escapeHtml(t('board_todos.workspace'))}">
                        ${renderWorkspaceOptions()}
                    </select>
                    <label class="board-todos-toggle">
                        <input type="checkbox" data-board-todo-archived ${includeArchived ? 'checked' : ''}>
                        <span>${escapeHtml(t('board_todos.show_archived'))}</span>
                    </label>
                    ${renderSyncButton()}
                    <button class="board-todos-primary-btn" type="button" data-board-todo-action="create">${escapeHtml(t('board_todos.action.new'))}</button>
                </div>
            </div>
            <div data-board-todo-diagnostics>${renderDiagnostics(board)}</div>
            <div data-board-todo-content>${renderBoardContent({ board })}</div>
            ${renderDetailModal(board)}
        </section>
    `;
}

function renderSyncButton() {
    const mode = currentSyncMode();
    const busy = Boolean(mode);
    const label = busy
        ? t(mode === 'full' ? 'board_todos.sync.button_full' : 'board_todos.sync.button_incremental')
        : t('board_todos.action.sync');
    const title = busy
        ? t(mode === 'full' ? 'board_todos.sync.full' : 'board_todos.sync.incremental')
        : t('board_todos.action.sync');
    return `
        <button
            class="board-todos-tool-btn board-todos-sync-btn ${busy ? 'is-busy' : ''}"
            type="button"
            data-board-todo-action="sync"
            ${busy ? 'disabled aria-busy="true"' : 'aria-busy="false"'}
            aria-label="${escapeHtml(title)}"
            title="${escapeHtml(title)}"
        >
            ${busy ? '<span class="board-todos-sync-spinner" aria-hidden="true"></span>' : ''}
            <span>${escapeHtml(label)}</span>
        </button>
    `;
}

function renderWorkspaceOptions() {
    if (!boardWorkspaces.length) {
        return `<option value="">${escapeHtml(t('board_todos.workspace_empty'))}</option>`;
    }
    return boardWorkspaces.map(workspace => {
        const workspaceId = String(workspace?.workspace_id || '').trim();
        const selected = workspaceId === selectedWorkspaceId ? 'selected' : '';
        return `<option value="${escapeHtml(workspaceId)}" ${selected}>${escapeHtml(workspaceLabel(workspace))}</option>`;
    }).join('');
}

function renderDiagnostics(board) {
    const rawDiagnostics = Array.isArray(board?.diagnostics)
        ? board.diagnostics.map(message => String(message || '').trim())
        : [];
    const hadBlankDiagnostics = rawDiagnostics.some(message => !message);
    const messages = rawDiagnostics.filter(Boolean);
    if (!messages.length && hadBlankDiagnostics) {
        messages.push(t('board_todos.sync.generic_failed'));
    }
    if (loadError) {
        messages.push(loadError);
    }
    if (!messages.length) {
        return '';
    }
    return `
        <div class="board-todos-diagnostics">
            ${messages.map(message => `<span>${escapeHtml(message)}</span>`).join('')}
        </div>
    `;
}

function currentSyncMode() {
    const key = currentCacheKey();
    return boardSyncModes.get(key) || (boardLoadRequests.has(key) ? 'incremental' : '');
}

function updateSyncButton(root) {
    if (!root) {
        return;
    }
    const button = root.querySelector('[data-board-todo-action="sync"]');
    if (!button) {
        return;
    }
    button.outerHTML = renderSyncButton();
}

function renderDiagnosticsInto(root) {
    if (!root) {
        return;
    }
    const diagnostics = root.querySelector('[data-board-todo-diagnostics]');
    if (!diagnostics) {
        return;
    }
    const board = selectedWorkspaceId ? boardCache.get(currentCacheKey()) : null;
    diagnostics.innerHTML = renderDiagnostics(board);
}

function refreshBoardContent(root) {
    if (!root) {
        return;
    }
    const content = root.querySelector('[data-board-todo-content]');
    if (!content) {
        renderBoard(root);
        return;
    }
    const board = selectedWorkspaceId ? boardCache.get(currentCacheKey()) : null;
    content.innerHTML = renderBoardContent({ board });
    renderDiagnosticsInto(root);
}

function renderBoardContent({ board }) {
    if (loadState === 'loading' && !board) {
        return includeArchived ? renderArchivedSkeleton() : renderSkeletonColumns();
    }
    if (loadState === 'error' && !board) {
        return `<div class="board-todos-state is-error">${escapeHtml(loadError || t('board_todos.load_error'))}</div>`;
    }
    if (!selectedWorkspaceId) {
        return `<div class="board-todos-state">${escapeHtml(t('board_todos.select_workspace'))}</div>`;
    }
    const items = Array.isArray(board?.items) ? board.items : [];
    if (includeArchived) {
        return renderArchivedContent(items);
    }
    return `
        <div class="board-todos-columns" role="list">
            ${STATUSES.map(status => renderColumn(status, items)).join('')}
        </div>
    `;
}

function renderArchivedContent(items) {
    const archivedItems = items.filter(item => String(item?.status || '') === 'archived');
    const filteredItems = filterAndSortColumnItems('archived', archivedItems);
    const visibleItems = stagedItems('archived', filteredItems);
    return `
        <div class="board-todos-archive-view">
            <div class="board-todos-archive-head">
                <span class="board-todos-archive-pill">${escapeHtml(t('board_todos.status.archived'))}</span>
                <span data-board-todo-count="archived">${renderColumnCount(filteredItems.length, archivedItems.length)}</span>
            </div>
            ${renderColumnControls('archived')}
            <div class="board-todos-archive-list" data-board-todo-list="archived">
                ${visibleItems.map(renderCard).join('') || renderArchiveEmptyOrSkeleton(filteredItems)}
            </div>
        </div>
    `;
}

function renderColumn(status, items) {
    const config = STATUS_CONFIG[status] || STATUS_CONFIG.todo;
    const columnItems = items.filter(item => String(item?.status || '') === status);
    const filteredItems = filterAndSortColumnItems(status, columnItems);
    const visibleItems = stagedItems(status, filteredItems);
    return `
        <section class="board-todos-column is-${escapeHtml(config.tone)}" role="listitem" data-board-todo-column="${escapeHtml(status)}">
            <header class="board-todos-column-head">
                <span class="board-todos-dot"></span>
                <strong>${escapeHtml(t(config.titleKey))}</strong>
                <span data-board-todo-count="${escapeHtml(status)}">${renderColumnCount(filteredItems.length, columnItems.length)}</span>
            </header>
            ${renderColumnControls(status)}
            <div class="board-todos-card-list" data-board-todo-list="${escapeHtml(status)}">
                ${visibleItems.map(renderCard).join('') || renderColumnEmptyOrSkeleton(filteredItems)}
            </div>
        </section>
    `;
}

function renderColumnControls(status) {
    const view = getColumnView(status);
    return `
        <div class="board-todos-column-controls">
            <label class="board-todos-column-search">
                ${renderIcon('search')}
                <input
                    type="search"
                    value="${escapeHtml(view.query)}"
                    placeholder="${escapeHtml(t('board_todos.search.placeholder'))}"
                    data-board-todo-search
                    data-status="${escapeHtml(status)}"
                >
            </label>
            <select class="board-todos-column-sort" data-board-todo-sort data-status="${escapeHtml(status)}" aria-label="${escapeHtml(t('board_todos.sort.label'))}">
                ${SORT_OPTIONS.map(option => `
                    <option value="${escapeHtml(option.value)}" ${option.value === view.sort ? 'selected' : ''}>${escapeHtml(t(option.labelKey))}</option>
                `).join('')}
            </select>
        </div>
    `;
}

function renderColumnCount(visibleCount, totalCount) {
    if (visibleCount === totalCount) {
        return escapeHtml(String(totalCount));
    }
    return `${escapeHtml(String(visibleCount))}/${escapeHtml(String(totalCount))}`;
}

function renderSkeletonColumns() {
    return `
        <div class="board-todos-columns" role="list" aria-busy="true">
            ${STATUSES.map(status => {
                const config = STATUS_CONFIG[status] || STATUS_CONFIG.todo;
                return `
                    <section class="board-todos-column is-${escapeHtml(config.tone)}" role="listitem">
                        <header class="board-todos-column-head">
                            <span class="board-todos-dot"></span>
                            <strong>${escapeHtml(t(config.titleKey))}</strong>
                            <span>...</span>
                        </header>
                        <div class="board-todos-card-list">
                            ${renderSkeletonCards(3)}
                        </div>
                    </section>
                `;
            }).join('')}
        </div>
    `;
}

function renderArchivedSkeleton() {
    return `
        <div class="board-todos-archive-view" aria-busy="true">
            <div class="board-todos-archive-head">
                <span class="board-todos-archive-pill">${escapeHtml(t('board_todos.status.archived'))}</span>
                <span>...</span>
            </div>
            <div class="board-todos-archive-list">
                ${renderSkeletonCards(4)}
            </div>
        </div>
    `;
}

function renderSkeletonCards(count) {
    return Array.from({ length: count }, () => `
        <div class="board-todos-skeleton-card">
            <span></span>
            <strong></strong>
            <p></p>
            <em></em>
        </div>
    `).join('');
}

function renderColumnEmptyOrSkeleton(columnItems) {
    if (columnItems.length > 0) {
        return renderSkeletonCards(Math.min(2, columnItems.length));
    }
    return `<div class="board-todos-empty">${escapeHtml(t('board_todos.empty_column'))}</div>`;
}

function renderArchiveEmptyOrSkeleton(archivedItems) {
    if (archivedItems.length > 0) {
        return renderSkeletonCards(Math.min(3, archivedItems.length));
    }
    return `<div class="board-todos-empty">${escapeHtml(t('board_todos.empty_archived'))}</div>`;
}

function stagedItems(status, items) {
    const staged = stagedCounts.get(currentCacheKey());
    if (!staged) {
        return items;
    }
    const limit = Number(staged[status] || 0);
    return items.slice(0, limit);
}

function renderCard(item) {
    const todoId = String(item?.todo_id || '');
    const sessionId = String(item?.session_id || '');
    const sourceLabel = formatSourceLabel(item);
    const htmlUrl = String(item?.html_url || '').trim();
    const enteringClass = enteringTodoIds.has(todoId) ? ' is-entering' : '';
    const prLink = item?.linked_pr_url
        ? `<a href="${escapeHtml(item.linked_pr_url)}" target="_blank" rel="noreferrer">PR #${escapeHtml(String(item.linked_pr_number || ''))}</a>`
        : item?.linked_pr_number
            ? `PR #${escapeHtml(String(item.linked_pr_number))}`
            : t('board_todos.value.no_pr');
    return `
        <article class="board-todos-card${enteringClass}" data-board-todo-card="${escapeHtml(todoId)}">
            <div class="board-todos-card-top">
                <div class="board-todos-card-meta">
                    <span>${escapeHtml(sourceLabel)}</span>
                    <span>${escapeHtml(item?.repository_full_name || t('board_todos.source.local'))}</span>
                </div>
                <div class="board-todos-card-quick-actions">
                    <button type="button" title="${escapeHtml(t('board_todos.action.details'))}" aria-label="${escapeHtml(t('board_todos.action.details'))}" data-board-todo-action="details" data-todo-id="${escapeHtml(todoId)}">${renderIcon('details')}</button>
                    ${htmlUrl ? `<a class="board-todos-icon-link" href="${escapeHtml(htmlUrl)}" target="_blank" rel="noreferrer" title="${escapeHtml(t('board_todos.action.open_source'))}" aria-label="${escapeHtml(t('board_todos.action.open_source'))}">${renderIcon('external')}</a>` : ''}
                </div>
            </div>
            <h3>${escapeHtml(item?.title || '')}</h3>
            ${item?.body ? `<p>${escapeHtml(truncateText(item.body, 96))}</p>` : ''}
            <div class="board-todos-card-links">
                <span>${escapeHtml(t('board_todos.card.session'))}: ${sessionId ? escapeHtml(sessionId) : escapeHtml(t('board_todos.value.none'))}</span>
                <span>${escapeHtml(t('board_todos.card.pr'))}: ${prLink}</span>
            </div>
            <time>${escapeHtml(formatDateTime(effectiveUpdatedAt(item)))}</time>
            <div class="board-todos-card-actions">
                ${renderCardActions(item)}
            </div>
        </article>
    `;
}

function formatSourceLabel(item) {
    const provider = String(item?.source_provider || '').trim().toLowerCase();
    const sourceType = String(item?.source_type || '').trim().toLowerCase();
    const providerLabel = provider === 'github' ? 'GitHub' : '';
    if (sourceType === 'github_issue' && item?.issue_number) {
        return `${providerLabel || t('board_todos.source.local')} Issue #${item.issue_number}`;
    }
    if (sourceType === 'github_pull_request' && item?.pull_request_number) {
        return `${providerLabel || t('board_todos.source.local')} PR #${item.pull_request_number}`;
    }
    if (sourceType === 'manual') {
        return t('board_todos.source.manual');
    }
    if (sourceType) {
        return providerLabel ? `${providerLabel} ${sourceType}` : sourceType;
    }
    return t('board_todos.source.manual');
}

function renderCardActions(item) {
    const todoId = escapeHtml(String(item?.todo_id || ''));
    const status = String(item?.status || '');
    const sessionId = String(item?.session_id || '');
    const actions = [];
    if (status === 'todo') {
        actions.push(`<button type="button" data-board-todo-action="start" data-todo-id="${todoId}">${escapeHtml(t('board_todos.action.start'))}</button>`);
    }
    if (sessionId) {
        actions.push(`<button type="button" data-board-todo-action="open-session" data-session-id="${escapeHtml(sessionId)}">${escapeHtml(t('board_todos.action.open_session'))}</button>`);
    }
    if (status === 'review') {
        actions.push(`<button type="button" data-board-todo-action="request-changes" data-todo-id="${todoId}">${escapeHtml(t('board_todos.action.request_changes'))}</button>`);
    }
    if (status !== 'archived') {
        actions.push(`<button type="button" data-board-todo-action="link-pr" data-todo-id="${todoId}">${escapeHtml(t('board_todos.action.link_pr'))}</button>`);
        actions.push(`<button type="button" data-board-todo-action="archive" data-todo-id="${todoId}">${escapeHtml(t('board_todos.action.archive'))}</button>`);
    } else {
        actions.push(`<button type="button" data-board-todo-action="restore" data-todo-id="${todoId}">${escapeHtml(t('board_todos.action.restore'))}</button>`);
    }
    return actions.join('');
}

function renderDetailModal(board) {
    if (!detailTodoId) {
        return '';
    }
    const items = Array.isArray(board?.items) ? board.items : [];
    const item = items.find(candidate => String(candidate?.todo_id || '') === detailTodoId);
    if (!item) {
        detailTodoId = '';
        return '';
    }
    const bodyPages = paginateDetailBody(item?.body || t('board_todos.value.none'));
    const safePage = Math.min(Math.max(detailBodyPage, 1), bodyPages.length);
    detailBodyPage = safePage;
    const bodyPage = bodyPages[safePage - 1] || '';
    const rows = [
        [t('board_todos.detail.source'), formatSourceLabel(item)],
        [t('board_todos.detail.repo'), item?.repository_full_name || t('board_todos.value.none')],
        [t('board_todos.card.session'), item?.session_id || t('board_todos.value.none')],
        [t('board_todos.detail.run'), item?.run_id || t('board_todos.value.none')],
        [t('board_todos.detail.issue_url'), item?.html_url || t('board_todos.value.none')],
        [t('board_todos.card.pr'), item?.linked_pr_url || (item?.linked_pr_number ? `PR #${item.linked_pr_number}` : t('board_todos.value.no_pr'))],
        [t('board_todos.detail.status_reason'), item?.last_status_reason || t('board_todos.value.none')],
        [t('board_todos.detail.source_updated_at'), formatDateTime(item?.source_updated_at)],
        [t('board_todos.detail.board_updated_at'), formatDateTime(item?.updated_at)],
    ];
    return `
        <div class="board-todos-detail-backdrop" data-board-todo-action="close-detail">
            <article class="board-todos-detail-modal" role="dialog" aria-modal="true" aria-labelledby="board-todo-detail-title">
                <header class="board-todos-detail-header">
                    <div>
                        <span>${escapeHtml(formatSourceLabel(item))}</span>
                        <h3 id="board-todo-detail-title">${escapeHtml(item?.title || '')}</h3>
                    </div>
                    <button type="button" aria-label="${escapeHtml(t('settings.action.cancel'))}" data-board-todo-action="close-detail">x</button>
                </header>
                <div class="board-todos-detail-body">
                    <dl>
                        ${rows.map(([label, value]) => `
                            <div>
                                <dt>${escapeHtml(label)}</dt>
                                <dd>${renderDetailValue(value)}</dd>
                            </div>
                        `).join('')}
                    </dl>
                    <section>
                        <h4>${escapeHtml(t('board_todos.field.body'))}</h4>
                        <p>${escapeHtml(bodyPage)}</p>
                    </section>
                </div>
                ${bodyPages.length > 1 ? `
                    <footer class="board-todos-detail-pagination">
                        <button type="button" data-board-todo-action="detail-page-prev" ${safePage <= 1 ? 'disabled' : ''}>${escapeHtml(t('board_todos.detail.prev'))}</button>
                        <span>${escapeHtml(formatMessage('board_todos.detail.page', { page: safePage, pages: bodyPages.length }))}</span>
                        <button type="button" data-board-todo-action="detail-page-next" ${safePage >= bodyPages.length ? 'disabled' : ''}>${escapeHtml(t('board_todos.detail.next'))}</button>
                    </footer>
                ` : ''}
            </article>
        </div>
    `;
}

function renderDetailValue(value) {
    const text = String(value || '').trim();
    if (text.startsWith('http://') || text.startsWith('https://')) {
        return `<a href="${escapeHtml(text)}" target="_blank" rel="noreferrer">${escapeHtml(text)}</a>`;
    }
    return escapeHtml(text || t('board_todos.value.none'));
}

async function handleAction(button) {
    const action = String(button.getAttribute('data-board-todo-action') || '').trim();
    if (action === 'close-detail') {
        detailTodoId = '';
        detailBodyPage = 1;
        updateDetailModal();
        return;
    }
    if (action === 'detail-page-prev') {
        detailBodyPage = Math.max(1, detailBodyPage - 1);
        updateDetailModal();
        return;
    }
    if (action === 'detail-page-next') {
        detailBodyPage += 1;
        updateDetailModal();
        return;
    }
    if (action === 'sync') {
        await forceSyncBoard({ workspaceId: selectedWorkspaceId });
        return;
    }
    if (action === 'create') {
        await handleCreate();
        return;
    }
    if (action === 'open-session') {
        const sessionId = String(button.getAttribute('data-session-id') || '').trim();
        if (sessionId) {
            document.dispatchEvent(new CustomEvent('agent-teams-select-session', {
                detail: { sessionId },
            }));
        }
        return;
    }
    const todoId = String(button.getAttribute('data-todo-id') || '').trim();
    if (!todoId) {
        return;
    }
    if (action === 'details') {
        detailTodoId = todoId;
        detailBodyPage = 1;
        updateDetailModal();
        return;
    }
    if (action === 'start') await handleStart(todoId);
    if (action === 'request-changes') await handleRequestChanges(todoId);
    if (action === 'link-pr') await handleLinkPr(todoId);
    if (action === 'archive') await handleArchive(todoId);
    if (action === 'restore') await handleRestore(todoId);
}

async function handleCreate() {
    const values = await showFormDialog({
        title: t('board_todos.dialog.create_title'),
        confirmLabel: t('board_todos.action.new'),
        fields: [
            { id: 'title', label: t('board_todos.field.title'), value: '' },
            { id: 'body', label: t('board_todos.field.body'), type: 'textarea', rows: 4, value: '' },
        ],
    });
    if (!values?.title || !selectedWorkspaceId) {
        return;
    }
    const item = await createBoardTodo({
        workspaceId: selectedWorkspaceId,
        title: values.title,
        body: values.body || '',
    });
    applyReturnedItem(item);
    void refreshCurrentDelta();
}

async function handleStart(todoId) {
    const item = await startBoardTodo(todoId, {});
    showToast({ tone: 'success', message: t('board_todos.toast.started') });
    applyReturnedItem(item);
    void refreshCurrentDelta();
}

async function handleRequestChanges(todoId) {
    const values = await showFormDialog({
        title: t('board_todos.dialog.request_changes_title'),
        confirmLabel: t('board_todos.action.request_changes'),
        fields: [
            { id: 'feedback', label: t('board_todos.field.feedback'), type: 'textarea', rows: 5, value: '' },
        ],
    });
    if (!values?.feedback) {
        return;
    }
    const item = await requestBoardTodoChanges(todoId, { feedback: values.feedback });
    applyReturnedItem(item);
    void refreshCurrentDelta();
}

async function handleLinkPr(todoId) {
    const values = await showFormDialog({
        title: t('board_todos.dialog.link_pr_title'),
        confirmLabel: t('board_todos.action.link_pr'),
        fields: [
            { id: 'pull_request_number', label: t('board_todos.field.pr_number'), value: '' },
            { id: 'pull_request_url', label: t('board_todos.field.pr_url'), value: '' },
        ],
    });
    const pullRequestNumber = Number.parseInt(String(values?.pull_request_number || ''), 10);
    if (!Number.isFinite(pullRequestNumber) || pullRequestNumber < 1) {
        return;
    }
    const item = await linkBoardTodoPullRequest(todoId, {
        pull_request_number: pullRequestNumber,
        pull_request_url: String(values?.pull_request_url || '').trim() || null,
    });
    applyReturnedItem(item);
    void refreshCurrentDelta();
}

async function handleArchive(todoId) {
    const confirmed = await showConfirmDialog({
        title: t('board_todos.dialog.archive_title'),
        message: t('board_todos.dialog.archive_message'),
        confirmLabel: t('board_todos.action.archive'),
        tone: 'warning',
    });
    if (!confirmed) {
        return;
    }
    const item = await archiveBoardTodo(todoId, {});
    applyReturnedItem(item);
    void refreshCurrentDelta();
}

async function handleRestore(todoId) {
    const item = await restoreBoardTodo(todoId);
    applyReturnedItem(item);
    void refreshCurrentDelta();
}

function resolveWorkspaceId(preferredWorkspaceId = '') {
    const ids = new Set(boardWorkspaces.map(workspace => String(workspace?.workspace_id || '').trim()).filter(Boolean));
    const preferred = String(preferredWorkspaceId || '').trim();
    if (preferred && ids.has(preferred)) return preferred;
    if (selectedWorkspaceId && ids.has(selectedWorkspaceId)) return selectedWorkspaceId;
    if (boardWorkspaces.length === 1) return String(boardWorkspaces[0]?.workspace_id || '').trim();
    return '';
}

function workspaceLabel(workspace) {
    return String(workspace?.profile?.display_name || workspace?.workspace_id || '').trim();
}

async function fetchBoardPayload({ workspaceId, includeArchived: archived, sync, cached, forceFull = false }) {
    const afterRevision = Number(cached?.revision || 0);
    if (cached && sync && forceFull) {
        return syncBoardTodos({ workspaceId, includeArchived: archived });
    }
    if (cached && sync) {
        const delta = await syncBoardTodoChanges({
            workspaceId,
            includeArchived: archived,
            afterRevision,
            forceFull,
        });
        return isStaleDeltaResponse(cached, delta)
            ? syncBoardTodos({ workspaceId, includeArchived: archived })
            : delta;
    }
    if (cached) {
        const delta = await fetchBoardTodoChanges({
            workspaceId,
            includeArchived: archived,
            afterRevision,
        });
        return isStaleDeltaResponse(cached, delta)
            ? fetchBoardTodos({ workspaceId, includeArchived: archived })
            : delta;
    }
    return sync
        ? syncBoardTodos({ workspaceId, includeArchived: archived })
        : fetchBoardTodos({ workspaceId, includeArchived: archived });
}

function isDeltaResponse(response) {
    return Array.isArray(response?.changed_items) || Array.isArray(response?.removed_todo_ids);
}

function isStaleDeltaResponse(cached, response) {
    if (!isDeltaResponse(response)) {
        return false;
    }
    return Number(response?.revision || 0) < Number(cached?.revision || 0);
}

function normalizeBoardResponse(response) {
    return {
        ...response,
        items: Array.isArray(response?.items) ? response.items : [],
        revision: Number(response?.revision || 0),
    };
}

function mergeBoardDelta(cached, delta) {
    const itemsById = new Map(
        (Array.isArray(cached?.items) ? cached.items : [])
            .map(item => [String(item?.todo_id || ''), item])
            .filter(([todoId]) => Boolean(todoId)),
    );
    (Array.isArray(delta?.removed_todo_ids) ? delta.removed_todo_ids : [])
        .forEach(todoId => itemsById.delete(String(todoId || '')));
    (Array.isArray(delta?.changed_items) ? delta.changed_items : [])
        .forEach(item => {
            const todoId = String(item?.todo_id || '');
            if (todoId) {
                itemsById.set(todoId, item);
            }
        });
    const items = Array.from(itemsById.values()).sort(compareBoardItems);
    return {
        workspace_id: delta?.workspace_id || cached?.workspace_id || selectedWorkspaceId,
        repository_full_name: delta?.repository_full_name || cached?.repository_full_name || null,
        items,
        status_counts: delta?.status_counts || computeStatusCounts(items),
        diagnostics: Array.isArray(delta?.diagnostics) ? delta.diagnostics : [],
        synced_at: delta?.synced_at || cached?.synced_at || null,
        revision: Number(delta?.revision || cached?.revision || 0),
    };
}

function applyReturnedItem(item) {
    const todoId = String(item?.todo_id || '');
    if (!todoId) {
        return;
    }
    const activeKey = cacheKey(item?.workspace_id || selectedWorkspaceId, false);
    const archivedKey = cacheKey(item?.workspace_id || selectedWorkspaceId, true);
    const activeBoard = boardCache.get(activeKey);
    if (activeBoard) {
        const delta = String(item?.status || '') === 'archived'
            ? { removed_todo_ids: [todoId], changed_items: [], revision: item?.item_revision }
            : { removed_todo_ids: [], changed_items: [item], revision: item?.item_revision };
        boardCache.set(activeKey, mergeBoardDelta(activeBoard, delta));
    }
    const archivedBoard = boardCache.get(archivedKey);
    if (archivedBoard) {
        boardCache.set(archivedKey, mergeBoardDelta(archivedBoard, {
            removed_todo_ids: [],
            changed_items: [item],
            revision: item?.item_revision,
        }));
    }
    persistBoardCache();
    renderBoard(getRoot());
}

async function refreshCurrentDelta() {
    if (!selectedWorkspaceId) {
        return;
    }
    await loadBoard({ workspaceId: selectedWorkspaceId, sync: false });
}

function currentCacheKey() {
    return cacheKey(selectedWorkspaceId, includeArchived);
}

function cacheKey(workspaceId, archived) {
    return `${String(workspaceId || '').trim()}::${archived ? 'archived' : 'active'}`;
}

function compareBoardItems(left, right) {
    const leftDate = new Date(effectiveUpdatedAt(left) || '').getTime();
    const rightDate = new Date(effectiveUpdatedAt(right) || '').getTime();
    return (Number.isFinite(rightDate) ? rightDate : 0) - (Number.isFinite(leftDate) ? leftDate : 0);
}

function getColumnView(status) {
    const key = String(status || '').trim();
    const existing = columnViews.get(key);
    if (existing) {
        return existing;
    }
    const nextView = { query: '', sort: 'updated_desc' };
    columnViews.set(key, nextView);
    return nextView;
}

function updateColumnView(status, patch) {
    const key = String(status || '').trim();
    if (!key) {
        return;
    }
    columnViews.set(key, { ...getColumnView(key), ...patch });
}

function filterAndSortColumnItems(status, items) {
    const view = getColumnView(status);
    const query = normalizeSearchValue(view.query);
    const filtered = query
        ? items.filter(item => searchHaystack(item).includes(query))
        : [...items];
    filtered.sort((left, right) => compareColumnItems(left, right, view.sort));
    return filtered;
}

function compareColumnItems(left, right, sort) {
    if (sort === 'updated_asc') {
        return dateValue(effectiveUpdatedAt(left)) - dateValue(effectiveUpdatedAt(right));
    }
    if (sort === 'title_asc') {
        return String(left?.title || '').localeCompare(String(right?.title || ''), undefined, { sensitivity: 'base' });
    }
    if (sort === 'title_desc') {
        return String(right?.title || '').localeCompare(String(left?.title || ''), undefined, { sensitivity: 'base' });
    }
    return dateValue(effectiveUpdatedAt(right)) - dateValue(effectiveUpdatedAt(left));
}

function effectiveUpdatedAt(item) {
    return item?.source_updated_at || item?.updated_at || '';
}

function dateValue(value) {
    const timestamp = new Date(value || '').getTime();
    return Number.isFinite(timestamp) ? timestamp : 0;
}

function searchHaystack(item) {
    return normalizeSearchValue([
        item?.title,
        item?.body,
        item?.repository_full_name,
        item?.issue_number ? `issue ${item.issue_number}` : '',
        item?.linked_pr_number ? `pr ${item.linked_pr_number}` : '',
        item?.pull_request_number ? `pr ${item.pull_request_number}` : '',
        item?.session_id,
        formatSourceLabel(item),
    ].filter(Boolean).join(' '));
}

function normalizeSearchValue(value) {
    return String(value || '').trim().toLowerCase();
}

function updateDetailModal() {
    const root = getRoot();
    if (!root) {
        return;
    }
    const existing = root.querySelector('.board-todos-detail-backdrop');
    if (existing) {
        existing.remove();
    }
    const section = root.querySelector('.board-todos');
    const board = selectedWorkspaceId ? boardCache.get(currentCacheKey()) : null;
    const html = renderDetailModal(board);
    if (html && section) {
        section.insertAdjacentHTML('beforeend', html);
    }
}

function cssEscape(value) {
    if (window.CSS?.escape) {
        return window.CSS.escape(String(value || ''));
    }
    return String(value || '').replace(/"/g, '\\"');
}

function refreshBoardTodoColumn(status) {
    const root = getRoot();
    const board = selectedWorkspaceId ? boardCache.get(currentCacheKey()) : null;
    if (!root || !board) {
        return;
    }
    const safeStatus = String(status || '').trim();
    const items = Array.isArray(board?.items) ? board.items : [];
    const columnItems = items.filter(item => String(item?.status || '') === safeStatus);
    const filteredItems = filterAndSortColumnItems(safeStatus, columnItems);
    const visibleItems = stagedItems(safeStatus, filteredItems);
    const countElement = root.querySelector(`[data-board-todo-count="${cssEscape(safeStatus)}"]`);
    if (countElement) {
        countElement.innerHTML = renderColumnCount(filteredItems.length, columnItems.length);
    }
    const listElement = root.querySelector(`[data-board-todo-list="${cssEscape(safeStatus)}"]`);
    if (!listElement) {
        return;
    }
    if (safeStatus === 'archived') {
        listElement.innerHTML = visibleItems.map(renderCard).join('') || renderArchiveEmptyOrSkeleton(filteredItems);
        return;
    }
    listElement.innerHTML = visibleItems.map(renderCard).join('') || renderColumnEmptyOrSkeleton(filteredItems);
}

function computeStatusCounts(items) {
    return items.reduce((counts, item) => {
        const status = String(item?.status || '');
        if (status && Object.prototype.hasOwnProperty.call(counts, status)) {
            counts[status] += 1;
        }
        return counts;
    }, { todo: 0, in_progress: 0, review: 0, done: 0, archived: 0 });
}

function startProgressiveRender(key, mountToken = boardMountToken) {
    const board = boardCache.get(key);
    const root = getRoot();
    if (!board || !root || !isCurrentBoardMount(mountToken)) {
        return;
    }
    const token = ++progressiveRenderToken;
    stagedCounts.set(key, initialStagedCounts());
    renderBoard(root);
    const advance = () => {
        if (token !== progressiveRenderToken || !isCurrentBoardMount(mountToken)) {
            return;
        }
        const previousCounts = { ...(stagedCounts.get(key) || initialStagedCounts()) };
        const nextCounts = { ...previousCounts };
        const items = Array.isArray(board.items) ? board.items : [];
        const done = updateStagedCounts(nextCounts, items);
        markEnteringItems(previousCounts, nextCounts, items);
        stagedCounts.set(key, nextCounts);
        renderBoard(root);
        if (done) {
            window.setTimeout(() => {
                if (token !== progressiveRenderToken || !isCurrentBoardMount(mountToken)) {
                    return;
                }
                stagedCounts.delete(key);
                enteringTodoIds.clear();
                renderBoard(root);
            }, PROGRESSIVE_FRAME_MS);
            return;
        }
        window.setTimeout(advance, PROGRESSIVE_FRAME_MS);
    };
    window.setTimeout(advance, PROGRESSIVE_FRAME_MS);
}

function cancelProgressiveRender() {
    progressiveRenderToken += 1;
    stagedCounts.clear();
    enteringTodoIds.clear();
}

function isCurrentBoardMount(mountToken) {
    return mountToken === boardMountToken && !!getRoot();
}

function initialStagedCounts() {
    return { todo: 0, in_progress: 0, review: 0, done: 0, archived: 0 };
}

function updateStagedCounts(counts, items) {
    let done = true;
    const statuses = includeArchived ? ['archived'] : STATUSES;
    statuses.forEach(status => {
        const total = items.filter(item => String(item?.status || '') === status).length;
        const nextValue = Math.min(total, Number(counts[status] || 0) + PROGRESSIVE_BATCH_SIZE);
        counts[status] = nextValue;
        if (nextValue < total) {
            done = false;
        }
    });
    return done;
}

function markEnteringItems(previousCounts, nextCounts, items) {
    enteringTodoIds.clear();
    const statuses = includeArchived ? ['archived'] : STATUSES;
    statuses.forEach(status => {
        const statusItems = filterAndSortColumnItems(
            status,
            items.filter(item => String(item?.status || '') === status),
        );
        const previousLimit = Number(previousCounts[status] || 0);
        const nextLimit = Number(nextCounts[status] || 0);
        statusItems.slice(previousLimit, nextLimit).forEach(item => {
            const todoId = String(item?.todo_id || '');
            if (todoId) {
                enteringTodoIds.add(todoId);
            }
        });
    });
}

function paginateDetailBody(value) {
    const text = String(value || '').trim() || t('board_todos.value.none');
    if (text.length <= DETAIL_BODY_PAGE_CHARS) {
        return [text];
    }
    const paragraphs = text.split(/\n{2,}/);
    const pages = [];
    let current = '';
    paragraphs.forEach(paragraph => {
        const next = current ? `${current}\n\n${paragraph}` : paragraph;
        if (next.length <= DETAIL_BODY_PAGE_CHARS || !current) {
            current = next;
            return;
        }
        pages.push(current);
        current = paragraph;
    });
    if (current) {
        pages.push(current);
    }
    return pages.length ? pages : [text];
}

function renderIcon(name) {
    if (name === 'external') {
        return '<svg aria-hidden="true" viewBox="0 0 20 20" focusable="false"><path d="M6.5 5.5h8v8m0-8-9 9" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    }
    if (name === 'search') {
        return '<svg aria-hidden="true" viewBox="0 0 20 20" focusable="false"><path d="m14 14 3 3M8.8 15a6.2 6.2 0 1 1 0-12.4 6.2 6.2 0 0 1 0 12.4Z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
    }
    return '<svg aria-hidden="true" viewBox="0 0 20 20" focusable="false"><path d="M10 9.5v5m0-9h.01" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
}

function hydrateBoardCache() {
    if (cacheHydrated) {
        return;
    }
    cacheHydrated = true;
    try {
        const rawValue = window.localStorage?.getItem(CACHE_STORAGE_KEY);
        if (!rawValue) {
            return;
        }
        const payload = JSON.parse(rawValue);
        const syncEntries = Array.isArray(payload?.auto_sync_times)
            ? payload.auto_sync_times
            : [];
        syncEntries.forEach(entry => {
            if (!Array.isArray(entry) || entry.length !== 2) {
                return;
            }
            const workspaceId = String(entry[0] || '').trim();
            const timestamp = Number(entry[1] || 0);
            if (workspaceId && Number.isFinite(timestamp) && timestamp > 0) {
                autoSyncTimes.set(workspaceId, timestamp);
            }
        });
        const entries = Array.isArray(payload?.boards) ? payload.boards : [];
        entries.forEach(entry => {
            if (!Array.isArray(entry) || entry.length !== 2) {
                return;
            }
            const key = String(entry[0] || '').trim();
            if (!key) {
                return;
            }
            if (key.includes('::')) {
                boardCache.set(key, entry[1]);
                return;
            }
            const workspaceId = key;
            if (workspaceId) {
                boardCache.set(cacheKey(workspaceId, false), entry[1]);
            }
        });
    } catch {
        // A corrupt UI cache should not block the board page.
    }
}

function persistBoardCache() {
    try {
        const boards = Array.from(boardCache.entries()).slice(-12);
        window.localStorage?.setItem(CACHE_STORAGE_KEY, JSON.stringify({
            version: 1,
            saved_at: new Date().toISOString(),
            boards,
            auto_sync_times: Array.from(autoSyncTimes.entries()).slice(-24),
        }));
    } catch {
        // Ignore browser storage failures and keep the in-memory cache.
    }
}

function truncateText(value, maxLength) {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (text.length <= maxLength) {
        return text;
    }
    return `${text.slice(0, maxLength - 1)}...`;
}

function formatDateTime(value) {
    if (!value) {
        return t('board_todos.value.none');
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return t('board_todos.value.none');
    }
    return date.toLocaleString();
}

function getRoot() {
    return document.getElementById(ROOT_ID);
}
