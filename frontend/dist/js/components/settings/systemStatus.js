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
import { errorToPayload, logError } from '../../utils/logger.js';

export function bindSystemStatusHandlers() {
    const reloadMcpBtn = document.getElementById('reload-mcp-btn');
    if (reloadMcpBtn) {
        reloadMcpBtn.onclick = handleReloadMcp;
    }

    const reloadSkillsBtn = document.getElementById('reload-skills-btn');
    if (reloadSkillsBtn) {
        reloadSkillsBtn.onclick = handleReloadSkills;
    }
}

export async function loadMcpStatusPanel() {
    try {
        const status = await fetchConfigStatus();
        const mcpStatus = document.getElementById('mcp-status');
        const servers = Array.isArray(status.mcp?.servers) ? status.mcp.servers : [];
        if (servers.length === 0) {
            mcpStatus.innerHTML = renderEmptyState('No MCP servers loaded', 'Add or enable a server, then reload to refresh the runtime view.');
            return;
        }

        const serverViews = await Promise.all(
            servers.map(serverName => loadMcpServerView(serverName)),
        );
        mcpStatus.innerHTML = renderMcpServerList(serverViews);
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
        const skills = status.skills?.skills || [];
        if (skills.length === 0) {
            skillsStatus.innerHTML = renderEmptyState('No skills loaded', 'Reload after updating the configured skill directories.');
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
        showToast({ title: 'MCP Reloaded', message: 'MCP config reloaded.', tone: 'success' });
        await loadMcpStatusPanel();
    } catch (e) {
        showToast({ title: 'Reload Failed', message: `Failed to reload: ${e.message}`, tone: 'danger' });
    }
}

async function handleReloadSkills() {
    try {
        await reloadSkillsConfig();
        showToast({ title: 'Skills Reloaded', message: 'Skills reloaded.', tone: 'success' });
        await loadSkillsStatusPanel();
    } catch (e) {
        showToast({ title: 'Reload Failed', message: `Failed to reload: ${e.message}`, tone: 'danger' });
    }
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
        };
    }
}

function renderMcpServerList(serverViews) {
    return `
        <div class="mcp-status-list">
            ${serverViews.map(serverView => renderMcpServerCard(serverView)).join('')}
        </div>
    `;
}

function renderMcpServerCard(serverView) {
    const meta = [serverView.transport, serverView.source].filter(Boolean).join(' / ');
    return `
        <section class="mcp-status-card">
            <div class="mcp-status-card-header">
                <div class="mcp-status-card-heading">
                    <div class="mcp-status-card-name">${escapeHtml(serverView.name)}</div>
                    ${meta ? `<div class="mcp-status-card-meta">${escapeHtml(meta)}</div>` : ''}
                </div>
                <div class="status-list-state">Loaded</div>
            </div>
            ${renderMcpServerTools(serverView)}
        </section>
    `;
}

function renderMcpServerTools(serverView) {
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
    return `
        <div class="status-list">
            ${items.map(item => `
                <div class="status-list-row">
                    <div class="status-list-name">${escapeHtml(item)}</div>
                    <div class="status-list-state">${escapeHtml(stateLabel)}</div>
                </div>
            `).join('')}
        </div>
    `;
}

function renderEmptyState(title, description) {
    return `
        <div class="settings-empty-state settings-empty-state-compact">
            <h4>${escapeHtml(title)}</h4>
            <p>${escapeHtml(description)}</p>
        </div>
    `;
}

function escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
