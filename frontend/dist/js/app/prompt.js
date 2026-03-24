/**
 * app/prompt.js
 * Prompt send flow: live round bootstrap and SSE stream start.
 */
import { appendRoundUserMessage, createLiveRound } from '../components/rounds.js';
import { refreshVisibleContextIndicators } from '../components/contextIndicators.js';
import { clearAllStreamState } from '../components/messageRenderer.js';
import {
    fetchRoleConfigOptions,
    fetchOrchestrationConfig,
    updateSessionTopology,
} from '../core/api.js';
import {
    hydrateSessionView,
    startSessionContinuity,
} from './recovery.js';
import {
    applyCurrentSessionRecord,
    getNormalModeRoles,
    setCoordinatorRoleId,
    setMainAgentRoleId,
    setNormalModeRoles,
    state,
} from '../core/state.js';
import { startIntentStream } from '../core/stream.js';
import { els } from '../utils/dom.js';
import { showToast } from '../utils/feedback.js';
import { t } from '../utils/i18n.js';
import { sysLog } from '../utils/logger.js';

const YOLO_STORAGE_KEY = 'agent_teams_yolo';
const THINKING_MODE_STORAGE_KEY = 'agent_teams_thinking_enabled';
const THINKING_EFFORT_STORAGE_KEY = 'agent_teams_thinking_effort';
let orchestrationConfig = {
    default_orchestration_preset_id: '',
    presets: [],
};
let topologyControlsBound = false;

export function initializeYoloToggle() {
    const savedYolo = readSavedYolo();
    applyYolo(savedYolo, { persist: false });
    if (!els.yoloToggle) return;
    els.yoloToggle.checked = savedYolo;
    els.yoloToggle.addEventListener('change', () => {
        applyYolo(els.yoloToggle.checked);
    });
}

export function initializeThinkingControls() {
    const savedThinking = readSavedThinkingState();
    applyThinkingState(savedThinking, { persist: false });
    if (els.thinkingModeToggle) {
        els.thinkingModeToggle.checked = savedThinking.enabled === true;
        els.thinkingModeToggle.addEventListener('change', () => {
            applyThinkingState({
                enabled: els.thinkingModeToggle.checked,
                effort: state.thinking?.effort || 'medium',
            });
        });
    }
    if (els.thinkingEffortSelect) {
        els.thinkingEffortSelect.value = savedThinking.effort || 'medium';
        els.thinkingEffortSelect.addEventListener('change', () => {
            applyThinkingState({
                enabled: state.thinking?.enabled === true,
                effort: String(els.thinkingEffortSelect.value || 'medium'),
            });
        });
    }
}

export async function initializeSessionTopologyControls() {
    await refreshRoleConfigOptions({ refreshControls: false });
    await refreshOrchestrationConfig({ refreshControls: false });
    bindSessionTopologyControls();
    refreshSessionTopologyControls();
}

export function refreshSessionTopologyControls() {
    syncThinkingControls();
    if (!els.sessionModeLock || !els.sessionModeNormalBtn || !els.sessionModeOrchestrationBtn) {
        return;
    }

    const mode = state.currentSessionMode === 'orchestration' ? 'orchestration' : 'normal';
    const normalModeRoles = getNormalModeRoles();
    const presets = Array.isArray(orchestrationConfig?.presets) ? orchestrationConfig.presets : [];
    const hasNormalModeRoles = normalModeRoles.length > 0;
    const hasPresets = presets.length > 0;
    const canSwitch = !!state.currentSessionId && state.currentSessionCanSwitchMode === true && !state.isGenerating;
    const disabledReason = resolveTopologyDisabledReason({ canSwitch, hasPresets });
    const orchestrationDisabled = !canSwitch || !hasPresets;

    els.sessionModeLock.title = disabledReason;
    els.sessionModeNormalBtn.disabled = !canSwitch;
    els.sessionModeOrchestrationBtn.disabled = orchestrationDisabled;
    els.sessionModeNormalBtn.classList.toggle('active', mode === 'normal');
    els.sessionModeOrchestrationBtn.classList.toggle('active', mode === 'orchestration');

    if (els.sessionModeLabel) {
        els.sessionModeLabel.textContent = mode === 'orchestration'
            ? t('composer.mode_orchestration')
            : t('composer.mode_normal');
    }

    syncSessionTopologyFieldVisibility(mode);
    if (els.normalRoleSelect) {
        const selectedRoleId = resolveSelectedNormalRoleId();
        els.normalRoleSelect.innerHTML = buildNormalRoleOptions(selectedRoleId);
        els.normalRoleSelect.disabled = !canSwitch || mode !== 'normal' || !hasNormalModeRoles;
        if (selectedRoleId) {
            els.normalRoleSelect.value = selectedRoleId;
        }
    }

    if (els.orchestrationPresetSelect) {
        const selectedPresetId = resolveSelectedPresetId();
        els.orchestrationPresetSelect.innerHTML = buildPresetOptions(selectedPresetId);
        els.orchestrationPresetSelect.disabled = !canSwitch || mode !== 'orchestration' || !hasPresets;
        if (selectedPresetId) {
            els.orchestrationPresetSelect.value = selectedPresetId;
        }
    }
}

export async function refreshOrchestrationConfig({ refreshControls = true } = {}) {
    try {
        const config = await fetchOrchestrationConfig();
        orchestrationConfig = normalizeOrchestrationConfig(config);
    } catch (error) {
        orchestrationConfig = normalizeOrchestrationConfig(null);
        sysLog(error.message || 'Failed to load orchestration settings', 'log-error');
    }
    if (refreshControls) {
        refreshSessionTopologyControls();
    }
}

async function refreshRoleConfigOptions({ refreshControls = true } = {}) {
    try {
        const options = await fetchRoleConfigOptions();
        setCoordinatorRoleId(options?.coordinator_role_id || '');
        setMainAgentRoleId(options?.main_agent_role_id || '');
        setNormalModeRoles(options?.normal_mode_roles || []);
    } catch (error) {
        setCoordinatorRoleId('');
        setMainAgentRoleId('');
        setNormalModeRoles([]);
        sysLog(error.message || 'Failed to load role options', 'log-error');
    }
    if (refreshControls) {
        refreshSessionTopologyControls();
    }
}

export async function handleSend() {
    const text = els.promptInput.value.trim();
    if (!text) return;
    if (state.isGenerating) {
        sysLog('A run is still in progress. Please wait for completion before sending the next message.', 'log-info');
        return;
    }
    if (!state.currentSessionId) {
        sysLog('No active session selected. Please select or create a session first.', 'log-error');
        return;
    }
    if (state.pausedSubagent) {
        const paused = state.pausedSubagent;
        sysLog(
            `Subagent is paused (${paused.roleId || paused.instanceId}). Send a follow-up in that subagent panel first.`,
            'log-error',
        );
        return;
    }

    els.promptInput.value = '';
    els.promptInput.style.height = 'auto';
    state.instanceRoleMap = {};
    state.roleInstanceMap = {};
    state.taskInstanceMap = {};
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    state.autoSwitchedSubagentInstances = {};
    state.activeRunId = null;
    state.isGenerating = true;
    if (els.sendBtn) els.sendBtn.disabled = true;
    if (els.promptInput) els.promptInput.disabled = true;
    if (els.stopBtn) {
        els.stopBtn.style.display = 'inline-flex';
        els.stopBtn.disabled = false;
    }
    refreshSessionTopologyControls();
    refreshVisibleContextIndicators({ immediate: true });
    clearAllStreamState({ preserveOverlay: true });

    sysLog('Sending prompt');
    startSessionContinuity(state.currentSessionId);
    await startIntentStream(
        text,
        state.currentSessionId,
        async sid => hydrateSessionView(sid, { includeRounds: true, quiet: true }),
        {
            yolo: state.yolo,
            thinking: state.thinking,
            onRunCreated: (run) => {
                state.currentSessionCanSwitchMode = false;
                refreshSessionTopologyControls();
                createLiveRound(run.run_id, text);
                appendRoundUserMessage(run.run_id, text);
            },
        },
    );
}

function bindSessionTopologyControls() {
    if (topologyControlsBound) {
        return;
    }
    topologyControlsBound = true;

    if (els.sessionModeNormalBtn) {
        els.sessionModeNormalBtn.addEventListener('click', () => {
            void handleTopologyModeChange('normal');
        });
    }
    if (els.sessionModeOrchestrationBtn) {
        els.sessionModeOrchestrationBtn.addEventListener('click', () => {
            void handleTopologyModeChange('orchestration');
        });
    }
    if (els.orchestrationPresetSelect) {
        els.orchestrationPresetSelect.addEventListener('change', event => {
            const nextPresetId = String(event?.target?.value || '').trim();
            if (!nextPresetId) {
                refreshSessionTopologyControls();
                return;
            }
            void persistSessionTopology('orchestration', {
                orchestrationPresetId: nextPresetId,
            });
        });
    }
    if (els.normalRoleSelect) {
        els.normalRoleSelect.addEventListener('change', event => {
            const nextRoleId = String(event?.target?.value || '').trim();
            if (!nextRoleId) {
                refreshSessionTopologyControls();
                return;
            }
            void persistSessionTopology('normal', {
                normalRootRoleId: nextRoleId,
            });
        });
    }
    if (typeof document.addEventListener === 'function') {
        document.addEventListener('orchestration-settings-updated', () => {
            void refreshOrchestrationConfig({ refreshControls: true });
        });
        document.addEventListener('agent-teams-session-selected', () => {
            void refreshRoleConfigOptions({ refreshControls: true });
        });
        document.addEventListener('agent-teams-language-changed', () => {
            refreshSessionTopologyControls();
        });
    }
}

async function handleTopologyModeChange(nextMode) {
    const normalizedMode = nextMode === 'orchestration' ? 'orchestration' : 'normal';
    if (normalizedMode === state.currentSessionMode) {
        return;
    }
    if (!state.currentSessionId) {
        return;
    }
    if (normalizedMode === 'orchestration' && !resolveSelectedPresetId()) {
        showToast({
            title: 'No Preset Available',
            message: resolveMissingPresetMessage(),
            tone: 'warning',
        });
        return;
    }
    await persistSessionTopology(normalizedMode, {
        orchestrationPresetId: normalizedMode === 'orchestration' ? resolveSelectedPresetId() : null,
        normalRootRoleId: normalizedMode === 'normal' ? resolveSelectedNormalRoleId() : null,
    });
}

async function persistSessionTopology(
    sessionMode,
    {
        orchestrationPresetId = null,
        normalRootRoleId = null,
    } = {},
) {
    if (!state.currentSessionId) {
        return;
    }
    try {
        const updated = await updateSessionTopology(state.currentSessionId, {
            session_mode: sessionMode,
            normal_root_role_id: sessionMode === 'normal' ? normalRootRoleId : undefined,
            orchestration_preset_id: sessionMode === 'orchestration' ? orchestrationPresetId : null,
        });
        applyCurrentSessionRecord(updated);
        refreshSessionTopologyControls();
        sysLog(
            `Session mode updated: ${
                sessionMode === 'orchestration'
                    ? t('composer.mode_orchestration')
                    : t('composer.mode_normal')
            }`,
        );
    } catch (error) {
        refreshSessionTopologyControls();
        showToast({
            title: 'Mode Update Failed',
            message: error.message || 'Failed to update session mode.',
            tone: 'danger',
        });
    }
}

function resolveTopologyDisabledReason({ canSwitch, hasPresets }) {
    if (!state.currentSessionId) {
        return t('composer.session_mode_title');
    }
    if (state.isGenerating) {
        return 'The session mode is locked while a run is active.';
    }
    if (!canSwitch) {
        return 'Only sessions that have not started their first run can switch mode.';
    }
    if (!hasPresets) {
        return resolveMissingPresetMessage();
    }
    return 'Only sessions that have not started their first run can switch mode.';
}

function resolveSelectedPresetId() {
    const presets = Array.isArray(orchestrationConfig?.presets) ? orchestrationConfig.presets : [];
    const currentPresetId = String(state.currentOrchestrationPresetId || '').trim();
    if (currentPresetId && presets.some(preset => preset?.preset_id === currentPresetId)) {
        return currentPresetId;
    }
    const defaultPresetId = String(orchestrationConfig?.default_orchestration_preset_id || '').trim();
    if (defaultPresetId && presets.some(preset => preset?.preset_id === defaultPresetId)) {
        return defaultPresetId;
    }
    return String(presets[0]?.preset_id || '').trim();
}

function resolveSelectedNormalRoleId() {
    const roles = getNormalModeRoles();
    if (roles.length === 0) {
        return '';
    }
    const currentRoleId = String(state.currentNormalRootRoleId || '').trim();
    if (currentRoleId && roles.some(role => role?.role_id === currentRoleId)) {
        return currentRoleId;
    }
    const mainAgentRoleId = String(state.mainAgentRoleId || '').trim();
    if (mainAgentRoleId && roles.some(role => role?.role_id === mainAgentRoleId)) {
        return mainAgentRoleId;
    }
    return String(roles[0]?.role_id || '').trim();
}

function buildNormalRoleOptions(selectedRoleId) {
    const roles = getNormalModeRoles();
    if (roles.length === 0) {
        return `<option value="">${escapeHtml(t('composer.no_roles'))}</option>`;
    }
    return roles.map(role => {
        const roleId = String(role?.role_id || '').trim();
        const name = String(role?.name || roleId || 'Role');
        const selected = roleId === selectedRoleId ? ' selected' : '';
        return `<option value="${escapeHtml(roleId)}"${selected}>${escapeHtml(name)}</option>`;
    }).join('');
}

function buildPresetOptions(selectedPresetId) {
    const presets = Array.isArray(orchestrationConfig?.presets) ? orchestrationConfig.presets : [];
    if (presets.length === 0) {
        return `<option value="">${escapeHtml(t('composer.no_presets'))}</option>`;
    }
    return presets.map(preset => {
        const presetId = String(preset?.preset_id || '').trim();
        const name = String(preset?.name || presetId || 'Preset');
        const selected = presetId === selectedPresetId ? ' selected' : '';
        return `<option value="${escapeHtml(presetId)}"${selected}>${escapeHtml(name)}</option>`;
    }).join('');
}

function resolveMissingPresetMessage() {
    return 'Create an orchestration preset in Settings before switching to Orchestrated Mode.';
}

function syncSessionTopologyFieldVisibility(mode) {
    const safeMode = mode === 'orchestration' ? 'orchestration' : 'normal';
    if (els.normalRoleField) {
        const showNormalRole = safeMode === 'normal';
        els.normalRoleField.hidden = !showNormalRole;
        els.normalRoleField.style.display = showNormalRole ? 'inline-flex' : 'none';
    }
    if (els.orchestrationPresetField) {
        const showPreset = safeMode === 'orchestration';
        els.orchestrationPresetField.hidden = !showPreset;
        els.orchestrationPresetField.style.display = showPreset ? 'inline-flex' : 'none';
    }
}

function normalizeOrchestrationConfig(config) {
    const presets = Array.isArray(config?.presets)
        ? config.presets.map(preset => ({
            preset_id: String(preset?.preset_id || '').trim(),
            name: String(preset?.name || '').trim(),
            description: String(preset?.description || '').trim(),
            role_ids: Array.isArray(preset?.role_ids)
                ? preset.role_ids.map(roleId => String(roleId || '').trim()).filter(Boolean)
                : [],
            orchestration_prompt: String(preset?.orchestration_prompt || '').trim(),
        })).filter(preset => preset.preset_id)
        : [];
    return {
        default_orchestration_preset_id: String(config?.default_orchestration_preset_id || '').trim(),
        presets,
    };
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function readSavedYolo() {
    try {
        return localStorage.getItem(YOLO_STORAGE_KEY) !== 'false';
    } catch (_error) {
        return true;
    }
}

function applyYolo(nextValue, { persist = true } = {}) {
    const safeYolo = nextValue === true;
    state.yolo = safeYolo;
    if (els.yoloToggle) {
        els.yoloToggle.checked = safeYolo;
    }
    if (!persist) return;
    try {
        localStorage.setItem(YOLO_STORAGE_KEY, safeYolo ? 'true' : 'false');
    } catch (_error) {
        return;
    }
}

function readSavedThinkingState() {
    try {
        const enabled = localStorage.getItem(THINKING_MODE_STORAGE_KEY) === 'true';
        const effort = String(localStorage.getItem(THINKING_EFFORT_STORAGE_KEY) || 'medium');
        return {
            enabled,
            effort: normalizeThinkingEffort(effort),
        };
    } catch (_error) {
        return {
            enabled: false,
            effort: 'medium',
        };
    }
}

function applyThinkingState(nextState, { persist = true } = {}) {
    const enabled = nextState?.enabled === true;
    const effort = normalizeThinkingEffort(nextState?.effort);
    state.thinking = {
        enabled,
        effort,
    };
    if (els.thinkingModeToggle) {
        els.thinkingModeToggle.checked = enabled;
    }
    if (els.thinkingEffortSelect) {
        els.thinkingEffortSelect.value = effort;
    }
    syncThinkingControls();
    if (!persist) return;
    try {
        localStorage.setItem(THINKING_MODE_STORAGE_KEY, enabled ? 'true' : 'false');
        localStorage.setItem(THINKING_EFFORT_STORAGE_KEY, effort);
    } catch (_error) {
        return;
    }
}

function normalizeThinkingEffort(value) {
    const safeValue = String(value || '').trim().toLowerCase();
    if (safeValue === 'minimal' || safeValue === 'low' || safeValue === 'high') {
        return safeValue;
    }
    return 'medium';
}

function syncThinkingControls() {
    const enabled = state.thinking?.enabled === true;
    if (els.thinkingEffortField) {
        els.thinkingEffortField.hidden = !enabled;
        els.thinkingEffortField.style.display = enabled ? 'inline-flex' : 'none';
    }
    if (els.thinkingEffortSelect) {
        els.thinkingEffortSelect.disabled = state.isGenerating || !enabled;
    }
}
