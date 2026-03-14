/**
 * utils/feedback.js
 * Unified in-app toast and dialog feedback helpers.
 */

let toastStack = null;
let dialogRoot = null;
let activeDialog = null;
let dialogQueue = [];
let escapeHandlerBound = false;

export function initUiFeedback() {
    ensureHosts();
}

export function showToast({
    title = '',
    message = '',
    tone = 'info',
    durationMs = 4200,
} = {}) {
    const hosts = ensureHosts();
    if (!hosts?.toastStack) return;

    const toast = document.createElement('div');
    toast.className = `feedback-toast feedback-tone-${normalizeTone(tone)}`;
    toast.innerHTML = `
        <div class="feedback-toast-body">
            ${title ? `<div class="feedback-toast-title">${escapeHtml(title)}</div>` : ''}
            <div class="feedback-toast-message">${escapeHtml(message || title || 'Notification')}</div>
        </div>
        <button type="button" class="feedback-toast-close" aria-label="Dismiss notification">Close</button>
    `;

    const closeBtn = toast.querySelector('.feedback-toast-close');
    const dismiss = () => {
        toast.classList.add('is-leaving');
        window.setTimeout(() => {
            toast.remove();
        }, 160);
    };
    if (closeBtn) {
        closeBtn.onclick = dismiss;
    }

    hosts.toastStack.appendChild(toast);
    if (durationMs > 0) {
        window.setTimeout(dismiss, durationMs);
    }
}

export function showAlertDialog({
    title = 'Notice',
    message = '',
    tone = 'info',
    confirmLabel = 'OK',
} = {}) {
    return enqueueDialog({
        kind: 'alert',
        title,
        message,
        tone,
        confirmLabel,
        cancelLabel: '',
    });
}

export function showConfirmDialog({
    title = 'Confirm Action',
    message = '',
    tone = 'warning',
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
} = {}) {
    return enqueueDialog({
        kind: 'confirm',
        title,
        message,
        tone,
        confirmLabel,
        cancelLabel,
    });
}

export function showTextInputDialog({
    title = 'Input Required',
    message = '',
    tone = 'info',
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    placeholder = '',
    value = '',
} = {}) {
    return enqueueDialog({
        kind: 'prompt',
        title,
        message,
        tone,
        confirmLabel,
        cancelLabel,
        placeholder,
        value,
    });
}

function enqueueDialog(dialog) {
    ensureHosts();
    return new Promise(resolve => {
        dialogQueue.push({ ...dialog, resolve });
        renderNextDialog();
    });
}

function renderNextDialog() {
    if (activeDialog || dialogQueue.length === 0) return;
    const hosts = ensureHosts();
    if (!hosts?.dialogRoot) return;

    activeDialog = dialogQueue.shift();
    hosts.dialogRoot.innerHTML = `
        <div class="feedback-dialog-backdrop">
            <div class="feedback-dialog feedback-tone-${normalizeTone(activeDialog.tone)}" role="alertdialog" aria-modal="true">
                <div class="feedback-dialog-header">
                    <h3>${escapeHtml(activeDialog.title || 'Notice')}</h3>
                </div>
                <div class="feedback-dialog-body">${escapeHtml(activeDialog.message || '')}</div>
                ${activeDialog.kind === 'prompt'
                    ? `
                        <div class="feedback-dialog-input-wrap">
                            <input
                                type="text"
                                class="feedback-dialog-input"
                                data-feedback-input
                                placeholder="${escapeHtml(activeDialog.placeholder || '')}"
                                value="${escapeHtml(activeDialog.value || '')}"
                            />
                        </div>
                    `
                    : ''
                }
                <div class="feedback-dialog-actions">
                    ${activeDialog.kind !== 'alert'
                        ? `<button type="button" class="secondary-btn feedback-action-btn" data-feedback-cancel>${escapeHtml(activeDialog.cancelLabel || 'Cancel')}</button>`
                        : ''
                    }
                    <button type="button" class="primary-btn feedback-action-btn" data-feedback-confirm>${escapeHtml(activeDialog.confirmLabel || 'OK')}</button>
                </div>
            </div>
        </div>
    `;
    hosts.dialogRoot.classList.add('active');

    const backdrop = hosts.dialogRoot.querySelector('.feedback-dialog-backdrop');
    const confirmBtn = hosts.dialogRoot.querySelector('[data-feedback-confirm]');
    const cancelBtn = hosts.dialogRoot.querySelector('[data-feedback-cancel]');
    const input = hosts.dialogRoot.querySelector('[data-feedback-input]');
    if (confirmBtn) {
        confirmBtn.onclick = () => {
            if (activeDialog?.kind === 'prompt') {
                settleDialog(String(input?.value || '').trim());
                return;
            }
            settleDialog(true);
        };
    }
    if (cancelBtn) {
        cancelBtn.onclick = () => settleDialog(activeDialog?.kind === 'prompt' ? null : false);
    }
    if (backdrop) {
        backdrop.onclick = event => {
            if (event.target !== backdrop) return;
            if (activeDialog?.kind === 'alert') {
                settleDialog(true);
                return;
            }
            settleDialog(activeDialog?.kind === 'prompt' ? null : false);
        };
    }
    if (input) {
        input.onkeydown = event => {
            if (event.key === 'Enter') {
                event.preventDefault();
                settleDialog(String(input.value || '').trim());
            }
        };
        input.focus();
        input.select?.();
    } else if (confirmBtn) {
        confirmBtn.focus();
    }
}

function settleDialog(value) {
    if (!activeDialog || !dialogRoot) return;
    const current = activeDialog;
    activeDialog = null;
    dialogRoot.classList.remove('active');
    dialogRoot.innerHTML = '';
    current.resolve(value);
    renderNextDialog();
}

function ensureHosts() {
    if (typeof document === 'undefined' || !document.body) return null;

    if (!toastStack) {
        toastStack = document.getElementById('feedback-toast-stack');
        if (!toastStack) {
            toastStack = document.createElement('div');
            toastStack.id = 'feedback-toast-stack';
            toastStack.className = 'feedback-toast-stack';
            document.body.appendChild(toastStack);
        }
    }

    if (!dialogRoot) {
        dialogRoot = document.getElementById('feedback-dialog-root');
        if (!dialogRoot) {
            dialogRoot = document.createElement('div');
            dialogRoot.id = 'feedback-dialog-root';
            dialogRoot.className = 'feedback-dialog-root';
            document.body.appendChild(dialogRoot);
        }
    }

    bindEscapeHandler();
    return { toastStack, dialogRoot };
}

function bindEscapeHandler() {
    if (escapeHandlerBound || typeof document === 'undefined') return;
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !activeDialog) return;
        if (activeDialog.kind === 'alert') {
            settleDialog(true);
            return;
        }
        settleDialog(activeDialog.kind === 'prompt' ? null : false);
    });
    escapeHandlerBound = true;
}

function normalizeTone(value) {
    const safe = String(value || 'info').trim();
    return safe || 'info';
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
