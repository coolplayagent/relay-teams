/**
 * app/prompt.js
 * Prompt send flow: live round bootstrap and SSE stream start.
 */
import { appendRoundUserMessage, createLiveRound } from '../components/rounds.js';
import { refreshVisibleContextIndicators } from '../components/contextIndicators.js';
import { clearAllStreamState } from '../components/messageRenderer.js';
import {
    fetchOrchestrationConfig,
    updateSessionTopology,
} from '../core/api.js';
import {
    hydrateSessionView,
    startSessionContinuity,
} from './recovery.js';
import {
    applyCurrentSessionRecord,
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
    await refreshOrchestrationConfig({ refreshControls: false });
    bindSessionTopologyControls();
    refreshSessionTopologyControls();
}

export function refreshSessionTopologyControls() {
    if (!els.sessionModeLock || !els.sessionModeNormalBtn || !els.sessionModeOrchestrationBtn) {
        return;
    }

    const mode = state.currentSessionMode === 'orchestration' ? 'orchestration' : 'normal';
    const presets = Array.isArray(orchestrationConfig?.presets) ? orchestrationConfig.presets : [];
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

    if (els.orchestrationPresetField) {
        els.orchestrationPresetField.hidden = mode !== 'orchestration';
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
    clearAllStreamState();

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
            void persistSessionTopology('orchestration', nextPresetId);
        });
    }
    if (typeof document.addEventListener === 'function') {
        document.addEventListener('orchestration-settings-updated', () => {
            void refreshOrchestrationConfig({ refreshControls: true });
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
    await persistSessionTopology(
        normalizedMode,
        normalizedMode === 'orchestration' ? resolveSelectedPresetId() : null,
    );
}

async function persistSessionTopology(sessionMode, orchestrationPresetId) {
    if (!state.currentSessionId) {
        return;
    }
    try {
        const updated = await updateSessionTopology(state.currentSessionId, {
            session_mode: sessionMode,
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
