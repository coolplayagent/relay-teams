/**
 * core/submission.js
 * Tracks a foreground prompt submission across draft session creation and run start.
 */
import { state } from './state.js';
import { els } from '../utils/dom.js';

let submissionTokenSeed = 0;
let activeSubmission = null;

export function beginForegroundSubmission() {
    const submission = {
        token: ++submissionTokenSeed,
        detached: false,
    };
    activeSubmission = submission;
    return submission;
}

export function isForegroundSubmissionActive(submission) {
    return !!(
        submission
        && activeSubmission === submission
        && submission.detached !== true
    );
}

export function finishForegroundSubmission(submission) {
    if (activeSubmission === submission) {
        activeSubmission = null;
    }
}

export function detachForegroundSubmission(options = {}) {
    if (!activeSubmission) {
        return false;
    }
    const focusPrompt = options.focusPrompt !== false;
    activeSubmission.detached = true;
    activeSubmission = null;
    state.isGenerating = false;
    if (els.sendBtn) {
        els.sendBtn.disabled = false;
    }
    if (els.promptInput) {
        els.promptInput.disabled = false;
        if (focusPrompt) {
            els.promptInput.focus?.();
        }
    }
    if (els.yoloToggle) {
        els.yoloToggle.disabled = false;
    }
    if (els.thinkingModeToggle) {
        els.thinkingModeToggle.disabled = false;
    }
    if (els.thinkingEffortSelect) {
        els.thinkingEffortSelect.disabled = false;
    }
    if (els.stopBtn) {
        els.stopBtn.style.display = 'none';
        els.stopBtn.disabled = true;
    }
    return true;
}
