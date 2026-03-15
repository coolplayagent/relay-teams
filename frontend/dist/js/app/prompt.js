/**
 * app/prompt.js
 * Prompt send flow: live round bootstrap and SSE stream start.
 */
import { appendRoundUserMessage, createLiveRound } from '../components/rounds.js';
import { refreshVisibleContextIndicators } from '../components/contextIndicators.js';
import { clearAllStreamState } from '../components/messageRenderer.js';
import {
    hydrateSessionView,
    startSessionContinuity,
} from './recovery.js';
import { state } from '../core/state.js';
import { startIntentStream } from '../core/stream.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';

const APPROVAL_MODE_STORAGE_KEY = 'agent_teams_approval_mode';
const THINKING_MODE_STORAGE_KEY = 'agent_teams_thinking_enabled';
const THINKING_EFFORT_STORAGE_KEY = 'agent_teams_thinking_effort';

export function initializeApprovalModeToggle() {
    const savedMode = readSavedApprovalMode();
    applyApprovalMode(savedMode, { persist: false });
    if (!els.yoloModeToggle) return;
    els.yoloModeToggle.checked = savedMode === 'yolo';
    els.yoloModeToggle.addEventListener('change', () => {
        const nextMode = els.yoloModeToggle.checked ? 'yolo' : 'standard';
        applyApprovalMode(nextMode);
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
    refreshVisibleContextIndicators({ immediate: true });
    clearAllStreamState();

    sysLog('Sending prompt');
    startSessionContinuity(state.currentSessionId);
    await startIntentStream(
        text,
        state.currentSessionId,
        async sid => hydrateSessionView(sid, { includeRounds: true, quiet: true }),
        {
            approvalMode: state.approvalMode,
            thinking: state.thinking,
            onRunCreated: (run) => {
                createLiveRound(run.run_id, text);
                appendRoundUserMessage(run.run_id, text);
            },
        },
    );
}

function readSavedApprovalMode() {
    try {
        const stored = localStorage.getItem(APPROVAL_MODE_STORAGE_KEY);
        return stored === 'standard' ? 'standard' : 'yolo';
    } catch (_error) {
        return 'yolo';
    }
}

function applyApprovalMode(mode, { persist = true } = {}) {
    const safeMode = mode === 'standard' ? 'standard' : 'yolo';
    state.approvalMode = safeMode;
    if (els.yoloModeToggle) {
        els.yoloModeToggle.checked = safeMode === 'yolo';
    }
    if (!persist) return;
    try {
        localStorage.setItem(APPROVAL_MODE_STORAGE_KEY, safeMode);
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
