/**
 * components/settings/systemStatus.js
 * MCP/Skills tab logic.
 */
import {
    fetchConfigStatus,
    fetchMcpServerTools,
    reloadMcpConfig,
    reloadSkillsConfig,
} from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const collapsedMcpServers = new Set();
let lastLoadedMcpServerViews = [];
let activeMcpLoadRequestId = 0;
let languageBound = false;

export function bindSystemStatusHandlers() {
    const reloadMcpBtn = document.getElementById('reload-mcp-btn');
    if (reloadMcpBtn) {
        reloadMcpBtn.onclick = handleReloadMcp;
    }

    const reloadSkillsBtn = document.getElementById('reload-skills-btn');
    if (reloadSkillsBtn) {
        reloadSkillsBtn.onclick = handleReloadSkills;
    }

    globalThis.__agentTeamsToggleMcpTools = toggleMcpTools;
    globalThis.__agentTeamsToggleAllMcpTools = toggleAllMcpTools;
    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderMcpStatusPanel();
            void loadSkillsStatusPanel();
        });
        languageBound = true;
    }
}

export async function loadMcpStatusPanel() {
    const requestId = ++activeMcpLoadRequestId;

    try {
        const status = await fetchConfigStatus();
        if (requestId !== activeMcpLoadRequestId) {
            return;
        }

        const mcpStatus = document.getElementById('mcp-status');
        if (!mcpStatus) {
            return;
        }

        const servers = Array.isArray(status.mcp?.servers) ? status.mcp.servers : [];
        if (servers.length === 0) {
            lastLoadedMcpServerViews = [];
            collapsedMcpServers.clear();
            mcpStatus.innerHTML = renderEmptyState(t('settings.system.no_mcp'), t('settings.system.no_mcp_copy'));
            return;
        }

        pruneCollapsedServers(servers);
        lastLoadedMcpServerViews = servers.map(serverName => createLoadingMcpServerView(serverName));
        renderMcpStatusPanel();

        await Promise.all(
            servers.map(serverName => hydrateMcpServerView(requestId, serverName)),
        );
    } catch (e) {
        logError(
            'frontend.system_status.mcp_load_failed',
            'Failed to load MCP status',
            errorToPayload(e),
        );
    }
}

export async function loadSkillsStatusPanel() {
    try {
        const status = await fetchConfigStatus();
        const skillsStatus = document.getElementById('skills-status');
        if (!skillsStatus) {
            return;
        }
        const skills = status.skills?.skills || [];
        if (skills.length === 0) {
            skillsStatus.innerHTML = renderEmptyState(t('settings.system.no_skills'), t('settings.system.no_skills_copy'));
        } else {
            skillsStatus.innerHTML = renderStatusList(skills, 'Ready');
        }
    } catch (e) {
        logError(
            'frontend.system_status.skills_load_failed',
            'Failed to load skills status',
            errorToPayload(e),
        );
    }
}

async function handleReloadMcp() {
    try {
        await reloadMcpConfig();
        showToast({ title: t('settings.system.mcp_reloaded'), message: t('settings.system.mcp_reloaded_message'), tone: 'success' });
        await loadMcpStatusPanel();
    } catch (e) {
        showToast({ title: t('settings.system.reload_failed'), message: `Failed to reload: ${e.message}`, tone: 'danger' });
    }
}

async function handleReloadSkills() {
    try {
        await reloadSkillsConfig();
        showToast({ title: t('settings.system.skills_reloaded'), message: t('settings.system.skills_reloaded_message'), tone: 'success' });
        await loadSkillsStatusPanel();
    } catch (e) {
        showToast({ title: t('settings.system.reload_failed'), message: `Failed to reload: ${e.message}`, tone: 'danger' });
    }
}

async function hydrateMcpServerView(requestId, serverName) {
    const serverView = await loadMcpServerView(serverName);
    if (requestId !== activeMcpLoadRequestId) {
        return;
    }

    lastLoadedMcpServerViews = lastLoadedMcpServerViews.map(existingView => (
        existingView.name === serverName ? serverView : existingView
    ));
    pruneCollapsedServers(lastLoadedMcpServerViews.map(existingView => existingView.name));
    renderMcpStatusPanel();
}

async function loadMcpServerView(serverName) {
    try {
        const summary = await fetchMcpServerTools(serverName);
        return {
            name: serverName,
            source: typeof summary?.source === 'string' ? summary.source : '',
            transport: typeof summary?.transport === 'string' ? summary.transport : '',
            tools: Array.isArray(summary?.tools) ? summary.tools : [],
            errorMessage: '',
            loading: false,
        };
    } catch (e) {
        logError(
            'frontend.system_status.mcp_tools_load_failed',
            'Failed to load MCP tools',
            errorToPayload(e, { server_name: serverName }),
        );
        return {
            name: serverName,
            source: '',
            transport: '',
            tools: [],
            errorMessage: e?.message || 'Failed to load tools for this MCP server.',
            loading: false,
        };
    }
}

function createLoadingMcpServerView(serverName) {
    return {
        name: serverName,
        source: '',
        transport: '',
        tools: [],
        errorMessage: '',
        loading: true,
    };
}

function renderMcpStatusPanel() {
    const mcpStatus = document.getElementById('mcp-status');
    if (!mcpStatus) {
        return;
    }
    mcpStatus.innerHTML = renderMcpServerList(lastLoadedMcpServerViews);
}

function toggleMcpTools(serverName) {
    if (!serverName || !canToggleServerTools(serverName)) {
        return;
    }

    if (collapsedMcpServers.has(serverName)) {
        collapsedMcpServers.delete(serverName);
    } else {
        collapsedMcpServers.add(serverName);
    }
    renderMcpStatusPanel();
}

function toggleAllMcpTools() {
    const collapsibleNames = getCollapsibleServerNames(lastLoadedMcpServerViews);
    if (collapsibleNames.length === 0) {
        return;
    }

    if (collapsibleNames.every(serverName => collapsedMcpServers.has(serverName))) {
        collapsibleNames.forEach(serverName => collapsedMcpServers.delete(serverName));
    } else {
        collapsibleNames.forEach(serverName => collapsedMcpServers.add(serverName));
    }
    renderMcpStatusPanel();
}

function renderMcpServerList(serverViews) {
    const collapsibleNames = getCollapsibleServerNames(serverViews);
    const allCollapsed = collapsibleNames.length > 0
        && collapsibleNames.every(serverName => collapsedMcpServers.has(serverName));
    const loadingCount = serverViews.filter(serverView => serverView.loading).length;
    return `
        <div class="mcp-status-shell">
            ${renderMcpStatusToolbar(serverViews.length, collapsibleNames.length, allCollapsed, loadingCount)}
            <div class="mcp-status-list">
                ${serverViews.map(serverView => renderMcpServerCard(serverView)).join('')}
            </div>
        </div>
    `;
}

function renderMcpStatusToolbar(serverCount, collapsibleCount, allCollapsed, loadingCount) {
    const summaryLabel = loadingCount > 0
        ? `${serverCount} server${serverCount === 1 ? '' : 's'} configured, ${loadingCount} loading..`
        : `${serverCount} server${serverCount === 1 ? '' : 's'} loaded`;
    return `
        <div class="mcp-status-toolbar">
            <div class="mcp-status-toolbar-copy">${escapeHtml(summaryLabel)}</div>
            ${collapsibleCount > 0 ? `
                <button
                    class="mcp-status-toolbar-btn"
                    type="button"
                    onclick="globalThis.__agentTeamsToggleAllMcpTools()"
                >
                    ${allCollapsed ? t('settings.system.expand_all') : t('settings.system.collapse_all')}
                </button>
            ` : ''}
        </div>
    `;
}

function renderMcpServerCard(serverView) {
    const meta = [serverView.transport, serverView.source].filter(Boolean).join(' / ');
    const collapsed = collapsedMcpServers.has(serverView.name);
    const canCollapse = canCollapseTools(serverView);
    return `
        <section class="mcp-status-card">
            <div class="mcp-status-card-header">
                <div class="mcp-status-card-heading">
                    <div class="mcp-status-card-name">${escapeHtml(serverView.name)}</div>
                    ${meta ? `<div class="mcp-status-card-meta">${escapeHtml(meta)}</div>` : ''}
                </div>
                <div class="mcp-status-card-actions">
                    ${canCollapse ? `
                        <button
                            class="mcp-status-toggle"
                            type="button"
                            onclick='globalThis.__agentTeamsToggleMcpTools(${serializeForInlineScript(serverView.name)})'
                        >
                            ${collapsed ? t('settings.system.expand_tools') : t('settings.system.collapse_tools')}
                        </button>
                    ` : ''}
                    <div class="status-list-state">${escapeHtml(getMcpServerStateLabel(serverView))}</div>
                </div>
            </div>
            ${renderMcpServerTools(serverView, collapsed)}
        </section>
    `;
}

function renderMcpServerTools(serverView, collapsed) {
    if (serverView.loading) {
        return `
            <div class="mcp-tools-empty panel-loading">Loading tools...</div>
        `;
    }

    if (serverView.errorMessage) {
        return `
            <div class="mcp-tools-empty mcp-tools-error">${escapeHtml(serverView.errorMessage)}</div>
        `;
    }

    if (serverView.tools.length === 0) {
        return `
            <div class="mcp-tools-empty">No tools exposed by this MCP server.</div>
        `;
    }

    if (collapsed) {
        return `
            <div class="mcp-tools-collapsed-summary">
                ${escapeHtml(formatHiddenToolsLabel(serverView.tools.length))}
            </div>
        `;
    }

    return `
        <div class="mcp-tools-list">
            ${serverView.tools.map(tool => renderMcpToolRow(tool)).join('')}
        </div>
    `;
}

function renderMcpToolRow(tool) {
    const description = typeof tool?.description === 'string' ? tool.description.trim() : '';
    return `
        <div class="mcp-tool-row">
            <div class="mcp-tool-name">${escapeHtml(tool?.name || 'Unnamed tool')}</div>
            <div class="mcp-tool-description${description ? '' : ' mcp-tool-description-empty'}">${escapeHtml(description || 'No description provided.')}</div>
        </div>
    `;
}

function renderStatusList(items, stateLabel) {
    const normalizedItems = Array.isArray(items)
        ? items.map(normalizeStatusItem).filter(item => item !== null)
        : [];
    return `
        <div class="status-list">
            ${normalizedItems.map(item => `
                <div class="status-list-row">
                    <div class="status-list-copy">
                        <div class="status-list-name">${escapeHtml(item.name)}</div>
                        <div class="status-list-description${item.description ? '' : ' status-list-description-empty'}">${escapeHtml(item.description || 'No description provided.')}</div>
                    </div>
                    <div class="status-list-state">${escapeHtml(stateLabel)}</div>
                </div>
            `).join('')}
        </div>
    `;
}

function normalizeStatusItem(item) {
    if (typeof item === 'string') {
        const name = item.trim();
        if (!name) {
            return null;
        }
        return {
            name,
            description: '',
        };
    }

    const name = typeof item?.name === 'string' ? item.name.trim() : '';
    if (!name) {
        return null;
    }

    return {
        name,
        description: typeof item?.description === 'string' ? item.description.trim() : '',
    };
}

function renderEmptyState(title, description) {
    return `
        <div class="settings-empty-state settings-empty-state-compact">
            <h4>${escapeHtml(title)}</h4>
            <p>${escapeHtml(description)}</p>
        </div>
    `;
}

function canCollapseTools(serverView) {
    return Boolean(serverView && !serverView.loading && !serverView.errorMessage && serverView.tools.length > 0);
}

function canToggleServerTools(serverName) {
    return lastLoadedMcpServerViews.some(
        serverView => serverView.name === serverName && canCollapseTools(serverView),
    );
}

function getCollapsibleServerNames(serverViews) {
    return serverViews.filter(canCollapseTools).map(serverView => serverView.name);
}

function getMcpServerStateLabel(serverView) {
    if (serverView.loading) {
        return 'Loading..';
    }
    if (serverView.errorMessage) {
        return 'Unavailable';
    }
    return 'Loaded';
}

function pruneCollapsedServers(validServerNames) {
    const validNameSet = new Set(validServerNames);
    Array.from(collapsedMcpServers).forEach(serverName => {
        if (!validNameSet.has(serverName)) {
            collapsedMcpServers.delete(serverName);
        }
    });
}

function formatHiddenToolsLabel(toolCount) {
    return `${toolCount} tool${toolCount === 1 ? '' : 's'} hidden.`;
}

function serializeForInlineScript(value) {
    return JSON.stringify(String(value));
}

function escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
