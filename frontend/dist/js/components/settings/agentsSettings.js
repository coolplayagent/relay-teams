/**
 * components/settings/agentsSettings.js
 * External ACP agent settings panel bindings.
 */
import {
    deleteExternalAgent,
    fetchExternalAgent,
    fetchExternalAgents,
    saveExternalAgent,
    testExternalAgent,
} from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let agentSummaries = [];
let selectedAgentId = '';
let selectedSourceAgentId = '';
let currentTransport = 'stdio';
let currentStdioEnv = [];
let currentHttpHeaders = [];

export function bindAgentSettingsHandlers() {
    bindActionButton('add-agent-btn', handleAddAgent);
    bindActionButton('save-agent-btn', handleSaveAgent);
    bindActionButton('test-agent-btn', handleTestAgent);
    bindActionButton('delete-agent-btn', handleDeleteAgent);
    bindActionButton('cancel-agent-btn', handleCancelAgent);
    bindActionButton('add-agent-stdio-env-btn', () => addBindingRow('stdio'));
    bindActionButton('add-agent-http-header-btn', () => addBindingRow('http'));

    const transportSelect = document.getElementById('agent-transport-input');
    if (transportSelect) {
        transportSelect.onchange = () => {
            currentTransport = String(transportSelect.value || 'stdio').trim() || 'stdio';
            renderTransportSections();
        };
    }
}

export async function loadAgentSettingsPanel(preferredAgentId = '') {
    try {
        const summaries = await fetchExternalAgents();
        agentSummaries = Array.isArray(summaries) ? summaries.map(normalizeAgentSummary) : [];
        renderAgentsList();
        if (agentSummaries.length === 0) {
            showAgentsList();
            renderEmptyAgentsList();
            return;
        }
        const targetAgentId = preferredAgentId && agentSummaries.some(item => item.agent_id === preferredAgentId)
            ? preferredAgentId
            : '';
        if (targetAgentId) {
            await loadAgentDocument(targetAgentId);
            return;
        }
        showAgentsList();
    } catch (error) {
        logError(
            'frontend.agents_settings.load_failed',
            'Failed to load external agents',
            errorToPayload(error),
        );
        showAgentsList();
        renderEmptyAgentsList('Failed to load agents', error.message || 'Unable to load agent settings.');
    }
}

function bindActionButton(id, handler) {
    const button = document.getElementById(id);
    if (button) {
        button.onclick = handler;
    }
}

function normalizeAgentSummary(agent) {
    return {
        agent_id: String(agent?.agent_id || '').trim(),
        name: String(agent?.name || '').trim(),
        description: String(agent?.description || '').trim(),
        transport: String(agent?.transport || 'stdio').trim() || 'stdio',
    };
}

function normalizeBinding(binding) {
    return {
        name: String(binding?.name || '').trim(),
        value: binding?.value == null ? '' : String(binding.value),
        secret: binding?.secret === true,
        configured: binding?.configured === true,
    };
}

function createBlankAgentConfig() {
    return {
        agent_id: '',
        name: '',
        description: '',
        transport: {
            transport: 'stdio',
            command: '',
            args: [],
            env: [],
        },
    };
}

function normalizeAgentConfig(config) {
    const safeTransport = String(config?.transport?.transport || 'stdio').trim() || 'stdio';
    if (safeTransport === 'streamable_http') {
        return {
            agent_id: String(config?.agent_id || '').trim(),
            name: String(config?.name || '').trim(),
            description: String(config?.description || '').trim(),
            transport: {
                transport: 'streamable_http',
                url: String(config?.transport?.url || '').trim(),
                headers: Array.isArray(config?.transport?.headers)
                    ? config.transport.headers.map(normalizeBinding)
                    : [],
                ssl_verify: config?.transport?.ssl_verify === true
                    ? true
                    : config?.transport?.ssl_verify === false
                        ? false
                        : null,
            },
        };
    }
    if (safeTransport === 'custom') {
        return {
            agent_id: String(config?.agent_id || '').trim(),
            name: String(config?.name || '').trim(),
            description: String(config?.description || '').trim(),
            transport: {
                transport: 'custom',
                adapter_id: String(config?.transport?.adapter_id || '').trim(),
                config: config?.transport?.config && typeof config.transport.config === 'object'
                    ? config.transport.config
                    : {},
            },
        };
    }
    return {
        agent_id: String(config?.agent_id || '').trim(),
        name: String(config?.name || '').trim(),
        description: String(config?.description || '').trim(),
        transport: {
            transport: 'stdio',
            command: String(config?.transport?.command || '').trim(),
            args: Array.isArray(config?.transport?.args)
                ? config.transport.args.map(item => String(item || '').trim()).filter(Boolean)
                : [],
            env: Array.isArray(config?.transport?.env)
                ? config.transport.env.map(normalizeBinding)
                : [],
        },
    };
}

function renderAgentsList() {
    const listEl = document.getElementById('agents-list');
    if (!listEl) return;

    if (!Array.isArray(agentSummaries) || agentSummaries.length === 0) {
        renderEmptyAgentsList();
        return;
    }

    listEl.innerHTML = `
        <div class="role-records">
            ${agentSummaries.map(agent => `
                <div class="role-record${agent.agent_id === selectedAgentId ? ' active' : ''}" data-agent-id="${escapeHtml(agent.agent_id)}">
                    <div class="role-record-main">
                        <div class="role-record-title-row">
                            <div class="role-record-title">${escapeHtml(agent.name || agent.agent_id)}</div>
                            <div class="role-record-id">${escapeHtml(agent.agent_id)}</div>
                            <div class="profile-card-chips role-record-chips">
                                <span class="profile-card-chip">${escapeHtml(formatTransportLabel(agent.transport))}</span>
                            </div>
                        </div>
                        <div class="role-record-meta">
                            <span>${escapeHtml(agent.description || 'No description')}</span>
                        </div>
                    </div>
                    <div class="role-record-actions">
                        <button class="settings-inline-action settings-list-action agent-record-edit-btn" data-agent-id="${escapeHtml(agent.agent_id)}" type="button">${escapeHtml(t('settings.roles.edit'))}</button>
                    </div>
                </div>
            `).join('')}
        </div>
    `;

    listEl.querySelectorAll('.role-record').forEach(button => {
        button.onclick = () => {
            const nextAgentId = String(button.dataset.agentId || '').trim();
            if (!nextAgentId) return;
            void loadAgentDocument(nextAgentId);
        };
    });
    listEl.querySelectorAll('.agent-record-edit-btn').forEach(button => {
        button.onclick = event => {
            event.stopPropagation();
            const nextAgentId = String(button.dataset.agentId || '').trim();
            if (!nextAgentId) return;
            void loadAgentDocument(nextAgentId);
        };
    });
}

async function loadAgentDocument(agentId) {
    selectedAgentId = String(agentId || '').trim();
    renderAgentsList();
    const record = normalizeAgentConfig(await fetchExternalAgent(agentId));
    selectedAgentId = record.agent_id;
    selectedSourceAgentId = record.agent_id;
    applyAgentRecord(record);
    renderAgentsList();
    showAgentEditor();
}

function applyAgentRecord(record) {
    const panel = document.getElementById('agent-editor-panel');
    const formEl = document.getElementById('agent-editor-form');
    const emptyEl = document.getElementById('agents-editor-empty');
    if (panel) panel.style.display = 'block';
    if (formEl) formEl.style.display = 'block';
    if (emptyEl) emptyEl.style.display = 'none';

    currentTransport = String(record?.transport?.transport || 'stdio').trim() || 'stdio';
    currentStdioEnv = currentTransport === 'stdio'
        ? record.transport.env.map(normalizeBinding)
        : [];
    currentHttpHeaders = currentTransport === 'streamable_http'
        ? record.transport.headers.map(normalizeBinding)
        : [];

    setInputValue('agent-id-input', record.agent_id || '');
    setInputValue('agent-name-input', record.name || '');
    setInputValue('agent-description-input', record.description || '');
    setInputValue('agent-transport-input', currentTransport);
    setInputValue('agent-stdio-command-input', currentTransport === 'stdio' ? record.transport.command || '' : '');
    setInputValue('agent-stdio-args-input', currentTransport === 'stdio' ? serializeLines(record.transport.args || []) : '');
    setInputValue('agent-http-url-input', currentTransport === 'streamable_http' ? record.transport.url || '' : '');
    setInputValue(
        'agent-http-ssl-verify-input',
        currentTransport === 'streamable_http'
            ? serializeTriStateValue(record.transport.ssl_verify)
            : '',
    );
    setInputValue(
        'agent-custom-adapter-id-input',
        currentTransport === 'custom' ? record.transport.adapter_id || '' : '',
    );
    setInputValue(
        'agent-custom-config-input',
        currentTransport === 'custom'
            ? JSON.stringify(record.transport.config || {}, null, 2)
            : '{}',
    );
    renderBindingRows('stdio');
    renderBindingRows('http');
    renderTransportSections();
    renderAgentStatus('', '');
}

function renderBindingRows(kind) {
    const containerId = kind === 'stdio'
        ? 'agent-stdio-env-list'
        : 'agent-http-header-list';
    const container = document.getElementById(containerId);
    if (!container) return;

    const rows = kind === 'stdio' ? currentStdioEnv : currentHttpHeaders;
    if (!rows.length) {
        container.innerHTML = `<div class="role-option-empty">No ${kind === 'stdio' ? 'env vars' : 'headers'} configured.</div>`;
        return;
    }

    container.innerHTML = rows.map((binding, index) => `
        <div class="role-workspace-row agent-binding-row" data-kind="${kind}" data-index="${index}">
            <div class="form-group">
                <label>${kind === 'stdio' ? 'Name' : 'Header'}</label>
                <input type="text" class="agent-binding-name" data-kind="${kind}" data-index="${index}" value="${escapeHtml(binding.name)}" autocomplete="off">
            </div>
            <div class="form-group">
                <label>Value</label>
                <input type="${binding.secret ? 'password' : 'text'}" class="agent-binding-value" data-kind="${kind}" data-index="${index}" value="${escapeHtml(binding.value || '')}" autocomplete="off" placeholder="${binding.configured ? 'Configured in keyring' : ''}">
            </div>
            <div class="form-group">
                <label>Secret</label>
                <select class="agent-binding-secret" data-kind="${kind}" data-index="${index}">
                    <option value="false"${binding.secret ? '' : ' selected'}>Plain</option>
                    <option value="true"${binding.secret ? ' selected' : ''}>Keyring</option>
                </select>
            </div>
            <div class="form-group">
                <label>Action</label>
                <button class="secondary-btn section-action-btn agent-binding-remove-btn" data-kind="${kind}" data-index="${index}" type="button">Remove</button>
            </div>
        </div>
    `).join('');

    container.querySelectorAll('.agent-binding-name').forEach(input => {
        input.oninput = event => updateBindingField(kind, event, 'name');
    });
    container.querySelectorAll('.agent-binding-value').forEach(input => {
        input.oninput = event => updateBindingField(kind, event, 'value');
    });
    container.querySelectorAll('.agent-binding-secret').forEach(select => {
        select.onchange = event => updateBindingField(kind, event, 'secret');
    });
    container.querySelectorAll('.agent-binding-remove-btn').forEach(button => {
        button.onclick = () => removeBindingRow(kind, button.dataset.index);
    });
}

function updateBindingField(kind, event, field) {
    const target = event?.target;
    const index = Number(target?.dataset?.index || -1);
    const rows = kind === 'stdio' ? currentStdioEnv : currentHttpHeaders;
    if (index < 0 || index >= rows.length) return;
    const current = rows[index];
    if (field === 'secret') {
        current.secret = String(target?.value || '').trim() === 'true';
        if (!current.secret) {
            current.configured = false;
        }
    } else {
        current[field] = String(target?.value || '');
        if (field === 'value' && current.secret) {
            current.configured = current.configured || !!String(target?.value || '').trim();
        }
    }
}

function addBindingRow(kind) {
    const rows = kind === 'stdio' ? currentStdioEnv : currentHttpHeaders;
    rows.push({
        name: '',
        value: '',
        secret: false,
        configured: false,
    });
    renderBindingRows(kind);
}

function removeBindingRow(kind, indexValue) {
    const index = Number(indexValue || -1);
    if (kind === 'stdio') {
        currentStdioEnv = currentStdioEnv.filter((_, itemIndex) => itemIndex !== index);
        renderBindingRows('stdio');
        return;
    }
    currentHttpHeaders = currentHttpHeaders.filter((_, itemIndex) => itemIndex !== index);
    renderBindingRows('http');
}

function renderTransportSections() {
    const stdioSection = document.getElementById('agent-transport-stdio');
    const httpSection = document.getElementById('agent-transport-http');
    const customSection = document.getElementById('agent-transport-custom');
    if (stdioSection) stdioSection.style.display = currentTransport === 'stdio' ? 'block' : 'none';
    if (httpSection) httpSection.style.display = currentTransport === 'streamable_http' ? 'block' : 'none';
    if (customSection) customSection.style.display = currentTransport === 'custom' ? 'block' : 'none';
}

function renderEmptyAgentsList(
    title = 'No external agents found',
    description = 'Add an ACP-compatible external agent to make it available for role bindings.',
) {
    const listEl = document.getElementById('agents-list');
    const editorPanel = document.getElementById('agent-editor-panel');
    if (editorPanel) {
        editorPanel.style.display = 'none';
    }
    if (listEl) {
        listEl.innerHTML = `
            <div class="settings-empty-state">
                <h4>${escapeHtml(title)}</h4>
                <p>${escapeHtml(description)}</p>
            </div>
        `;
        listEl.style.display = 'block';
    }
    renderAgentStatus('', '');
}

function handleAddAgent() {
    selectedAgentId = '';
    selectedSourceAgentId = '';
    renderAgentsList();
    applyAgentRecord(createBlankAgentConfig());
    showAgentEditor();
    const idInput = document.getElementById('agent-id-input');
    if (idInput?.focus) {
        idInput.focus();
    }
}

async function handleSaveAgent() {
    try {
        const draft = buildDraftFromForm();
        const pathAgentId = selectedSourceAgentId || draft.agent_id;
        const saved = normalizeAgentConfig(await saveExternalAgent(pathAgentId, draft));
        selectedAgentId = saved.agent_id;
        selectedSourceAgentId = saved.agent_id;
        showToast({
            title: 'Agent Saved',
            message: `${saved.agent_id} saved and reloaded.`,
            tone: 'success',
        });
        await loadAgentSettingsPanel(saved.agent_id);
        renderAgentStatus('Saved successfully.', 'success');
    } catch (error) {
        renderAgentStatus(error.message || 'Save failed.', 'danger');
        showToast({
            title: 'Save Failed',
            message: error.message || 'Failed to save external agent config.',
            tone: 'danger',
        });
    }
}

async function handleTestAgent() {
    try {
        const draft = buildDraftFromForm();
        const pathAgentId = selectedSourceAgentId || draft.agent_id;
        const saved = normalizeAgentConfig(await saveExternalAgent(pathAgentId, draft));
        const result = await testExternalAgent(saved.agent_id);
        selectedAgentId = saved.agent_id;
        selectedSourceAgentId = saved.agent_id;
        await loadAgentSettingsPanel(saved.agent_id);
        renderAgentStatus(result.message || 'Connection succeeded.', 'success');
        showToast({
            title: 'Agent Test Passed',
            message: result.message || `${saved.agent_id} responded to ACP initialize.`,
            tone: 'success',
        });
    } catch (error) {
        renderAgentStatus(error.message || 'Connection failed.', 'danger');
        showToast({
            title: 'Agent Test Failed',
            message: error.message || 'Failed to test external agent config.',
            tone: 'danger',
        });
    }
}

async function handleDeleteAgent() {
    const agentId = String(selectedSourceAgentId || getInputValue('agent-id-input')).trim();
    if (!agentId) {
        renderAgentStatus('Select an agent to delete.', 'danger');
        return;
    }
    try {
        await deleteExternalAgent(agentId);
        selectedAgentId = '';
        selectedSourceAgentId = '';
        showToast({
            title: 'Agent Deleted',
            message: `${agentId} removed from settings.`,
            tone: 'success',
        });
        await loadAgentSettingsPanel();
    } catch (error) {
        renderAgentStatus(error.message || 'Delete failed.', 'danger');
        showToast({
            title: 'Delete Failed',
            message: error.message || 'Failed to delete external agent config.',
            tone: 'danger',
        });
    }
}

function handleCancelAgent() {
    showAgentsList();
}

function buildDraftFromForm() {
    const agentId = String(getInputValue('agent-id-input')).trim();
    if (!agentId) {
        throw new Error('Agent ID is required.');
    }
    const name = String(getInputValue('agent-name-input')).trim();
    if (!name) {
        throw new Error('Agent name is required.');
    }
    const description = String(getInputValue('agent-description-input')).trim();
    const transport = String(getInputValue('agent-transport-input')).trim() || 'stdio';
    if (transport === 'streamable_http') {
        const url = String(getInputValue('agent-http-url-input')).trim();
        if (!url) {
            throw new Error('HTTP transport URL is required.');
        }
        return {
            agent_id: agentId,
            name,
            description,
            transport: {
                transport: 'streamable_http',
                url,
                headers: normalizeBindingsForSave(currentHttpHeaders),
                ssl_verify: parseTriStateValue(getInputValue('agent-http-ssl-verify-input')),
            },
        };
    }
    if (transport === 'custom') {
        const adapterId = String(getInputValue('agent-custom-adapter-id-input')).trim();
        if (!adapterId) {
            throw new Error('Custom transport adapter ID is required.');
        }
        return {
            agent_id: agentId,
            name,
            description,
            transport: {
                transport: 'custom',
                adapter_id: adapterId,
                config: parseJsonObject(getInputValue('agent-custom-config-input'), 'Custom transport config'),
            },
        };
    }
    const command = String(getInputValue('agent-stdio-command-input')).trim();
    if (!command) {
        throw new Error('Stdio command is required.');
    }
    return {
        agent_id: agentId,
        name,
        description,
        transport: {
            transport: 'stdio',
            command,
            args: parseLineList(getInputValue('agent-stdio-args-input')),
            env: normalizeBindingsForSave(currentStdioEnv),
        },
    };
}

function normalizeBindingsForSave(bindings) {
    return (Array.isArray(bindings) ? bindings : [])
        .map(item => ({
            name: String(item?.name || '').trim(),
            value: String(item?.value || ''),
            secret: item?.secret === true,
            configured: item?.configured === true,
        }))
        .filter(item => item.name);
}

function showAgentsList() {
    const listEl = document.getElementById('agents-list');
    const editorPanel = document.getElementById('agent-editor-panel');
    if (listEl) listEl.style.display = 'block';
    if (editorPanel) editorPanel.style.display = 'none';
    toggleAgentActions({
        add: true,
        test: false,
        save: false,
        delete: false,
        cancel: false,
    });
}

function showAgentEditor() {
    const listEl = document.getElementById('agents-list');
    const editorPanel = document.getElementById('agent-editor-panel');
    if (listEl) listEl.style.display = 'none';
    if (editorPanel) editorPanel.style.display = 'block';
    toggleAgentActions({
        add: false,
        test: true,
        save: true,
        delete: Boolean(selectedSourceAgentId || getInputValue('agent-id-input')),
        cancel: true,
    });
}

function toggleAgentActions(visibility) {
    setActionDisplay('add-agent-btn', visibility.add);
    setActionDisplay('test-agent-btn', visibility.test);
    setActionDisplay('save-agent-btn', visibility.save);
    setActionDisplay('delete-agent-btn', visibility.delete);
    setActionDisplay('cancel-agent-btn', visibility.cancel);
}

function setActionDisplay(id, visible) {
    const button = document.getElementById(id);
    if (button) {
        button.style.display = visible ? 'inline-flex' : 'none';
    }
}

function renderAgentStatus(message, tone) {
    const statusEl = document.getElementById('agent-editor-status');
    if (!statusEl) return;
    statusEl.className = 'role-editor-status';
    if (!message) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        return;
    }
    statusEl.style.display = 'block';
    if (tone) {
        statusEl.classList.add(`role-editor-status-${tone}`);
    }
    statusEl.textContent = message;
}

function setInputValue(id, value) {
    const input = document.getElementById(id);
    if (input) {
        input.value = value || '';
    }
}

function getInputValue(id) {
    const input = document.getElementById(id);
    return input ? input.value || '' : '';
}

function parseLineList(raw) {
    return String(raw || '')
        .split('\n')
        .map(item => item.trim())
        .filter(Boolean);
}

function serializeLines(values) {
    return Array.isArray(values) ? values.join('\n') : '';
}

function parseJsonObject(raw, label) {
    const source = String(raw || '').trim() || '{}';
    try {
        const parsed = JSON.parse(source);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new Error(`${label} must be a JSON object.`);
        }
        return parsed;
    } catch (error) {
        throw new Error(error.message || `${label} must be valid JSON.`);
    }
}

function parseTriStateValue(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'true') return true;
    if (normalized === 'false') return false;
    return null;
}

function serializeTriStateValue(value) {
    if (value === true) return 'true';
    if (value === false) return 'false';
    return '';
}

function formatTransportLabel(transport) {
    if (transport === 'streamable_http') return 'HTTP';
    if (transport === 'custom') return 'Custom';
    return 'Stdio';
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
