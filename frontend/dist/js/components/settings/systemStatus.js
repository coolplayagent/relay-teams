/**
 * components/settings/systemStatus.js
 * MCP/Skills tab logic.
 */
import {
    fetchConfigStatus,
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
        const servers = status.mcp?.servers || [];
        if (servers.length === 0) {
            mcpStatus.innerHTML = renderEmptyState('No MCP servers loaded', 'Add or enable a server, then reload to refresh the runtime view.');
        } else {
            mcpStatus.innerHTML = renderStatusList(servers, 'Loaded');
        }
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
