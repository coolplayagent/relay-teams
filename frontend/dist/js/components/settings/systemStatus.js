/**
 * components/settings/systemStatus.js
 * MCP/Skills tab logic.
 */
import * as api from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const addMcpServer = api.addMcpServer || postMcpServer;
const fetchMcpServer = api.fetchMcpServer || getMcpServer;
const fetchConfigStatus = api.fetchConfigStatus;
const fetchMcpServers = api.fetchMcpServers || fetchMcpServersFromConfigStatus;
const fetchMcpServerTools = api.fetchMcpServerTools;
const reloadMcpConfig = api.reloadMcpConfig;
const reloadSkillsConfig = api.reloadSkillsConfig;
const setMcpServerEnabled = api.setMcpServerEnabled || putMcpServerEnabled;
const testMcpServerConnection = api.testMcpServerConnection || postMcpServerConnectionTest;
const updateMcpServer = api.updateMcpServer || putMcpServer;

const collapsedMcpServers = new Set();
let lastLoadedMcpServerViews = [];
let activeMcpLoadRequestId = 0;
let languageBound = false;
let mcpEditorVisible = false;
let mcpEditorMode = 'add';
let editingMcpServerName = '';
let editingMcpServerConfig = null;
const mcpConnectionTests = new Map();

async function fetchMcpServersFromConfigStatus() {
    const status = await fetchConfigStatus();
    const names = Array.isArray(status?.mcp?.servers) ? status.mcp.servers : [];
    return names.map(name => ({
        name,
        source: '',
        transport: '',
        enabled: true,
    }));
}

async function postMcpServer(payload) {
    return requestMcpJson('/api/mcp/servers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
}

async function getMcpServer(serverName) {
    return requestMcpJson(`/api/mcp/servers/${encodeURIComponent(serverName)}`);
}

async function putMcpServer(serverName, payload) {
    return requestMcpJson(`/api/mcp/servers/${encodeURIComponent(serverName)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
}

async function putMcpServerEnabled(serverName, enabled) {
    return requestMcpJson(`/api/mcp/servers/${encodeURIComponent(serverName)}/enabled`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
    });
}

async function postMcpServerConnectionTest(serverName) {
    return requestMcpJson(
        `/api/mcp/servers/${encodeURIComponent(serverName)}/test`,
        { method: 'POST' },
    );
}

async function requestMcpJson(url, options) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(payload?.detail || payload?.message || response.statusText);
    }
    return payload;
}

export function bindSystemStatusHandlers() {
    bindActionButton('add-mcp-server-btn', showMcpEditor);
    bindActionButton('save-mcp-server-btn', handleSaveMcpServer);
    bindActionButton('cancel-mcp-server-btn', hideMcpEditor);

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
    globalThis.__agentTeamsTestMcpServer = testMcpServer;
    globalThis.__agentTeamsSetMcpServerEnabled = setMcpServerEnabledFromPanel;
    globalThis.__agentTeamsEditMcpServer = editMcpServer;
    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderMcpStatusPanel();
            void loadSkillsStatusPanel();
        });
        languageBound = true;
    }
}

function bindActionButton(id, handler) {
    const button = safeGetElementById(id);
    if (button) {
        button.onclick = handler;
    }
}

export async function loadMcpStatusPanel() {
    const requestId = ++activeMcpLoadRequestId;

    try {
        const summaries = await fetchMcpServers();
        if (requestId !== activeMcpLoadRequestId) {
            return;
        }

        const mcpStatus = document.getElementById('mcp-status');
        if (!mcpStatus) {
            return;
        }

        const servers = Array.isArray(summaries) ? summaries : [];
        if (servers.length === 0) {
            lastLoadedMcpServerViews = [];
            collapsedMcpServers.clear();
            mcpStatus.innerHTML = renderEmptyState(t('settings.system.no_mcp'), t('settings.system.no_mcp_copy'));
            return;
        }

        pruneCollapsedServers(servers.map(server => server?.name).filter(Boolean));
        lastLoadedMcpServerViews = servers.map(server => createLoadingMcpServerView(server));
        renderMcpStatusPanel();

        await Promise.all(
            servers.map(server => hydrateMcpServerView(requestId, server)),
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
            skillsStatus.innerHTML = renderStatusList(skills, t('settings.system.ready_state'));
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
        showToast({
            title: t('settings.system.reload_failed'),
            message: formatMessage('settings.system.reload_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

function showMcpEditor() {
    mcpEditorMode = 'add';
    editingMcpServerName = '';
    editingMcpServerConfig = null;
    mcpEditorVisible = true;
    renderMcpStatusPanel();
}

function hideMcpEditor() {
    mcpEditorVisible = false;
    mcpEditorMode = 'add';
    editingMcpServerName = '';
    editingMcpServerConfig = null;
    renderMcpStatusPanel();
}

async function handleSaveMcpServer() {
    try {
        const payload = buildMcpServerPayloadFromForm();
        const wasEditing = mcpEditorMode === 'edit';
        if (wasEditing) {
            await updateMcpServer(editingMcpServerName, { config: payload.config });
        } else {
            await addMcpServer(payload);
        }
        mcpEditorVisible = false;
        mcpEditorMode = 'add';
        editingMcpServerName = '';
        editingMcpServerConfig = null;
        showToast({
            title: wasEditing ? t('settings.mcp.updated') : t('settings.mcp.saved'),
            message: formatMessage('settings.mcp.saved_message', { name: payload.name }),
            tone: 'success',
        });
        await loadMcpStatusPanel();
    } catch (e) {
        showToast({
            title: t('settings.mcp.save_failed'),
            message: e.message || t('settings.mcp.save_failed_message'),
            tone: 'danger',
        });
    }
}

async function editMcpServer(serverName) {
    const safeName = String(serverName || '').trim();
    if (!safeName) {
        return;
    }
    try {
        const result = await fetchMcpServer(safeName);
        mcpEditorMode = 'edit';
        editingMcpServerName = safeName;
        editingMcpServerConfig = normalizeEditableMcpConfig(result?.config || {});
        mcpEditorVisible = true;
        renderMcpStatusPanel();
    } catch (e) {
        showToast({
            title: t('settings.mcp.load_failed'),
            message: e.message || t('settings.mcp.load_failed_message'),
            tone: 'danger',
        });
    }
}

async function testMcpServer(serverName) {
    const safeName = String(serverName || '').trim();
    if (!safeName) {
        return;
    }
    mcpConnectionTests.set(safeName, {
        loading: true,
        ok: false,
        message: t('settings.mcp.testing'),
    });
    renderMcpStatusPanel();
    try {
        const result = await testMcpServerConnection(safeName);
        mcpConnectionTests.set(safeName, {
            loading: false,
            ok: result?.ok === true,
            message: result?.ok === true
                ? formatMessage('settings.mcp.test_ok', { count: result?.tool_count || 0 })
                : (result?.error || t('settings.mcp.test_failed_message')),
        });
    } catch (e) {
        mcpConnectionTests.set(safeName, {
            loading: false,
            ok: false,
            message: e.message || t('settings.mcp.test_failed_message'),
        });
    }
    renderMcpStatusPanel();
}

async function setMcpServerEnabledFromPanel(serverName, enabled) {
    const safeName = String(serverName || '').trim();
    if (!safeName) {
        return;
    }
    try {
        await setMcpServerEnabled(safeName, enabled === true);
        mcpConnectionTests.delete(safeName);
        showToast({
            title: enabled ? t('settings.mcp.enabled') : t('settings.mcp.disabled'),
            message: formatMessage(
                enabled ? 'settings.mcp.enabled_message' : 'settings.mcp.disabled_message',
                { name: safeName },
            ),
            tone: 'success',
        });
        await loadMcpStatusPanel();
    } catch (e) {
        showToast({
            title: t('settings.mcp.toggle_failed'),
            message: e.message || t('settings.mcp.toggle_failed_message'),
            tone: 'danger',
        });
    }
}

async function handleReloadSkills() {
    try {
        await reloadSkillsConfig();
        showToast({ title: t('settings.system.skills_reloaded'), message: t('settings.system.skills_reloaded_message'), tone: 'success' });
        await loadSkillsStatusPanel();
    } catch (e) {
        showToast({
            title: t('settings.system.reload_failed'),
            message: formatMessage('settings.system.reload_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function hydrateMcpServerView(requestId, serverSummary) {
    const serverView = await loadMcpServerView(serverSummary);
    if (requestId !== activeMcpLoadRequestId) {
        return;
    }

    lastLoadedMcpServerViews = lastLoadedMcpServerViews.map(existingView => (
        existingView.name === serverView.name ? serverView : existingView
    ));
    pruneCollapsedServers(lastLoadedMcpServerViews.map(existingView => existingView.name));
    renderMcpStatusPanel();
}

async function loadMcpServerView(serverSummary) {
    const serverName = normalizeMcpServerName(serverSummary);
    const baseView = createBaseMcpServerView(serverSummary);
    if (baseView.enabled === false) {
        return {
            ...baseView,
            loading: false,
        };
    }
    try {
        const summary = await fetchMcpServerTools(serverName);
        return {
            name: serverName,
            source: typeof summary?.source === 'string' ? summary.source : '',
            transport: typeof summary?.transport === 'string' ? summary.transport : '',
            enabled: summary?.enabled !== false,
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
            source: baseView.source,
            transport: baseView.transport,
            enabled: baseView.enabled,
            tools: [],
            errorMessage: e?.message || t('settings.system.load_tools_failed_detail'),
            loading: false,
        };
    }
}

function createLoadingMcpServerView(serverSummary) {
    const baseView = createBaseMcpServerView(serverSummary);
    return {
        ...baseView,
        tools: [],
        errorMessage: '',
        loading: baseView.enabled !== false,
    };
}

function createBaseMcpServerView(serverSummary) {
    if (typeof serverSummary === 'string') {
        return {
            name: serverSummary,
            source: '',
            transport: '',
            enabled: true,
        };
    }
    return {
        name: normalizeMcpServerName(serverSummary),
        source: typeof serverSummary?.source === 'string' ? serverSummary.source : '',
        transport: typeof serverSummary?.transport === 'string' ? serverSummary.transport : '',
        enabled: serverSummary?.enabled !== false,
    };
}

function normalizeMcpServerName(serverSummary) {
    if (typeof serverSummary === 'string') {
        return serverSummary;
    }
    return typeof serverSummary?.name === 'string' ? serverSummary.name : '';
}

function renderMcpStatusPanel() {
    const mcpStatus = document.getElementById('mcp-status');
    if (!mcpStatus) {
        return;
    }
    mcpStatus.innerHTML = renderMcpServerList(lastLoadedMcpServerViews);
    bindMcpEditorHandlers();
    syncMcpActionButtons();
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
            ${mcpEditorVisible ? renderMcpEditor() : ''}
            ${renderMcpStatusToolbar(serverViews.length, collapsibleNames.length, allCollapsed, loadingCount)}
            <div class="mcp-status-list">
                ${serverViews.map(serverView => renderMcpServerCard(serverView)).join('')}
            </div>
        </div>
    `;
}

function renderMcpStatusToolbar(serverCount, collapsibleCount, allCollapsed, loadingCount) {
    const summaryLabel = loadingCount > 0
        ? formatMessage('settings.system.server_count_loading', { count: serverCount, loading: loadingCount })
        : formatMessage('settings.system.server_count_loaded', { count: serverCount });
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
    const testState = mcpConnectionTests.get(serverView.name);
    return `
        <section class="mcp-status-card">
            <div class="mcp-status-card-header">
                <div class="mcp-status-card-heading">
                    <div class="mcp-status-card-name">${escapeHtml(serverView.name)}</div>
                    ${meta ? `<div class="mcp-status-card-meta">${escapeHtml(meta)}</div>` : ''}
                </div>
                <div class="mcp-status-card-actions">
                    <button
                        class="mcp-status-toggle"
                        type="button"
                        onclick='globalThis.__agentTeamsEditMcpServer(${serializeForInlineScript(serverView.name)})'
                    >
                        ${t('settings.action.edit')}
                    </button>
                    <button
                        class="mcp-status-toggle"
                        type="button"
                        onclick='globalThis.__agentTeamsTestMcpServer(${serializeForInlineScript(serverView.name)})'
                        ${testState?.loading || serverView.enabled === false ? 'disabled' : ''}
                    >
                        ${testState?.loading ? t('settings.mcp.testing') : t('settings.action.test')}
                    </button>
                    <button
                        class="mcp-status-toggle"
                        type="button"
                        onclick='globalThis.__agentTeamsSetMcpServerEnabled(${serializeForInlineScript(serverView.name)}, ${serverView.enabled === false ? 'true' : 'false'})'
                    >
                        ${serverView.enabled === false ? t('settings.action.enable') : t('settings.action.disable')}
                    </button>
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
            ${testState ? renderMcpConnectionTestState(testState) : ''}
            ${renderMcpServerTools(serverView, collapsed)}
        </section>
    `;
}

function renderMcpConnectionTestState(testState) {
    const tone = testState.ok ? 'success' : 'danger';
    return `
        <div class="mcp-test-result mcp-test-result-${tone}">
            ${escapeHtml(testState.message || '')}
        </div>
    `;
}

function renderMcpEditor() {
    const config = editingMcpServerConfig || {};
    const name = mcpEditorMode === 'edit' ? editingMcpServerName : '';
    const transport = normalizeEditableTransport(config);
    const isRemote = transport !== 'stdio';
    const extra = isRemote ? formatKeyValueLines(config.headers) : formatKeyValueLines(config.env);
    return `
        <section class="mcp-editor-panel">
            <div class="mcp-editor-grid">
                <div class="form-group form-group-span-2">
                    <label for="mcp-server-json-input">${escapeHtml(t('settings.mcp.json_config'))}</label>
                    <textarea id="mcp-server-json-input" class="config-textarea mcp-editor-textarea mcp-editor-json-textarea" placeholder="${escapeHtml(t('settings.mcp.json_placeholder'))}" spellcheck="false"></textarea>
                </div>
                <div class="form-group">
                    <label for="mcp-server-name-input">${escapeHtml(t('settings.mcp.name'))}</label>
                    <input type="text" id="mcp-server-name-input" placeholder="${escapeHtml(t('settings.mcp.name_placeholder'))}" autocomplete="off" value="${escapeHtml(name)}" ${mcpEditorMode === 'edit' ? 'disabled' : ''}>
                </div>
                <div class="form-group">
                    <label for="mcp-server-transport-input">${escapeHtml(t('settings.mcp.transport'))}</label>
                    <select id="mcp-server-transport-input">
                        <option value="stdio" ${transport === 'stdio' ? 'selected' : ''}>${escapeHtml(t('settings.mcp.transport_stdio'))}</option>
                        <option value="http" ${transport === 'http' ? 'selected' : ''}>${escapeHtml(t('settings.mcp.transport_http'))}</option>
                        <option value="sse" ${transport === 'sse' ? 'selected' : ''}>${escapeHtml(t('settings.mcp.transport_sse'))}</option>
                        <option value="streamable-http" ${transport === 'streamable-http' ? 'selected' : ''}>${escapeHtml(t('settings.mcp.transport_streamable_http'))}</option>
                    </select>
                </div>
                <div class="form-group form-group-span-2 mcp-stdio-field">
                    <label for="mcp-server-command-input">${escapeHtml(t('settings.mcp.command'))}</label>
                    <input type="text" id="mcp-server-command-input" placeholder="${escapeHtml(t('settings.mcp.command_placeholder'))}" autocomplete="off" value="${escapeHtml(config.command || '')}">
                </div>
                <div class="form-group form-group-span-2 mcp-stdio-field">
                    <label for="mcp-server-args-input">${escapeHtml(t('settings.mcp.args'))}</label>
                    <textarea id="mcp-server-args-input" class="config-textarea mcp-editor-textarea" placeholder="${escapeHtml(t('settings.mcp.args_placeholder'))}" spellcheck="false">${escapeHtml(formatLineList(config.args))}</textarea>
                </div>
                <div class="form-group form-group-span-2 mcp-remote-field" style="${isRemote ? '' : 'display:none;'}">
                    <label for="mcp-server-url-input">${escapeHtml(t('settings.mcp.url'))}</label>
                    <input type="text" id="mcp-server-url-input" placeholder="${escapeHtml(t('settings.mcp.url_placeholder'))}" autocomplete="off" value="${escapeHtml(config.url || '')}">
                </div>
                <div class="form-group form-group-span-2">
                    <label for="mcp-server-extra-input">${escapeHtml(t('settings.mcp.extra'))}</label>
                    <textarea id="mcp-server-extra-input" class="config-textarea mcp-editor-textarea" placeholder="${escapeHtml(t('settings.mcp.extra_placeholder'))}" spellcheck="false">${escapeHtml(extra)}</textarea>
                </div>
                <label class="notification-toggle mcp-overwrite-toggle" style="${mcpEditorMode === 'edit' ? 'display:none;' : ''}">
                    <input type="checkbox" id="mcp-server-overwrite-input">
                    <span class="notification-toggle-check"></span>
                    <span class="notification-toggle-label">${escapeHtml(t('settings.mcp.overwrite'))}</span>
                </label>
            </div>
        </section>
    `;
}

function bindMcpEditorHandlers() {
    const transportInput = safeGetElementById('mcp-server-transport-input');
    if (transportInput) {
        transportInput.onchange = syncMcpEditorTransportFields;
        syncMcpEditorTransportFields();
    }
    const jsonInput = safeGetElementById('mcp-server-json-input');
    if (jsonInput) {
        jsonInput.oninput = applyMcpJsonConfigFromInput;
        jsonInput.onpaste = () => {
            setTimeout(applyMcpJsonConfigFromInput, 0);
        };
    }
}

function syncMcpEditorTransportFields() {
    const transport = getInputValue('mcp-server-transport-input') || 'stdio';
    const isStdio = transport === 'stdio';
    if (typeof document.querySelectorAll !== 'function') {
        return;
    }
    document.querySelectorAll('.mcp-stdio-field').forEach(element => {
        element.style.display = isStdio ? '' : 'none';
    });
    document.querySelectorAll('.mcp-remote-field').forEach(element => {
        element.style.display = isStdio ? 'none' : '';
    });
}

function syncMcpActionButtons() {
    setActionDisplay('add-mcp-server-btn', !mcpEditorVisible);
    setActionDisplay('reload-mcp-btn', !mcpEditorVisible);
    setActionDisplay('save-mcp-server-btn', mcpEditorVisible);
    setActionDisplay('cancel-mcp-server-btn', mcpEditorVisible);
}

function buildMcpServerPayloadFromForm() {
    const name = String(getInputValue('mcp-server-name-input')).trim();
    if (!name) {
        throw new Error(t('settings.mcp.name_required'));
    }
    const transport = String(getInputValue('mcp-server-transport-input') || 'stdio').trim() || 'stdio';
    const config = mcpEditorMode === 'edit' && editingMcpServerConfig
        ? { ...editingMcpServerConfig, transport }
        : { transport };
    if (transport === 'stdio') {
        const command = String(getInputValue('mcp-server-command-input')).trim();
        if (!command) {
            throw new Error(t('settings.mcp.command_required'));
        }
        config.command = command;
        config.args = parseLineList(getInputValue('mcp-server-args-input'));
        const env = parseKeyValueLines(getInputValue('mcp-server-extra-input'), t('settings.mcp.extra'));
        if (Object.keys(env).length > 0) {
            config.env = env;
        } else {
            delete config.env;
        }
        delete config.url;
        delete config.headers;
    } else {
        const url = String(getInputValue('mcp-server-url-input')).trim();
        if (!url) {
            throw new Error(t('settings.mcp.url_required'));
        }
        config.url = url;
        const headers = parseKeyValueLines(getInputValue('mcp-server-extra-input'), t('settings.mcp.extra'));
        if (Object.keys(headers).length > 0) {
            config.headers = headers;
        } else {
            delete config.headers;
        }
        delete config.command;
        delete config.args;
    }
    return {
        name,
        config,
        overwrite: safeGetElementById('mcp-server-overwrite-input')?.checked === true,
    };
}

function applyMcpJsonConfigFromInput() {
    const parsed = parseMcpJsonConfig(getInputValue('mcp-server-json-input'));
    if (!parsed) {
        return;
    }
    const config = normalizeEditableMcpConfig(parsed.config);
    setInputValue('mcp-server-name-input', parsed.name, { overwriteDisabled: false });
    setInputValue('mcp-server-transport-input', normalizeEditableTransport(config));
    syncMcpEditorTransportFields();
    setInputValue('mcp-server-command-input', config.command || '');
    setInputValue('mcp-server-args-input', formatLineList(config.args));
    setInputValue('mcp-server-url-input', config.url || '');
    const extra = normalizeEditableTransport(config) === 'stdio'
        ? formatKeyValueLines(config.env)
        : formatKeyValueLines(config.headers);
    setInputValue('mcp-server-extra-input', extra);
}

function parseMcpJsonConfig(raw) {
    const text = String(raw || '').trim();
    if (!text) {
        return null;
    }
    let payload;
    try {
        payload = JSON.parse(text);
    } catch (_) {
        return null;
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        return null;
    }

    const mcpServers = payload.mcpServers;
    if (mcpServers && typeof mcpServers === 'object' && !Array.isArray(mcpServers)) {
        const firstServer = Object.entries(mcpServers).find(([, config]) => (
            config && typeof config === 'object' && !Array.isArray(config)
        ));
        if (firstServer) {
            return {
                name: String(firstServer[0] || ''),
                config: { ...firstServer[1] },
            };
        }
    }

    if (typeof payload.name === 'string' && payload.config && typeof payload.config === 'object' && !Array.isArray(payload.config)) {
        return {
            name: payload.name,
            config: { ...payload.config },
        };
    }

    return null;
}

function normalizeEditableMcpConfig(config) {
    const normalized = { ...config };
    normalized.transport = normalizeEditableTransport(normalized);
    if (Array.isArray(normalized.command)) {
        const commandParts = normalized.command
            .map(item => String(item).trim())
            .filter(Boolean);
        if (commandParts.length > 0) {
            normalized.command = commandParts[0];
            if (!Array.isArray(normalized.args)) {
                normalized.args = commandParts.slice(1);
            }
        }
    }
    return normalized;
}

function normalizeEditableTransport(config) {
    const rawTransport = String(config?.transport || '').trim();
    const transport = normalizeMcpTransportValue(rawTransport);
    if (transport) {
        return transport;
    }
    const rawType = String(config?.type || '').trim();
    const configType = normalizeMcpTransportValue(rawType);
    if (configType === 'local') {
        return 'stdio';
    }
    if (configType === 'remote') {
        return detectRemoteMcpTransport(config);
    }
    if (configType) {
        return configType;
    }
    if (typeof config?.command === 'string' && config.command.trim()) {
        return 'stdio';
    }
    if (typeof config?.url === 'string' && config.url.trim()) {
        return detectRemoteMcpTransport(config);
    }
    return 'stdio';
}

function normalizeMcpTransportValue(value) {
    const normalized = String(value || '').trim().toLowerCase().replaceAll('_', '-');
    if (!normalized) {
        return '';
    }
    if (normalized === 'streamablehttp' || normalized === 'streamable-http') {
        return 'streamable-http';
    }
    if (normalized === 'stdio' || normalized === 'http' || normalized === 'sse') {
        return normalized;
    }
    if (normalized === 'local' || normalized === 'remote') {
        return normalized;
    }
    return normalized;
}

function detectRemoteMcpTransport(config) {
    const url = typeof config?.url === 'string' ? config.url : '';
    return url.includes('/sse') ? 'sse' : 'http';
}

function formatLineList(value) {
    if (!Array.isArray(value)) {
        return '';
    }
    return value.map(item => String(item)).join('\n');
}

function formatKeyValueLines(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return '';
    }
    return Object.entries(value)
        .map(([key, item]) => `${key}=${item}`)
        .join('\n');
}

function getInputValue(id) {
    const input = safeGetElementById(id);
    return input ? input.value || '' : '';
}

function setInputValue(id, value, options = {}) {
    const input = safeGetElementById(id);
    if (!input || (input.disabled && options.overwriteDisabled !== true)) {
        return;
    }
    input.value = String(value || '');
}

function setActionDisplay(id, visible) {
    const button = safeGetElementById(id);
    if (button) {
        button.style.display = visible ? 'inline-flex' : 'none';
    }
}

function safeGetElementById(id) {
    try {
        return document.getElementById(id);
    } catch (_) {
        return null;
    }
}

function parseLineList(raw) {
    return String(raw || '')
        .split('\n')
        .map(item => item.trim())
        .filter(Boolean);
}

function parseKeyValueLines(raw, label) {
    const entries = {};
    parseLineList(raw).forEach(line => {
        const separatorIndex = line.indexOf('=');
        if (separatorIndex <= 0) {
            throw new Error(formatMessage('settings.mcp.key_value_invalid', { label }));
        }
        const key = line.slice(0, separatorIndex).trim();
        const value = line.slice(separatorIndex + 1);
        if (!key) {
            throw new Error(formatMessage('settings.mcp.key_value_invalid', { label }));
        }
        entries[key] = value;
    });
    return entries;
}

function renderMcpServerTools(serverView, collapsed) {
    if (serverView.enabled === false) {
        return `
            <div class="mcp-tools-empty">${t('settings.mcp.disabled_state')}</div>
        `;
    }

    if (serverView.loading) {
        return `
            <div class="mcp-tools-empty panel-loading">${t('settings.system.loading_tools')}</div>
        `;
    }

    if (serverView.errorMessage) {
        return `
            <div class="mcp-tools-empty mcp-tools-error">${escapeHtml(serverView.errorMessage)}</div>
        `;
    }

    if (serverView.tools.length === 0) {
        return `
            <div class="mcp-tools-empty">${t('settings.system.no_tools_exposed')}</div>
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
            <div class="mcp-tool-description${description ? '' : ' mcp-tool-description-empty'}">${escapeHtml(description || t('settings.system.no_description'))}</div>
        </div>
    `;
}

function renderStatusList(items, stateLabel) {
    const normalizedItems = Array.isArray(items)
        ? items.map(normalizeStatusItem).filter(item => item !== null)
        : [];
    const nameCounts = buildStatusNameCounts(normalizedItems);
    return `
        <div class="status-list">
            ${normalizedItems.map(item => `
                <div class="status-list-row">
                    <div class="status-list-copy">
                        <div class="status-list-name">${escapeHtml(formatStatusItemLabel(item, nameCounts))}</div>
                        <div class="status-list-description${item.description ? '' : ' status-list-description-empty'}">${escapeHtml(item.description || t('settings.system.no_description'))}</div>
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
            source: '',
        };
    }

    const name = typeof item?.name === 'string' ? item.name.trim() : '';
    if (!name) {
        return null;
    }

    return {
        name,
        description: typeof item?.description === 'string' ? item.description.trim() : '',
        source: typeof item?.source === 'string'
            ? item.source.trim()
            : (typeof item?.scope === 'string' ? item.scope.trim() : ''),
    };
}

function buildStatusNameCounts(items) {
    const counts = new Map();
    items.forEach(item => {
        const name = String(item?.name || '').trim();
        if (!name) {
            return;
        }
        counts.set(name, (counts.get(name) || 0) + 1);
    });
    return counts;
}

function formatStatusItemLabel(item, nameCounts) {
    const safeName = String(item?.name || '').trim();
    const duplicateCount = nameCounts.get(safeName) || 0;
    if (duplicateCount <= 1) {
        return safeName;
    }
    return formatSkillStatusLabel(safeName, item?.source);
}

function formatSkillStatusLabel(name, scope) {
    const safeName = String(name || '').trim();
    const safeScope = String(scope || '').trim().toUpperCase();
    if (!safeScope) {
        return safeName;
    }
    return `${safeName} · ${safeScope}`;
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
    return Boolean(serverView && serverView.enabled !== false && !serverView.loading && !serverView.errorMessage && serverView.tools.length > 0);
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
    if (serverView.enabled === false) {
        return t('settings.system.disabled_state');
    }
    if (serverView.loading) {
        return t('settings.system.loading_state');
    }
    if (serverView.errorMessage) {
        return t('settings.system.unavailable_state');
    }
    return t('settings.system.loaded_state');
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
