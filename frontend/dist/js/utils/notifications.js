/**
 * utils/notifications.js
 * Unified in-app notification helpers.
 */
import { initUiFeedback, showToast } from './feedback.js';

const notifiedApprovalToolCalls = new Set();
const notifiedKeys = new Set();
let titleFlashTimer = null;
let baseDocumentTitle = '';
let titleFlashBound = false;

export function primeNotificationPermission() {
    initUiFeedback();
}

export function notifyToolApprovalRequested(payload = {}) {
    return notifyFromRequest({
        notification_type: 'tool_approval_requested',
        title: 'Approval Required',
        body: buildApprovalBody(payload),
        channels: ['browser', 'toast'],
        dedupe_key: String(payload?.tool_call_id || ''),
    });
}

export function notifyFromRequest(payload = {}) {
    const dedupeKey = String(payload?.dedupe_key || '');
    if (dedupeKey && notifiedKeys.has(dedupeKey)) return false;

    const notificationType = String(payload?.notification_type || '');
    const toolCallId = String(payload?.context?.tool_call_id || payload?.tool_call_id || '');
    if (
        notificationType === 'tool_approval_requested'
        && toolCallId
        && notifiedApprovalToolCalls.has(toolCallId)
    ) {
        return false;
    }

    const title = String(payload?.title || 'Notification');
    const body = String(payload?.body || buildApprovalBody(payload?.context || payload));
    const channels = Array.isArray(payload?.channels) ? payload.channels : ['toast'];

    if (typeof window === 'undefined') {
        return false;
    }

    if (dedupeKey) {
        notifiedKeys.add(dedupeKey);
    }
    if (toolCallId) {
        notifiedApprovalToolCalls.add(toolCallId);
    }

    startTitleFlash(`[${title}]`);

    if (channels.includes('browser') || channels.includes('toast')) {
        showToast({
            title,
            message: body,
            tone: notificationType === 'tool_approval_requested' ? 'warning' : 'info',
            durationMs: channels.includes('browser') ? 5200 : 4000,
        });
        return true;
    }
    return false;
}

function buildApprovalBody(payload = {}) {
    const toolName = String(payload?.tool_name || 'tool');
    const roleId = String(payload?.role_id || '');
    return roleId
        ? `${roleId} requests approval for ${toolName}.`
        : `A tool call (${toolName}) is waiting for your approval.`;
}

function startTitleFlash(alertTitle) {
    if (typeof document === 'undefined' || typeof window === 'undefined') return;
    const currentlyFocused = document.visibilityState === 'visible' && document.hasFocus();
    if (currentlyFocused) return;

    bindTitleResetEvents();
    if (!baseDocumentTitle) {
        baseDocumentTitle = document.title || 'Agent Teams';
    }
    if (titleFlashTimer) return;

    let showAlert = true;
    titleFlashTimer = window.setInterval(() => {
        document.title = showAlert ? alertTitle : baseDocumentTitle;
        showAlert = !showAlert;
    }, 900);
}

function stopTitleFlash() {
    if (typeof window === 'undefined' || typeof document === 'undefined') return;
    if (titleFlashTimer) {
        window.clearInterval(titleFlashTimer);
        titleFlashTimer = null;
    }
    if (baseDocumentTitle) {
        document.title = baseDocumentTitle;
    }
}

function bindTitleResetEvents() {
    if (titleFlashBound || typeof document === 'undefined' || typeof window === 'undefined') return;
    const resetIfVisible = () => {
        if (document.visibilityState === 'visible' && document.hasFocus()) {
            stopTitleFlash();
        }
    };
    document.addEventListener('visibilitychange', resetIfVisible);
    window.addEventListener('focus', resetIfVisible);
    titleFlashBound = true;
}
