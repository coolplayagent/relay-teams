/**
 * components/rounds/utils.js
 * Shared utility helpers for rounds timeline rendering.
 */
import { t } from '../../utils/i18n.js';

export function roundSectionId(runId) {
    return `round-${String(runId).replace(/[^a-zA-Z0-9_-]/g, '_')}`;
}

export function esc(text) {
    if (!text) return '';
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

export function roundStateTone(round) {
    const phase = String(round?.run_phase || '');
    const status = String(round?.run_status || '');
    if (phase === 'awaiting_tool_approval' || phase === 'awaiting_subagent_followup') {
        return 'warning';
    }
    switch (status) {
        case 'running':
            return 'running';
        case 'completed':
            return 'success';
        case 'failed':
            return 'danger';
        case 'stopped':
            return 'stopped';
        default:
            return 'idle';
    }
}

export function roundStateLabel(round) {
    const phase = String(round?.run_phase || '');
    const status = String(round?.run_status || '');
    if (phase === 'awaiting_tool_approval') return t('rounds.state.awaiting_approval');
    if (phase === 'awaiting_subagent_followup') return t('rounds.state.awaiting_followup');
    switch (status) {
        case 'queued':
            return t('rounds.state.queued');
        case 'running':
            return t('rounds.state.running');
        case 'paused':
            return t('rounds.state.paused');
        case 'stopped':
            return t('rounds.state.stopped');
        case 'completed':
            return t('rounds.state.completed');
        case 'failed':
            return t('rounds.state.failed');
        default:
            return '';
    }
}
