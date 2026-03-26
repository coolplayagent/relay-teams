/**
 * components/settings/agentsSettings.js
 * External ACP agent settings panel bindings.
 */
import {
    deleteExternalAgent,
    fetchExternalAgent,
    fetchExternalAgents,
    fetchEnvironmentVariables,
    saveExternalAgent,
    testExternalAgent,
} from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const HIDDEN_APP_ENV_KEYS = new Set([
    'HTTP_PROXY',
    'http_proxy',
    'HTTPS_PROXY',
    'https_proxy',
    'ALL_PROXY',
    'all_proxy',
    'NO_PROXY',
    'no_proxy',
    'SSL_VERIFY',
    'ssl_verify',
]);

let agentSummaries = [];
let selectedAgentId = '';
let selectedSourceAgentId = '';
let currentTransport = 'stdio';
let currentStdioEnv = [];
let currentHttpHeaders = [];
let availableEnvironmentBindings = [];
let languageBound = false;

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
    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderAgentsList();
            renderBindingRows('stdio');
            renderBindingRows('http');
        });
        languageBound = true;
    }
}

export async function loadAgentSettingsPanel(preferredAgentId = '') {
    try {
        const [summaries, environmentBindings] = await Promise.all([
            fetchExternalAgents(),
            loadEnvironmentBindings(),
        ]);
        agentSummaries = Array.isArray(summaries) ? summaries.map(normalizeAgentSummary) : [];
        availableEnvironmentBindings = environmentBindings;
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
        renderEmptyAgentsList(
            t('settings.agents.load_failed'),
            error.message || t('settings.agents.load_failed_message'),
        );
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

async function loadEnvironmentBindings() {
    try {
        return normalizeEnvironmentBindings(await fetchEnvironmentVariables());
    } catch (error) {
        logError(
            'frontend.agents_settings.environment_bindings_load_failed',
            'Failed to load environment variables for agents settings',
            errorToPayload(error),
        );
        return [];
    }
}

function normalizeEnvironmentBindings(payload) {
    const bindings = new Map();
    normalizeEnvironmentScope(Array.isArray(payload?.app) ? payload.app : [], 'app', true).forEach(record => {
        bindings.set(record.key, record);
    });
    normalizeEnvironmentScope(Array.isArray(payload?.system) ? payload.system : [], 'system', false).forEach(record => {
        if (!bindings.has(record.key)) {
            bindings.set(record.key, record);
        }
    });
    return Array.from(bindings.values()).sort((left, right) =>
        left.key.localeCompare(right.key, undefined, {
            sensitivity: 'base',
        }),
    );
}

function normalizeEnvironmentScope(records, fallbackScope, hideAppKeys) {
    return records
        .map(record => ({
            key: String(record?.key || '').trim(),
            value: String(record?.value || ''),
            scope: String(record?.scope || fallbackScope).trim() || fallbackScope,
            value_kind: String(record?.value_kind || 'string').trim() || 'string',
        }))
        .filter(record => record.key && (!hideAppKeys || !HIDDEN_APP_ENV_KEYS.has(record.key)));
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
                            <span>${escapeHtml(agent.description || t('settings.agents.no_description'))}</span>
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
    container.className = 'agent-binding-list';
    if (!rows.length) {
        container.innerHTML = renderEmptyBindingState(kind);
        return;
    }

    container.innerHTML = rows
        .map((binding, index) =>
            kind === 'stdio'
                ? renderEnvironmentBindingRow(binding, index)
                : renderHttpHeaderBindingRow(binding, index),
        )
        .join('');

    if (kind === 'stdio') {
        container.querySelectorAll('.agent-binding-name-select').forEach(select => {
            select.onchange = event => updateBindingField(kind, event, 'name');
        });
    } else {
        container.querySelectorAll('.agent-binding-name-input').forEach(input => {
            input.oninput = event => updateBindingField(kind, event, 'name');
        });
        container.querySelectorAll('.agent-binding-value').forEach(input => {
            input.oninput = event => updateBindingField(kind, event, 'value');
        });
        container.querySelectorAll('.agent-binding-secret-select').forEach(select => {
            select.onchange = event => updateBindingField(kind, event, 'secret');
        });
    }
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
    if (kind === 'stdio' && field === 'name') {
        const nextName = String(target?.value || '').trim();
        const matchedBinding = resolveEnvironmentBinding(nextName);
        rows[index] = matchedBinding
            ? createEnvironmentBinding(matchedBinding)
            : {
                ...current,
                name: nextName,
            };
        renderBindingRows('stdio');
        return;
    }
    if (field === 'secret') {
        current.secret = String(target?.value || '').trim() === 'true';
        if (!current.secret) {
            current.configured = false;
        }
        renderBindingRows(kind);
    } else {
        current[field] = String(target?.value || '');
        if (field === 'value' && current.secret) {
            current.configured = current.configured || !!String(target?.value || '').trim();
        }
    }
}

function addBindingRow(kind) {
    const rows = kind === 'stdio' ? currentStdioEnv : currentHttpHeaders;
    if (kind === 'stdio') {
        if (!availableEnvironmentBindings.length) {
            showToast({
                title: t('settings.agents.no_env_options'),
                message: t('settings.agents.no_env_options_copy'),
                tone: 'warning',
            });
            renderBindingRows('stdio');
            return;
        }
        const nextBinding = availableEnvironmentBindings.find(option =>
            !rows.some(item => item.name === option.key),
        ) || availableEnvironmentBindings[0];
        rows.push(createEnvironmentBinding(nextBinding));
        renderBindingRows('stdio');
        return;
    }
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
    title = t('settings.agents.none'),
    description = t('settings.agents.none_copy'),
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
            title: t('settings.agents.saved'),
            message: `${saved.agent_id} ${t('settings.agents.saved_message')}`,
            tone: 'success',
        });
        await loadAgentSettingsPanel(saved.agent_id);
        renderAgentStatus(t('settings.agents.saved_status'), 'success');
    } catch (error) {
        renderAgentStatus(error.message || t('settings.agents.save_failed_message'), 'danger');
        showToast({
            title: t('settings.agents.save_failed'),
            message: error.message || t('settings.agents.save_failed_message'),
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
        renderAgentStatus(result.message || t('settings.agents.test_passed_message'), 'success');
        showToast({
            title: t('settings.agents.test_passed'),
            message: result.message || `${saved.agent_id} ${t('settings.agents.test_passed_detail')}`,
            tone: 'success',
        });
    } catch (error) {
        renderAgentStatus(error.message || t('settings.agents.test_failed_message'), 'danger');
        showToast({
            title: t('settings.agents.test_failed'),
            message: error.message || t('settings.agents.test_failed_message'),
            tone: 'danger',
        });
    }
}

async function handleDeleteAgent() {
    const agentId = String(selectedSourceAgentId || getInputValue('agent-id-input')).trim();
    if (!agentId) {
        renderAgentStatus(t('settings.agents.select_to_delete'), 'danger');
        return;
    }
    try {
        await deleteExternalAgent(agentId);
        selectedAgentId = '';
        selectedSourceAgentId = '';
        showToast({
            title: t('settings.agents.deleted'),
            message: `${agentId} ${t('settings.agents.deleted_message')}`,
            tone: 'success',
        });
        await loadAgentSettingsPanel();
    } catch (error) {
        renderAgentStatus(error.message || t('settings.agents.delete_failed_message'), 'danger');
        showToast({
            title: t('settings.agents.delete_failed'),
            message: error.message || t('settings.agents.delete_failed_message'),
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
        throw new Error(t('settings.agents.id_required'));
    }
    const name = String(getInputValue('agent-name-input')).trim();
    if (!name) {
        throw new Error(t('settings.agents.name_required'));
    }
    const description = String(getInputValue('agent-description-input')).trim();
    const transport = String(getInputValue('agent-transport-input')).trim() || 'stdio';
    if (transport === 'streamable_http') {
        const url = String(getInputValue('agent-http-url-input')).trim();
        if (!url) {
            throw new Error(t('settings.agents.http_url_required'));
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
            throw new Error(t('settings.agents.custom_adapter_required'));
        }
        return {
            agent_id: agentId,
            name,
            description,
            transport: {
                transport: 'custom',
                adapter_id: adapterId,
                config: parseJsonObject(
                    getInputValue('agent-custom-config-input'),
                    t('settings.agents.custom_config'),
                ),
            },
        };
    }
    const command = String(getInputValue('agent-stdio-command-input')).trim();
    if (!command) {
        throw new Error(t('settings.agents.stdio_command_required'));
    }
    return {
        agent_id: agentId,
        name,
        description,
        transport: {
            transport: 'stdio',
            command,
            args: parseLineList(getInputValue('agent-stdio-args-input')),
            env: normalizeBindingsForSave(syncEnvironmentBindings(currentStdioEnv)),
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
    let parsed;
    try {
        parsed = JSON.parse(source);
    } catch (_) {
        throw new Error(`${label} ${t('settings.agents.json_invalid')}`);
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error(`${label} ${t('settings.agents.json_object_required')}`);
    }
    return parsed;
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
    if (transport === 'streamable_http') return t('settings.agents.transport_http_label');
    if (transport === 'custom') return t('settings.agents.transport_custom_label');
    return t('settings.agents.transport_stdio_label');
}

function renderEmptyBindingState(kind) {
    if (kind === 'stdio' && !availableEnvironmentBindings.length) {
        return `
            <div class="role-option-empty agent-binding-empty">
                <div>${escapeHtml(t('settings.agents.no_env_options'))}</div>
                <div class="agent-binding-empty-note">${escapeHtml(t('settings.agents.no_env_options_copy'))}</div>
            </div>
        `;
    }
    if (kind === 'stdio') {
        return `<div class="role-option-empty agent-binding-empty">${escapeHtml(t('settings.agents.no_env_bindings'))}</div>`;
    }
    return `<div class="role-option-empty agent-binding-empty">${escapeHtml(t('settings.agents.no_headers'))}</div>`;
}

function renderEnvironmentBindingRow(binding, index) {
    const selectedName = String(binding?.name || '').trim();
    return `
        <div class="agent-binding-row agent-env-binding-row" data-kind="stdio" data-index="${index}">
            <div class="agent-env-binding-main">
                <select class="agent-binding-name-select agent-env-binding-select" data-kind="stdio" data-index="${index}" aria-label="${escapeHtml(t('settings.agents.select_env'))}">
                    ${renderEnvironmentBindingOptions(selectedName)}
                </select>
                <div class="agent-env-binding-meta">${escapeHtml(formatEnvironmentBindingMeta(selectedName))}</div>
            </div>
            <button class="secondary-btn section-action-btn agent-binding-remove-btn agent-env-binding-remove" data-kind="stdio" data-index="${index}" type="button">${escapeHtml(t('settings.agents.action_remove'))}</button>
        </div>
    `;
}

function renderEnvironmentBindingOptions(selectedName) {
    const normalizedName = String(selectedName || '').trim();
    const options = [];
    if (normalizedName && !resolveEnvironmentBinding(normalizedName)) {
        options.push(
            `<option value="${escapeHtml(normalizedName)}" selected>${escapeHtml(`${normalizedName} · ${t('settings.agents.env_missing')}`)}</option>`,
        );
    }
    availableEnvironmentBindings.forEach(binding => {
        const isSelected = binding.key === normalizedName;
        options.push(
            `<option value="${escapeHtml(binding.key)}"${isSelected ? ' selected' : ''}>${escapeHtml(formatEnvironmentBindingOptionLabel(binding))}</option>`,
        );
    });
    return options.join('');
}

function renderHttpHeaderBindingRow(binding, index) {
    return `
        <div class="agent-binding-row" data-kind="http" data-index="${index}">
            <div class="form-group">
                <label>${escapeHtml(t('settings.agents.header_name'))}</label>
                <input type="text" class="agent-binding-name-input" data-kind="http" data-index="${index}" value="${escapeHtml(binding.name)}" autocomplete="off">
            </div>
            <div class="form-group">
                <label>${escapeHtml(t('settings.agents.header_value'))}</label>
                <input type="${binding.secret ? 'password' : 'text'}" class="agent-binding-value" data-kind="http" data-index="${index}" value="${escapeHtml(binding.value || '')}" autocomplete="off" placeholder="${binding.configured ? escapeHtml(t('settings.agents.secret_configured')) : ''}">
            </div>
            <div class="form-group">
                <label>${escapeHtml(t('settings.agents.secret_mode'))}</label>
                <select class="agent-binding-secret-select" data-kind="http" data-index="${index}">
                    <option value="false"${binding.secret ? '' : ' selected'}>${escapeHtml(t('settings.agents.secret_plain'))}</option>
                    <option value="true"${binding.secret ? ' selected' : ''}>${escapeHtml(t('settings.agents.secret_keyring'))}</option>
                </select>
            </div>
            <div class="form-group">
                <label>${escapeHtml(t('settings.agents.action_label'))}</label>
                <button class="secondary-btn section-action-btn agent-binding-remove-btn" data-kind="http" data-index="${index}" type="button">${escapeHtml(t('settings.agents.action_remove'))}</button>
            </div>
        </div>
    `;
}

function resolveEnvironmentBinding(name) {
    const normalizedName = String(name || '').trim();
    return availableEnvironmentBindings.find(binding => binding.key === normalizedName) || null;
}

function createEnvironmentBinding(binding) {
    return {
        name: String(binding?.key || '').trim(),
        value: String(binding?.value || ''),
        secret: false,
        configured: false,
    };
}

function syncEnvironmentBindings(bindings) {
    return (Array.isArray(bindings) ? bindings : []).map(binding => {
        const normalizedBinding = normalizeBinding(binding);
        const matchedBinding = resolveEnvironmentBinding(normalizedBinding.name);
        return matchedBinding ? createEnvironmentBinding(matchedBinding) : normalizedBinding;
    });
}

function formatEnvironmentBindingMeta(bindingName) {
    const matchedBinding = resolveEnvironmentBinding(bindingName);
    if (!matchedBinding) {
        return t('settings.agents.env_missing_note');
    }
    const scopeLabel = matchedBinding.scope === 'system'
        ? t('settings.agents.env_scope_system')
        : t('settings.agents.env_scope_app');
    return `${scopeLabel} · ${formatEnvironmentValueKind(matchedBinding.value_kind)} · ${formatEnvironmentBindingValuePreview(matchedBinding.value)}`;
}

function formatEnvironmentBindingOptionLabel(binding) {
    return `${binding.key} = ${formatEnvironmentBindingValuePreview(binding.value)}`;
}

function formatEnvironmentBindingValuePreview(value) {
    const normalizedValue = String(value || '').replace(/\s+/g, ' ').trim();
    if (!normalizedValue) {
        return '""';
    }
    return normalizedValue.length > 72
        ? `${normalizedValue.slice(0, 69)}...`
        : normalizedValue;
}

function formatEnvironmentValueKind(valueKind) {
    const normalizedKind = String(valueKind || 'string').trim().toLowerCase();
    if (normalizedKind === 'secret') {
        return t('settings.agents.env_value_kind_secret');
    }
    if (normalizedKind === 'masked') {
        return t('settings.agents.env_value_kind_masked');
    }
    return t('settings.agents.env_value_kind_string');
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
