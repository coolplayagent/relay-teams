/**
 * components/contextIndicators.js
 * Provider-reported token usage badges for coordinator and subagent composers.
 */
import { fetchModelProfiles, fetchRunTokenUsage } from '../core/api.js';
import { state, getPrimaryRoleId, getRoleOption } from '../core/state.js';
import { els } from '../utils/dom.js';
import { formatMessage, t } from '../utils/i18n.js';
import { currentRounds } from './rounds/timeline.js';
import {
    getActiveInstanceId,
    getPanel,
    getPanels,
} from './agentPanel/state.js';

const PREVIEW_DEBOUNCE_MS = 180;
const EMPTY_LABEL = '-- / --';
const mainPreviewState = createPreviewState();
const panelPreviewStates = new Map();

export function initializeContextIndicators() {
    renderIdle(getMainIndicator());
}

export function bindPanelContextIndicator(panelEl, instanceId) {
    const indicator = panelEl?.querySelector('.panel-context-indicator');
    void instanceId;
    if (!indicator) return;
    renderIdle(indicator);
}

export function scheduleCoordinatorContextPreview({ immediate = false } = {}) {
    schedulePreview(mainPreviewState, () => {
        void refreshCoordinatorContextPreview();
    }, immediate);
}

export function schedulePanelContextPreview(instanceId, { immediate = false } = {}) {
    if (!instanceId) return;
    schedulePreview(getPanelPreviewState(instanceId), () => {
        void refreshPanelContextPreview(instanceId);
    }, immediate);
}

export function refreshVisibleContextIndicators({ immediate = false } = {}) {
    scheduleCoordinatorContextPreview({ immediate });
    const activeInstanceId = getActiveInstanceId();
    if (activeInstanceId) {
        schedulePanelContextPreview(activeInstanceId, { immediate });
    }
}

export function clearContextIndicators() {
    resetPreviewState(mainPreviewState);
    renderIdle(getMainIndicator());
    panelPreviewStates.forEach(previewState => {
        resetPreviewState(previewState);
    });
    getPanels().forEach(panel => {
        const indicator = panel?.panelEl?.querySelector('.panel-context-indicator');
        renderIdle(indicator);
    });
}

async function refreshCoordinatorContextPreview() {
    const indicator = getMainIndicator();
    if (!indicator) return;
    if (!state.currentSessionId || state.isGenerating || els.promptInput?.disabled) {
        renderIdle(indicator, { hidden: !!state.isGenerating });
        return;
    }
    const runId = resolveUsageRunId();
    const roleId = getPrimaryRoleId();
    if (!runId || !roleId) {
        renderIdle(indicator);
        return;
    }

    await fetchAndRenderUsage({
        previewState: mainPreviewState,
        indicator,
        sessionId: state.currentSessionId,
        runId,
        roleId,
    });
}

async function refreshPanelContextPreview(instanceId) {
    const panel = getPanel(instanceId);
    const indicator = panel?.panelEl?.querySelector('.panel-context-indicator');
    const textarea = panel?.panelEl?.querySelector('.panel-inject-input');
    if (!panel || !indicator || !textarea) return;
    if (
        !state.currentSessionId
        || panel.panelEl.style.display === 'none'
        || state.isGenerating
    ) {
        renderIdle(indicator, { hidden: !!state.isGenerating });
        return;
    }
    const runId = resolveUsageRunId();
    const roleId = String(panel.roleId || state.instanceRoleMap[instanceId] || '').trim();
    if (!runId || !roleId) {
        renderIdle(indicator);
        return;
    }

    await fetchAndRenderUsage({
        previewState: getPanelPreviewState(instanceId),
        indicator,
        sessionId: state.currentSessionId,
        runId,
        roleId,
        instanceId,
    });
}

async function fetchAndRenderUsage({
    previewState,
    indicator,
    sessionId,
    runId,
    roleId,
    instanceId = '',
}) {
    const nextRequestId = previewState.requestId + 1;
    previewState.requestId = nextRequestId;
    if (previewState.controller) {
        previewState.controller.abort();
    }

    const controller = new AbortController();
    previewState.controller = controller;
    renderLoading(indicator);

    try {
        const [usage, profiles] = await Promise.all([
            fetchRunTokenUsage(sessionId, runId, {
                signal: controller.signal,
            }),
            fetchModelProfiles({
                signal: controller.signal,
            }),
        ]);
        if (previewState.requestId !== nextRequestId) return;
        const agentUsage = selectAgentUsage(usage, { roleId, instanceId });
        if (!agentUsage) {
            renderIdle(indicator);
            return;
        }
        renderUsage(
            indicator,
            agentUsage,
            resolveContextWindow(profiles, agentUsage, agentUsage.role_id || roleId),
        );
    } catch (error) {
        if (error?.name === 'AbortError') return;
        if (previewState.requestId !== nextRequestId) return;
        renderError(indicator);
    } finally {
        if (previewState.requestId === nextRequestId) {
            previewState.controller = null;
        }
    }
}

function schedulePreview(previewState, runRefresh, immediate) {
    if (previewState.timerId) {
        clearTimeout(previewState.timerId);
        previewState.timerId = 0;
    }
    if (immediate) {
        runRefresh();
        return;
    }
    previewState.timerId = setTimeout(() => {
        previewState.timerId = 0;
        runRefresh();
    }, PREVIEW_DEBOUNCE_MS);
}

function resetPreviewState(previewState) {
    if (previewState.timerId) {
        clearTimeout(previewState.timerId);
    }
    if (previewState.controller) {
        previewState.controller.abort();
    }
    previewState.timerId = 0;
    previewState.controller = null;
}

function getMainIndicator() {
    return document.getElementById('main-context-indicator');
}

function getPanelPreviewState(instanceId) {
    if (!panelPreviewStates.has(instanceId)) {
        panelPreviewStates.set(instanceId, createPreviewState());
    }
    return panelPreviewStates.get(instanceId);
}

function createPreviewState() {
    return {
        timerId: 0,
        requestId: 0,
        controller: null,
    };
}

function renderUsage(indicator, usage, contextWindow) {
    if (!indicator) return;
    indicator.style.display = 'inline-flex';
    indicator.dataset.state = 'ready';
    const inputTokens = resolveLatestInputTokens(usage);
    const upper = typeof contextWindow === 'number' && contextWindow > 0
        ? formatTokenCount(contextWindow)
        : '--';
    indicator.textContent = `${formatTokenCount(inputTokens)} / ${upper}`;
    indicator.title = typeof contextWindow === 'number' && contextWindow > 0
        ? formatMessage('context_indicator.latest_with_window', {
            input_tokens: inputTokens,
            context_window: contextWindow,
        })
        : formatMessage('context_indicator.latest_without_window', {
            input_tokens: inputTokens,
        });
}

function renderIdle(indicator, { hidden = false } = {}) {
    if (!indicator) return;
    indicator.dataset.state = 'idle';
    indicator.textContent = EMPTY_LABEL;
    indicator.title = t('context_indicator.latest_title');
    indicator.style.display = hidden ? 'none' : 'inline-flex';
}

function renderLoading(indicator) {
    if (!indicator) return;
    indicator.style.display = 'inline-flex';
    indicator.dataset.state = 'loading';
    indicator.title = t('context_indicator.loading_title');
    if (!indicator.textContent || indicator.textContent === EMPTY_LABEL) {
        indicator.textContent = EMPTY_LABEL;
    }
}

function renderError(indicator) {
    if (!indicator) return;
    indicator.style.display = 'inline-flex';
    indicator.dataset.state = 'error';
    indicator.textContent = EMPTY_LABEL;
    indicator.title = t('context_indicator.unavailable_title');
}

function resolveUsageRunId() {
    const activeRunId = String(state.activeRunId || '').trim();
    if (activeRunId) {
        return activeRunId;
    }
    const latestRound = Array.isArray(currentRounds) && currentRounds.length > 0
        ? currentRounds[currentRounds.length - 1]
        : null;
    return String(latestRound?.run_id || '').trim();
}

function selectAgentUsage(usage, { roleId = '', instanceId = '' } = {}) {
    const agents = Array.isArray(usage?.by_agent) ? usage.by_agent : [];
    if (instanceId) {
        const exactMatch = agents.find(agent => agent.instance_id === instanceId);
        if (exactMatch) {
            return exactMatch;
        }
    }
    if (roleId) {
        const roleMatch = agents.find(agent => agent.role_id === roleId);
        if (roleMatch) {
            return roleMatch;
        }
    }
    return agents.length === 1 ? agents[0] : null;
}

function resolveContextWindow(profiles, usage, roleId = '') {
    const usageContextWindow = Number(usage?.context_window);
    if (Number.isFinite(usageContextWindow) && usageContextWindow > 0) {
        return usageContextWindow;
    }
    if (!profiles || typeof profiles !== 'object') {
        return null;
    }
    const modelProfile = String(usage?.model_profile || '').trim();
    if (modelProfile && profiles[modelProfile] && typeof profiles[modelProfile] === 'object') {
        const profileContextWindow = Number(profiles[modelProfile].context_window);
        if (Number.isFinite(profileContextWindow) && profileContextWindow > 0) {
            return profileContextWindow;
        }
    }
    const profileEntries = Object.entries(profiles).filter(([, profile]) => (
        profile && typeof profile === 'object'
    ));
    const roleProfileName = String(getRoleOption(roleId)?.model_profile || '').trim();
    if (roleProfileName) {
        const activeProfile = profileEntries.find(([name]) => name === roleProfileName)?.[1];
        const activeContextWindow = Number(activeProfile?.context_window);
        if (Number.isFinite(activeContextWindow) && activeContextWindow > 0) {
            return activeContextWindow;
        }
    }
    const defaultProfile = profileEntries
        .map(([, profile]) => profile)
        .find(profile => profile.is_default === true);
    const contextWindow = Number(defaultProfile?.context_window);
    return Number.isFinite(contextWindow) && contextWindow > 0 ? contextWindow : null;
}

function resolveLatestInputTokens(usage) {
    const latestInputTokens = Number(usage?.latest_input_tokens);
    if (Number.isFinite(latestInputTokens) && latestInputTokens > 0) {
        return latestInputTokens;
    }
    return Number(usage?.input_tokens);
}

function formatTokenCount(value) {
    const safeValue = Number.isFinite(value) ? Math.max(0, Number(value)) : 0;
    if (safeValue >= 1000000) {
        return `${trimFraction(safeValue / 1000000)}m`;
    }
    if (safeValue >= 1000) {
        return `${trimFraction(safeValue / 1000)}k`;
    }
    return String(Math.round(safeValue));
}

function trimFraction(value) {
    const rounded = value >= 100 ? value.toFixed(0) : value.toFixed(1);
    return rounded.replace(/\.0$/, '');
}
