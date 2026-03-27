/**
 * components/projectView.js
 * Renders the main workspace snapshot for a selected project.
 */
import {
    disableAutomationProject,
    enableAutomationProject,
    fetchAutomationFeishuBindings,
    fetchAutomationProject,
    fetchAutomationProjectSessions,
    fetchWorkspaceDiffFile,
    fetchWorkspaces,
    fetchWorkspaceDiffs,
    fetchWorkspaceSnapshot,
    fetchWorkspaceTree,
    runAutomationProject,
    updateAutomationProject,
} from '../core/api.js';
import { clearAllPanels } from './agentPanel.js';
import { hideRoundNavigator } from './rounds/navigator.js';
import { setSubagentRailExpanded } from './subagentRail.js';
import { state } from '../core/state.js';
import { els } from '../utils/dom.js';
import { t } from '../utils/i18n.js';
import { showFormDialog } from '../utils/feedback.js';
import { sysLog } from '../utils/logger.js';

let currentWorkspace = null;
let currentAutomationProject = null;
let currentProjectViewMode = 'workspace';
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


function findWorkspaceById(workspaces, workspaceId) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    return (Array.isArray(workspaces) ? workspaces : []).find(workspace => String(workspace?.workspace_id || '').trim() === safeWorkspaceId) || null;
}

function formatWorkspaceOptionLabel(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    const rootPath = String(workspace?.root_path || '').trim();
    if (workspaceId && rootPath) {
        return `${workspaceId} - ${rootPath}`;
    }
    return workspaceId || rootPath;
}

function formatWorkspaceOptionDescription(workspace) {
    const rootPath = String(workspace?.root_path || '').trim();
    return rootPath || t('automation.workspace.help');
}

const AUTOMATION_TIMEZONE_OPTIONS = [
    { value: 'UTC', label: 'UTC' },
    { value: 'Asia/Shanghai', label: 'Asia/Shanghai' },
    { value: 'America/Los_Angeles', label: 'America/Los_Angeles' },
    { value: 'America/New_York', label: 'America/New_York' },
    { value: 'Europe/London', label: 'Europe/London' },
];

function buildFeishuBindingKey(binding) {
    const triggerId = String(binding?.trigger_id || '').trim();
    const tenantKey = String(binding?.tenant_key || '').trim();
    const chatId = String(binding?.chat_id || '').trim();
    const sessionId = String(binding?.session_id || '').trim();
    if (!triggerId || !tenantKey || !chatId || !sessionId) {
        return '';
    }
    return `${triggerId}::${tenantKey}::${chatId}::${sessionId}`;
}

function buildFeishuBindingOptions(bindings) {
    const safeBindings = Array.isArray(bindings) ? bindings : [];
    const options = [
        {
            value: '',
            label: 'No Feishu delivery',
            description: 'Do not send automation updates to Feishu.',
        },
    ];
    safeBindings.forEach(binding => {
        const bindingKey = buildFeishuBindingKey(binding);
        if (!bindingKey) {
            return;
        }
        const triggerName = String(binding?.trigger_name || '').trim();
        const sourceLabel = String(binding?.source_label || '').trim();
        const chatType = String(binding?.chat_type || '').trim();
        const sessionTitle = String(binding?.session_title || '').trim();
        options.push({
            value: bindingKey,
            label: sessionTitle || sourceLabel || bindingKey,
            description: [triggerName, chatType].filter(Boolean).join(' - '),
        });
    });
    return options;
}

function resolveFeishuBindingDisplayName(binding, bindings) {
    const bindingKey = buildFeishuBindingKey(binding);
    const candidate = (Array.isArray(bindings) ? bindings : []).find(
        item => buildFeishuBindingKey(item) === bindingKey,
    );
    const sessionTitle = String(candidate?.session_title || '').trim();
    if (sessionTitle) {
        return sessionTitle;
    }
    const sourceLabel = String(binding?.source_label || '').trim();
    if (sourceLabel) {
        return sourceLabel;
    }
    return String(binding?.chat_id || '').trim();
}

function formatAutomationRunLogMessage(result) {
    const sessionId = String(result?.session_id || '').trim();
    const suffix = sessionId ? `: ${sessionId}` : '';
    if (result?.queued === true) {
        return `Queued automation run in bound IM session${suffix}`;
    }
    return `Started automation run in bound IM session${suffix}`;
}

async function requestAutomationProjectEditInput(project) {
    const [workspaces, feishuBindings] = await Promise.all([
        fetchWorkspaces(),
        fetchAutomationFeishuBindings(),
    ]);
    const workspaceOptions = (Array.isArray(workspaces) ? workspaces : []).map(workspace => ({
        value: String(workspace?.workspace_id || '').trim(),
        label: formatWorkspaceOptionLabel(workspace),
        description: formatWorkspaceOptionDescription(workspace),
    })).filter(option => option.value);
    const bindingOptions = buildFeishuBindingOptions(feishuBindings);
    if (workspaceOptions.length === 0) {
        return null;
    }
    const currentBindingKey = buildFeishuBindingKey(project?.delivery_binding);
    const deliveryEvents = Array.isArray(project?.delivery_events) ? project.delivery_events : [];
    const values = await showFormDialog({
        title: t('automation.edit.title'),
        message: t('automation.edit.message'),
        tone: 'info',
        confirmLabel: t('automation.edit.save'),
        cancelLabel: t('settings.action.cancel'),
        fields: [
            {
                id: 'display_name',
                label: t('automation.field.project_name'),
                placeholder: 'Daily Briefing',
                value: String(project?.display_name || project?.name || '').trim(),
            },
            {
                id: 'workspace_id',
                label: 'Workspace Directory',
                type: 'select',
                value: String(project?.workspace_id || '').trim(),
                options: workspaceOptions,
            },
            {
                id: 'prompt',
                label: t('automation.detail.prompt'),
                placeholder: 'Summarize the latest project changes.',
                value: String(project?.prompt || '').trim(),
                multiline: true,
            },
            {
                id: 'cron_expression',
                label: t('automation.detail.schedule'),
                placeholder: '0 9 * * *',
                value: String(project?.cron_expression || '').trim(),
            },
            {
                id: 'timezone',
                label: t('automation.detail.timezone'),
                type: 'select',
                value: String(project?.timezone || 'UTC').trim() || 'UTC',
                options: AUTOMATION_TIMEZONE_OPTIONS,
            },
            {
                id: 'enabled',
                label: t('automation.field.enabled'),
                type: 'checkbox',
                value: String(project?.status || '').trim().toLowerCase() === 'enabled',
                description: t('automation.field.enabled_help'),
            },
            {
                id: 'delivery_binding_key',
                label: 'Feishu Chat',
                type: 'select',
                value: currentBindingKey,
                options: bindingOptions,
            },
            {
                id: 'delivery_event_started',
                label: 'Notify on start',
                type: 'checkbox',
                value: deliveryEvents.includes('started'),
                description: 'Send a start message to the selected Feishu chat.',
            },
            {
                id: 'delivery_event_completed',
                label: 'Notify on completion',
                type: 'checkbox',
                value: deliveryEvents.includes('completed'),
                description: 'Send the final success result to Feishu.',
            },
            {
                id: 'delivery_event_failed',
                label: 'Notify on failure',
                type: 'checkbox',
                value: deliveryEvents.includes('failed'),
                description: 'Send the failure reason to Feishu.',
            },
        ],
    });
    if (!values || typeof values !== 'object') {
        return null;
    }
    const displayName = String(values.display_name || '').trim();
    const workspaceId = String(values.workspace_id || '').trim();
    const prompt = String(values.prompt || '').trim();
    const cronExpression = String(values.cron_expression || '').trim();
    const timezone = String(values.timezone || 'UTC').trim() || 'UTC';
    const enabled = values.enabled !== false;
    const selectedBindingKey = String(values.delivery_binding_key || '').trim();
    const selectedBinding = (Array.isArray(feishuBindings) ? feishuBindings : []).find(binding => buildFeishuBindingKey(binding) === selectedBindingKey) || null;
    const nextDeliveryEvents = [
        values.delivery_event_started === true ? 'started' : null,
        values.delivery_event_completed === true ? 'completed' : null,
        values.delivery_event_failed === true ? 'failed' : null,
    ].filter(Boolean);
    if (!workspaceId || !displayName || !prompt || !cronExpression) {
        return null;
    }
    const slug = displayName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || String(project?.name || 'automation-project');
    return {
        name: slug,
        display_name: displayName,
        workspace_id: workspaceId,
        prompt,
        schedule_mode: 'cron',
        cron_expression: cronExpression,
        timezone,
        enabled,
        delivery_binding: selectedBinding ? {
            provider: 'feishu',
            trigger_id: String(selectedBinding.trigger_id || '').trim(),
            tenant_key: String(selectedBinding.tenant_key || '').trim(),
            chat_id: String(selectedBinding.chat_id || '').trim(),
            session_id: String(selectedBinding.session_id || '').trim(),
            chat_type: String(selectedBinding.chat_type || '').trim(),
            source_label: String(selectedBinding.source_label || '').trim(),
        } : null,
        delivery_events: selectedBinding ? nextDeliveryEvents : [],
    };
}

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
            if (state.currentMainView !== 'project') {
                return;
            }
            if (currentProjectViewMode === 'automation') {
                if (currentAutomationProject) {
                    void openAutomationProjectView(currentAutomationProject);
                }
                return;
            }
            if (currentSnapshot) {
                renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
            } else {
                renderLoadingState(currentWorkspace);
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
    currentProjectViewMode = 'workspace';
    currentAutomationProject = null;
    currentWorkspace = workspace;
    currentSnapshotWorkspaceId = workspaceId;
    state.currentMainView = 'project';
    state.currentProjectViewWorkspaceId = workspaceId;
    state.currentWorkspaceId = workspaceId;
    state.currentSessionId = null;
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

export async function openAutomationProjectView(project) {
    const automationProjectId = String(project?.automation_project_id || '').trim();
    if (!automationProjectId) {
        return;
    }

    currentProjectViewMode = 'automation';
    currentWorkspace = null;
    currentAutomationProject = project;
    currentSnapshot = null;
    currentSnapshotWorkspaceId = null;
    selectedTreePath = null;
    currentDiffState = createInitialDiffState();
    state.currentMainView = 'project';
    state.currentProjectViewWorkspaceId = `automation:${automationProjectId}`;
    state.currentWorkspaceId = String(project?.workspace_id || '').trim() || 'automation-system';
    state.currentSessionId = null;
    clearAllPanels();
    hideRoundNavigator();
    setSubagentRailExpanded(false);
    setProjectViewVisible(true);
    renderAutomationLoadingState(project);

    try {
        const [freshProject, sessions, workspaces, feishuBindings] = await Promise.all([
            fetchAutomationProject(automationProjectId),
            fetchAutomationProjectSessions(automationProjectId),
            fetchWorkspaces(),
            fetchAutomationFeishuBindings(),
        ]);
        currentAutomationProject = freshProject;
        renderAutomationProjectView(
            freshProject,
            Array.isArray(sessions) ? sessions : [],
            findWorkspaceById(workspaces, freshProject.workspace_id),
            Array.isArray(feishuBindings) ? feishuBindings : [],
        );
    } catch (error) {
        renderAutomationErrorState(project, error);
        sysLog(`Failed to load automation project: ${error?.message || error}`, 'log-error');
    }
}

export async function refreshProjectView() {
    if (currentProjectViewMode === 'automation') {
        if (!currentAutomationProject) {
            return;
        }
        await openAutomationProjectView(currentAutomationProject);
        return;
    }
    if (!currentWorkspace) {
        return;
    }
    await openWorkspaceProjectView(currentWorkspace);
}

export function hideProjectView() {
    cacheProjectViewState();
    currentWorkspace = null;
    currentAutomationProject = null;
    currentProjectViewMode = 'workspace';
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

function renderAutomationLoadingState(project) {
    renderToolbar(project, {
        summary: 'Loading automation project...',
        mode: 'automation',
        actions: '',
    });
    if (els.projectViewContent) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-grid">
                <section class="workspace-view-panel">
                    <div class="workspace-view-panel-header">
                        <h3>Schedule</h3>
                        <span class="workspace-view-panel-meta">Automation</span>
                    </div>
                    ${renderInlineState('Loading automation details...')}
                </section>
                <section class="workspace-view-panel workspace-diff-panel">
                    <div class="workspace-view-panel-header">
                        <h3>Recent Runs</h3>
                        <span class="workspace-view-panel-meta"></span>
                    </div>
                    ${renderInlineState('Loading automation sessions...')}
                </section>
            </div>
        `;
    }
}

function renderAutomationErrorState(project, error) {
    renderToolbar(project, {
        summary: 'Failed to load automation project',
        mode: 'automation',
        actions: '',
    });
    if (els.projectViewContent) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-empty-state is-error">
                <p>Failed to load automation project.</p>
                <p>${escapeHtml(String(error?.message || error || ''))}</p>
            </div>
        `;
    }
}

function renderAutomationProjectView(project, sessions, workspaceRecord = null, feishuBindings = []) {
    const safeSessions = Array.isArray(sessions) ? sessions : [];
    const status = String(project?.status || '').trim() || 'unknown';
    const scheduleMode = String(project?.schedule_mode || '').trim() || 'cron';
    const scheduleText = scheduleMode === 'one_shot'
        ? (String(project?.run_at || '').trim() || t('automation.detail.not_scheduled'))
        : (String(project?.cron_expression || '').trim() || t('automation.detail.not_scheduled'));
    const cronDescription = scheduleMode === 'one_shot'
        ? t('automation.cron.one_shot')
        : describeCronExpression(project?.cron_expression);
    const timezone = String(project?.timezone || 'UTC').trim() || 'UTC';
    const workspaceId = String(project?.workspace_id || '').trim() || 'automation-system';
    const workspaceRootPath = String(workspaceRecord?.root_path || '').trim() || t('automation.workspace.missing');
    const nextRunAt = String(project?.next_run_at || '').trim() || t('automation.detail.not_scheduled');
    const lastRunAt = String(project?.last_run_started_at || '').trim() || t('automation.detail.never');
    const lastError = String(project?.last_error || '').trim() || t('automation.detail.none');
    const deliveryBinding = project?.delivery_binding && typeof project.delivery_binding === 'object'
        ? project.delivery_binding
        : null;
    const deliveryBindingName = deliveryBinding
        ? resolveFeishuBindingDisplayName(deliveryBinding, feishuBindings)
        : '';
    const deliveryEvents = Array.isArray(project?.delivery_events) ? project.delivery_events : [];
    const deliveryEventsLabel = deliveryEvents.length > 0 ? deliveryEvents.join(', ') : 'none';
    const runButtonLabel = t('automation.action.run_now');
    const toggleButtonLabel = status === 'enabled' ? t('automation.action.disable') : t('automation.action.enable');
    const statusLabel = t(`automation.status.${status}`);

    renderToolbar(project, {
        summary: `${statusLabel} - ${safeSessions.length} ${t('automation.detail.session_count')}`,
        mode: 'automation',
        actions: `
            <button class="secondary-btn project-view-toolbar-btn" type="button" data-automation-edit>${escapeHtml(t('automation.action.edit'))}</button>
            <button class="secondary-btn project-view-toolbar-btn" type="button" data-automation-run>${escapeHtml(runButtonLabel)}</button>
            <button class="secondary-btn project-view-toolbar-btn" type="button" data-automation-toggle>${escapeHtml(toggleButtonLabel)}</button>
        `,
    });
    if (!els.projectViewContent) {
        return;
    }

    els.projectViewContent.innerHTML = `
        <div class="automation-detail-layout">
            <section class="workspace-view-panel automation-hero-panel">
                <div class="automation-hero-grid">
                    <div class="automation-hero-copy">
                        <span class="automation-status-pill is-${escapeHtml(status.toLowerCase())}">${escapeHtml(statusLabel)}</span>
                        <h3>${escapeHtml(t('automation.detail.overview'))}</h3>
                        <p>${escapeHtml(t('automation.detail.overview_copy'))}</p>
                    </div>
                    <div class="automation-stat-grid">
                        <article class="automation-stat-card automation-stat-card-wide">
                            <span>${escapeHtml(t('automation.detail.schedule'))}</span>
                            <strong>${escapeHtml(scheduleText)}</strong>
                            <p class="automation-stat-note">${escapeHtml(cronDescription)}</p>
                            <p class="automation-stat-hint">${escapeHtml(t('automation.cron.hint'))}</p>
                        </article>
                        <article class="automation-stat-card">
                            <span>${escapeHtml(t('automation.field.workspace'))}</span>
                            <strong>${escapeHtml(workspaceId)}</strong>
                        </article>
                        <article class="automation-stat-card">
                            <span>${escapeHtml(t('automation.detail.timezone'))}</span>
                            <strong>${escapeHtml(timezone)}</strong>
                        </article>
                        <article class="automation-stat-card">
                            <span>${escapeHtml(t('automation.detail.next_run'))}</span>
                            <strong>${escapeHtml(nextRunAt)}</strong>
                        </article>
                        <article class="automation-stat-card">
                            <span>${escapeHtml(t('automation.detail.last_run'))}</span>
                            <strong>${escapeHtml(lastRunAt)}</strong>
                        </article>
                    </div>
                </div>
            </section>
            <div class="automation-detail-grid">
                <section class="workspace-view-panel automation-detail-panel">
                    <div class="workspace-view-panel-header">
                        <h3>${escapeHtml(t('automation.detail.configuration'))}</h3>
                        <span class="workspace-view-panel-meta">${escapeHtml(scheduleMode)}</span>
                    </div>
                    <div class="automation-detail-section">
                        <div class="automation-detail-row automation-detail-row-block">
                            <span class="automation-detail-label">${escapeHtml(t('automation.detail.prompt'))}</span>
                            <div class="automation-prompt-card">${escapeHtml(String(project?.prompt || ''))}</div>
                        </div>
                        <div class="automation-detail-row">
                            <span class="automation-detail-label">${escapeHtml(t('automation.detail.last_error'))}</span>
                            <span class="automation-detail-value${lastError === t('automation.detail.none') ? '' : ' is-error'}">${escapeHtml(lastError)}</span>
                        </div>
                    </div>
                </section>
                <section class="workspace-view-panel automation-binding-panel">
                    <div class="workspace-view-panel-header">
                        <h3>Bindings</h3>
                        <span class="workspace-view-panel-meta">${escapeHtml(deliveryBinding ? 'Feishu' : 'Disabled')}</span>
                    </div>
                    <div class="automation-binding-list">
                        <div class="automation-binding-item">
                            <span>${escapeHtml(t('automation.field.workspace'))}</span>
                            <strong>${escapeHtml(workspaceId)}</strong>
                        </div>
                        <div class="automation-binding-item">
                            <span>${escapeHtml(t('automation.workspace.directory'))}</span>
                            <code>${escapeHtml(workspaceRootPath)}</code>
                        </div>
                        <div class="automation-binding-item">
                            <span>Delivery events</span>
                            <strong>${escapeHtml(deliveryEventsLabel)}</strong>
                        </div>
                        ${deliveryBinding ? `
                            <div class="automation-binding-item">
                                <span>Feishu trigger</span>
                                <strong>${escapeHtml(String(deliveryBinding.trigger_id || ''))}</strong>
                            </div>
                            <div class="automation-binding-item">
                                <span>Feishu chat</span>
                                <strong>${escapeHtml(deliveryBindingName)}</strong>
                            </div>
                            <div class="automation-binding-item">
                                <span>Chat type</span>
                                <strong>${escapeHtml(String(deliveryBinding.chat_type || ''))}</strong>
                            </div>
                        ` : ''}
                        <p class="automation-binding-help">${escapeHtml(deliveryBinding ? 'Automation updates will be pushed to the selected Feishu chat.' : t('automation.workspace.help'))}</p>
                    </div>
                </section>
            </div>
            <section class="workspace-view-panel automation-runs-panel">
                <div class="workspace-view-panel-header">
                    <h3>${escapeHtml(t('automation.detail.recent_runs'))}</h3>
                    <span class="workspace-view-panel-meta">${escapeHtml(String(safeSessions.length))} ${escapeHtml(t('automation.detail.session_count'))}</span>
                </div>
                ${safeSessions.length > 0 ? `
                    <div class="automation-run-list">
                        ${safeSessions.map(session => {
                            const sessionStatus = String(session.active_run_status || 'completed').trim() || 'completed';
                            const sessionStatusLabel = t(`automation.run_status.${sessionStatus}`);
                            const sessionTitle = String(session?.metadata?.title || session.session_id || '').trim() || String(session.session_id || '');
                            return `
                                <article class="automation-run-card" data-automation-session-id="${escapeHtml(String(session.session_id || ''))}">
                                    <div class="automation-run-card-header">
                                        <span class="workspace-diff-status is-modified">${escapeHtml(sessionStatusLabel)}</span>
                                        <code class="workspace-diff-path">${escapeHtml(sessionTitle)}</code>
                                    </div>
                                    <div class="automation-run-card-meta">
                                        <span>${escapeHtml(t('automation.detail.updated_at'))}</span>
                                        <strong>${escapeHtml(String(session.updated_at || ''))}</strong>
                                    </div>
                                </article>
                            `;
                        }).join('')}
                    </div>
                ` : renderInlineState(t('automation.detail.no_runs'))}
            </section>
        </div>
    `;

    const editAction = async () => {
        const nextPayload = await requestAutomationProjectEditInput(project);
        if (!nextPayload) {
            return;
        }
        await updateAutomationProject(String(project?.automation_project_id || ''), nextPayload);
        document.dispatchEvent(new CustomEvent('agent-teams-projects-changed'));
        await openAutomationProjectView(project);
    };
    document.querySelector('[data-automation-edit]')?.addEventListener('click', editAction);
    const runAction = async () => {
        const result = await runAutomationProject(String(project?.automation_project_id || ''));
        if (result?.reused_bound_session === true) {
            sysLog(formatAutomationRunLogMessage(result));
            await openAutomationProjectView(project);
            return;
        }
        if (result?.session_id) {
            document.dispatchEvent(new CustomEvent('agent-teams-select-session', { detail: { sessionId: result.session_id } }));
        }
    };
    document.querySelector('[data-automation-run]')?.addEventListener('click', runAction);
    const toggleAction = async () => {
        const projectId = String(project?.automation_project_id || '');
        if (status === 'enabled') {
            await disableAutomationProject(projectId);
        } else {
            await enableAutomationProject(projectId);
        }
        await openAutomationProjectView(project);
    };
    document.querySelector('[data-automation-toggle]')?.addEventListener('click', toggleAction);
    els.projectViewContent.querySelectorAll('[data-automation-session-id]').forEach(node => {
        node.addEventListener('click', () => {
            const sessionId = String(node.getAttribute('data-automation-session-id') || '').trim();
            if (!sessionId) return;
            document.dispatchEvent(new CustomEvent('agent-teams-select-session', { detail: { sessionId } }));
        });
    });
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

function renderToolbar(projectOrWorkspace, { summary = '', mode = 'workspace', actions = '' } = {}) {
    if (els.projectViewTitle) {
        els.projectViewTitle.textContent = mode === 'automation'
            ? formatAutomationTitle(projectOrWorkspace)
            : formatWorkspaceTitle(projectOrWorkspace);
    }
    if (els.projectViewSummary) {
        els.projectViewSummary.textContent = summary;
    }
    if (els.projectViewToolbarActions) {
        els.projectViewToolbarActions.innerHTML = `
            ${actions || ''}
            <button id="project-view-reload" class="secondary-btn" type="button" data-project-view-reload>${escapeHtml(t('workspace_view.reload'))}</button>
            <button id="project-view-close" class="icon-btn" type="button" title="${escapeHtml(t('workspace_view.back'))}" aria-label="${escapeHtml(t('workspace_view.back'))}" data-project-view-close>
                <svg viewBox="0 0 24 24" fill="none" class="icon" aria-hidden="true">
                    <path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                </svg>
            </button>
        `;
        els.projectViewReloadBtn = els.projectViewToolbarActions.querySelector('[data-project-view-reload]');
        els.projectViewCloseBtn = els.projectViewToolbarActions.querySelector('[data-project-view-close]');
        if (els.projectViewReloadBtn) {
            els.projectViewReloadBtn.onclick = () => {
                void refreshProjectView();
            };
        }
        if (els.projectViewCloseBtn) {
            els.projectViewCloseBtn.onclick = () => {
                hideProjectView();
            };
        }
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


function describeCronExpression(expression) {
    const cron = String(expression || '').trim();
    if (!cron) {
        return t('automation.cron.empty');
    }
    const parts = cron.split(/\s+/);
    if (parts.length !== 5) {
        return formatTemplate(t('automation.cron.fallback'), { expression: cron });
    }
    const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
    if (month === '*' && dayOfMonth === '*' && dayOfWeek === '*') {
        return formatTemplate(t('automation.cron.daily'), {
            time: formatCronTime(hour, minute),
        });
    }
    if (month === '*' && dayOfMonth === '*' && dayOfWeek !== '*') {
        return formatTemplate(t('automation.cron.weekly'), {
            weekday: formatCronWeekday(dayOfWeek),
            time: formatCronTime(hour, minute),
        });
    }
    if (month === '*' && dayOfMonth !== '*' && dayOfWeek === '*') {
        return formatTemplate(t('automation.cron.monthly'), {
            day: dayOfMonth,
            time: formatCronTime(hour, minute),
        });
    }
    return formatTemplate(t('automation.cron.fallback'), { expression: cron });
}

function formatCronTime(hour, minute) {
    const safeHour = /^\d+$/.test(String(hour || '')) ? String(hour).padStart(2, '0') : String(hour || '*');
    const safeMinute = /^\d+$/.test(String(minute || '')) ? String(minute).padStart(2, '0') : String(minute || '*');
    return `${safeHour}:${safeMinute}`;
}

function formatCronWeekday(value) {
    const map = {
        '0': t('automation.cron.weekday.sun'),
        '1': t('automation.cron.weekday.mon'),
        '2': t('automation.cron.weekday.tue'),
        '3': t('automation.cron.weekday.wed'),
        '4': t('automation.cron.weekday.thu'),
        '5': t('automation.cron.weekday.fri'),
        '6': t('automation.cron.weekday.sat'),
        '7': t('automation.cron.weekday.sun'),
    };
    return map[String(value || '').trim()] || String(value || '*');
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

function formatAutomationTitle(project) {
    const label = String(project?.display_name || project?.name || project?.automation_project_id || '').trim();
    return label ? `${label} Automation` : 'Automation Project';
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
