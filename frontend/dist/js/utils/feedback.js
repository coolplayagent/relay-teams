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

export function showFormDialog({
    title = 'Form',
    message = '',
    tone = 'info',
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    fields = [],
} = {}) {
    return enqueueDialog({
        kind: 'form',
        title,
        message,
        tone,
        confirmLabel,
        cancelLabel,
        fields: Array.isArray(fields) ? fields : [],
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
                ${activeDialog.kind === 'form'
                    ? `
                        <div class="feedback-dialog-form">
                            ${(Array.isArray(activeDialog.fields) ? activeDialog.fields : []).map((field, index) => {
                                const fieldId = field.id || `field_${index}`;
                                const inputLabel = escapeHtml(field.label || fieldId || `Field ${index + 1}`);
                                const placeholder = escapeHtml(field.placeholder || '');
                                const fieldValue = field.value ?? '';
                                const fieldType = String(field.type || '').trim().toLowerCase();
                                if (fieldType === 'checkbox') {
                                    return `
                                        <label class="feedback-dialog-input-wrap feedback-dialog-checkbox-wrap">
                                            <span class="feedback-dialog-input-label">${inputLabel}</span>
                                            <span class="feedback-dialog-checkbox">
                                                <input type="checkbox" class="feedback-dialog-checkbox-input" data-feedback-form-input="${escapeHtml(fieldId)}" ${fieldValue === true ? 'checked' : ''} />
                                                <span class="feedback-dialog-checkbox-copy">${escapeHtml(field.description || 'Enable this automation after creation.')}</span>
                                            </span>
                                        </label>
                                    `;
                                }
                                const fieldCopy = field.description
                                    ? `<span class="feedback-dialog-field-copy">${escapeHtml(field.description)}</span>`
                                    : '';
                                if (fieldType === 'select') {
                                    const options = Array.isArray(field.options) ? field.options : [];
                                    return `
                                        <label class="feedback-dialog-input-wrap">
                                            <span class="feedback-dialog-input-label">${inputLabel}</span>
                                            ${fieldCopy}
                                            <select class="feedback-dialog-input feedback-dialog-select" data-feedback-form-input="${escapeHtml(fieldId)}">
                                                ${options.map(option => {
                                                    const optionValue = String(option?.value ?? '');
                                                    const optionLabel = escapeHtml(option?.label || optionValue);
                                                    const selected = optionValue === String(fieldValue ?? '') ? 'selected' : '';
                                                    return `<option value="${escapeHtml(optionValue)}" ${selected}>${optionLabel}</option>`;
                                                }).join('')}
                                            </select>
                                        </label>
                                    `;
                                }
                                if (String(field.multiline || '').trim() === 'true' || field.multiline === true || fieldType === 'textarea') {
                                    return `
                                        <label class="feedback-dialog-input-wrap">
                                            <span class="feedback-dialog-input-label">${inputLabel}</span>
                                            ${fieldCopy}
                                            <textarea class="feedback-dialog-input feedback-dialog-textarea" data-feedback-form-input="${escapeHtml(fieldId)}" placeholder="${placeholder}">${escapeHtml(fieldValue)}</textarea>
                                        </label>
                                    `;
                                }
                                return `
                                    <label class="feedback-dialog-input-wrap">
                                        <span class="feedback-dialog-input-label">${inputLabel}</span>
                                        ${fieldCopy}
                                        <input type="text" class="feedback-dialog-input" data-feedback-form-input="${escapeHtml(fieldId)}" placeholder="${placeholder}" value="${escapeHtml(fieldValue)}" />
                                    </label>
                                `;
                            }).join('')}
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
    const formInputs = Array.from(hosts.dialogRoot.querySelectorAll('[data-feedback-form-input]'));
    if (confirmBtn) {
        confirmBtn.onclick = () => {
            if (activeDialog?.kind === 'prompt') {
                settleDialog(String(input?.value || '').trim());
                return;
            }
            if (activeDialog?.kind === 'form') {
                const payload = {};
                formInputs.forEach(node => {
                    const key = String(node.getAttribute('data-feedback-form-input') || '').trim();
                    if (!key) return;
                    if (node instanceof HTMLInputElement && node.type === 'checkbox') {
                        payload[key] = node.checked;
                        return;
                    }
                    payload[key] = String(node.value || '').trim();
                });
                settleDialog(payload);
                return;
            }
            settleDialog(true);
        };
    }
    if (cancelBtn) {
        cancelBtn.onclick = () => settleDialog(activeDialog?.kind === 'prompt' || activeDialog?.kind === 'form' ? null : false);
    }
    if (backdrop) {
        backdrop.onclick = event => {
            if (event.target !== backdrop) return;
            if (activeDialog?.kind === 'alert') {
                settleDialog(true);
                return;
            }
            settleDialog(activeDialog?.kind === 'prompt' || activeDialog?.kind === 'form' ? null : false);
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
    } else if (formInputs.length > 0) {
        formInputs.forEach(node => {
            node.onkeydown = event => {
                const tagName = String(node.tagName || '').toUpperCase();
                if (event.key === 'Enter' && tagName !== 'TEXTAREA' && tagName !== 'SELECT') {
                    event.preventDefault();
                    confirmBtn?.click();
                }
            };
        });
        formInputs[0].focus();
        formInputs[0].select?.();
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
        settleDialog(activeDialog.kind === 'prompt' || activeDialog.kind === 'form' ? null : false);
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
