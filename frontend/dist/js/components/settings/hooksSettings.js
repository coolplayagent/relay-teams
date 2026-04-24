/**
 * components/settings/hooksSettings.js
 * Merged user hooks cards and runtime view.
 */
import {
    fetchHooksConfig,
    fetchHookRuntimeView,
    saveHooksConfig,
    validateHooksConfig,
} from '../../core/api.js';
import { showAlertDialog } from '../../utils/feedback.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const EVENT_OPTIONS = [
    'SessionStart',
    'SessionEnd',
    'UserPromptSubmit',
    'PreToolUse',
    'PermissionRequest',
    'PostToolUse',
    'PostToolUseFailure',
    'Stop',
    'StopFailure',
    'SubagentStart',
    'SubagentStop',
    'TaskCreated',
    'TaskCompleted',
    'PreCompact',
    'PostCompact',
];

const MATCHER_UNSUPPORTED_EVENTS = new Set([
    'UserPromptSubmit',
    'Stop',
    'TaskCreated',
    'TaskCompleted',
]);

const TOOL_EVENTS = new Set([
    'PreToolUse',
    'PermissionRequest',
    'PostToolUse',
    'PostToolUseFailure',
]);

const COMMAND_ONLY_EVENTS = new Set(['SessionStart']);
const COMMAND_HTTP_ONLY_EVENTS = new Set([
    'SessionEnd',
    'StopFailure',
    'SubagentStart',
    'PreCompact',
    'PostCompact',
]);

let latestRuntimeView = null;
let editorGroups = [];
let latestLoadErrorMessage = '';
let latestRuntimeLoadErrorMessage = '';
let latestConfigMessage = '';
let latestConfigMessageTone = 'info';
let loadInFlight = false;
let handlersBound = false;
let activeHooksLoadRequestId = 0;
let nextGroupId = 1;
let nextHandlerId = 1;
let editingGroupId = null;
let groupEditSnapshots = new Map();

export function bindHooksSettingsHandlers() {
    if (handlersBound || typeof document?.addEventListener !== 'function') {
        return;
    }
    document.addEventListener('agent-teams-language-changed', () => {
        renderHooksPanel();
    });
    document.addEventListener('click', handleHooksClick);
    document.addEventListener('input', handleHooksInput);
    document.addEventListener('change', handleHooksInput);
    const addButton = document.getElementById('add-hook-btn');
    if (addButton) {
        addButton.addEventListener('click', () => {
            const group = createDefaultGroup();
            editorGroups = [...editorGroups, group];
            editingGroupId = group.id;
            latestConfigMessage = '';
            renderHooksPanel();
        });
    }
    const validateButton = document.getElementById('validate-hooks-btn');
    if (validateButton) {
        validateButton.addEventListener('click', () => {
            void validateCurrentHooksConfig();
        });
    }
    const saveButton = document.getElementById('save-hooks-btn');
    if (saveButton) {
        saveButton.addEventListener('click', () => {
            void saveCurrentHooksConfig();
        });
    }
    handlersBound = true;
}

export async function loadHooksSettingsPanel() {
    const requestId = ++activeHooksLoadRequestId;
    loadInFlight = true;
    latestLoadErrorMessage = '';
    latestRuntimeLoadErrorMessage = '';
    latestConfigMessage = '';
    renderHooksPanel();
    const configPromise = fetchHooksConfig();
    const runtimeViewPromise = fetchHookRuntimeView();
    try {
        const config = await configPromise;
        if (requestId !== activeHooksLoadRequestId) {
            return;
        }
        editorGroups = deserializeHooksConfig(config);
        latestRuntimeLoadErrorMessage = '';
        editingGroupId = null;
        groupEditSnapshots = new Map();
        try {
            const runtimeView = await runtimeViewPromise;
            if (requestId !== activeHooksLoadRequestId) {
                return;
            }
            latestRuntimeView = runtimeView;
        } catch (e) {
            if (requestId !== activeHooksLoadRequestId) {
                return;
            }
            latestRuntimeView = null;
            latestRuntimeLoadErrorMessage =
                e?.message || t('settings.hooks.runtime_load_failed');
            logError(
                'frontend.hooks_settings.runtime_load_failed',
                'Failed to load hooks runtime view',
                errorToPayload(e),
            );
        }
    } catch (e) {
        void runtimeViewPromise.catch(() => {});
        if (requestId !== activeHooksLoadRequestId) {
            return;
        }
        latestLoadErrorMessage = e?.message || t('settings.hooks.load_failed');
        latestRuntimeLoadErrorMessage = '';
        latestRuntimeView = null;
        editorGroups = [];
        editingGroupId = null;
        groupEditSnapshots = new Map();
        logError(
            'frontend.hooks_settings.load_failed',
            'Failed to load hooks settings',
            errorToPayload(e),
        );
    } finally {
        if (requestId !== activeHooksLoadRequestId) {
            return;
        }
        loadInFlight = false;
        renderHooksPanel();
    }
}

async function handleHooksClick(event) {
    const target = event?.target;
    if (!target?.closest) {
        return;
    }
    const actionTarget = target.closest('[data-hooks-action]');
    if (!actionTarget) {
        return;
    }
    const action = actionTarget.dataset.hooksAction;
    if (!action) {
        return;
    }
    if (action === 'edit-group') {
        const groupId = Number(actionTarget.dataset.groupId || '0');
        if (editingGroupId === groupId) {
            cancelEditingGroup(groupId);
        } else {
            if (editingGroupId) {
                cancelEditingGroup(editingGroupId);
            }
            startEditingGroup(groupId);
        }
        latestConfigMessage = '';
        renderHooksPanel();
        return;
    }
    if (action === 'remove-group') {
        const groupId = Number(actionTarget.dataset.groupId || '0');
        editorGroups = editorGroups.filter(group => group.id !== groupId);
        if (editingGroupId === groupId) {
            editingGroupId = null;
        }
        groupEditSnapshots.delete(groupId);
        latestConfigMessage = '';
        renderHooksPanel();
        return;
    }
    if (action === 'add-handler') {
        const groupId = Number(actionTarget.dataset.groupId || '0');
        editorGroups = editorGroups.map(group => {
            if (group.id !== groupId) {
                return group;
            }
            return {
                ...group,
                handlers: [...group.handlers, createDefaultHandler(group.event_name)],
            };
        });
        latestConfigMessage = '';
        renderHooksPanel();
        return;
    }
    if (action === 'remove-handler') {
        const groupId = Number(actionTarget.dataset.groupId || '0');
        const handlerId = Number(actionTarget.dataset.handlerId || '0');
        editorGroups = editorGroups.map(group => {
            if (group.id !== groupId) {
                return group;
            }
            return {
                ...group,
                handlers: group.handlers.filter(handler => handler.id !== handlerId),
            };
        });
        latestConfigMessage = '';
        renderHooksPanel();
        return;
    }
    if (action === 'validate-config') {
        await validateCurrentHooksConfig();
        return;
    }
    if (action === 'save-config') {
        await saveCurrentHooksConfig();
    }
}

function handleHooksInput(event) {
    const target = event?.target;
    if (!target?.dataset) {
        return;
    }
    const field = target.dataset.hooksField;
    if (!field) {
        return;
    }
    const groupId = Number(target.dataset.groupId || '0');
    const handlerId = Number(target.dataset.handlerId || '0');
    latestConfigMessage = '';
    if (handlerId) {
        updateHandlerField(groupId, handlerId, field, readInputValue(target));
    } else {
        updateGroupField(groupId, field, readInputValue(target));
    }
    if (shouldRerenderAfterInput(field, handlerId)) {
        renderHooksPanel();
    }
}

async function validateCurrentHooksConfig() {
    try {
        await validateHooksConfig(serializeHooksConfig(editorGroups));
        latestConfigMessageTone = 'success';
        latestConfigMessage = t('settings.hooks.validate_success');
        await showAlertDialog({
            title: t('settings.hooks.validate_result_title'),
            message: latestConfigMessage,
            tone: 'success',
        });
    } catch (e) {
        latestConfigMessageTone = 'error';
        const errorReason = resolveHooksErrorReason(e);
        latestConfigMessage = errorReason
            ? formatMessage('settings.hooks.validate_failed_detail', { error: errorReason })
            : t('settings.hooks.validate_failed');
        logError(
            'frontend.hooks_settings.validate_failed',
            'Failed to validate hooks config',
            errorToPayload(e),
        );
        await showAlertDialog({
            title: t('settings.hooks.validate_result_title'),
            message: latestConfigMessage,
            tone: 'error',
        });
    }
    renderHooksPanel();
}

async function saveCurrentHooksConfig() {
    try {
        const payload = serializeHooksConfig(editorGroups);
        await validateHooksConfig(payload);
        await saveHooksConfig(payload);
        latestConfigMessageTone = 'success';
        latestConfigMessage = t('settings.hooks.save_success');
        editorGroups = editorGroups.map(group => ({ ...group, isNew: false }));
        editingGroupId = null;
        groupEditSnapshots = new Map();
        try {
            latestRuntimeView = await fetchHookRuntimeView();
            latestRuntimeLoadErrorMessage = '';
        } catch (e) {
            latestRuntimeView = null;
            latestRuntimeLoadErrorMessage =
                e?.message || t('settings.hooks.runtime_load_failed');
            logError(
                'frontend.hooks_settings.runtime_load_failed',
                'Failed to refresh hooks runtime view after save',
                errorToPayload(e),
            );
        }
        await showAlertDialog({
            title: t('settings.hooks.save_result_title'),
            message: latestConfigMessage,
            tone: 'success',
        });
    } catch (e) {
        latestConfigMessageTone = 'error';
        const errorReason = resolveHooksErrorReason(e);
        latestConfigMessage = errorReason
            ? formatMessage('settings.hooks.save_failed_detail', { error: errorReason })
            : t('settings.hooks.save_failed');
        logError(
            'frontend.hooks_settings.save_failed',
            'Failed to save hooks config',
            errorToPayload(e),
        );
        await showAlertDialog({
            title: t('settings.hooks.save_result_title'),
            message: latestConfigMessage,
            tone: 'error',
        });
    }
    renderHooksPanel();
}

function renderHooksPanel() {
    const host = document.getElementById('hooks-runtime-status');
    if (!host) {
        return;
    }
    if (loadInFlight) {
        host.innerHTML = renderLoadingState();
        return;
    }
    if (latestLoadErrorMessage) {
        host.innerHTML = renderEmptyState(
            t('settings.hooks.load_failed'),
            formatMessage('settings.hooks.load_failed_detail', {
                error: latestLoadErrorMessage,
            }),
        );
        return;
    }
    host.innerHTML = renderMergedHooksShell();
}

function startEditingGroup(groupId) {
    const group = editorGroups.find(item => item.id === groupId);
    if (!group) {
        editingGroupId = null;
        return;
    }
    groupEditSnapshots.set(groupId, cloneGroup(group));
    editingGroupId = groupId;
}

function cancelEditingGroup(groupId) {
    const snapshot = groupEditSnapshots.get(groupId);
    if (snapshot) {
        editorGroups = editorGroups.map(group => group.id === groupId ? cloneGroup(snapshot) : group);
    } else {
        editorGroups = editorGroups.filter(group => group.id !== groupId);
    }
    groupEditSnapshots.delete(groupId);
    if (editingGroupId === groupId) {
        editingGroupId = null;
    }
}

function renderMergedHooksShell() {
    const groupedMarkup = renderGroupedHooksMarkup();
    return `
        <section class="proxy-form-section">
            <div class="settings-content-stack hooks-config-editor">
                ${renderRuntimeLoadWarning()}
                ${groupedMarkup || renderNoHooksState()}
            </div>
        </section>
    `;
}

function renderRuntimeLoadWarning() {
    if (!latestRuntimeLoadErrorMessage) {
        return '';
    }
    return `
        <div class="status-message warning">
            <strong>${escapeHtml(t('settings.hooks.runtime_load_failed'))}</strong>
            <div>${escapeHtml(
                formatMessage('settings.hooks.runtime_load_failed_detail', {
                    error: latestRuntimeLoadErrorMessage,
                }),
            )}</div>
        </div>
    `;
}

function renderGroupedHooksMarkup() {
    const grouped = new Map();
    editorGroups.forEach(group => {
        const eventName = normalizeString(group?.event_name) || 'PreToolUse';
        const bucket = grouped.get(eventName) || [];
        bucket.push(renderUserHookCard(group));
        grouped.set(eventName, bucket);
    });
    getRuntimeOnlyHooks().forEach(hook => {
        const eventName = normalizeString(hook?.event_name) || t('settings.hooks.unnamed');
        const bucket = grouped.get(eventName) || [];
        bucket.push(renderHookCard(hook));
        grouped.set(eventName, bucket);
    });
    const orderedEventNames = [
        ...EVENT_OPTIONS.filter(eventName => grouped.has(eventName)),
        ...Array.from(grouped.keys()).filter(eventName => !EVENT_OPTIONS.includes(eventName)),
    ];
    return orderedEventNames
        .map(eventName => renderEventGroup(eventName, grouped.get(eventName) || []))
        .join('');
}

function renderEventGroup(eventName, cards) {
    if (!Array.isArray(cards) || cards.length === 0) {
        return '';
    }
    return `
        <section class="proxy-form-section">
            <div class="proxy-form-section-header">
                <h5>${escapeHtml(eventName)}</h5>
            </div>
            <div class="settings-content-stack hooks-config-editor">
                ${cards.join('')}
            </div>
        </section>
    `;
}

function renderNoHooksState() {
    return `
        <div class="settings-empty-state settings-empty-state-compact">
            <h4>${escapeHtml(t('settings.hooks.empty'))}</h4>
            <p>${escapeHtml(t('settings.hooks.empty_copy'))}</p>
        </div>
    `;
}

function renderUserHookCard(group) {
    if (editingGroupId === group.id) {
        return renderGroupEditor(group);
    }
    const matcherValue = MATCHER_UNSUPPORTED_EVENTS.has(group.event_name)
        ? t('settings.hooks.matcher_not_supported')
        : group.matcher;
    const typeSummary = group.handlers.map(handler => handler.type).join(', ');
    const detailRows = buildDetailRows([
        renderDetailItem(t('settings.hooks.trigger'), group.event_name),
        renderDetailItem(t('settings.hooks.matcher'), matcherValue),
        renderDetailItem(t('settings.hooks.handler_summary'), String(group.handlers.length)),
        renderDetailItem(t('settings.hooks.type'), typeSummary || t('settings.hooks.all')),
    ]);
    return `
        <section class="mcp-status-card hooks-runtime-card hooks-runtime-card-editable">
            <div class="mcp-status-card-header">
                <div class="mcp-status-card-heading">
                    <div class="mcp-status-card-name">${escapeHtml(resolveGroupTitle(group))}</div>
                </div>
                <div class="settings-inline-actions">
                    <button class="secondary-btn section-action-btn" type="button" data-hooks-action="edit-group" data-group-id="${group.id}">${escapeHtml(t('settings.hooks.edit_group'))}</button>
                    <button class="secondary-btn section-action-btn" type="button" data-hooks-action="remove-group" data-group-id="${group.id}">${escapeHtml(t('settings.hooks.delete_group'))}</button>
                </div>
            </div>
            <div class="hooks-runtime-detail-list status-list">
                ${detailRows.join('')}
            </div>
        </section>
    `;
}

function renderGroupEditor(group) {
    const matcherSupported = !MATCHER_UNSUPPORTED_EVENTS.has(group.event_name);
    const eventFieldMarkup = group.isNew
        ? renderSelectField({
            groupId: group.id,
            field: 'event_name',
            label: t('settings.hooks.event_name'),
            options: EVENT_OPTIONS,
            value: group.event_name,
        })
        : renderStaticField(t('settings.hooks.event_name'), group.event_name);
    return `
        <section class="mcp-status-card hooks-config-card hooks-config-card-editing">
            <div class="mcp-status-card-header">
                <div class="mcp-status-card-heading">
                    <div class="mcp-status-card-name">${escapeHtml(resolveGroupTitle(group))}</div>
                </div>
                <div class="settings-inline-actions">
                    <button class="secondary-btn section-action-btn" type="button" data-hooks-action="edit-group" data-group-id="${group.id}">${escapeHtml(t('settings.action.cancel'))}</button>
                    <button class="secondary-btn section-action-btn" type="button" data-hooks-action="remove-group" data-group-id="${group.id}">${escapeHtml(t('settings.hooks.delete_group'))}</button>
                </div>
            </div>
            <div class="proxy-form-grid">
                ${eventFieldMarkup}
                ${matcherSupported ? renderTextField({
                    groupId: group.id,
                    field: 'matcher',
                    label: t('settings.hooks.matcher'),
                    value: group.matcher,
                    placeholder: '*',
                }) : renderStaticField(t('settings.hooks.matcher'), t('settings.hooks.matcher_not_supported'))}
            </div>
            <div class="hooks-handler-stack">
                <div class="proxy-form-section-header">
                    <h6>${escapeHtml(t('settings.hooks.handlers'))}</h6>
                    <button class="secondary-btn section-action-btn" type="button" data-hooks-action="add-handler" data-group-id="${group.id}">${escapeHtml(t('settings.hooks.add_handler'))}</button>
                </div>
                ${group.handlers.map(handler => renderHandlerEditor(group, handler)).join('')}
            </div>
        </section>
    `;
}

function renderHandlerEditor(group, handler) {
    const supportsIf = TOOL_EVENTS.has(group.event_name);
    const allowedTypes = getAllowedHandlerTypes(group.event_name);
    return `
        <div class="hooks-handler-card">
            ${renderTextField({
                groupId: group.id,
                handlerId: handler.id,
                field: 'name',
                label: t('settings.hooks.name'),
                value: handler.name,
                span2: true,
            })}
            <div class="proxy-form-grid">
                ${renderSelectField({
                    groupId: group.id,
                    handlerId: handler.id,
                    field: 'type',
                    label: t('settings.hooks.type'),
                    options: allowedTypes,
                    value: handler.type,
                })}
                ${supportsIf ? renderTextField({
                    groupId: group.id,
                    handlerId: handler.id,
                    field: 'if',
                    label: t('settings.hooks.if_rule'),
                    value: handler.if,
                    placeholder: 'Bash(git *)',
                }) : renderStaticField(t('settings.hooks.if_rule'), t('settings.hooks.if_not_supported'))}
                ${renderTextField({
                    groupId: group.id,
                    handlerId: handler.id,
                    field: 'timeout',
                    label: t('settings.hooks.timeout'),
                    value: String(handler.timeout),
                })}
                ${renderBooleanField({
                    groupId: group.id,
                    handlerId: handler.id,
                    field: 'async',
                    label: t('settings.hooks.run_async'),
                    value: handler.async,
                })}
                ${renderSelectField({
                    groupId: group.id,
                    handlerId: handler.id,
                    field: 'on_error',
                    label: t('settings.hooks.on_error'),
                    options: ['ignore', 'fail'],
                    value: handler.on_error,
                })}
                ${renderTypeSpecificHandlerFields(group, handler)}
            </div>
            <div class="settings-inline-actions">
                <button class="secondary-btn section-action-btn" type="button" data-hooks-action="remove-handler" data-group-id="${group.id}" data-handler-id="${handler.id}">${escapeHtml(t('settings.hooks.delete_handler'))}</button>
            </div>
        </div>
    `;
}

function renderTypeSpecificHandlerFields(group, handler) {
    if (handler.type === 'command') {
        return renderTextField({
            groupId: group.id,
            handlerId: handler.id,
            field: 'command',
            label: t('settings.hooks.command'),
            value: handler.command,
        });
    }
    if (handler.type === 'http') {
        return `
            ${renderTextField({
                groupId: group.id,
                handlerId: handler.id,
                field: 'url',
                label: t('settings.hooks.url'),
                value: handler.url,
            })}
            ${renderTextareaField({
                groupId: group.id,
                handlerId: handler.id,
                field: 'headers',
                label: t('settings.hooks.headers'),
                value: handler.headers,
                placeholder: '{\n  "Authorization": "Bearer ..."\n}',
            })}
        `;
    }
    if (handler.type === 'prompt') {
        return `
            ${renderTextareaField({
                groupId: group.id,
                handlerId: handler.id,
                field: 'prompt',
                label: t('settings.hooks.prompt'),
                value: handler.prompt,
            })}
            ${renderTextField({
                groupId: group.id,
                handlerId: handler.id,
                field: 'model',
                label: t('settings.hooks.model'),
                value: handler.model,
            })}
        `;
    }
    return `
        ${renderTextField({
            groupId: group.id,
            handlerId: handler.id,
            field: 'role_id',
            label: t('settings.hooks.role_id'),
            value: handler.role_id,
        })}
        ${renderTextField({
            groupId: group.id,
            handlerId: handler.id,
            field: 'model',
            label: t('settings.hooks.model'),
            value: handler.model,
        })}
        ${renderTextareaField({
            groupId: group.id,
            handlerId: handler.id,
            field: 'prompt',
            label: t('settings.hooks.prompt'),
            value: handler.prompt,
        })}
    `;
}

function renderHookCard(hook) {
    const detailRows = buildRuntimeHookRows(hook);
    return `
        <section class="mcp-status-card hooks-runtime-card">
            <div class="mcp-status-card-header">
                <div class="mcp-status-card-heading">
                    <div class="mcp-status-card-name">${escapeHtml(resolveHookName(hook))}</div>
                </div>
            </div>
            <div class="hooks-runtime-detail-list status-list">
                ${detailRows.join('')}
            </div>
        </section>
    `;
}

function buildRuntimeHookRows(hook) {
    const trigger = typeof hook?.event_name === 'string' ? hook.event_name : t('settings.hooks.all');
    const handlerType = typeof hook?.handler_type === 'string' ? hook.handler_type : t('settings.hooks.all');
    const matcher = typeof hook?.matcher === 'string' && hook.matcher ? hook.matcher : '*';
    return buildDetailRows([
        renderDetailItem(t('settings.hooks.trigger'), trigger),
        renderDetailItem(t('settings.hooks.matcher'), matcher),
        renderDetailItem(t('settings.hooks.type'), handlerType),
        renderOptionalDetailItem(t('settings.hooks.if_rule'), hook?.if ?? hook?.if_rule),
    ]);
}

function buildDetailRows(items) {
    const normalizedItems = items.filter(Boolean);
    const rows = [];
    for (let index = 0; index < normalizedItems.length; index += 2) {
        rows.push(`
            <div class="hooks-runtime-detail-row status-list-row">
                ${normalizedItems[index]}
                ${normalizedItems[index + 1] || ''}
            </div>
        `);
    }
    return rows;
}

function renderDetailItem(label, value) {
    const displayValue =
        typeof value === 'boolean'
            ? value
                ? t('settings.hooks.enabled')
                : t('settings.hooks.disabled')
            : value || t('settings.hooks.all');
    return `
        <div class="hooks-runtime-detail-item status-list-copy">
            <div class="hooks-runtime-detail-label status-list-name">${escapeHtml(label)}</div>
            <div class="hooks-runtime-detail-value status-list-description">${escapeHtml(displayValue)}</div>
        </div>
    `;
}

function renderOptionalDetailItem(label, value) {
    if (typeof value !== 'string' || !value.trim()) {
        return '';
    }
    return renderDetailItem(label, value.trim());
}

function renderOptionalListDetailItem(label, values) {
    if (!Array.isArray(values)) {
        return '';
    }
    const normalizedValues = values
        .filter(value => typeof value === 'string')
        .map(value => value.trim())
        .filter(Boolean);
    if (normalizedValues.length === 0) {
        return '';
    }
    return renderDetailItem(label, normalizedValues.join(', '));
}

function renderTextField({ groupId, handlerId = 0, field, label, value, placeholder = '', span2 = false }) {
    return `
        <div class="form-group${span2 ? ' form-group-span-2' : ''}">
            <label>${escapeHtml(label)}</label>
            <input
                type="text"
                data-hooks-field="${escapeHtml(field)}"
                data-group-id="${groupId}"
                data-handler-id="${handlerId}"
                value="${escapeHtml(value || '')}"
                placeholder="${escapeHtml(placeholder)}"
                spellcheck="false"
            >
        </div>
    `;
}

function renderTextareaField({ groupId, handlerId = 0, field, label, value, placeholder = '' }) {
    return `
        <div class="form-group form-group-span-2">
            <label>${escapeHtml(label)}</label>
            <textarea
                class="config-textarea"
                data-hooks-field="${escapeHtml(field)}"
                data-group-id="${groupId}"
                data-handler-id="${handlerId}"
                placeholder="${escapeHtml(placeholder)}"
                rows="4"
                spellcheck="false"
            >${escapeHtml(value || '')}</textarea>
        </div>
    `;
}

function renderBooleanField({ groupId, handlerId = 0, field, label, value }) {
    return `
        <div class="form-group">
            <label>${escapeHtml(label)}</label>
            <select
                data-hooks-field="${escapeHtml(field)}"
                data-group-id="${groupId}"
                data-handler-id="${handlerId}"
            >
                <option value="true" ${value ? 'selected' : ''}>${escapeHtml(t('settings.hooks.enabled'))}</option>
                <option value="false" ${value ? '' : 'selected'}>${escapeHtml(t('settings.hooks.disabled'))}</option>
            </select>
        </div>
    `;
}

function renderSelectField({ groupId, handlerId = 0, field, label, options, value }) {
    return `
        <div class="form-group">
            <label>${escapeHtml(label)}</label>
            <select
                data-hooks-field="${escapeHtml(field)}"
                data-group-id="${groupId}"
                data-handler-id="${handlerId}"
            >
                ${options.map(option => `<option value="${escapeHtml(option)}" ${option === value ? 'selected' : ''}>${escapeHtml(option)}</option>`).join('')}
            </select>
        </div>
    `;
}

function renderStaticField(label, value) {
    return `
        <div class="form-group">
            <label>${escapeHtml(label)}</label>
            <div class="status-list-description">${escapeHtml(value)}</div>
        </div>
    `;
}

function renderLoadingState() {
    return `
        <div class="settings-empty-state settings-empty-state-compact">
            <h4>${escapeHtml(t('settings.hooks.loading'))}</h4>
        </div>
    `;
}

function renderEmptyState(title, copy) {
    return `
        <div class="settings-empty-state">
            <h4>${escapeHtml(title)}</h4>
            <p>${escapeHtml(copy)}</p>
        </div>
    `;
}

function deserializeHooksConfig(config) {
    const groups = [];
    const hooks = config?.hooks && typeof config.hooks === 'object' ? config.hooks : {};
    for (const [eventName, rawGroups] of Object.entries(hooks)) {
        if (!Array.isArray(rawGroups)) {
            continue;
        }
        for (const rawGroup of rawGroups) {
            if (!rawGroup || typeof rawGroup !== 'object') {
                continue;
            }
            const handlers = Array.isArray(rawGroup.hooks) ? rawGroup.hooks : [];
            groups.push({
                id: nextGroupId++,
                isNew: false,
                event_name: eventName,
                matcher: normalizeMatcherForEvent(eventName, rawGroup.matcher),
                role_ids: normalizeStringList(rawGroup.role_ids),
                session_modes: normalizeStringList(rawGroup.session_modes),
                run_kinds: normalizeStringList(rawGroup.run_kinds),
                handlers: handlers.map(rawHandler => ({
                    id: nextHandlerId++,
                    type: getNormalizedHandlerType(rawHandler?.type, eventName),
                    name: normalizeString(rawHandler?.name),
                    if: TOOL_EVENTS.has(eventName) ? normalizeString(rawHandler?.if ?? rawHandler?.if_rule) : '',
                    timeout: normalizeNumber(rawHandler?.timeout ?? rawHandler?.timeout_seconds, 5),
                    async: Boolean(rawHandler?.async ?? rawHandler?.run_async),
                    on_error: normalizeString(rawHandler?.on_error) || 'ignore',
                    command: normalizeString(rawHandler?.command),
                    url: normalizeString(rawHandler?.url),
                    headers: serializeHeaders(rawHandler?.headers),
                    prompt: normalizeString(rawHandler?.prompt),
                    model: normalizeString(rawHandler?.model),
                    role_id: normalizeString(rawHandler?.role_id),
                })),
            });
        }
    }
    return groups;
}

function serializeHooksConfig(groups) {
    const hooks = {};
    for (const group of groups) {
        const normalizedEventName = normalizeString(group.event_name) || 'PreToolUse';
        const nextGroup = {
            matcher: normalizeMatcherForEvent(normalizedEventName, group.matcher),
            role_ids: normalizeStringList(group.role_ids),
            session_modes: normalizeStringList(group.session_modes),
            run_kinds: normalizeStringList(group.run_kinds),
            hooks: group.handlers.map(handler => serializeHandler(normalizedEventName, handler)),
        };
        if (MATCHER_UNSUPPORTED_EVENTS.has(normalizedEventName)) {
            delete nextGroup.matcher;
        }
        hooks[normalizedEventName] = hooks[normalizedEventName] || [];
        hooks[normalizedEventName].push(nextGroup);
    }
    return { hooks };
}

function serializeHandler(eventName, handler) {
    const serialized = {
        type: handler.type,
        name: normalizeString(handler.name),
        timeout: normalizeNumber(handler.timeout, 5),
        async: Boolean(handler.async),
        on_error: normalizeString(handler.on_error) || 'ignore',
    };
    if (TOOL_EVENTS.has(eventName) && normalizeString(handler.if)) {
        serialized.if = normalizeString(handler.if);
    }
    if (handler.type === 'command') {
        serialized.command = normalizeString(handler.command);
    } else if (handler.type === 'http') {
        serialized.url = normalizeString(handler.url);
        const parsedHeaders = parseHeaders(handler.headers);
        if (Object.keys(parsedHeaders).length > 0) {
            serialized.headers = parsedHeaders;
        }
    } else if (handler.type === 'prompt') {
        serialized.prompt = normalizeString(handler.prompt);
        if (normalizeString(handler.model)) {
            serialized.model = normalizeString(handler.model);
        }
    } else {
        serialized.role_id = normalizeString(handler.role_id);
        if (normalizeString(handler.prompt)) {
            serialized.prompt = normalizeString(handler.prompt);
        }
        if (normalizeString(handler.model)) {
            serialized.model = normalizeString(handler.model);
        }
    }
    return serialized;
}

function createDefaultGroup() {
    return {
        id: nextGroupId++,
        isNew: true,
        event_name: 'PreToolUse',
        matcher: '*',
        role_ids: [],
        session_modes: [],
        run_kinds: [],
        handlers: [createDefaultHandler('PreToolUse')],
    };
}

function createDefaultHandler(eventName) {
    const type = getAllowedHandlerTypes(eventName)[0];
    return {
        id: nextHandlerId++,
        type,
        name: '',
        if: '',
        timeout: 5,
        async: false,
        on_error: 'ignore',
        command: '',
        url: '',
        headers: '',
        prompt: '',
        model: '',
        role_id: '',
    };
}

function updateGroupField(groupId, field, rawValue) {
    editorGroups = editorGroups.map(group => {
        if (group.id !== groupId) {
            return group;
        }
        const nextGroup = { ...group };
        if (field === 'event_name') {
            nextGroup.event_name = normalizeString(rawValue) || 'PreToolUse';
            nextGroup.matcher = normalizeMatcherForEvent(nextGroup.event_name, nextGroup.matcher);
            nextGroup.handlers = nextGroup.handlers.map(handler => normalizeHandlerForEvent(handler, nextGroup.event_name));
            return nextGroup;
        }
        if (field === 'matcher') {
            nextGroup.matcher = normalizeMatcherForEvent(group.event_name, rawValue);
            return nextGroup;
        }
        if (field === 'role_ids' || field === 'session_modes' || field === 'run_kinds') {
            nextGroup[field] = parseCsvList(rawValue);
        }
        return nextGroup;
    });
}

function updateHandlerField(groupId, handlerId, field, rawValue) {
    editorGroups = editorGroups.map(group => {
        if (group.id !== groupId) {
            return group;
        }
        return {
            ...group,
            handlers: group.handlers.map(handler => {
                if (handler.id !== handlerId) {
                    return handler;
                }
                if (field === 'type') {
                    return normalizeHandlerForEvent(
                        { ...handler, type: rawValue },
                        group.event_name,
                    );
                }
                if (field === 'async') {
                    return {
                        ...handler,
                        async: rawValue === true || rawValue === 'true',
                    };
                }
                if (field === 'timeout') {
                    return { ...handler, timeout: normalizeNumber(rawValue, 5) };
                }
                return { ...handler, [field]: rawValue };
            }),
        };
    });
}

function normalizeHandlerForEvent(handler, eventName) {
    const allowedTypes = getAllowedHandlerTypes(eventName);
    const nextHandler = { ...handler };
    if (!allowedTypes.includes(nextHandler.type)) {
        nextHandler.type = allowedTypes[0];
    }
    if (!TOOL_EVENTS.has(eventName)) {
        nextHandler.if = '';
    }
    return nextHandler;
}

function getAllowedHandlerTypes(eventName) {
    if (COMMAND_ONLY_EVENTS.has(eventName)) {
        return ['command'];
    }
    if (COMMAND_HTTP_ONLY_EVENTS.has(eventName)) {
        return ['command', 'http'];
    }
    return ['command', 'http', 'prompt', 'agent'];
}

function normalizeMatcherForEvent(eventName, value) {
    if (MATCHER_UNSUPPORTED_EVENTS.has(eventName)) {
        return '*';
    }
    return normalizeString(value) || '*';
}

function getNormalizedHandlerType(value, eventName) {
    const candidate = normalizeString(value) || 'command';
    return getAllowedHandlerTypes(eventName).includes(candidate)
        ? candidate
        : getAllowedHandlerTypes(eventName)[0];
}

function resolveGroupTitle(group) {
    const namedHandlers = Array.isArray(group?.handlers)
        ? group.handlers
            .map(handler => normalizeString(handler?.name))
            .filter(Boolean)
        : [];
    if (namedHandlers.length === 1) {
        return namedHandlers[0];
    }
    if (namedHandlers.length > 1) {
        return namedHandlers.join(' / ');
    }
    return `${group.event_name} / ${group.handlers.length} ${t('settings.hooks.handlers_count_suffix')}`;
}

function resolveHookName(hook) {
    const name = typeof hook?.name === 'string' ? hook.name.trim() : '';
    return name || t('settings.hooks.unnamed');
}

function formatSourceScope(scope) {
    if (scope === 'project') {
        return t('settings.hooks.scope_project');
    }
    if (scope === 'project_local') {
        return t('settings.hooks.scope_project_local');
    }
    if (scope === 'user') {
        return t('settings.hooks.scope_user');
    }
    return t('settings.hooks.scope_unknown');
}

function normalizeString(value) {
    return typeof value === 'string' ? value.trim() : '';
}

function normalizeStringList(values) {
    if (!Array.isArray(values)) {
        return [];
    }
    return values
        .filter(value => typeof value === 'string')
        .map(value => value.trim())
        .filter(Boolean);
}

function parseCsvList(value) {
    return String(value || '')
        .split(',')
        .map(item => item.trim())
        .filter(Boolean);
}

function normalizeNumber(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function getRuntimeOnlyHooks() {
    const loadedHooks = Array.isArray(latestRuntimeView?.loaded_hooks)
        ? latestRuntimeView.loaded_hooks
        : [];
    return loadedHooks.filter(
        hook => String(hook?.source?.scope || '').trim() !== 'user',
    );
}

function serializeHeaders(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return '';
    }
    return JSON.stringify(value, null, 2);
}

function parseHeaders(value) {
    const raw = String(value || '').trim();
    if (!raw) {
        return {};
    }
    const errorMessage = t('settings.hooks.headers_invalid_json');
    try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new Error(errorMessage);
        }
        const normalizedEntries = Object.entries(parsed).map(([name, headerValue]) => {
            if (typeof headerValue !== 'string') {
                throw new Error(errorMessage);
            }
            return [name, headerValue.trim()];
        });
        return Object.fromEntries(normalizedEntries);
    } catch {
        throw new Error(errorMessage);
    }
}

function readInputValue(target) {
    if (target.type === 'checkbox') {
        return Boolean(target.checked);
    }
    return target.value;
}

function shouldRerenderAfterInput(field, handlerId) {
    if (handlerId) {
        return field === 'type';
    }
    return field === 'event_name';
}

function cloneGroup(group) {
    return {
        ...group,
        role_ids: Array.isArray(group?.role_ids) ? [...group.role_ids] : [],
        session_modes: Array.isArray(group?.session_modes) ? [...group.session_modes] : [],
        run_kinds: Array.isArray(group?.run_kinds) ? [...group.run_kinds] : [],
        handlers: Array.isArray(group?.handlers)
            ? group.handlers.map(handler => ({ ...handler }))
            : [],
    };
}

function resolveHooksErrorReason(error) {
    if (!error || typeof error !== 'object') {
        return '';
    }
    return formatHooksErrorValue(error.detail)
        || formatHooksErrorValue(error.message)
        || formatHooksErrorValue(error.error)
        || '';
}

function formatHooksErrorValue(value) {
    if (typeof value === 'string') {
        return value.trim();
    }
    if (Array.isArray(value)) {
        const parts = value.map(formatHooksErrorEntry).filter(Boolean);
        return parts.join('; ');
    }
    if (value && typeof value === 'object') {
        return formatHooksErrorValue(value.detail)
            || formatHooksErrorValue(value.message)
            || formatHooksErrorValue(value.error)
            || '';
    }
    return '';
}

function formatHooksErrorEntry(entry) {
    if (typeof entry === 'string') {
        return entry.trim();
    }
    if (!entry || typeof entry !== 'object') {
        return '';
    }
    const location = Array.isArray(entry.loc)
        ? entry.loc.map(part => String(part ?? '').trim()).filter(Boolean).join('.')
        : '';
    const message = typeof entry.msg === 'string'
        ? entry.msg.trim()
        : (typeof entry.message === 'string' ? entry.message.trim() : '');
    if (location && message) {
        return `${location}: ${message}`;
    }
    if (message) {
        return message;
    }
    return '';
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
