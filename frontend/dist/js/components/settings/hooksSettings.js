/**
 * components/settings/hooksSettings.js
 * Merged user hooks cards and runtime view.
 */
import {
    fetchHooksConfig,
    fetchHookRuntimeView,
    fetchRoleConfigOptions,
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
    'PermissionDenied',
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
    'InstructionsLoaded',
    'Notification',
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
    'PermissionDenied',
    'PostToolUse',
    'PostToolUseFailure',
]);

const COMMAND_ONLY_EVENTS = new Set(['SessionStart']);
const COMMAND_HTTP_ONLY_EVENTS = new Set([
    'SessionEnd',
    'StopFailure',
    'SubagentStart',
    'InstructionsLoaded',
    'Notification',
    'PreCompact',
    'PostCompact',
]);

const MATCHER_PLACEHOLDERS = {
    SessionStart: 'resume',
    SessionEnd: 'completed',
    PreToolUse: 'write|edit|shell',
    PermissionRequest: 'shell',
    PermissionDenied: 'shell',
    PostToolUse: 'read|write',
    PostToolUseFailure: 'shell',
    StopFailure: 'tool_timeout',
    SubagentStart: 'verifier',
    SubagentStop: 'Reviewer',
    PreCompact: 'token_threshold',
    PostCompact: 'token_threshold',
    InstructionsLoaded: 'initial',
    Notification: 'run_failed',
};

const HOOK_NAME_PLACEHOLDERS = {
    SessionStart: 'Session startup setup',
    SessionEnd: 'Run completion archive',
    UserPromptSubmit: 'Submitted prompt policy',
    PreToolUse: 'Tool policy guard',
    PermissionRequest: 'Approval policy',
    PermissionDenied: 'Denied permission audit',
    PostToolUse: 'Tool result review',
    PostToolUseFailure: 'Tool failure review',
    Stop: 'Final answer verification',
    StopFailure: 'Stop failure audit',
    SubagentStart: 'Subagent launch context',
    SubagentStop: 'Subagent output verification',
    TaskCreated: 'Task creation policy',
    TaskCompleted: 'Task completion verification',
    PreCompact: 'Compaction preflight',
    PostCompact: 'Compaction audit',
    InstructionsLoaded: 'Instruction source audit',
    Notification: 'Notification webhook',
};

const HANDLER_NAME_PLACEHOLDERS = {
    SessionStart: 'Prepare session environment',
    SessionEnd: 'Archive run summary',
    UserPromptSubmit: 'Review submitted prompt',
    PreToolUse: 'Check tool policy',
    PermissionRequest: 'Review approval request',
    PermissionDenied: 'Record denied permission',
    PostToolUse: 'Review tool result',
    PostToolUseFailure: 'Inspect tool failure',
    Stop: 'Verify final answer',
    StopFailure: 'Inspect stop failure',
    SubagentStart: 'Prepare subagent context',
    SubagentStop: 'Verify subagent output',
    TaskCreated: 'Inspect new task',
    TaskCompleted: 'Verify completed task',
    PreCompact: 'Review compaction request',
    PostCompact: 'Record compaction result',
    InstructionsLoaded: 'Review loaded instructions',
    Notification: 'Send notification payload',
};

const IF_RULE_PLACEHOLDERS = {
    PreToolUse: 'shell(git *)',
    PermissionRequest: 'shell(npm publish*)',
    PermissionDenied: 'shell(rm *)',
    PostToolUse: 'write(*.py)',
    PostToolUseFailure: 'shell(*)',
};

const COMMAND_PLACEHOLDERS = {
    SessionStart: 'python .relay/hooks/session_start.py',
    SessionEnd: 'python .relay/hooks/session_end.py',
    UserPromptSubmit: 'python .relay/hooks/prompt_policy.py',
    PreToolUse: 'python .relay/hooks/tool_policy.py',
    PermissionRequest: 'python .relay/hooks/approval_policy.py',
    PermissionDenied: 'python .relay/hooks/permission_denied.py',
    PostToolUse: 'python .relay/hooks/post_tool_review.py',
    PostToolUseFailure: 'python .relay/hooks/tool_failure.py',
    Stop: 'python .relay/hooks/verify_stop.py',
    StopFailure: 'python .relay/hooks/stop_failure.py',
    SubagentStart: 'python .relay/hooks/subagent_start.py',
    SubagentStop: 'python .relay/hooks/subagent_stop.py',
    TaskCreated: 'python .relay/hooks/task_created.py',
    TaskCompleted: 'python .relay/hooks/task_completed.py',
    PreCompact: 'python .relay/hooks/pre_compact.py',
    PostCompact: 'python .relay/hooks/post_compact.py',
    InstructionsLoaded: 'python .relay/hooks/instructions_loaded.py',
    Notification: 'python .relay/hooks/notify.py',
};

const URL_PLACEHOLDERS = {
    SessionEnd: 'https://example.test/hooks/session-end',
    UserPromptSubmit: 'https://example.test/hooks/prompt-policy',
    PreToolUse: 'https://example.test/hooks/tool-policy',
    PermissionRequest: 'https://example.test/hooks/approval',
    PermissionDenied: 'https://example.test/hooks/permission-denied',
    PostToolUse: 'https://example.test/hooks/tool-result',
    PostToolUseFailure: 'https://example.test/hooks/tool-failure',
    Stop: 'https://example.test/hooks/final-answer',
    StopFailure: 'https://example.test/hooks/stop-failure',
    SubagentStart: 'https://example.test/hooks/subagent-start',
    SubagentStop: 'https://example.test/hooks/subagent-stop',
    TaskCreated: 'https://example.test/hooks/task-created',
    TaskCompleted: 'https://example.test/hooks/task-completed',
    Notification: 'https://example.test/hooks/notification',
    PreCompact: 'https://example.test/hooks/pre-compact',
    PostCompact: 'https://example.test/hooks/post-compact',
    InstructionsLoaded: 'https://example.test/hooks/instructions-loaded',
};

const PROMPT_PLACEHOLDERS = {
    UserPromptSubmit: 'Review the submitted prompt and return a hook decision JSON object.',
    PreToolUse: 'Review whether this tool call should continue.',
    PermissionRequest: 'Review whether this approval request should be allowed.',
    PermissionDenied: 'Summarize the denied permission for follow-up context.',
    PostToolUse: 'Review the tool result and add useful follow-up context.',
    PostToolUseFailure: 'Review the tool failure and suggest next steps.',
    Stop: 'Review whether the pending answer is complete and verified.',
    SubagentStop: 'Review whether the subagent output satisfies the task.',
    TaskCreated: 'Review whether the new task should be accepted.',
    TaskCompleted: 'Review whether the completed task output is sufficient.',
};

let latestRuntimeView = null;
let editorGroups = [];
let lastSavedEditorGroups = [];
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
let hooksConfigDirty = false;
let groupEditSnapshots = new Map();
let hooksPersistenceChain = Promise.resolve();
const collapsedHandlerEditors = new Map();
let agentRoleOptions = [];

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
            hooksConfigDirty = true;
            latestLoadErrorMessage = '';
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

export function syncHooksSettingsActions() {
    if (!isHooksPanelActive()) {
        return;
    }
    setActionDisplay('add-hook-btn', true);
    const hasEditableHooks = !loadInFlight
        && !latestLoadErrorMessage
        && (editorGroups.length > 0 || editingGroupId !== null || hooksConfigDirty);
    setActionDisplay('validate-hooks-btn', hasEditableHooks);
    setActionDisplay('save-hooks-btn', hasEditableHooks);
}

export async function loadHooksSettingsPanel() {
    const requestId = ++activeHooksLoadRequestId;
    loadInFlight = true;
    latestLoadErrorMessage = '';
    latestRuntimeLoadErrorMessage = '';
    latestConfigMessage = '';
    hooksConfigDirty = false;
    renderHooksPanel();
    const configPromise = fetchHooksConfig();
    const runtimeViewPromise = fetchHookRuntimeView();
    const roleOptionsPromise = fetchRoleConfigOptions();
    try {
        const config = await configPromise;
        if (requestId !== activeHooksLoadRequestId) {
            return;
        }
        editorGroups = deserializeHooksConfig(config);
        lastSavedEditorGroups = cloneGroups(editorGroups);
        try {
            agentRoleOptions = normalizeAgentRoleOptions(await roleOptionsPromise);
        } catch (e) {
            agentRoleOptions = [];
            logError(
                'frontend.hooks_settings.role_options_failed',
                'Failed to load role options for hooks settings',
                errorToPayload(e),
            );
        }
        latestRuntimeLoadErrorMessage = '';
        editingGroupId = null;
        hooksConfigDirty = false;
        groupEditSnapshots = new Map();
        collapsedHandlerEditors.clear();
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
        void roleOptionsPromise.catch(() => {});
        if (requestId !== activeHooksLoadRequestId) {
            return;
        }
        latestLoadErrorMessage = e?.message || t('settings.hooks.load_failed');
        latestRuntimeLoadErrorMessage = '';
        latestRuntimeView = null;
        editorGroups = [];
        lastSavedEditorGroups = [];
        editingGroupId = null;
        hooksConfigDirty = false;
        groupEditSnapshots = new Map();
        collapsedHandlerEditors.clear();
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
        const deletedSavedGroups = lastSavedEditorGroups.filter(group => group.id === groupId);
        editorGroups = editorGroups.filter(group => group.id !== groupId);
        if (editingGroupId === groupId) {
            editingGroupId = null;
        }
        groupEditSnapshots.delete(groupId);
        clearCollapsedHandlersForGroup(groupId);
        latestConfigMessage = '';
        renderHooksPanel();
        await queuePersistHooksAfterDelete({
            deletedSavedGroups: cloneGroups(deletedSavedGroups),
            requestId: activeHooksLoadRequestId,
        });
        return;
    }
    if (action === 'add-handler') {
        const groupId = Number(actionTarget.dataset.groupId || '0');
        const group = editorGroups.find(candidate => candidate.id === groupId);
        const nextHandler = createDefaultHandler(group?.event_name || 'PreToolUse');
        editorGroups = editorGroups.map(group => {
            if (group.id !== groupId) {
                return group;
            }
            return {
                ...group,
                handlers: [...group.handlers, nextHandler],
            };
        });
        collapsedHandlerEditors.set(getHandlerEditorKey(groupId, nextHandler.id), true);
        hooksConfigDirty = true;
        latestConfigMessage = '';
        renderHooksPanel();
        return;
    }
    if (action === 'toggle-handler') {
        const groupId = Number(actionTarget.dataset.groupId || '0');
        const handlerId = Number(actionTarget.dataset.handlerId || '0');
        const group = editorGroups.find(candidate => candidate.id === groupId);
        const handlerIndex = group?.handlers.findIndex(handler => handler.id === handlerId) ?? -1;
        if (group && handlerIndex >= 0) {
            const collapsed = isHandlerEditorCollapsed(group, handlerId);
            collapsedHandlerEditors.set(getHandlerEditorKey(groupId, handlerId), !collapsed);
        }
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
        collapsedHandlerEditors.delete(getHandlerEditorKey(groupId, handlerId));
        hooksConfigDirty = true;
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
    hooksConfigDirty = true;
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
        const payload = serializeHooksConfig(editorGroups);
        validateHooksPayloadForEditor(payload);
        await validateHooksConfig(payload);
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
    hooksPersistenceChain = hooksPersistenceChain.then(() => persistCurrentHooksConfig());
    await hooksPersistenceChain;
}

async function persistCurrentHooksConfig() {
    try {
        const savedEditorGroups = cloneGroups(editorGroups).map(group => ({ ...group, isNew: false }));
        const payload = serializeHooksConfig(editorGroups);
        validateHooksPayloadForEditor(payload);
        await validateHooksConfig(payload);
        await saveHooksConfig(payload);
        latestConfigMessageTone = 'success';
        latestConfigMessage = t('settings.hooks.save_success');
        editorGroups = editorGroups.map(group => ({ ...group, isNew: false }));
        lastSavedEditorGroups = savedEditorGroups;
        editingGroupId = null;
        hooksConfigDirty = false;
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

async function queuePersistHooksAfterDelete(deleteSnapshot) {
    hooksPersistenceChain = hooksPersistenceChain.then(() =>
        persistHooksAfterDelete(deleteSnapshot),
    );
    await hooksPersistenceChain;
}

async function persistHooksAfterDelete(deleteSnapshot) {
    const { deletedSavedGroups, requestId } = deleteSnapshot;
    const requestIsCurrent = requestId === activeHooksLoadRequestId;
    const persistedGroups = lastSavedEditorGroups.filter(
        group => !matchesDeletedSavedGroup(group, deletedSavedGroups, requestIsCurrent),
    );
    if (persistedGroups.length === lastSavedEditorGroups.length) {
        if (requestId !== activeHooksLoadRequestId) {
            return;
        }
        reconcileHooksDirtyState();
        renderHooksPanel();
        return;
    }
    try {
        const payload = serializeHooksConfig(persistedGroups);
        validateHooksPayloadForEditor(payload);
        await validateHooksConfig(payload);
        await saveHooksConfig(payload);
        if (requestId !== activeHooksLoadRequestId) {
            return;
        }
        lastSavedEditorGroups = cloneGroups(persistedGroups);
        reconcileHooksDirtyState();
        latestConfigMessageTone = 'info';
        latestConfigMessage = '';
        try {
            const runtimeView = await fetchHookRuntimeView();
            if (requestId !== activeHooksLoadRequestId) {
                return;
            }
            latestRuntimeView = runtimeView;
            latestRuntimeLoadErrorMessage = '';
        } catch (e) {
            if (requestId !== activeHooksLoadRequestId) {
                return;
            }
            latestRuntimeView = null;
            latestRuntimeLoadErrorMessage =
                e?.message || t('settings.hooks.runtime_load_failed');
            logError(
                'frontend.hooks_settings.runtime_load_failed',
                'Failed to refresh hooks runtime view after delete',
                errorToPayload(e),
            );
        }
    } catch (e) {
        if (requestId !== activeHooksLoadRequestId) {
            return;
        }
        hooksConfigDirty = true;
        latestConfigMessageTone = 'error';
        const errorReason = resolveHooksErrorReason(e);
        latestConfigMessage = errorReason
            ? formatMessage('settings.hooks.delete_failed_detail', { error: errorReason })
            : t('settings.hooks.delete_failed');
        logError(
            'frontend.hooks_settings.delete_save_failed',
            'Failed to save hooks config after deleting hook group',
            errorToPayload(e),
        );
        await showAlertDialog({
            title: t('settings.hooks.delete_result_title'),
            message: latestConfigMessage,
            tone: 'error',
        });
    }
    renderHooksPanel();
}

function matchesDeletedSavedGroup(group, deletedSavedGroups, requestIsCurrent) {
    return deletedSavedGroups.some(deletedGroup => {
        if (requestIsCurrent && group.id === deletedGroup.id) {
            return true;
        }
        return groupsHaveSameSavedIdentity(group, deletedGroup);
    });
}

function groupsHaveSameSavedIdentity(group, deletedGroup) {
    return group.event_name === deletedGroup.event_name
        && group.name === deletedGroup.name
        && group.matcher === deletedGroup.matcher
        && JSON.stringify(group.role_ids) === JSON.stringify(deletedGroup.role_ids)
        && JSON.stringify(group.session_modes) === JSON.stringify(deletedGroup.session_modes)
        && JSON.stringify(group.run_kinds) === JSON.stringify(deletedGroup.run_kinds)
        && JSON.stringify(group.handlers) === JSON.stringify(deletedGroup.handlers);
}

function renderHooksPanel() {
    syncHooksSettingsActions();
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

function isHooksPanelActive() {
    const panel = document.getElementById('hooks-panel');
    return !panel || panel.style.display !== 'none';
}

function setActionDisplay(id, visible) {
    const element = document.getElementById(id);
    if (element?.style) {
        element.style.display = visible ? 'inline-flex' : 'none';
    }
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
    reconcileHooksDirtyState();
}

function reconcileHooksDirtyState() {
    try {
        hooksConfigDirty = JSON.stringify(serializeHooksConfig(editorGroups))
            !== JSON.stringify(serializeHooksConfig(lastSavedEditorGroups));
    } catch {
        hooksConfigDirty = true;
    }
}

function cloneGroups(groups) {
    return groups.map(group => cloneGroup(group));
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
    const typeSummary = buildHandlerTypeSummary(group.handlers);
    const detailRows = buildDetailRows([
        renderDetailItem(t('settings.hooks.trigger'), group.event_name),
        renderDetailItem(t('settings.hooks.matcher'), matcherValue),
        renderDetailItem(t('settings.hooks.handler_summary'), String(group.handlers.length)),
        renderDetailItem(t('settings.hooks.handler_type'), typeSummary || t('settings.hooks.all')),
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
    const typeSummary = buildHandlerTypeSummary(group.handlers);
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
                ${renderTextField({
                    groupId: group.id,
                    field: 'name',
                    label: t('settings.hooks.hook_name'),
                    value: group.name,
                    placeholder: getHookNamePlaceholder(group.event_name),
                })}
                ${eventFieldMarkup}
                ${matcherSupported ? renderTextField({
                    groupId: group.id,
                    field: 'matcher',
                    label: t('settings.hooks.matcher'),
                    value: group.matcher,
                    placeholder: getMatcherPlaceholder(group.event_name),
                }) : renderStaticField(t('settings.hooks.matcher'), t('settings.hooks.matcher_not_supported'))}
                ${renderStaticField(t('settings.hooks.handler_summary'), String(group.handlers.length))}
                ${renderStaticField(t('settings.hooks.handler_type'), typeSummary || t('settings.hooks.all'))}
            </div>
            <div class="hooks-handler-stack">
                <div class="proxy-form-section-header">
                    <h6>${escapeHtml(t('settings.hooks.handlers'))}</h6>
                    <button class="secondary-btn section-action-btn" type="button" data-hooks-action="add-handler" data-group-id="${group.id}">${escapeHtml(t('settings.hooks.add_handler'))}</button>
                </div>
                ${group.handlers.map((handler, index) => renderHandlerEditor(group, handler, index)).join('')}
            </div>
        </section>
    `;
}

function renderHandlerEditor(group, handler, index) {
    const allowedTypes = getAllowedHandlerTypes(group.event_name);
    const collapsed = isHandlerEditorCollapsed(group, handler.id);
    const bodyId = `hooks-handler-body-${group.id}-${handler.id}`;
    return `
        <div class="hooks-handler-card${collapsed ? ' hooks-handler-card-collapsed' : ''}">
            <div class="hooks-handler-card-header">
                <button
                    class="hooks-handler-toggle"
                    type="button"
                    data-hooks-action="toggle-handler"
                    data-group-id="${group.id}"
                    data-handler-id="${handler.id}"
                    aria-expanded="${collapsed ? 'false' : 'true'}"
                    aria-controls="${escapeHtml(bodyId)}"
                >
                    <span class="hooks-handler-chevron" aria-hidden="true"></span>
                    <span class="hooks-handler-title">${escapeHtml(resolveHandlerEditorTitle(handler, index))}</span>
                    <span class="hooks-handler-hint">${escapeHtml(t(collapsed ? 'settings.hooks.expand_handler' : 'settings.hooks.collapse_handler'))}</span>
                </button>
                <button class="secondary-btn section-action-btn" type="button" data-hooks-action="remove-handler" data-group-id="${group.id}" data-handler-id="${handler.id}">${escapeHtml(t('settings.hooks.delete_handler'))}</button>
            </div>
            <div class="hooks-handler-card-body" id="${escapeHtml(bodyId)}" ${collapsed ? 'hidden' : ''}>
                <div class="proxy-form-grid">
                    ${renderTextField({
                        groupId: group.id,
                        handlerId: handler.id,
                        field: 'name',
                        label: t('settings.hooks.name'),
                        value: handler.name,
                        placeholder: getHandlerNamePlaceholder(group.event_name),
                    })}
                    ${renderSelectField({
                        groupId: group.id,
                        handlerId: handler.id,
                        field: 'type',
                        label: t('settings.hooks.handler_type'),
                        options: allowedTypes,
                        value: handler.type,
                    })}
                    ${TOOL_EVENTS.has(group.event_name) ? renderTextField({
                        groupId: group.id,
                        handlerId: handler.id,
                        field: 'if',
                        label: t('settings.hooks.if_rule'),
                        value: handler.if,
                        placeholder: getIfRulePlaceholder(group.event_name),
                    }) : ''}
                    ${renderTextField({
                        groupId: group.id,
                        handlerId: handler.id,
                        field: 'timeout',
                        label: t('settings.hooks.timeout_seconds'),
                        value: String(handler.timeout || ''),
                        placeholder: '5',
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
            </div>
        </div>
    `;
}

function renderTypeSpecificHandlerFields(group, handler) {
    if (handler.type === 'command') {
        return `
            ${renderTextField({
                groupId: group.id,
                handlerId: handler.id,
                field: 'command',
                label: t('settings.hooks.command'),
                value: handler.command,
                placeholder: getCommandPlaceholder(group.event_name),
            })}
        `;
    }
    if (handler.type === 'http') {
        return `
            ${renderTextField({
                groupId: group.id,
                handlerId: handler.id,
                field: 'url',
                label: t('settings.hooks.url'),
                value: handler.url,
                placeholder: getUrlPlaceholder(group.event_name),
            })}
            ${renderTextareaField({
                groupId: group.id,
                handlerId: handler.id,
                field: 'headers',
                label: t('settings.hooks.headers'),
                value: handler.headers,
                placeholder: '{\n  "Authorization": "Bearer $HOOK_TOKEN"\n}',
            })}
            ${renderTextField({
                groupId: group.id,
                handlerId: handler.id,
                field: 'allowed_env_vars',
                label: t('settings.hooks.allowed_env_vars'),
                value: handler.allowed_env_vars.join(','),
                placeholder: t('settings.hooks.csv_placeholder'),
                span2: true,
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
                placeholder: getPromptPlaceholder(group.event_name),
            })}
        `;
    }
    if (handler.type === 'agent') {
        return `
            ${renderRoleSelectField({
                groupId: group.id,
                handlerId: handler.id,
                field: 'role_id',
                label: t('settings.hooks.role_id'),
                value: handler.role_id,
            })}
            ${renderTextareaField({
                groupId: group.id,
                handlerId: handler.id,
                field: 'prompt',
                label: t('settings.hooks.prompt'),
                value: handler.prompt,
                placeholder: getPromptPlaceholder(group.event_name),
            })}
        `;
    }
    return `
        ${renderTextareaField({
            groupId: group.id,
            handlerId: handler.id,
            field: 'prompt',
            label: t('settings.hooks.prompt'),
            value: handler.prompt,
            placeholder: getPromptPlaceholder(group.event_name),
        })}
    `;
}

function getMatcherPlaceholder(eventName) {
    return MATCHER_PLACEHOLDERS[eventName] || '*';
}

function getHookNamePlaceholder(eventName) {
    return HOOK_NAME_PLACEHOLDERS[eventName] || 'Hook event policy';
}

function getHandlerNamePlaceholder(eventName) {
    return HANDLER_NAME_PLACEHOLDERS[eventName] || 'Review hook event';
}

function getIfRulePlaceholder(eventName) {
    return IF_RULE_PLACEHOLDERS[eventName] || 'shell(git *)';
}

function getCommandPlaceholder(eventName) {
    return COMMAND_PLACEHOLDERS[eventName] || 'python .relay/hooks/hook.py';
}

function getUrlPlaceholder(eventName) {
    return URL_PLACEHOLDERS[eventName] || 'https://example.test/hooks/relay';
}

function getPromptPlaceholder(eventName) {
    return PROMPT_PLACEHOLDERS[eventName] || 'Review the hook event and return a hook decision JSON object.';
}

function resolveHandlerEditorTitle(handler, index) {
    return normalizeString(handler.name)
        || formatMessage('settings.hooks.handler_fallback_title', { index: index + 1 });
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
    const source = hook?.source && typeof hook.source === 'object' ? hook.source : {};
    const handlerCount =
        typeof hook?.handler_count === 'number' && Number.isFinite(hook.handler_count)
            ? hook.handler_count
            : 1;
    return buildDetailRows([
        renderDetailItem(t('settings.hooks.trigger'), trigger),
        renderDetailItem(t('settings.hooks.matcher'), matcher),
        renderOptionalDetailItem(t('settings.hooks.if_rule'), hook?.if),
        renderDetailItem(t('settings.hooks.scope'), formatSourceScope(source.scope)),
        renderOptionalDetailItem(t('settings.hooks.source_path'), source.path),
        renderOptionalListDetailItem(t('settings.hooks.role_ids'), hook?.role_ids),
        renderOptionalListDetailItem(t('settings.hooks.session_modes'), hook?.session_modes),
        renderOptionalListDetailItem(t('settings.hooks.run_kinds'), hook?.run_kinds),
        renderDetailItem(t('settings.hooks.handler_summary'), String(handlerCount)),
        renderDetailItem(t('settings.hooks.handler_type'), handlerType),
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

function buildHandlerTypeSummary(handlers) {
    const types = Array.isArray(handlers)
        ? handlers.map(handler => normalizeString(handler?.type)).filter(Boolean)
        : [];
    return Array.from(new Set(types)).join(', ');
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

function renderSelectField({ groupId, handlerId = 0, field, label, options, value }) {
    return `
        <div class="form-group">
            <label>${escapeHtml(label)}</label>
            <select
                data-hooks-field="${escapeHtml(field)}"
                data-group-id="${groupId}"
                data-handler-id="${handlerId}"
            >
                ${options.map(option => `<option value="${escapeHtml(option)}" ${option === value ? 'selected' : ''}>${escapeHtml(getSelectOptionLabel(field, option))}</option>`).join('')}
            </select>
        </div>
    `;
}

function renderRoleSelectField({ groupId, handlerId = 0, field, label, value }) {
    const selectedValue = normalizeString(value);
    const options = [...agentRoleOptions];
    const hasSelectedValue = selectedValue
        && options.some(option => option.role_id === selectedValue);
    if (selectedValue && !hasSelectedValue) {
        options.unshift({
            role_id: selectedValue,
            name: selectedValue,
            missing: true,
        });
    }
    const emptyLabel = options.length
        ? t('settings.hooks.role_id_select_option')
        : t('settings.hooks.no_agent_roles');
    return `
        <div class="form-group">
            <label>${escapeHtml(label)}</label>
            <select
                data-hooks-field="${escapeHtml(field)}"
                data-group-id="${groupId}"
                data-handler-id="${handlerId}"
                required
            >
                <option value="" disabled ${selectedValue ? '' : 'selected'}>${escapeHtml(emptyLabel)}</option>
                ${options.map(option => renderRoleSelectOption(option, selectedValue)).join('')}
            </select>
        </div>
    `;
}

function renderRoleSelectOption(option, selectedValue) {
    const roleId = normalizeString(option?.role_id);
    if (!roleId) {
        return '';
    }
    const selected = roleId === selectedValue ? ' selected' : '';
    return `<option value="${escapeHtml(roleId)}"${selected}>${escapeHtml(formatRoleOptionLabel(option))}</option>`;
}

function formatRoleOptionLabel(option) {
    const roleId = normalizeString(option?.role_id);
    const name = normalizeString(option?.name);
    if (option?.missing === true) {
        return formatMessage('settings.hooks.role_id_missing_option', { role_id: roleId });
    }
    if (name && name !== roleId) {
        return `${name} (${roleId})`;
    }
    return roleId;
}

function getSelectOptionLabel(field, option) {
    if (field === 'on_error') {
        return t(`settings.hooks.on_error.${option}`);
    }
    return option;
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
                name: normalizeString(rawGroup.name),
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
                    timeout: normalizeOptionalNumber(rawHandler?.timeout ?? rawHandler?.timeout_seconds),
                    async: Boolean(rawHandler?.async ?? rawHandler?.run_async),
                    on_error: normalizeString(rawHandler?.on_error) || 'ignore',
                    command: normalizeString(rawHandler?.command),
                    shell: normalizeString(rawHandler?.shell),
                    url: normalizeString(rawHandler?.url),
                    headers: serializeHeaders(rawHandler?.headers),
                    allowed_env_vars: normalizeStringList(rawHandler?.allowed_env_vars),
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
            hooks: group.handlers.map(handler => serializeHandler(normalizedEventName, handler)),
        };
        if (!MATCHER_UNSUPPORTED_EVENTS.has(normalizedEventName)) {
            const matcher = normalizeString(group.matcher) || '*';
            if (matcher !== '*') {
                nextGroup.matcher = matcher;
            }
        }
        if (normalizeString(group.name)) {
            nextGroup.name = normalizeString(group.name);
        }
        const roleIds = normalizeStringList(group.role_ids);
        if (roleIds.length > 0) {
            nextGroup.role_ids = roleIds;
        }
        const sessionModes = normalizeStringList(group.session_modes);
        if (sessionModes.length > 0) {
            nextGroup.session_modes = sessionModes;
        }
        const runKinds = normalizeStringList(group.run_kinds);
        if (runKinds.length > 0) {
            nextGroup.run_kinds = runKinds;
        }
        hooks[normalizedEventName] = hooks[normalizedEventName] || [];
        hooks[normalizedEventName].push(nextGroup);
    }
    return { hooks };
}

function normalizeAgentRoleOptions(options) {
    const rows = Array.isArray(options?.subagent_roles) ? options.subagent_roles : [];
    const seen = new Set();
    return rows
        .map(role => ({
            role_id: normalizeString(role?.role_id),
            name: normalizeString(role?.name),
        }))
        .filter(role => {
            if (!role.role_id || seen.has(role.role_id)) {
                return false;
            }
            seen.add(role.role_id);
            return true;
        })
        .sort((left, right) => {
            if (left.role_id === right.role_id) return 0;
            return left.role_id.localeCompare(right.role_id);
        });
}

function validateHooksPayloadForEditor(payload) {
    const hooks = payload?.hooks;
    if (!hooks || typeof hooks !== 'object') {
        return;
    }
    const reasons = [];
    Object.entries(hooks).forEach(([eventName, groups]) => {
        if (!Array.isArray(groups)) {
            return;
        }
        groups.forEach((group, groupIndex) => {
            const handlers = Array.isArray(group?.hooks) ? group.hooks : [];
            if (handlers.length === 0) {
                reasons.push(formatHooksEditorValidationReason({
                    eventName,
                    groupIndex,
                    message: t('settings.hooks.error_handler_required'),
                }));
                return;
            }
            handlers.forEach((handler, handlerIndex) => {
                const type = normalizeString(handler?.type) || 'command';
                if (type === 'command' && !normalizeString(handler?.command)) {
                    reasons.push(formatHooksRequiredFieldReason({
                        eventName,
                        groupIndex,
                        handlerIndex,
                        fieldLabel: t('settings.hooks.command'),
                    }));
                } else if (type === 'http' && !normalizeString(handler?.url)) {
                    reasons.push(formatHooksRequiredFieldReason({
                        eventName,
                        groupIndex,
                        handlerIndex,
                        fieldLabel: t('settings.hooks.url'),
                    }));
                } else if (type === 'prompt' && !normalizeString(handler?.prompt)) {
                    reasons.push(formatHooksRequiredFieldReason({
                        eventName,
                        groupIndex,
                        handlerIndex,
                        fieldLabel: t('settings.hooks.prompt'),
                    }));
                } else if (type === 'agent' && !normalizeString(handler?.role_id)) {
                    reasons.push(formatHooksRequiredFieldReason({
                        eventName,
                        groupIndex,
                        handlerIndex,
                        fieldLabel: t('settings.hooks.role_id'),
                    }));
                } else if (type === 'agent' && !normalizeString(handler?.prompt)) {
                    reasons.push(formatHooksRequiredFieldReason({
                        eventName,
                        groupIndex,
                        handlerIndex,
                        fieldLabel: t('settings.hooks.prompt'),
                    }));
                }
            });
        });
    });
    if (reasons.length > 0) {
        throw new HooksEditorValidationError(reasons);
    }
}

class HooksEditorValidationError extends Error {
    constructor(reasons) {
        super(reasons.join('; '));
        this.name = 'HooksEditorValidationError';
        this.detail = reasons;
    }
}

function formatHooksRequiredFieldReason({
    eventName,
    groupIndex,
    handlerIndex,
    fieldLabel,
}) {
    return formatHooksEditorValidationReason({
        eventName,
        groupIndex,
        handlerIndex,
        message: formatMessage('settings.hooks.error_required_field', {
            field: fieldLabel,
        }),
    });
}

function formatHooksEditorValidationReason({
    eventName,
    groupIndex,
    handlerIndex = null,
    message,
}) {
    const location = handlerIndex === null
        ? formatMessage('settings.hooks.error_group_location', {
            event: eventName,
            group: groupIndex + 1,
        })
        : formatMessage('settings.hooks.error_handler_location', {
            event: eventName,
            group: groupIndex + 1,
            handler: handlerIndex + 1,
        });
    return `${location}: ${message}`;
}

function serializeHandler(eventName, handler) {
    const serialized = {
        type: handler.type,
    };
    if (normalizeString(handler.name)) {
        serialized.name = normalizeString(handler.name);
    }
    if (TOOL_EVENTS.has(eventName) && normalizeString(handler.if)) {
        serialized.if = normalizeString(handler.if);
    }
    const timeout = normalizeNumber(handler.timeout, 5);
    if (timeout !== 5) {
        serialized.timeout = timeout;
    }
    if (normalizeString(handler.on_error) && normalizeString(handler.on_error) !== 'ignore') {
        serialized.on_error = normalizeString(handler.on_error);
    }
    if (handler.async) {
        serialized.run_async = true;
    }
    if (handler.type === 'command') {
        serialized.command = normalizeString(handler.command);
        if (normalizeString(handler.shell)) {
            serialized.shell = normalizeString(handler.shell);
        }
    } else if (handler.type === 'http') {
        serialized.url = normalizeString(handler.url);
        const parsedHeaders = parseHeaders(handler.headers);
        if (Object.keys(parsedHeaders).length > 0) {
            serialized.headers = parsedHeaders;
        }
        const allowedEnvVars = normalizeStringList(handler.allowed_env_vars);
        if (allowedEnvVars.length > 0) {
            serialized.allowed_env_vars = allowedEnvVars;
        }
    } else if (handler.type === 'prompt') {
        serialized.prompt = normalizeString(handler.prompt);
        if (normalizeString(handler.model)) {
            serialized.model = normalizeString(handler.model);
        }
    } else {
        if (normalizeString(handler.role_id)) {
            serialized.role_id = normalizeString(handler.role_id);
        }
        serialized.prompt = normalizeString(handler.prompt);
    }
    return serialized;
}

function createDefaultGroup() {
    return {
        id: nextGroupId++,
        isNew: true,
        name: '',
        event_name: 'PreToolUse',
        matcher: '',
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
        timeout: '',
        async: false,
        on_error: 'ignore',
        command: '',
        url: '',
        headers: '',
        allowed_env_vars: [],
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
        if (field === 'name') {
            nextGroup.name = normalizeString(rawValue);
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
                if (field === 'allowed_env_vars') {
                    return { ...handler, allowed_env_vars: parseCsvList(rawValue) };
                }
                if (field === 'timeout') {
                    return { ...handler, timeout: normalizeOptionalNumber(rawValue) };
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
    return normalizeString(value);
}

function getNormalizedHandlerType(value, eventName) {
    const candidate = normalizeString(value) || 'command';
    return getAllowedHandlerTypes(eventName).includes(candidate)
        ? candidate
        : getAllowedHandlerTypes(eventName)[0];
}

function resolveGroupTitle(group) {
    const name = normalizeString(group?.name);
    if (name) {
        return name;
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
    if (scope === 'role') {
        return t('settings.hooks.scope_role');
    }
    if (scope === 'skill') {
        return t('settings.hooks.scope_skill');
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

function normalizeOptionalNumber(value) {
    if (value === undefined || value === null) {
        return '';
    }
    if (typeof value === 'string' && value.trim() === '') {
        return '';
    }
    return normalizeNumber(value, 5);
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

function getHandlerEditorKey(groupId, handlerId) {
    return `${groupId}:${handlerId}`;
}

function isHandlerEditorCollapsed(group, handlerId) {
    const key = getHandlerEditorKey(group.id, handlerId);
    if (collapsedHandlerEditors.has(key)) {
        return collapsedHandlerEditors.get(key) === true;
    }
    return true;
}

function clearCollapsedHandlersForGroup(groupId) {
    const prefix = `${groupId}:`;
    Array.from(collapsedHandlerEditors.keys()).forEach(key => {
        if (key.startsWith(prefix)) {
            collapsedHandlerEditors.delete(key);
        }
    });
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
        return translateHooksErrorText(value.trim());
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
        return translateHooksErrorText(entry.trim());
    }
    if (!entry || typeof entry !== 'object') {
        return '';
    }
    const location = formatHooksErrorLocation(entry.loc);
    const message = typeof entry.msg === 'string'
        ? entry.msg.trim()
        : (typeof entry.message === 'string' ? entry.message.trim() : '');
    const translatedMessage = translateHooksErrorText(message);
    if (location && translatedMessage) {
        return `${location}: ${translatedMessage}`;
    }
    if (translatedMessage) {
        return translatedMessage;
    }
    return '';
}

function formatHooksErrorLocation(loc) {
    if (!Array.isArray(loc)) {
        return '';
    }
    const parts = [];
    for (let index = 0; index < loc.length; index += 1) {
        const part = loc[index];
        const nextPart = loc[index + 1];
        if (part === 'hooks' && typeof nextPart === 'string') {
            parts.push(formatMessage('settings.hooks.error_event_location', {
                event: nextPart,
            }));
            index += 1;
            continue;
        }
        if (typeof part === 'number') {
            const previousPart = loc[index - 1];
            if (previousPart === 'hooks') {
                parts.push(formatMessage('settings.hooks.error_handler_index', {
                    index: part + 1,
                }));
            } else {
                parts.push(formatMessage('settings.hooks.error_group_index', {
                    index: part + 1,
                }));
            }
            continue;
        }
        const fieldLabel = hooksErrorFieldLabel(part);
        if (fieldLabel) {
            parts.push(fieldLabel);
        }
    }
    return parts.join(' / ');
}

function hooksErrorFieldLabel(value) {
    const field = String(value ?? '').trim();
    if (!field || field === 'hooks') {
        return '';
    }
    const labels = {
        matcher: t('settings.hooks.matcher'),
        name: t('settings.hooks.hook_name'),
        type: t('settings.hooks.handler_type'),
        command: t('settings.hooks.command'),
        url: t('settings.hooks.url'),
        prompt: t('settings.hooks.prompt'),
        model: t('settings.hooks.model'),
        role_id: t('settings.hooks.role_id'),
        timeout: t('settings.hooks.timeout_seconds'),
        timeout_seconds: t('settings.hooks.timeout_seconds'),
        on_error: t('settings.hooks.on_error'),
        headers: t('settings.hooks.headers'),
        allowed_env_vars: t('settings.hooks.allowed_env_vars'),
        role_ids: t('settings.hooks.role_ids'),
        session_modes: t('settings.hooks.session_modes'),
        run_kinds: t('settings.hooks.run_kinds'),
        if: t('settings.hooks.if_rule'),
        if_rule: t('settings.hooks.if_rule'),
    };
    return labels[field] || field;
}

function translateHooksErrorText(message) {
    const text = String(message || '').trim();
    if (!text) {
        return '';
    }
    const lower = text.toLowerCase();
    if (lower === 'field required' || lower === 'missing') {
        return t('settings.hooks.error_field_required');
    }
    if (lower.includes('command hook requires command')) {
        return formatMessage('settings.hooks.error_required_field', {
            field: t('settings.hooks.command'),
        });
    }
    if (lower.includes('http hook requires url')) {
        return formatMessage('settings.hooks.error_required_field', {
            field: t('settings.hooks.url'),
        });
    }
    if (lower.includes('prompt hook requires prompt')) {
        return formatMessage('settings.hooks.error_required_field', {
            field: t('settings.hooks.prompt'),
        });
    }
    if (
        lower.includes('agent hook requires prompt')
        || lower.includes('agent hook requires a prompt')
    ) {
        return formatMessage('settings.hooks.error_required_field', {
            field: t('settings.hooks.prompt'),
        });
    }
    if (
        lower.includes('agent hook requires role_id')
        || lower.includes('agent hook role_id is required')
    ) {
        return formatMessage('settings.hooks.error_required_field', {
            field: t('settings.hooks.role_id'),
        });
    }
    const unknownAgentRoleMatch = text.match(/unknown agent hook role_id:\s*([^.;\n]+)/i);
    if (unknownAgentRoleMatch) {
        return formatMessage('settings.hooks.error_unknown_agent_role', {
            role_id: unknownAgentRoleMatch[1].trim(),
        });
    }
    const nonSubagentRoleMatch = text.match(
        /agent hook role_id must reference a subagent role:\s*([^.;\n]+)/i,
    );
    if (nonSubagentRoleMatch) {
        return formatMessage('settings.hooks.error_agent_role_must_be_subagent', {
            role_id: nonSubagentRoleMatch[1].trim(),
        });
    }
    if (lower.includes('matcher is not supported')) {
        return t('settings.hooks.matcher_not_supported');
    }
    if (lower.includes('tool hook matcher must contain at least one pattern')) {
        return t('settings.hooks.error_tool_matcher_required');
    }
    if (lower.includes('hook matcher group must contain at least one handler')) {
        return t('settings.hooks.error_handler_required');
    }
    if (lower.includes('if rules are only supported')) {
        return t('settings.hooks.if_not_supported');
    }
    if (lower.includes('async hooks are only supported for command handlers')) {
        return t('settings.hooks.error_async_command_only');
    }
    return text;
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
