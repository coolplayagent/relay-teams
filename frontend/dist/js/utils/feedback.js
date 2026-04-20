/**
 * utils/feedback.js
 * Unified in-app toast and dialog feedback helpers.
 */
import { t } from './i18n.js';

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
            <div class="feedback-toast-message">${escapeHtml(message || title || t('feedback.notification'))}</div>
        </div>
        <button type="button" class="feedback-toast-close" aria-label="${escapeHtml(t('feedback.dismiss_notification'))}">${escapeHtml(t('feedback.close'))}</button>
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
    title = t('feedback.notice'),
    message = '',
    tone = 'info',
    confirmLabel = t('feedback.ok'),
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
    title = t('feedback.confirm_action'),
    message = '',
    tone = 'warning',
    confirmLabel = t('feedback.confirm'),
    cancelLabel = t('feedback.cancel'),
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
    title = t('feedback.input_required'),
    message = '',
    tone = 'info',
    confirmLabel = t('feedback.confirm'),
    cancelLabel = t('feedback.cancel'),
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
    title = t('feedback.form'),
    message = '',
    tone = 'info',
    confirmLabel = t('feedback.confirm'),
    cancelLabel = t('feedback.cancel'),
    fields = [],
    submitHandler = null,
} = {}) {
    return enqueueDialog({
        kind: 'form',
        title,
        message,
        tone,
        confirmLabel,
        cancelLabel,
        fields: Array.isArray(fields) ? fields : [],
        submitHandler: typeof submitHandler === 'function' ? submitHandler : null,
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
                    <h3>${escapeHtml(activeDialog.title || t('feedback.notice'))}</h3>
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
                                        <label class="feedback-dialog-input-wrap feedback-dialog-checkbox-wrap" data-feedback-form-field data-feedback-field-id="${escapeHtml(fieldId)}">
                                            <span class="feedback-dialog-input-label">${inputLabel}</span>
                                            <span class="feedback-dialog-checkbox">
                                                <input type="checkbox" class="feedback-dialog-checkbox-input" data-feedback-form-input="${escapeHtml(fieldId)}" ${fieldValue === true ? 'checked' : ''} />
                                                <span class="feedback-dialog-checkbox-copy">${escapeHtml(field.description || t('feedback.enable_after_creation'))}</span>
                                            </span>
                                        </label>
                                    `;
                                }
                                const fieldCopy = field.description
                                    ? `<span class="feedback-dialog-field-copy">${escapeHtml(field.description)}</span>`
                                    : '';
                                if (fieldType === 'multiselect') {
                                    const options = Array.isArray(field.options) ? field.options : [];
                                    const selectedValues = Array.isArray(fieldValue)
                                        ? fieldValue.map(value => String(value ?? ''))
                                        : [String(fieldValue ?? '')];
                                    return `
                                        <div class="feedback-dialog-input-wrap" data-feedback-form-field data-feedback-field-id="${escapeHtml(fieldId)}">
                                            <span class="feedback-dialog-input-label">${inputLabel}</span>
                                            ${fieldCopy}
                                            <details class="feedback-dialog-multiselect" data-feedback-form-input="${escapeHtml(fieldId)}" data-feedback-form-type="multiselect" data-feedback-multiselect-placeholder="${placeholder}">
                                                <summary class="feedback-dialog-multiselect-trigger" data-feedback-multiselect-summary>${escapeHtml(formatMultiselectSummary(selectedValues, options, placeholder))}</summary>
                                                <div class="feedback-dialog-multiselect-menu">
                                                    ${options.map(option => {
                                                        const optionValue = String(option?.value ?? '');
                                                        const optionLabel = escapeHtml(option?.label || optionValue);
                                                        const checked = selectedValues.includes(optionValue) ? 'checked' : '';
                                                        return `
                                                            <label class="feedback-dialog-multiselect-option">
                                                                <input type="checkbox" class="feedback-dialog-multiselect-checkbox" data-feedback-multiselect-option value="${escapeHtml(optionValue)}" data-feedback-multiselect-label="${optionLabel}" ${checked} />
                                                                <span>${optionLabel}</span>
                                                            </label>
                                                        `;
                                                    }).join('')}
                                                </div>
                                            </details>
                                        </div>
                                    `;
                                }
                                if (fieldType === 'select') {
                                    const options = Array.isArray(field.options) ? field.options : [];
                                    return `
                                        <label class="feedback-dialog-input-wrap" data-feedback-form-field data-feedback-field-id="${escapeHtml(fieldId)}">
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
                                        <label class="feedback-dialog-input-wrap" data-feedback-form-field data-feedback-field-id="${escapeHtml(fieldId)}">
                                            <span class="feedback-dialog-input-label">${inputLabel}</span>
                                            ${fieldCopy}
                                            <textarea class="feedback-dialog-input feedback-dialog-textarea" data-feedback-form-input="${escapeHtml(fieldId)}" placeholder="${placeholder}">${escapeHtml(fieldValue)}</textarea>
                                        </label>
                                    `;
                                }
                                if (fieldType === 'password') {
                                    const showLabel = escapeHtml(field.showLabel || t('feedback.show_sensitive'));
                                    const hideLabel = escapeHtml(field.hideLabel || t('feedback.hide_sensitive'));
                                    const hasValue = String(fieldValue ?? '').trim().length > 0;
                                    const allowEmptyReveal = field.allowEmptyReveal === true;
                                    return `
                                        <label class="feedback-dialog-input-wrap" data-feedback-form-field data-feedback-field-id="${escapeHtml(fieldId)}">
                                            <span class="feedback-dialog-input-label">${inputLabel}</span>
                                            ${fieldCopy}
                                            <div class="secure-input-row feedback-dialog-secure-row">
                                                <input type="password" class="feedback-dialog-input" data-feedback-form-input="${escapeHtml(fieldId)}" data-feedback-form-type="password" placeholder="${placeholder}" value="${escapeHtml(fieldValue)}" autocomplete="${escapeHtml(field.autocomplete || 'current-password')}" />
                                                <button class="secure-input-btn feedback-dialog-secure-toggle" data-feedback-password-toggle="${escapeHtml(fieldId)}" data-feedback-show-label="${showLabel}" data-feedback-hide-label="${hideLabel}" data-feedback-allow-empty-reveal="${allowEmptyReveal ? 'true' : 'false'}" type="button" title="${showLabel}" aria-label="${showLabel}"${hasValue || allowEmptyReveal ? '' : ' style="display:none;"'}>
                                                    ${renderSecureInputIcon()}
                                                </button>
                                            </div>
                                        </label>
                                    `;
                                }
                                return `
                                    <label class="feedback-dialog-input-wrap" data-feedback-form-field data-feedback-field-id="${escapeHtml(fieldId)}">
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
                <div class="feedback-dialog-submit-error" data-feedback-submit-error hidden></div>
                <div class="feedback-dialog-actions">
                    ${activeDialog.kind !== 'alert'
                        ? `<button type="button" class="secondary-btn feedback-action-btn" data-feedback-cancel>${escapeHtml(activeDialog.cancelLabel || t('feedback.cancel'))}</button>`
                        : ''
                    }
                    <button type="button" class="primary-btn feedback-action-btn" data-feedback-confirm>${escapeHtml(activeDialog.confirmLabel || t('feedback.ok'))}</button>
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
    const submitError = hosts.dialogRoot.querySelector('[data-feedback-submit-error]');
    bindMultiselectControls(hosts.dialogRoot);
    bindPasswordInputControls(hosts.dialogRoot);
    bindConditionalFieldVisibility(hosts.dialogRoot, activeDialog.fields, formInputs);
    if (confirmBtn) {
        confirmBtn.onclick = async () => {
            if (activeDialog?.submitting === true) {
                return;
            }
            if (activeDialog?.kind === 'prompt') {
                settleDialog(String(input?.value || '').trim());
                return;
            }
            if (activeDialog?.kind === 'form') {
                const payload = collectFormDialogValues(formInputs);
                if (submitError) {
                    submitError.textContent = '';
                    submitError.hidden = true;
                }
                if (typeof activeDialog?.submitHandler === 'function') {
                    setDialogSubmittingState({
                        activeDialog,
                        confirmBtn,
                        cancelBtn,
                        formInputs,
                    }, true);
                    try {
                        const result = await activeDialog.submitHandler(payload);
                        settleDialog(result ?? payload);
                        return;
                    } catch (error) {
                        setDialogSubmittingState({
                            activeDialog,
                            confirmBtn,
                            cancelBtn,
                            formInputs,
                        }, false);
                        if (submitError) {
                            submitError.textContent = String(error?.message || error || '');
                            submitError.hidden = false;
                        }
                        return;
                    }
                }
                settleDialog(payload);
                return;
            }
            settleDialog(true);
        };
    }
    if (cancelBtn) {
        cancelBtn.onclick = () => {
            if (activeDialog?.submitting === true) {
                return;
            }
            settleDialog(activeDialog?.kind === 'prompt' || activeDialog?.kind === 'form' ? null : false);
        };
    }
    if (backdrop) {
        backdrop.onclick = event => {
            if (event.target !== backdrop) return;
            if (activeDialog?.submitting === true) return;
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
                const formType = String(node.getAttribute?.('data-feedback-form-type') || '').trim();
                if (event.key === 'Enter' && tagName !== 'TEXTAREA' && tagName !== 'SELECT' && formType !== 'multiselect') {
                    event.preventDefault();
                    confirmBtn?.click();
                }
            };
        });
        const firstVisibleInput = findFirstVisibleFormInput(formInputs);
        firstVisibleInput?.focus();
        firstVisibleInput?.select?.();
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
        if (activeDialog.submitting === true) return;
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

function collectFormDialogValues(formInputs) {
    const payload = {};
    formInputs.forEach(node => {
        if (isDialogFormInputHidden(node)) {
            return;
        }
        const key = String(node.getAttribute('data-feedback-form-input') || '').trim();
        if (!key) return;
        const formType = String(node.getAttribute('data-feedback-form-type') || '').trim();
        if (formType === 'multiselect' && node instanceof HTMLElement) {
            payload[key] = Array.from(node.querySelectorAll('[data-feedback-multiselect-option]:checked'))
                .map(option => String(option.value || '').trim())
                .filter(Boolean);
            return;
        }
        if (node instanceof HTMLInputElement && node.type === 'checkbox') {
            payload[key] = node.checked;
            return;
        }
        if (node instanceof HTMLSelectElement && node.multiple) {
            payload[key] = Array.from(node.selectedOptions)
                .map(option => String(option.value || '').trim())
                .filter(Boolean);
            return;
        }
        payload[key] = String(node.value || '').trim();
    });
    return payload;
}

function bindConditionalFieldVisibility(dialogNode, fields, formInputs) {
    const fieldConfigs = new Map(
        (Array.isArray(fields) ? fields : [])
            .map((field, index) => {
                const fieldId = String(field?.id || `field_${index}`).trim();
                return fieldId ? [fieldId, field] : null;
            })
            .filter(Boolean),
    );
    if (fieldConfigs.size === 0) {
        return;
    }
    const applyVisibility = () => {
        const currentValues = collectCurrentDialogFieldValues(formInputs);
        dialogNode.querySelectorAll('[data-feedback-form-field]').forEach(wrapper => {
            if (!(wrapper instanceof HTMLElement)) {
                return;
            }
            const fieldId = String(wrapper.getAttribute('data-feedback-field-id') || '').trim();
            if (!fieldId) {
                return;
            }
            const field = fieldConfigs.get(fieldId);
            const isVisible = evaluateDialogFieldVisibility(field, currentValues);
            wrapper.hidden = !isVisible;
            wrapper.setAttribute('aria-hidden', isVisible ? 'false' : 'true');
            wrapper.querySelectorAll('[data-feedback-form-input]').forEach(node => {
                if (
                    node instanceof HTMLInputElement
                    || node instanceof HTMLSelectElement
                    || node instanceof HTMLTextAreaElement
                ) {
                    node.disabled = !isVisible;
                    return;
                }
                if (node instanceof HTMLElement) {
                    node.dataset.feedbackHidden = isVisible ? 'false' : 'true';
                    node.classList.toggle('is-disabled', !isVisible);
                    node.querySelectorAll('input').forEach(option => {
                        if (option instanceof HTMLInputElement) {
                            option.disabled = !isVisible;
                        }
                    });
                }
            });
        });
    };
    formInputs.forEach(node => {
        if (
            node instanceof HTMLInputElement
            || node instanceof HTMLSelectElement
            || node instanceof HTMLTextAreaElement
        ) {
            node.addEventListener('change', applyVisibility);
            node.addEventListener('input', applyVisibility);
            return;
        }
        if (node instanceof HTMLElement) {
            node.querySelectorAll('input').forEach(option => {
                option.addEventListener('change', applyVisibility);
                option.addEventListener('input', applyVisibility);
            });
        }
    });
    applyVisibility();
}

function collectCurrentDialogFieldValues(formInputs) {
    const payload = {};
    formInputs.forEach(node => {
        const key = String(node.getAttribute('data-feedback-form-input') || '').trim();
        if (!key) {
            return;
        }
        payload[key] = readDialogFieldValue(node);
    });
    return payload;
}

function readDialogFieldValue(node) {
    const formType = String(node.getAttribute?.('data-feedback-form-type') || '').trim();
    if (formType === 'multiselect' && node instanceof HTMLElement) {
        return Array.from(node.querySelectorAll('[data-feedback-multiselect-option]:checked'))
            .map(option => String(option.value || '').trim())
            .filter(Boolean);
    }
    if (node instanceof HTMLInputElement && node.type === 'checkbox') {
        return node.checked;
    }
    if (node instanceof HTMLSelectElement && node.multiple) {
        return Array.from(node.selectedOptions)
            .map(option => String(option.value || '').trim())
            .filter(Boolean);
    }
    return String(node.value || '').trim();
}

function evaluateDialogFieldVisibility(field, currentValues) {
    const visibleWhen = field?.visibleWhen;
    if (!visibleWhen) {
        return true;
    }
    const rules = Array.isArray(visibleWhen) ? visibleWhen : [visibleWhen];
    return rules.every(rule => {
        if (!rule || typeof rule !== 'object') {
            return true;
        }
        const targetField = String(rule.field || '').trim();
        if (!targetField) {
            return true;
        }
        const currentValue = currentValues[targetField];
        if (Object.prototype.hasOwnProperty.call(rule, 'equals')) {
            return currentValue === rule.equals;
        }
        if (Array.isArray(rule.oneOf)) {
            return rule.oneOf.includes(currentValue);
        }
        if (rule.truthy === true) {
            return Boolean(currentValue);
        }
        if (rule.notEmpty === true) {
            return Array.isArray(currentValue)
                ? currentValue.length > 0
                : String(currentValue ?? '').trim().length > 0;
        }
        return true;
    });
}

function findFirstVisibleFormInput(formInputs) {
    return formInputs.find(node => !isDialogFormInputHidden(node) && node.disabled !== true) || null;
}

function isDialogFormInputHidden(node) {
    const wrapper = node.closest?.('[data-feedback-form-field]');
    if (!(wrapper instanceof HTMLElement)) {
        return false;
    }
    return wrapper.hidden === true || wrapper.getAttribute('aria-hidden') === 'true';
}

function formatMultiselectSummary(selectedValues, options, placeholder = '') {
    const normalizedSelectedValues = Array.isArray(selectedValues)
        ? selectedValues.map(value => String(value ?? '')).filter(Boolean)
        : [];
    if (normalizedSelectedValues.length === 0) {
        return placeholder || t('feedback.confirm');
    }
    const labels = normalizedSelectedValues.map(value => {
        const matchedOption = (Array.isArray(options) ? options : []).find(option => String(option?.value ?? '') === value);
        return String(matchedOption?.label || value);
    });
    return labels.join(', ');
}

function bindMultiselectControls(dialogNode) {
    Array.from(dialogNode.querySelectorAll('[data-feedback-form-type="multiselect"]')).forEach(node => {
        if (!(node instanceof HTMLElement)) {
            return;
        }
        const checkboxes = Array.from(node.querySelectorAll('[data-feedback-multiselect-option]'));
        const summary = node.querySelector('[data-feedback-multiselect-summary]');
        const updateSummary = () => {
            if (!(summary instanceof HTMLElement)) {
                return;
            }
            const selectedLabels = checkboxes
                .filter(option => option instanceof HTMLInputElement && option.checked)
                .map(option => String(option.getAttribute('data-feedback-multiselect-label') || option.value || '').trim())
                .filter(Boolean);
            const placeholder = String(node.getAttribute('data-feedback-multiselect-placeholder') || '').trim();
            summary.textContent = selectedLabels.length > 0 ? selectedLabels.join(', ') : (placeholder || t('feedback.confirm'));
        };
        checkboxes.forEach(option => {
            if (option instanceof HTMLInputElement) {
                option.onchange = updateSummary;
            }
        });
        updateSummary();
    });
}

function bindPasswordInputControls(dialogNode) {
    const formInputs = Array.from(dialogNode.querySelectorAll('[data-feedback-form-input]'));
    Array.from(dialogNode.querySelectorAll('[data-feedback-password-toggle]')).forEach(toggleBtn => {
        if (!(toggleBtn instanceof HTMLButtonElement)) {
            return;
        }
        const fieldId = String(toggleBtn.getAttribute('data-feedback-password-toggle') || '').trim();
        if (!fieldId) {
            return;
        }
        const matchedInput = formInputs.find(node => String(node.getAttribute?.('data-feedback-form-input') || '').trim() === fieldId);
        if (!(matchedInput instanceof HTMLInputElement)) {
            return;
        }
        const showLabel = String(toggleBtn.getAttribute('data-feedback-show-label') || t('feedback.show_sensitive')).trim();
        const hideLabel = String(toggleBtn.getAttribute('data-feedback-hide-label') || t('feedback.hide_sensitive')).trim();
        const allowEmptyReveal = toggleBtn.getAttribute('data-feedback-allow-empty-reveal') === 'true';
        const renderToggle = () => {
            const hasValue = Boolean(String(matchedInput.value || '').trim());
            const revealed = toggleBtn.getAttribute('data-feedback-revealed') === 'true';
            const shouldShow = hasValue || allowEmptyReveal;
            if (!shouldShow) {
                matchedInput.type = 'password';
                toggleBtn.style.display = 'none';
                toggleBtn.className = 'secure-input-btn feedback-dialog-secure-toggle';
                toggleBtn.title = showLabel;
            } else {
                matchedInput.type = revealed ? 'text' : 'password';
                toggleBtn.style.display = 'inline-flex';
                toggleBtn.className = revealed
                    ? 'secure-input-btn feedback-dialog-secure-toggle is-active'
                    : 'secure-input-btn feedback-dialog-secure-toggle';
                toggleBtn.title = revealed ? hideLabel : showLabel;
            }
            toggleBtn.setAttribute('aria-label', toggleBtn.title);
        };
        toggleBtn.onclick = () => {
            if (!String(matchedInput.value || '').trim() && !allowEmptyReveal) {
                return;
            }
            const nextRevealed = toggleBtn.getAttribute('data-feedback-revealed') !== 'true';
            toggleBtn.setAttribute('data-feedback-revealed', nextRevealed ? 'true' : 'false');
            renderToggle();
            matchedInput.focus?.();
        };
        const handleInput = () => {
            if (!String(matchedInput.value || '').trim() && !allowEmptyReveal) {
                toggleBtn.setAttribute('data-feedback-revealed', 'false');
            }
            renderToggle();
        };
        matchedInput.addEventListener('input', handleInput);
        matchedInput.addEventListener('change', handleInput);
        renderToggle();
    });
}

function setDialogSubmittingState(context, isSubmitting) {
    const { activeDialog: dialog, confirmBtn, cancelBtn, formInputs } = context;
    if (dialog && typeof dialog === 'object') {
        dialog.submitting = isSubmitting;
    }
    if (confirmBtn) {
        confirmBtn.disabled = isSubmitting;
    }
    if (cancelBtn) {
        cancelBtn.disabled = isSubmitting;
    }
    formInputs.forEach(node => {
        if (node instanceof HTMLInputElement || node instanceof HTMLSelectElement || node instanceof HTMLTextAreaElement) {
            node.disabled = isSubmitting || isDialogFormInputHidden(node);
            return;
        }
        if (String(node.getAttribute?.('data-feedback-form-type') || '').trim() === 'multiselect' && node instanceof HTMLElement) {
            const isHidden = isDialogFormInputHidden(node);
            node.classList.toggle('is-disabled', isSubmitting || isHidden);
            Array.from(node.querySelectorAll('input')).forEach(option => {
                if (option instanceof HTMLInputElement) {
                    option.disabled = isSubmitting || isHidden;
                }
            });
        }
    });
    const dialogNode = confirmBtn?.closest?.('.feedback-dialog') || cancelBtn?.closest?.('.feedback-dialog');
    if (dialogNode instanceof HTMLElement) {
        Array.from(dialogNode.querySelectorAll('[data-feedback-password-toggle]')).forEach(button => {
            if (button instanceof HTMLButtonElement) {
                button.disabled = isSubmitting;
            }
        });
    }
}

function renderSecureInputIcon() {
    return `
        <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
            <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
            <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"></circle>
        </svg>
    `;
}
