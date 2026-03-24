/**
 * components/projectView.js
 * Renders the main workspace snapshot for a selected project.
 */
import {
    fetchWorkspaceDiffFile,
    fetchWorkspaceDiffs,
    fetchWorkspaceSnapshot,
    fetchWorkspaceTree,
} from '../core/api.js';
import { clearAllPanels } from './agentPanel.js';
import { hideRoundNavigator } from './rounds/navigator.js';
import { setSubagentRailExpanded } from './subagentRail.js';
import { state } from '../core/state.js';
import { els } from '../utils/dom.js';
import { t } from '../utils/i18n.js';
import { sysLog } from '../utils/logger.js';

let currentWorkspace = null;
let currentSnapshot = null;
let currentSnapshotWorkspaceId = null;
let currentLoadToken = 0;
let languageBound = false;
let selectedTreePath = null;
let currentDiffState = createInitialDiffState();
const expandedTreePaths = new Set();
const loadingTreePaths = new Set();
const treeLoadErrors = new Map();
const workspaceViewCache = new Map();

export function initializeProjectView() {
    syncActionLabels();
    if (els.projectViewReloadBtn) {
        els.projectViewReloadBtn.onclick = () => {
            void refreshProjectView();
        };
    }
    if (els.projectViewCloseBtn) {
        els.projectViewCloseBtn.title = t('workspace_view.back');
        els.projectViewCloseBtn.setAttribute('aria-label', t('workspace_view.back'));
        els.projectViewCloseBtn.onclick = () => {
            hideProjectView();
        };
    }
    if (!languageBound && typeof document?.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            syncActionLabels();
            if (state.currentMainView === 'project') {
                if (currentSnapshot) {
                    renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
                } else {
                    renderLoadingState(currentWorkspace);
                }
            }
        });
        languageBound = true;
    }
}

function syncActionLabels() {
    if (els.projectViewReloadBtn) {
        els.projectViewReloadBtn.textContent = t('workspace_view.reload');
    }
    if (els.projectViewCloseBtn) {
        els.projectViewCloseBtn.title = t('workspace_view.back');
        els.projectViewCloseBtn.setAttribute('aria-label', t('workspace_view.back'));
    }
}

export async function openWorkspaceProjectView(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    if (!workspaceId) {
        return;
    }

    cacheProjectViewState();
    currentWorkspace = workspace;
    currentSnapshotWorkspaceId = workspaceId;
    state.currentMainView = 'project';
    state.currentProjectViewWorkspaceId = workspaceId;
    state.currentWorkspaceId = workspaceId;
    clearAllPanels();
    hideRoundNavigator();
    setSubagentRailExpanded(false);
    setProjectViewVisible(true);

    const restoredFromCache = restoreProjectViewState(workspaceId);
    if (restoredFromCache && currentSnapshot) {
        renderWorkspaceSnapshot(workspace, currentSnapshot);
        if (selectedTreePath && findDiffSummary(selectedTreePath)) {
            void ensureDiffFileLoaded(selectedTreePath);
        }
    } else {
        resetProjectViewState(workspaceId);
        currentDiffState = {
            ...createInitialDiffState(),
            status: 'loading',
        };
        renderLoadingState(workspace);
    }

    const loadToken = ++currentLoadToken;
    void loadWorkspaceSnapshot(workspaceId, loadToken);
    void loadWorkspaceDiffs(workspaceId, loadToken);
}

export async function refreshProjectView() {
    if (!currentWorkspace) {
        return;
    }
    await openWorkspaceProjectView(currentWorkspace);
}

export function hideProjectView() {
    cacheProjectViewState();
    currentWorkspace = null;
    resetProjectViewState(null);
    state.currentMainView = 'session';
    state.currentProjectViewWorkspaceId = null;
    currentLoadToken += 1;
    setProjectViewVisible(false);
}

function resetProjectViewState(workspaceId) {
    currentSnapshot = null;
    currentSnapshotWorkspaceId = workspaceId;
    selectedTreePath = null;
    currentDiffState = createInitialDiffState();
    expandedTreePaths.clear();
    loadingTreePaths.clear();
    treeLoadErrors.clear();
}

function createInitialDiffState() {
    return {
        status: 'idle',
        diffFiles: [],
        diffMessage: null,
        isGitRepository: null,
        gitRootPath: null,
        loadedDiffs: new Map(),
        loadingFilePaths: new Set(),
        fileErrors: new Map(),
    };
}

function cacheProjectViewState() {
    const workspaceId = String(currentSnapshotWorkspaceId || currentWorkspace?.workspace_id || '').trim();
    if (!workspaceId) {
        return;
    }
    if (!currentSnapshot && currentDiffState.status !== 'ready') {
        return;
    }
    workspaceViewCache.set(workspaceId, {
        snapshot: cloneSnapshot(currentSnapshot),
        selectedTreePath,
        expandedTreePaths: Array.from(expandedTreePaths),
        diffState: cloneDiffState(currentDiffState),
    });
}

function restoreProjectViewState(workspaceId) {
    const cachedState = workspaceViewCache.get(workspaceId);
    resetProjectViewState(workspaceId);
    if (!cachedState) {
        return false;
    }

    currentSnapshot = cloneSnapshot(cachedState.snapshot);
    selectedTreePath = String(cachedState.selectedTreePath || '').trim() || null;
    currentDiffState = cloneDiffState(cachedState.diffState);

    for (const path of Array.isArray(cachedState.expandedTreePaths) ? cachedState.expandedTreePaths : []) {
        const normalizedPath = String(path || '').trim();
        if (normalizedPath) {
            expandedTreePaths.add(normalizedPath);
        }
    }

    return currentSnapshot !== null;
}

async function loadWorkspaceSnapshot(workspaceId, loadToken) {
    try {
        const snapshot = await fetchWorkspaceSnapshot(workspaceId);
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId) {
            return;
        }

        const nextSnapshot = normalizeSnapshot(snapshot);
        if (currentSnapshot && currentSnapshotWorkspaceId === workspaceId) {
            mergeTreeState(nextSnapshot?.tree, currentSnapshot?.tree);
        }
        currentSnapshot = nextSnapshot;
        currentSnapshotWorkspaceId = workspaceId;

        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        cacheProjectViewState();
    } catch (error) {
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId) {
            return;
        }
        if (!currentSnapshot) {
            currentDiffState = {
                ...createInitialDiffState(),
                status: 'error',
                diffMessage: String(error?.message || error || ''),
            };
            renderErrorState(currentWorkspace, error);
        }
        sysLog(`Failed to load project snapshot: ${error?.message || error}`, 'log-error');
    }
}

async function loadWorkspaceDiffs(workspaceId, loadToken) {
    try {
        const payload = await fetchWorkspaceDiffs(workspaceId);
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId) {
            return;
        }

        const diffFiles = Array.isArray(payload?.diff_files) ? payload.diff_files : [];
        currentDiffState = {
            status: 'ready',
            diffFiles,
            diffMessage: String(payload?.diff_message || '').trim() || null,
            isGitRepository: payload?.is_git_repository === true,
            gitRootPath: payload?.git_root_path || null,
            loadedDiffs: filterLoadedDiffs(currentDiffState.loadedDiffs, diffFiles),
            loadingFilePaths: new Set(),
            fileErrors: filterFileErrors(currentDiffState.fileErrors, diffFiles),
        };
        if (!selectedTreePath && currentDiffState.diffFiles.length > 0) {
            selectedTreePath = String(currentDiffState.diffFiles[0]?.path || '').trim() || null;
        }
        if (currentSnapshot) {
            renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        }
        cacheProjectViewState();
        if (selectedTreePath && findDiffSummary(selectedTreePath)) {
            void ensureDiffFileLoaded(selectedTreePath);
        }
    } catch (error) {
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId) {
            return;
        }
        if (currentDiffState.status !== 'ready') {
            currentDiffState = {
                ...createInitialDiffState(),
                status: 'error',
                diffMessage: String(error?.message || error || ''),
            };
        }
        if (currentSnapshot) {
            renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        }
        sysLog(`Failed to load project diffs: ${error?.message || error}`, 'log-error');
    }
}

function setProjectViewVisible(visible) {
    if (els.projectView) {
        els.projectView.style.display = visible ? 'block' : 'none';
    }
    if (els.chatContainer) {
        els.chatContainer.style.display = visible ? 'none' : 'flex';
    }

    if (visible) {
        const observabilityView = document.getElementById('observability-view');
        const observabilityButton = document.getElementById('observability-btn');
        if (observabilityView) {
            observabilityView.style.display = 'none';
        }
        if (observabilityButton) {
            observabilityButton.classList.remove('active');
        }
        document.body?.classList?.remove('observability-mode');
    }
}

function renderLoadingState(workspace) {
    renderToolbar(workspace, {
        summary: t('workspace_view.loading'),
    });
    if (els.projectViewContent) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-grid">
                <section class="workspace-view-panel">
                    <div class="workspace-view-panel-header">
                        <h3>${escapeHtml(t('workspace_view.tree'))}</h3>
                        <span class="workspace-view-panel-meta"></span>
                    </div>
                    <div class="workspace-tree-shell">
                        ${renderInlineState(t('workspace_view.loading_tree'))}
                    </div>
                </section>
                <section class="workspace-view-panel workspace-diff-panel">
                    <div class="workspace-view-panel-header">
                        <h3>${escapeHtml(t('workspace_view.diffs'))}</h3>
                        <span class="workspace-view-panel-meta"></span>
                    </div>
                    ${renderInlineState(t('workspace_view.loading_diffs'))}
                </section>
            </div>
        `;
    }
}

function renderErrorState(workspace, error) {
    renderToolbar(workspace, {
        summary: t('workspace_view.load_failed'),
    });
    if (els.projectViewContent) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-empty-state is-error">
                <p>${escapeHtml(t('workspace_view.load_failed'))}</p>
                <p>${escapeHtml(String(error?.message || error || ''))}</p>
            </div>
        `;
    }
}

function renderWorkspaceSnapshot(workspace, snapshot) {
    renderToolbar(workspace, { summary: summarizeDiffState() });
    if (!els.projectViewContent) {
        return;
    }

    els.projectViewContent.innerHTML = `
        <div class="workspace-view-grid">
            <section class="workspace-view-panel">
                <div class="workspace-view-panel-header">
                    <h3>${escapeHtml(t('workspace_view.tree'))}</h3>
                    <span class="workspace-view-panel-meta">${escapeHtml(snapshot?.root_path || '')}</span>
                </div>
                <div class="workspace-tree-shell">
                    ${renderTree(snapshot?.tree)}
                </div>
            </section>
            <section class="workspace-view-panel workspace-diff-panel">
                <div class="workspace-view-panel-header">
                    <h3>${escapeHtml(t('workspace_view.diffs'))}</h3>
                    <span class="workspace-view-panel-meta">${escapeHtml(summarizeDiffState())}</span>
                </div>
                ${renderDiffSection()}
            </section>
        </div>
    `;

    bindTreeInteractions();
    bindDiffInteractions();
}

function renderToolbar(workspace, { summary = '' } = {}) {
    if (els.projectViewTitle) {
        els.projectViewTitle.textContent = formatWorkspaceTitle(workspace);
    }
    if (els.projectViewSummary) {
        els.projectViewSummary.textContent = summary;
    }
}

function summarizeDiffState() {
    if (currentDiffState.status === 'loading') {
        return t('workspace_view.loading_diffs');
    }
    if (currentDiffState.status === 'error') {
        return t('workspace_view.load_failed');
    }
    if (currentDiffState.status !== 'ready') {
        return '';
    }
    if (currentDiffState.isGitRepository !== true) {
        return currentDiffState.diffMessage || t('workspace_view.not_git_repository');
    }
    if (currentDiffState.diffMessage) {
        return currentDiffState.diffMessage;
    }
    return formatTemplate(t('workspace_view.diff_summary'), {
        count: currentDiffState.diffFiles.length,
    });
}

function normalizeSnapshot(snapshot) {
    return {
        workspace_id: snapshot?.workspace_id || '',
        root_path: snapshot?.root_path || '',
        tree: normalizeTreeNode(snapshot?.tree, true),
    };
}

function normalizeTreeNode(node, childrenLoaded) {
    if (!node || typeof node !== 'object') {
        return null;
    }
    const isDirectory = node.kind === 'directory';
    const children = Array.isArray(node.children)
        ? node.children
            .map(child => normalizeTreeNode(child, false))
            .filter(Boolean)
        : [];
    return {
        name: String(node.name || node.path || '.'),
        path: String(node.path || '.').trim() || '.',
        kind: isDirectory ? 'directory' : 'file',
        hasChildren: node.has_children === true,
        children,
        childrenLoaded: childrenLoaded === true,
    };
}

function renderTree(tree) {
    if (!tree || typeof tree !== 'object') {
        return renderInlineState(t('workspace_view.loading_tree'));
    }

    const children = Array.isArray(tree.children) ? tree.children : [];
    if (children.length === 0) {
        return renderInlineState(t('workspace_view.empty_tree'));
    }

    return `
        <div class="workspace-tree-root">
            ${children.map(child => renderTreeNode(child)).join('')}
        </div>
    `;
}

function renderTreeNode(node) {
    if (!node || typeof node !== 'object') {
        return '';
    }

    const nodePath = String(node.path || '.').trim() || '.';
    const nodeLabel = escapeHtml(node.name || node.path || '.');

    if (node.kind !== 'directory') {
        const isSelected = selectedTreePath === nodePath;
        return `
            <div class="workspace-tree-node is-file">
                <button
                    type="button"
                    class="workspace-tree-entry workspace-tree-file${isSelected ? ' is-selected' : ''}"
                    data-tree-file-path="${escapeHtml(nodePath)}"
                    aria-pressed="${isSelected ? 'true' : 'false'}"
                >
                    <span class="workspace-tree-chevron is-placeholder" aria-hidden="true"></span>
                    ${renderFileIcon()}
                    <span class="workspace-tree-label">${nodeLabel}</span>
                </button>
            </div>
        `;
    }

    const isExpanded = expandedTreePaths.has(nodePath);
    const isLoading = loadingTreePaths.has(nodePath);
    const loadError = treeLoadErrors.get(nodePath) || '';
    return `
        <div class="workspace-tree-node is-directory">
            <button
                type="button"
                class="workspace-tree-toggle"
                data-tree-toggle-path="${escapeHtml(nodePath)}"
                aria-expanded="${isExpanded ? 'true' : 'false'}"
            >
                <span class="workspace-tree-chevron" aria-hidden="true">${isExpanded ? '&#9662;' : '&#9656;'}</span>
                ${renderFolderIcon(isExpanded)}
                <span class="workspace-tree-label">${nodeLabel}</span>
            </button>
            ${renderTreeChildren(node, { isExpanded, isLoading, loadError })}
        </div>
    `;
}

function renderTreeChildren(node, { isExpanded, isLoading, loadError }) {
    if (!isExpanded) {
        return '';
    }
    if (isLoading) {
        return `
            <div class="workspace-tree-children">
                ${renderTreePlaceholder(t('workspace_view.loading_directory'))}
            </div>
        `;
    }
    if (loadError) {
        return `
            <div class="workspace-tree-children">
                ${renderTreePlaceholder(loadError, 'is-error')}
            </div>
        `;
    }
    const children = Array.isArray(node.children) ? node.children : [];
    if (children.length === 0) {
        return '';
    }
    return `
        <div class="workspace-tree-children">
            ${children.map(child => renderTreeNode(child)).join('')}
        </div>
    `;
}

function renderTreePlaceholder(message, extraClass = '') {
    return `
        <div class="workspace-tree-placeholder ${extraClass}">
            <span>${escapeHtml(message)}</span>
        </div>
    `;
}

function renderDiffSection() {
    if (currentDiffState.status === 'loading') {
        return renderInlineState(t('workspace_view.loading_diffs'));
    }
    if (currentDiffState.status === 'error') {
        return renderInlineState(currentDiffState.diffMessage || t('workspace_view.load_failed'), 'is-error');
    }
    if (currentDiffState.status !== 'ready') {
        return renderInlineState(t('workspace_view.loading_diffs'));
    }
    if (currentDiffState.isGitRepository !== true) {
        return renderInlineState(currentDiffState.diffMessage || t('workspace_view.not_git_repository'));
    }
    if (currentDiffState.diffMessage) {
        return renderInlineState(currentDiffState.diffMessage, 'is-error');
    }
    if (currentDiffState.diffFiles.length === 0) {
        return renderInlineState(t('workspace_view.no_diffs'));
    }
    return `
        <div class="workspace-diff-list">
            ${currentDiffState.diffFiles.map(file => renderDiffFile(file)).join('')}
        </div>
    `;
}

function renderDiffFile(file) {
    const changeType = String(file?.change_type || '').trim() || 'modified';
    const changeLabel = t(`workspace_view.change.${changeType}`);
    const previousPath = String(file?.previous_path || '').trim();
    const filePath = String(file?.path || '').trim();
    const isSelected = filePath && selectedTreePath === filePath;
    const diffBody = renderDiffBody(filePath, isSelected);
    return `
        <article
            class="workspace-diff-card${isSelected ? ' is-selected' : ''}${diffBody ? ' has-body' : ''}"
            data-diff-path="${escapeHtml(filePath)}"
        >
            <div class="workspace-diff-header">
                <span class="workspace-diff-status is-${escapeHtml(changeType)}">${escapeHtml(changeLabel)}</span>
                <code class="workspace-diff-path">${escapeHtml(filePath)}</code>
                ${previousPath ? `<span class="workspace-diff-previous">${escapeHtml(previousPath)} -> ${escapeHtml(filePath)}</span>` : ''}
            </div>
            ${diffBody}
        </article>
    `;
}

function renderDiffBody(filePath, isSelected) {
    if (!isSelected) {
        return '';
    }
    if (currentDiffState.loadingFilePaths.has(filePath)) {
        return renderDiffBodyState(t('workspace_view.loading_diff'));
    }
    const loadError = currentDiffState.fileErrors.get(filePath);
    if (loadError) {
        return renderDiffBodyState(loadError, 'is-error');
    }
    const diffFile = currentDiffState.loadedDiffs.get(filePath);
    if (!diffFile) {
        return renderDiffBodyState(t('workspace_view.loading_diff'));
    }
    if (diffFile.is_binary === true) {
        return renderDiffBodyState(t('workspace_view.binary_diff'));
    }
    const diffText = String(diffFile.diff || '').replace(/\r\n/g, '\n');
    if (!diffText.trim()) {
        return renderDiffBodyState(t('workspace_view.empty_diff'));
    }
    return renderStructuredDiff(diffText);
}

function renderStructuredDiff(diffText) {
    const segments = parseDiffSegments(diffText);
    if (segments.length === 0) {
        return `
            <pre class="workspace-diff-pre"><code>${escapeHtml(diffText)}</code></pre>
        `;
    }
    return `
        <div class="workspace-diff-view">
            ${segments.map(renderDiffSegment).join('')}
        </div>
    `;
}

function parseDiffSegments(diffText) {
    const lines = String(diffText || '').split('\n');
    const segments = [];
    let currentSegment = null;
    let oldLine = 0;
    let newLine = 0;

    for (const line of lines) {
        if (line.startsWith('@@')) {
            const match = /@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)/.exec(line);
            oldLine = Number(match?.[1] || 0);
            newLine = Number(match?.[3] || 0);
            currentSegment = {
                header: line,
                rows: [],
            };
            segments.push(currentSegment);
            continue;
        }

        if (!currentSegment) {
            currentSegment = {
                header: null,
                rows: [],
            };
            segments.push(currentSegment);
        }

        let kind = 'meta';
        let marker = '';
        let content = line;
        let oldNumber = '';
        let newNumber = '';

        if (line.startsWith('+') && !line.startsWith('+++')) {
            kind = 'added';
            marker = '+';
            content = line.slice(1);
            newNumber = String(newLine);
            newLine += 1;
        } else if (line.startsWith('-') && !line.startsWith('---')) {
            kind = 'deleted';
            marker = '-';
            content = line.slice(1);
            oldNumber = String(oldLine);
            oldLine += 1;
        } else if (line.startsWith(' ')) {
            kind = 'context';
            marker = ' ';
            content = line.slice(1);
            oldNumber = String(oldLine);
            newNumber = String(newLine);
            oldLine += 1;
            newLine += 1;
        } else if (line.startsWith('\\')) {
            kind = 'note';
            marker = '\\';
        }

        currentSegment.rows.push({
            kind,
            marker,
            content,
            oldNumber,
            newNumber,
        });
    }

    return segments;
}

function renderDiffSegment(segment) {
    const header = segment?.header
        ? `<div class="workspace-diff-hunk-header">${escapeHtml(segment.header)}</div>`
        : '';
    const rows = Array.isArray(segment?.rows) ? segment.rows.map(renderDiffRow).join('') : '';
    return `
        <section class="workspace-diff-hunk">
            ${header}
            <div class="workspace-diff-grid" role="table">
                ${rows}
            </div>
        </section>
    `;
}

function renderDiffRow(row) {
    const kind = String(row?.kind || 'context');
    return `
        <div class="workspace-diff-row is-${escapeHtml(kind)}" role="row">
            <span class="workspace-diff-line-number" role="cell">${escapeHtml(row?.oldNumber || '')}</span>
            <span class="workspace-diff-line-number" role="cell">${escapeHtml(row?.newNumber || '')}</span>
            <span class="workspace-diff-line-marker" role="cell">${escapeHtml(row?.marker || '')}</span>
            <code class="workspace-diff-line-text" role="cell">${escapeHtml(row?.content || '')}</code>
        </div>
    `;
}

function renderDiffBodyState(message, extraClass = '') {
    return `
        <div class="workspace-diff-body-state ${extraClass}">
            <p>${escapeHtml(message)}</p>
        </div>
    `;
}

function renderInlineState(message, extraClass = '') {
    return `
        <div class="workspace-view-empty-state ${extraClass}">
            <p>${escapeHtml(message)}</p>
        </div>
    `;
}

function bindTreeInteractions() {
    if (!els.projectViewContent || typeof els.projectViewContent.querySelectorAll !== 'function') {
        return;
    }

    for (const toggle of els.projectViewContent.querySelectorAll('.workspace-tree-toggle')) {
        const togglePath = String(toggle.getAttribute('data-tree-toggle-path') || '').trim();
        toggle.onclick = () => {
            void toggleTreePath(togglePath);
        };
        toggle.onkeydown = (event) => {
            if (event?.key === 'Enter' || event?.key === ' ' || event?.key === 'Spacebar') {
                event.preventDefault?.();
                void toggleTreePath(togglePath);
            }
        };
    }

    for (const fileEntry of els.projectViewContent.querySelectorAll('.workspace-tree-file')) {
        const filePath = String(fileEntry.getAttribute('data-tree-file-path') || '').trim();
        fileEntry.onclick = () => {
            void selectTreePath(filePath);
        };
        fileEntry.onkeydown = (event) => {
            if (event?.key === 'Enter' || event?.key === ' ' || event?.key === 'Spacebar') {
                event.preventDefault?.();
                void selectTreePath(filePath);
            }
        };
    }
}

function bindDiffInteractions() {
    if (!els.projectViewContent || typeof els.projectViewContent.querySelectorAll !== 'function') {
        return;
    }

    for (const diffCard of els.projectViewContent.querySelectorAll('.workspace-diff-card')) {
        const diffPath = String(diffCard.getAttribute('data-diff-path') || '').trim();
        diffCard.onclick = () => {
            void selectTreePath(diffPath);
        };
    }
}

async function toggleTreePath(path) {
    if (!path || !currentWorkspace || !currentSnapshot) {
        return;
    }

    if (expandedTreePaths.has(path)) {
        expandedTreePaths.delete(path);
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        cacheProjectViewState();
        return;
    }

    expandedTreePaths.add(path);
    treeLoadErrors.delete(path);
    const node = findTreeNode(currentSnapshot.tree, path);
    if (node?.kind === 'directory' && node.hasChildren && node.childrenLoaded !== true) {
        loadingTreePaths.add(path);
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        await loadWorkspaceTree(path);
        return;
    }
    renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
    cacheProjectViewState();
}

async function loadWorkspaceTree(path) {
    if (!currentWorkspace || !currentSnapshot) {
        return;
    }
    const workspaceId = String(currentWorkspace.workspace_id || '').trim();
    const loadToken = currentLoadToken;
    try {
        const listing = await fetchWorkspaceTree(workspaceId, path);
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId || !currentSnapshot) {
            return;
        }
        const node = findTreeNode(currentSnapshot.tree, path);
        if (node) {
            node.children = Array.isArray(listing?.children)
                ? listing.children
                    .map(child => normalizeTreeNode(child, false))
                    .filter(Boolean)
                : [];
            node.childrenLoaded = true;
        }
        loadingTreePaths.delete(path);
        treeLoadErrors.delete(path);
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        cacheProjectViewState();
    } catch (error) {
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId) {
            return;
        }
        loadingTreePaths.delete(path);
        treeLoadErrors.set(path, String(error?.message || error || t('workspace_view.load_failed')));
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        cacheProjectViewState();
        sysLog(`Failed to load project tree path ${path}: ${error?.message || error}`, 'log-error');
    }
}

async function selectTreePath(path) {
    if (!path || !currentWorkspace || !currentSnapshot) {
        return;
    }

    await revealTreePath(path);
    selectedTreePath = path;
    renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
    cacheProjectViewState();
    if (findDiffSummary(path)) {
        void ensureDiffFileLoaded(path);
    }
}

function findDiffSummary(path) {
    const normalizedPath = String(path || '').trim();
    if (!normalizedPath || currentDiffState.status !== 'ready') {
        return null;
    }
    return currentDiffState.diffFiles.find(file => String(file?.path || '').trim() === normalizedPath) || null;
}

function ensureDiffFileLoaded(path) {
    const normalizedPath = String(path || '').trim();
    if (!normalizedPath || currentDiffState.status !== 'ready') {
        return;
    }
    if (currentDiffState.loadedDiffs.has(normalizedPath) || currentDiffState.loadingFilePaths.has(normalizedPath)) {
        return;
    }
    currentDiffState.fileErrors.delete(normalizedPath);
    currentDiffState.loadingFilePaths.add(normalizedPath);
    renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
    cacheProjectViewState();
    void loadWorkspaceDiffFile(normalizedPath);
}

async function loadWorkspaceDiffFile(path) {
    if (!currentWorkspace || currentDiffState.status !== 'ready') {
        return;
    }

    const workspaceId = String(currentWorkspace.workspace_id || '').trim();
    const loadToken = currentLoadToken;
    try {
        const diffFile = await fetchWorkspaceDiffFile(workspaceId, path);
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId || currentDiffState.status !== 'ready') {
            return;
        }
        currentDiffState.loadingFilePaths.delete(path);
        currentDiffState.fileErrors.delete(path);
        currentDiffState.loadedDiffs.set(path, diffFile);
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        cacheProjectViewState();
    } catch (error) {
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId || currentDiffState.status !== 'ready') {
            return;
        }
        currentDiffState.loadingFilePaths.delete(path);
        currentDiffState.fileErrors.set(path, String(error?.message || error || t('workspace_view.load_failed')));
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        cacheProjectViewState();
        sysLog(`Failed to load project diff file ${path}: ${error?.message || error}`, 'log-error');
    }
}

async function revealTreePath(path) {
    if (!currentSnapshot || !currentWorkspace) {
        return;
    }
    const parentPaths = buildParentPaths(path);
    for (const parentPath of parentPaths) {
        expandedTreePaths.add(parentPath);
        const node = findTreeNode(currentSnapshot.tree, parentPath);
        if (node?.kind === 'directory' && node.hasChildren && node.childrenLoaded !== true) {
            loadingTreePaths.add(parentPath);
            renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
            await loadWorkspaceTree(parentPath);
        }
    }
    cacheProjectViewState();
}

function buildParentPaths(path) {
    const normalizedPath = String(path || '').trim();
    if (!normalizedPath || normalizedPath === '.') {
        return [];
    }
    const segments = normalizedPath.split('/');
    const parentPaths = [];
    let currentPath = '';
    for (const segment of segments.slice(0, -1)) {
        currentPath = currentPath ? `${currentPath}/${segment}` : segment;
        parentPaths.push(currentPath);
    }
    return parentPaths;
}

function findTreeNode(node, targetPath) {
    if (!node || typeof node !== 'object') {
        return null;
    }
    if (String(node.path || '.').trim() === targetPath) {
        return node;
    }
    const children = Array.isArray(node.children) ? node.children : [];
    for (const child of children) {
        const match = findTreeNode(child, targetPath);
        if (match) {
            return match;
        }
    }
    return null;
}

function mergeTreeState(nextNode, cachedNode) {
    if (!nextNode || !cachedNode || nextNode.kind !== 'directory' || cachedNode.kind !== 'directory') {
        return;
    }

    if (nextNode.childrenLoaded !== true && cachedNode.childrenLoaded === true) {
        nextNode.children = Array.isArray(cachedNode.children)
            ? cachedNode.children.map(child => cloneTreeNode(child)).filter(Boolean)
            : [];
        nextNode.childrenLoaded = true;
        nextNode.hasChildren = nextNode.hasChildren || nextNode.children.length > 0;
        return;
    }

    if (!Array.isArray(nextNode.children) || !Array.isArray(cachedNode.children)) {
        return;
    }

    const cachedChildrenByPath = new Map(
        cachedNode.children
            .filter(Boolean)
            .map(child => [String(child.path || '').trim(), child]),
    );

    for (const child of nextNode.children) {
        const childPath = String(child?.path || '').trim();
        const cachedChild = cachedChildrenByPath.get(childPath);
        if (cachedChild) {
            mergeTreeState(child, cachedChild);
        }
    }
}

function filterLoadedDiffs(loadedDiffs, diffFiles) {
    const nextLoadedDiffs = new Map();
    const safeLoadedDiffs = loadedDiffs instanceof Map ? loadedDiffs : new Map();
    for (const file of Array.isArray(diffFiles) ? diffFiles : []) {
        const filePath = String(file?.path || '').trim();
        if (!filePath || !safeLoadedDiffs.has(filePath)) {
            continue;
        }
        nextLoadedDiffs.set(filePath, cloneDiffFile(safeLoadedDiffs.get(filePath)));
    }
    return nextLoadedDiffs;
}

function filterFileErrors(fileErrors, diffFiles) {
    const nextFileErrors = new Map();
    const safeFileErrors = fileErrors instanceof Map ? fileErrors : new Map();
    for (const file of Array.isArray(diffFiles) ? diffFiles : []) {
        const filePath = String(file?.path || '').trim();
        if (!filePath || !safeFileErrors.has(filePath)) {
            continue;
        }
        nextFileErrors.set(filePath, String(safeFileErrors.get(filePath) || ''));
    }
    return nextFileErrors;
}

function cloneSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') {
        return null;
    }
    return {
        workspace_id: String(snapshot.workspace_id || ''),
        root_path: String(snapshot.root_path || ''),
        tree: cloneTreeNode(snapshot.tree),
    };
}

function cloneTreeNode(node) {
    if (!node || typeof node !== 'object') {
        return null;
    }
    return {
        name: String(node.name || node.path || '.'),
        path: String(node.path || '.').trim() || '.',
        kind: node.kind === 'directory' ? 'directory' : 'file',
        hasChildren: node.hasChildren === true,
        children: Array.isArray(node.children)
            ? node.children.map(child => cloneTreeNode(child)).filter(Boolean)
            : [],
        childrenLoaded: node.childrenLoaded === true,
    };
}

function cloneDiffState(diffState) {
    if (!diffState || typeof diffState !== 'object') {
        return createInitialDiffState();
    }
    return {
        status: String(diffState.status || 'idle'),
        diffFiles: Array.isArray(diffState.diffFiles)
            ? diffState.diffFiles.map(file => ({ ...file }))
            : [],
        diffMessage: diffState.diffMessage ? String(diffState.diffMessage) : null,
        isGitRepository: diffState.isGitRepository === true,
        gitRootPath: diffState.gitRootPath ? String(diffState.gitRootPath) : null,
        loadedDiffs: new Map(
            Array.from(diffState.loadedDiffs instanceof Map ? diffState.loadedDiffs.entries() : [])
                .map(([path, file]) => [String(path || '').trim(), cloneDiffFile(file)]),
        ),
        loadingFilePaths: new Set(),
        fileErrors: new Map(
            Array.from(diffState.fileErrors instanceof Map ? diffState.fileErrors.entries() : [])
                .map(([path, message]) => [String(path || '').trim(), String(message || '')]),
        ),
    };
}

function cloneDiffFile(diffFile) {
    if (!diffFile || typeof diffFile !== 'object') {
        return null;
    }
    return {
        ...diffFile,
        workspace_id: String(diffFile.workspace_id || ''),
        path: String(diffFile.path || ''),
        previous_path: diffFile.previous_path ? String(diffFile.previous_path) : null,
        change_type: String(diffFile.change_type || 'modified'),
        diff: diffFile.diff ? String(diffFile.diff) : '',
        is_binary: diffFile.is_binary === true,
    };
}

function renderFolderIcon(isExpanded) {
    const folderClass = isExpanded ? 'workspace-tree-icon is-folder-open' : 'workspace-tree-icon is-folder';
    return `
        <span class="${folderClass}" aria-hidden="true">
            <svg viewBox="0 0 16 16" focusable="false">
                <path d="M1.5 4.5a1 1 0 0 1 1-1h3.2l1.2 1.5H13.5a1 1 0 0 1 1 1v5.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z" />
            </svg>
        </span>
    `;
}

function renderFileIcon() {
    return `
        <span class="workspace-tree-icon is-file" aria-hidden="true">
            <svg viewBox="0 0 16 16" focusable="false">
                <path d="M4 1.5h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1z" />
                <path d="M9 1.5v3h3" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
        </span>
    `;
}

function formatWorkspaceTitle(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    if (workspaceId) {
        return formatTemplate(t('workspace_view.title'), { workspace: workspaceId });
    }
    return t('workspace_view.title');
}

function formatTemplate(template, values) {
    return Object.entries(values).reduce(
        (result, [key, value]) => result.replace(`{${key}}`, String(value)),
        String(template || ''),
    );
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
