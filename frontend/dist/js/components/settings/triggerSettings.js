/**
 * components/settings/triggerSettings.js
 * Trigger settings panel bindings.
 */
import {
    createTrigger,
    deleteEnvironmentVariable,
    disableTrigger,
    enableTrigger,
    fetchEnvironmentVariables,
    fetchTriggers,
    saveEnvironmentVariable,
    updateTrigger,
} from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const FEISHU_PLATFORM = 'feishu';
const FEISHU_SOURCE_TYPE = 'im';
const DEFAULT_TRIGGER_RULE = 'mention_only';
const DEFAULT_WORKSPACE_ID = 'default';
const FEISHU_CREDENTIAL_FIELDS = [
    {
        envKey: 'FEISHU_APP_ID',
        inputId: 'feishu-app-id-input',
        labelKey: 'settings.triggers.feishu_app_id',
        placeholderKey: 'settings.triggers.feishu_app_id_placeholder',
        type: 'text',
        required: true,
    },
    {
        envKey: 'FEISHU_APP_SECRET',
        inputId: 'feishu-app-secret-input',
        labelKey: 'settings.triggers.feishu_app_secret',
        placeholderKey: 'settings.triggers.feishu_app_secret_placeholder',
        type: 'password',
        required: true,
    },
    {
        envKey: 'FEISHU_APP_NAME',
        inputId: 'feishu-app-name-input',
        labelKey: 'settings.triggers.feishu_app_name',
        placeholderKey: 'settings.triggers.feishu_app_name_placeholder',
        type: 'text',
        required: true,
    },
];

let handlersBound = false;
let languageBound = false;
let triggerSettingsState = createInitialState();

export function bindTriggerSettingsHandlers() {
    if (!handlersBound) {
        bindActionButton('add-trigger-btn', handleAddTrigger);
        bindActionButton('save-trigger-btn', handleSaveTriggerSettings);
        bindActionButton('cancel-trigger-btn', handleCancelTriggerSettings);
        handlersBound = true;
    }
    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderTriggerSettingsPanel();
        });
        languageBound = true;
    }
}

export async function loadTriggerSettingsPanel(options = {}) {
    try {
        const [triggers, environmentCatalog] = await Promise.all([
            fetchTriggers(),
            fetchEnvironmentVariables(),
        ]);
        const feishuTriggers = normalizeFeishuTriggers(triggers);
        const credentialSnapshot = normalizeFeishuCredentialSnapshot(environmentCatalog);
        triggerSettingsState = {
            ...createInitialState(),
            view:
                options.openProvider === FEISHU_PLATFORM
                    ? 'provider_detail'
                    : 'platform_list',
            feishuTriggers,
            credentialSnapshot,
            credentialDraft: { ...credentialSnapshot },
        };
        if (options.editTriggerId) {
            openTriggerEditor(options.editTriggerId);
            return;
        }
        renderTriggerSettingsPanel();
    } catch (error) {
        logError(
            'frontend.trigger_settings.load_failed',
            'Failed to load trigger settings',
            errorToPayload(error),
        );
        renderTriggerLoadError(error?.message || 'Unable to load trigger settings.');
    }
}

function createInitialState() {
    return {
        view: 'platform_list',
        feishuTriggers: [],
        credentialSnapshot: buildEmptyCredentialSnapshot(),
        credentialDraft: buildEmptyCredentialSnapshot(),
        editingTriggerId: '',
        editingTriggerDraft: null,
        statusMessage: '',
        statusTone: '',
    };
}

function buildEmptyCredentialSnapshot() {
    return FEISHU_CREDENTIAL_FIELDS.reduce((accumulator, field) => {
        accumulator[field.envKey] = '';
        return accumulator;
    }, {});
}

function bindActionButton(id, handler) {
    const button = document.getElementById(id);
    if (button) {
        button.onclick = handler;
    }
}

function normalizeFeishuTriggers(payload) {
    const rows = Array.isArray(payload) ? payload : [];
    return rows
        .filter(isFeishuTrigger)
        .map(trigger => ({
            trigger_id: String(trigger?.trigger_id || '').trim(),
            name: String(trigger?.name || '').trim(),
            display_name: String(trigger?.display_name || '').trim(),
            status: String(trigger?.status || 'disabled').trim() || 'disabled',
            source_config:
                trigger?.source_config && typeof trigger.source_config === 'object'
                    ? { ...trigger.source_config }
                    : {},
            target_config:
                trigger?.target_config && typeof trigger.target_config === 'object'
                    ? { ...trigger.target_config }
                    : {},
        }))
        .sort((left, right) => {
            const leftLabel = resolveTriggerLabel(left).toLowerCase();
            const rightLabel = resolveTriggerLabel(right).toLowerCase();
            return leftLabel.localeCompare(rightLabel);
        });
}

function normalizeFeishuCredentialSnapshot(payload) {
    const snapshot = buildEmptyCredentialSnapshot();
    const appRecords = Array.isArray(payload?.app) ? payload.app : [];
    appRecords.forEach(record => {
        const key = String(record?.key || '').trim();
        if (!Object.prototype.hasOwnProperty.call(snapshot, key)) {
            return;
        }
        snapshot[key] = String(record?.value || '');
    });
    return snapshot;
}

function isFeishuTrigger(trigger) {
    if (!trigger || typeof trigger !== 'object') {
        return false;
    }
    const sourceType = String(trigger.source_type || '').trim().toLowerCase();
    const provider = String(trigger.source_config?.provider || '').trim().toLowerCase();
    return sourceType === FEISHU_SOURCE_TYPE && provider === FEISHU_PLATFORM;
}

function renderTriggerSettingsPanel() {
    renderTriggerPlatformList();
    renderTriggerProviderDetail();
    renderTriggerStatus();
    renderTriggerActionMode();
}

function renderTriggerPlatformList() {
    const host = document.getElementById('trigger-platform-list');
    if (!host) {
        return;
    }
    if (triggerSettingsState.view !== 'platform_list') {
        host.style.display = 'none';
        host.innerHTML = '';
        return;
    }
    host.style.display = 'block';
    host.innerHTML = `
        <div class="role-records trigger-platform-records">
            <div class="role-record trigger-platform-record" data-trigger-platform="${FEISHU_PLATFORM}">
                <div class="role-record-main">
                    <div class="role-record-title-row">
                        <div class="role-record-title">${escapeHtml(
                            t('settings.triggers.feishu'),
                        )}</div>
                        <div class="profile-card-chips role-record-chips">
                            ${renderCredentialChip()}
                        </div>
                    </div>
                    <div class="role-record-meta">
                        <span>${escapeHtml(
                            t('settings.triggers.trigger_count').replace(
                                '{count}',
                                String(triggerSettingsState.feishuTriggers.length),
                            ),
                        )}</span>
                        <span>${escapeHtml(
                            t('settings.triggers.enabled_count').replace(
                                '{count}',
                                String(
                                    triggerSettingsState.feishuTriggers.filter(trigger =>
                                        isTriggerEnabled(trigger),
                                    ).length,
                                ),
                            ),
                        )}</span>
                        <span>${escapeHtml(formatCredentialSummary())}</span>
                    </div>
                </div>
                <div class="role-record-actions">
                    <button class="settings-inline-action settings-list-action trigger-platform-open-btn" data-trigger-platform="${FEISHU_PLATFORM}" type="button">${escapeHtml(
                        t('settings.triggers.configure'),
                    )}</button>
                </div>
            </div>
        </div>
    `;
    host.querySelectorAll('.trigger-platform-open-btn').forEach(button => {
        button.onclick = event => {
            event?.stopPropagation?.();
            openFeishuProviderDetail();
        };
    });
    host.querySelectorAll('.trigger-platform-record').forEach(button => {
        button.onclick = () => {
            openFeishuProviderDetail();
        };
    });
}

function renderCredentialChip() {
    if (missingFeishuCredentialCount() === 0) {
        return `<span class="profile-card-chip profile-card-chip-accent">${escapeHtml(
            t('settings.triggers.ready'),
        )}</span>`;
    }
    return `<span class="profile-card-chip">${escapeHtml(
        t('settings.triggers.credentials_missing'),
    )}</span>`;
}

function renderTriggerProviderDetail() {
    const panel = document.getElementById('trigger-provider-detail-panel');
    const host = document.getElementById('trigger-provider-detail');
    if (!panel || !host) {
        return;
    }
    if (triggerSettingsState.view !== 'provider_detail') {
        panel.style.display = 'none';
        host.innerHTML = '';
        return;
    }
    panel.style.display = 'block';
    host.innerHTML = `
        <div class="trigger-detail-header">
            <button class="secondary-btn section-action-btn trigger-detail-back-btn" id="trigger-provider-back-btn" type="button">${escapeHtml(
                t('settings.triggers.back'),
            )}</button>
            <div class="trigger-detail-copy">
                <h4>${escapeHtml(t('settings.triggers.feishu'))}</h4>
                <p>${escapeHtml(t('settings.triggers.feishu_detail_copy'))}</p>
            </div>
        </div>
        <section class="role-editor-section trigger-detail-section">
            <div class="trigger-section-header">
                <div>
                    <h5>${escapeHtml(t('settings.triggers.credentials'))}</h5>
                    <p>${escapeHtml(t('settings.triggers.credentials_copy'))}</p>
                </div>
            </div>
            <div class="profile-editor-grid role-editor-grid trigger-credentials-grid">
                ${FEISHU_CREDENTIAL_FIELDS.map(renderCredentialField).join('')}
            </div>
            <p class="trigger-section-note">${escapeHtml(
                t('settings.triggers.sdk_mode_note'),
            )}</p>
            <p class="trigger-section-note">${escapeHtml(
                t('settings.triggers.encrypt_key_note'),
            )}</p>
        </section>
        <section class="role-editor-section trigger-detail-section">
            <div class="trigger-section-header">
                <div>
                    <h5>${escapeHtml(t('settings.triggers.records'))}</h5>
                    <p>${escapeHtml(t('settings.triggers.records_copy'))}</p>
                </div>
            </div>
            ${renderTriggerRecordList()}
        </section>
        ${renderTriggerEditorSection()}
    `;
    bindProviderDetailHandlers();
    syncDraftValuesToInputs();
}

function renderCredentialField(field) {
    return `
        <div class="form-group">
            <label for="${escapeHtml(field.inputId)}">${escapeHtml(
                t(field.labelKey),
            )}</label>
            <input type="${escapeHtml(field.type)}" id="${escapeHtml(
                field.inputId,
            )}" autocomplete="off" placeholder="${escapeHtml(
                t(field.placeholderKey),
            )}" value="${escapeHtml(
                triggerSettingsState.credentialDraft[field.envKey] || '',
            )}">
        </div>
    `;
}

function renderTriggerRecordList() {
    if (triggerSettingsState.feishuTriggers.length === 0) {
        return `
            <div class="settings-empty-state settings-empty-state-compact">
                <h4>${escapeHtml(t('settings.triggers.none'))}</h4>
                <p>${escapeHtml(t('settings.triggers.none_copy'))}</p>
            </div>
        `;
    }
    return `
        <div class="role-records trigger-records">
            ${triggerSettingsState.feishuTriggers.map(renderTriggerRecord).join('')}
        </div>
    `;
}

function renderTriggerRecord(trigger) {
    return `
        <div class="role-record trigger-record${
            triggerSettingsState.editingTriggerId === trigger.trigger_id ? ' active' : ''
        }" data-trigger-id="${escapeHtml(trigger.trigger_id)}">
            <div class="role-record-main">
                <div class="role-record-title-row">
                    <div class="role-record-title">${escapeHtml(
                        resolveTriggerLabel(trigger),
                    )}</div>
                    <div class="role-record-id">${escapeHtml(trigger.name)}</div>
                    <div class="profile-card-chips role-record-chips">
                        <span class="profile-card-chip${
                            isTriggerEnabled(trigger) ? ' profile-card-chip-accent' : ''
                        }">${escapeHtml(
                            isTriggerEnabled(trigger)
                                ? t('settings.field.enabled')
                                : t('settings.roles.disabled'),
                        )}</span>
                    </div>
                </div>
                <div class="role-record-meta">
                    <span>${escapeHtml(
                        `${t('settings.triggers.workspace')}: ${resolveWorkspaceId(
                            trigger.target_config,
                        )}`,
                    )}</span>
                    <span>${escapeHtml(
                        `${t('settings.triggers.rule')}: ${resolveTriggerRule(
                            trigger.source_config,
                        )}`,
                    )}</span>
                </div>
            </div>
            <div class="role-record-actions">
                <button class="settings-inline-action settings-list-action trigger-record-edit-btn" data-trigger-id="${escapeHtml(
                    trigger.trigger_id,
                )}" type="button">${escapeHtml(t('settings.roles.edit'))}</button>
            </div>
        </div>
    `;
}

function renderTriggerEditorSection() {
    if (!triggerSettingsState.editingTriggerDraft) {
        return '';
    }
    const trigger = triggerSettingsState.editingTriggerDraft;
    return `
        <section class="role-editor-section trigger-detail-section">
            <div class="role-editor-header">
                <div>
                    <h4>${escapeHtml(t('settings.triggers.editor'))}</h4>
                    <p>${escapeHtml(
                        triggerSettingsState.editingTriggerId
                            ? t('settings.triggers.editing_existing')
                            : t('settings.triggers.editing_new'),
                    )}</p>
                </div>
            </div>
            <div class="role-editor-sections">
                <section class="role-editor-section">
                    <div class="profile-editor-grid role-editor-grid">
                        <div class="form-group">
                            <label for="feishu-trigger-name-input">${escapeHtml(
                                t('settings.triggers.trigger_name'),
                            )}</label>
                            <input type="text" id="feishu-trigger-name-input" autocomplete="off" value="${escapeHtml(
                                trigger.name,
                            )}">
                        </div>
                        <div class="form-group">
                            <label for="feishu-trigger-display-name-input">${escapeHtml(
                                t('settings.triggers.display_name'),
                            )}</label>
                            <input type="text" id="feishu-trigger-display-name-input" autocomplete="off" value="${escapeHtml(
                                trigger.display_name,
                            )}">
                        </div>
                        <div class="form-group">
                            <label for="feishu-trigger-workspace-id-input">${escapeHtml(
                                t('settings.triggers.workspace'),
                            )}</label>
                            <input type="text" id="feishu-trigger-workspace-id-input" autocomplete="off" value="${escapeHtml(
                                resolveWorkspaceId(trigger.target_config),
                            )}">
                        </div>
                        <div class="form-group">
                            <label for="feishu-trigger-rule-input">${escapeHtml(
                                t('settings.triggers.rule'),
                            )}</label>
                            <select id="feishu-trigger-rule-input">
                                <option value="mention_only"${
                                    resolveTriggerRule(trigger.source_config) ===
                                    'mention_only'
                                        ? ' selected'
                                        : ''
                                }>mention_only</option>
                                <option value="all_messages"${
                                    resolveTriggerRule(trigger.source_config) ===
                                    'all_messages'
                                        ? ' selected'
                                        : ''
                                }>all_messages</option>
                            </select>
                        </div>
                    </div>
                    <div class="profile-default-row trigger-enabled-row">
                        <input type="checkbox" id="feishu-trigger-enabled-input"${
                            isTriggerEnabled(trigger) ? ' checked' : ''
                        }>
                        <label for="feishu-trigger-enabled-input">${escapeHtml(
                            t('settings.triggers.enable_trigger'),
                        )}</label>
                    </div>
                </section>
                <section class="role-editor-section">
                    <div class="profile-editor-grid role-editor-grid">
                        <div class="form-group">
                            <label>${escapeHtml(t('settings.triggers.provider'))}</label>
                            <div class="trigger-readonly-value">${escapeHtml(
                                FEISHU_PLATFORM,
                            )}</div>
                        </div>
                        <div class="form-group">
                            <label>${escapeHtml(
                                t('settings.triggers.source_type'),
                            )}</label>
                            <div class="trigger-readonly-value">${escapeHtml(
                                FEISHU_SOURCE_TYPE,
                            )}</div>
                        </div>
                    </div>
                </section>
            </div>
        </section>
    `;
}

function bindProviderDetailHandlers() {
    const backButton = document.getElementById('trigger-provider-back-btn');
    if (backButton) {
        backButton.onclick = handleBackToTriggerPlatforms;
    }
    const host = document.getElementById('trigger-provider-detail');
    if (host) {
        host.querySelectorAll('.trigger-record').forEach(button => {
            button.onclick = () => {
                openTriggerEditor(button.dataset.triggerId);
            };
        });
        host.querySelectorAll('.trigger-record-edit-btn').forEach(button => {
            button.onclick = event => {
                event?.stopPropagation?.();
                openTriggerEditor(button.dataset.triggerId);
            };
        });
    }
}

function syncDraftValuesToInputs() {
    FEISHU_CREDENTIAL_FIELDS.forEach(field => {
        const input = document.getElementById(field.inputId);
        if (input) {
            input.value = formatCredentialDraftValue(field.envKey);
            input.oninput = () => {
                triggerSettingsState.credentialDraft[field.envKey] = normalizeCredentialValue(
                    field.envKey,
                    input.value,
                );
                renderTriggerActionMode();
            };
        }
    });
    bindTriggerDraftInput('feishu-trigger-name-input', value => {
        ensureEditingTriggerDraft();
        triggerSettingsState.editingTriggerDraft.name = value;
    });
    bindTriggerDraftInput('feishu-trigger-display-name-input', value => {
        ensureEditingTriggerDraft();
        triggerSettingsState.editingTriggerDraft.display_name = value;
    });
    bindTriggerDraftInput('feishu-trigger-workspace-id-input', value => {
        ensureEditingTriggerDraft();
        triggerSettingsState.editingTriggerDraft.target_config = {
            workspace_id: value,
        };
    });
    const ruleInput = document.getElementById('feishu-trigger-rule-input');
    if (ruleInput) {
        ruleInput.onchange = () => {
            ensureEditingTriggerDraft();
            triggerSettingsState.editingTriggerDraft.source_config = {
                provider: FEISHU_PLATFORM,
                trigger_rule: String(ruleInput.value || DEFAULT_TRIGGER_RULE),
            };
            renderTriggerActionMode();
        };
    }
    const enabledInput = document.getElementById('feishu-trigger-enabled-input');
    if (enabledInput) {
        enabledInput.onchange = () => {
            ensureEditingTriggerDraft();
            triggerSettingsState.editingTriggerDraft.status = enabledInput.checked
                ? 'enabled'
                : 'disabled';
            renderTriggerActionMode();
        };
    }
}

function bindTriggerDraftInput(id, applyValue) {
    const input = document.getElementById(id);
    if (!input) {
        return;
    }
    input.oninput = () => {
        applyValue(String(input.value || ''));
        renderTriggerActionMode();
    };
}

function ensureEditingTriggerDraft() {
    if (triggerSettingsState.editingTriggerDraft) {
        return;
    }
    triggerSettingsState.editingTriggerDraft = createDefaultTriggerDraft();
    triggerSettingsState.editingTriggerId = '';
}

function openFeishuProviderDetail() {
    triggerSettingsState.view = 'provider_detail';
    triggerSettingsState.statusMessage = '';
    triggerSettingsState.statusTone = '';
    renderTriggerSettingsPanel();
}

function handleBackToTriggerPlatforms() {
    triggerSettingsState.view = 'platform_list';
    triggerSettingsState.credentialDraft = { ...triggerSettingsState.credentialSnapshot };
    triggerSettingsState.editingTriggerDraft = null;
    triggerSettingsState.editingTriggerId = '';
    triggerSettingsState.statusMessage = '';
    triggerSettingsState.statusTone = '';
    renderTriggerSettingsPanel();
}

function handleAddTrigger() {
    if (triggerSettingsState.view !== 'provider_detail') {
        return;
    }
    triggerSettingsState.editingTriggerId = '';
    triggerSettingsState.editingTriggerDraft = createDefaultTriggerDraft();
    triggerSettingsState.statusMessage = '';
    triggerSettingsState.statusTone = '';
    renderTriggerSettingsPanel();
}

function openTriggerEditor(triggerId) {
    const safeTriggerId = String(triggerId || '').trim();
    const source = triggerSettingsState.feishuTriggers.find(
        trigger => trigger.trigger_id === safeTriggerId,
    );
    if (!source) {
        return;
    }
    triggerSettingsState.view = 'provider_detail';
    triggerSettingsState.editingTriggerId = source.trigger_id;
    triggerSettingsState.editingTriggerDraft = cloneTriggerRecord(source);
    triggerSettingsState.statusMessage = '';
    triggerSettingsState.statusTone = '';
    renderTriggerSettingsPanel();
}

function createDefaultTriggerDraft() {
    return {
        trigger_id: '',
        name: createDefaultTriggerName(),
        display_name: '',
        status: 'enabled',
        source_config: {
            provider: FEISHU_PLATFORM,
            trigger_rule: DEFAULT_TRIGGER_RULE,
        },
        target_config: {
            workspace_id: DEFAULT_WORKSPACE_ID,
        },
    };
}

function createDefaultTriggerName() {
    const existingNames = new Set(
        triggerSettingsState.feishuTriggers.map(trigger => String(trigger.name || '').trim()),
    );
    let suffix = triggerSettingsState.feishuTriggers.length + 1;
    let candidate = `feishu_trigger_${suffix}`;
    while (existingNames.has(candidate)) {
        suffix += 1;
        candidate = `feishu_trigger_${suffix}`;
    }
    return candidate;
}

function cloneTriggerRecord(trigger) {
    return {
        trigger_id: String(trigger?.trigger_id || '').trim(),
        name: String(trigger?.name || '').trim(),
        display_name: String(trigger?.display_name || '').trim(),
        status: isTriggerEnabled(trigger) ? 'enabled' : 'disabled',
        source_config: {
            provider: FEISHU_PLATFORM,
            trigger_rule: resolveTriggerRule(trigger?.source_config),
        },
        target_config: {
            workspace_id: resolveWorkspaceId(trigger?.target_config),
        },
    };
}

async function handleSaveTriggerSettings() {
    try {
        syncCredentialDraftFromInputs();
        const draft = readTriggerDraftFromInputs();
        await saveFeishuCredentialsIfNeeded();
        if (draft) {
            await saveTriggerDraft(draft);
        }
        showToast({
            title: t('settings.triggers.saved'),
            message: t('settings.triggers.saved_message'),
            tone: 'success',
        });
        await loadTriggerSettingsPanel({
            openProvider: FEISHU_PLATFORM,
        });
    } catch (error) {
        renderStatus(error?.message || 'Failed to save trigger settings.', 'danger');
        showToast({
            title: t('settings.triggers.save_failed'),
            message: error?.message || 'Failed to save trigger settings.',
            tone: 'danger',
        });
    }
}

function handleCancelTriggerSettings() {
    triggerSettingsState.credentialDraft = { ...triggerSettingsState.credentialSnapshot };
    triggerSettingsState.editingTriggerDraft = null;
    triggerSettingsState.editingTriggerId = '';
    triggerSettingsState.statusMessage = '';
    triggerSettingsState.statusTone = '';
    renderTriggerSettingsPanel();
}

async function saveFeishuCredentialsIfNeeded() {
    for (const field of FEISHU_CREDENTIAL_FIELDS) {
        const nextValue = normalizeCredentialValue(
            field.envKey,
            triggerSettingsState.credentialDraft[field.envKey] || '',
        );
        const sourceValue = String(
            triggerSettingsState.credentialSnapshot[field.envKey] || '',
        );
        if (nextValue === sourceValue) {
            continue;
        }
        if (nextValue.trim()) {
            await saveEnvironmentVariable('app', field.envKey, {
                source_key: sourceValue ? field.envKey : null,
                value: nextValue,
            });
        } else if (sourceValue) {
            await deleteEnvironmentVariable('app', field.envKey);
        }
    }
}

async function saveTriggerDraft(draft) {
    if (draft.trigger_id) {
        await updateTrigger(draft.trigger_id, {
            name: draft.name,
            display_name: draft.display_name || null,
            source_config: {
                provider: FEISHU_PLATFORM,
                trigger_rule: resolveTriggerRule(draft.source_config),
            },
            target_config: {
                workspace_id: resolveWorkspaceId(draft.target_config),
            },
        });
        const source = triggerSettingsState.feishuTriggers.find(
            trigger => trigger.trigger_id === draft.trigger_id,
        );
        const sourceEnabled = source ? isTriggerEnabled(source) : true;
        const nextEnabled = isTriggerEnabled(draft);
        if (nextEnabled !== sourceEnabled) {
            if (nextEnabled) {
                await enableTrigger(draft.trigger_id);
            } else {
                await disableTrigger(draft.trigger_id);
            }
        }
        return;
    }
    await createTrigger({
        name: draft.name,
        display_name: draft.display_name || null,
        source_type: FEISHU_SOURCE_TYPE,
        source_config: {
            provider: FEISHU_PLATFORM,
            trigger_rule: resolveTriggerRule(draft.source_config),
        },
        auth_policies: [],
        target_config: {
            workspace_id: resolveWorkspaceId(draft.target_config),
        },
        enabled: isTriggerEnabled(draft),
    });
}

function readTriggerDraftFromInputs() {
    if (!triggerSettingsState.editingTriggerDraft) {
        return null;
    }
    const draft = cloneTriggerRecord(triggerSettingsState.editingTriggerDraft);
    draft.name = String(
        document.getElementById('feishu-trigger-name-input')?.value || draft.name,
    ).trim();
    draft.display_name = String(
        document.getElementById('feishu-trigger-display-name-input')?.value ||
            draft.display_name,
    ).trim();
    draft.target_config = {
        workspace_id: String(
            document.getElementById('feishu-trigger-workspace-id-input')?.value ||
                resolveWorkspaceId(draft.target_config),
        ).trim(),
    };
    draft.source_config = {
        provider: FEISHU_PLATFORM,
        trigger_rule:
            String(
                document.getElementById('feishu-trigger-rule-input')?.value ||
                    resolveTriggerRule(draft.source_config),
            ).trim() || DEFAULT_TRIGGER_RULE,
    };
    draft.status =
        document.getElementById('feishu-trigger-enabled-input')?.checked === false
            ? 'disabled'
            : 'enabled';
    if (!draft.name) {
        throw new Error(t('settings.triggers.missing_name'));
    }
    if (!resolveWorkspaceId(draft.target_config)) {
        throw new Error(t('settings.triggers.missing_workspace'));
    }
    triggerSettingsState.editingTriggerDraft = draft;
    return draft;
}

function renderTriggerActionMode() {
    const actionsBar = document.getElementById('settings-actions-bar');
    const isDetailView = triggerSettingsState.view === 'provider_detail';
    const showSaveMode =
        isDetailView &&
        (triggerSettingsState.editingTriggerDraft !== null || hasCredentialChanges());
    const showAddMode = isDetailView && !showSaveMode;
    if (actionsBar) {
        actionsBar.style.display = isDetailView ? 'flex' : 'none';
    }
    setActionDisplay('add-trigger-btn', showAddMode);
    setActionDisplay('save-trigger-btn', showSaveMode);
    setActionDisplay('cancel-trigger-btn', showSaveMode);
}

function hasCredentialChanges() {
    return FEISHU_CREDENTIAL_FIELDS.some(
        field =>
            normalizeCredentialValue(
                field.envKey,
                triggerSettingsState.credentialDraft[field.envKey] || '',
            ) !==
            normalizeCredentialValue(
                field.envKey,
                triggerSettingsState.credentialSnapshot[field.envKey] || '',
            ),
    );
}

function syncCredentialDraftFromInputs() {
    FEISHU_CREDENTIAL_FIELDS.forEach(field => {
        const input = document.getElementById(field.inputId);
        if (input) {
            triggerSettingsState.credentialDraft[field.envKey] = normalizeCredentialValue(
                field.envKey,
                input.value || '',
            );
        }
    });
}

function renderStatus(message, tone) {
    triggerSettingsState.statusMessage = message;
    triggerSettingsState.statusTone = tone || '';
    renderTriggerStatus();
}

function renderTriggerStatus() {
    const statusEl = document.getElementById('trigger-editor-status');
    if (!statusEl) {
        return;
    }
    statusEl.className = 'role-editor-status';
    if (!triggerSettingsState.statusMessage) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        return;
    }
    statusEl.style.display = 'block';
    if (triggerSettingsState.statusTone) {
        statusEl.classList.add(`role-editor-status-${triggerSettingsState.statusTone}`);
    }
    statusEl.textContent = triggerSettingsState.statusMessage;
}

function renderTriggerLoadError(message) {
    const listHost = document.getElementById('trigger-platform-list');
    const panel = document.getElementById('trigger-provider-detail-panel');
    if (listHost) {
        listHost.style.display = 'block';
        listHost.innerHTML = `
            <div class="settings-empty-state">
                <h4>${escapeHtml(t('settings.triggers.load_failed'))}</h4>
                <p>${escapeHtml(message)}</p>
            </div>
        `;
    }
    if (panel) {
        panel.style.display = 'none';
    }
    triggerSettingsState.view = 'platform_list';
    triggerSettingsState.statusMessage = '';
    triggerSettingsState.statusTone = '';
    renderTriggerActionMode();
}

function resolveTriggerLabel(trigger) {
    return (
        String(trigger?.display_name || '').trim() ||
        String(trigger?.name || '').trim() ||
        t('settings.triggers.unnamed')
    );
}

function resolveWorkspaceId(targetConfig) {
    return String(targetConfig?.workspace_id || DEFAULT_WORKSPACE_ID).trim()
        || DEFAULT_WORKSPACE_ID;
}

function resolveTriggerRule(sourceConfig) {
    return String(sourceConfig?.trigger_rule || DEFAULT_TRIGGER_RULE).trim()
        || DEFAULT_TRIGGER_RULE;
}

function isTriggerEnabled(trigger) {
    return String(trigger?.status || '').trim().toLowerCase() === 'enabled';
}

function missingFeishuCredentialCount() {
    return FEISHU_CREDENTIAL_FIELDS.filter(
        field =>
            field.required &&
            !String(triggerSettingsState.credentialDraft[field.envKey] || '').trim(),
    ).length;
}

function formatCredentialSummary() {
    const missingCount = missingFeishuCredentialCount();
    if (missingCount === 0) {
        return t('settings.triggers.credentials_ready');
    }
    return t('settings.triggers.credentials_missing_count').replace(
        '{count}',
        String(missingCount),
    );
}

function formatCredentialDraftValue(envKey) {
    return normalizeCredentialValue(
        envKey,
        triggerSettingsState.credentialDraft[envKey] || '',
    );
}

function normalizeCredentialValue(envKey, value) {
    return String(value || '');
}

function setActionDisplay(id, visible) {
    const button = document.getElementById(id);
    if (button) {
        button.style.display = visible ? 'inline-flex' : 'none';
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
