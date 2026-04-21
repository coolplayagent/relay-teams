/**
 * components/imagePreview.js
 * Global image preview modal for chat images and prompt attachments.
 */
import { t } from '../utils/i18n.js';

const PREVIEW_TRIGGER_SELECTOR = '[data-image-preview-trigger="true"]';
const MODAL_ROOT_ID = 'image-preview-modal-root';
const MIN_SCALE = 1;
const MAX_SCALE = 4;
const DOUBLE_CLICK_SCALE = 2;
const WHEEL_SCALE_STEP = 0.2;

let initialized = false;
let modalRoot = null;
let modalEl = null;
let imageEl = null;
let titleEl = null;
let metaEl = null;
let bodyEl = null;
let canvasEl = null;
let previewScale = MIN_SCALE;
let previewTranslateX = 0;
let previewTranslateY = 0;
let previewDragging = false;
let previewDragOriginX = 0;
let previewDragOriginY = 0;
let previewDragStartX = 0;
let previewDragStartY = 0;
let overlayPointerDown = false;

export function initializeImagePreview() {
    if (initialized) {
        return;
    }
    initialized = true;
    ensureModal();
    document.addEventListener('click', handleDocumentClick);
    document.addEventListener('keydown', handleDocumentKeydown);
    document.addEventListener('mousemove', handlePreviewDragMove);
    document.addEventListener('mouseup', handlePreviewDragEnd);
}

export function openImagePreview(payload = {}) {
    const nextImageUrl = String(payload.src || '').trim();
    if (!nextImageUrl) {
        return false;
    }
    ensureModal();

    const nextImageName = String(payload.name || '').trim();
    const nextImageAlt = String(payload.alt || nextImageName || t('media.preview_alt')).trim();

    if (imageEl) {
        imageEl.src = nextImageUrl;
        imageEl.alt = nextImageAlt;
    }
    if (titleEl) {
        titleEl.textContent = nextImageName || t('media.preview_title');
    }
    if (metaEl) {
        metaEl.textContent = '';
        metaEl.hidden = true;
    }
    resetPreviewTransform();
    if (imageEl?.complete) {
        syncPreviewTransform();
    }
    if (modalEl) {
        modalEl.style.display = 'flex';
        modalEl.setAttribute('aria-hidden', 'false');
    }
    document.body?.classList?.add('image-preview-open');
    return true;
}

export function closeImagePreview() {
    if (!modalEl) {
        return;
    }
    handlePreviewDragEnd();
    overlayPointerDown = false;
    resetPreviewTransform();
    modalEl.style.display = 'none';
    modalEl.setAttribute('aria-hidden', 'true');
    document.body?.classList?.remove('image-preview-open');
}

function ensureModal() {
    if (modalEl && imageEl && titleEl && metaEl) {
        return modalEl;
    }

    modalRoot = document.getElementById(MODAL_ROOT_ID);
    if (!modalRoot) {
        modalRoot = document.createElement('div');
        modalRoot.id = MODAL_ROOT_ID;
        document.body?.appendChild(modalRoot);
    }

    modalRoot.innerHTML = `
        <div class="modal image-preview-modal" data-image-preview-modal aria-hidden="true">
            <div class="modal-content image-preview-modal-content" role="dialog" aria-modal="true" aria-labelledby="image-preview-title">
                <div class="modal-header image-preview-modal-header">
                    <div class="image-preview-modal-copy">
                        <h2 id="image-preview-title"></h2>
                        <div class="image-preview-modal-meta" hidden></div>
                    </div>
                    <button type="button" class="icon-btn image-preview-close" data-image-preview-close aria-label="${escapeHtml(t('media.preview_close'))}">
                        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                        </svg>
                    </button>
                </div>
                <div class="image-preview-modal-body">
                    <div class="image-preview-modal-canvas">
                        <img class="image-preview-modal-image" alt="" />
                    </div>
                </div>
            </div>
        </div>
    `;

    modalEl = modalRoot.querySelector('[data-image-preview-modal]');
    imageEl = modalRoot.querySelector('.image-preview-modal-image');
    titleEl = modalRoot.querySelector('#image-preview-title');
    metaEl = modalRoot.querySelector('.image-preview-modal-meta');
    bodyEl = modalRoot.querySelector('.image-preview-modal-body');
    canvasEl = modalRoot.querySelector('.image-preview-modal-canvas');

    modalRoot.querySelectorAll('[data-image-preview-close]').forEach(button => {
        button.addEventListener('click', event => {
            event.preventDefault();
            closeImagePreview();
        });
    });
    modalEl?.addEventListener('pointerdown', event => {
        overlayPointerDown = event.target === modalEl;
    });
    modalEl?.addEventListener('click', event => {
        const shouldClose = overlayPointerDown
            && event.target === modalEl
            && previewDragging !== true;
        overlayPointerDown = false;
        if (shouldClose) {
            closeImagePreview();
        }
    });
    imageEl?.addEventListener('load', () => {
        syncPreviewTransform();
    });
    imageEl?.addEventListener('dragstart', event => {
        event.preventDefault();
    });
    imageEl?.addEventListener('dblclick', handlePreviewImageDoubleClick);
    bodyEl?.addEventListener('wheel', handlePreviewImageWheel, { passive: false });
    bodyEl?.addEventListener('mousedown', handlePreviewDragStart);
    return modalEl;
}

function handleDocumentClick(event) {
    const trigger = event?.target?.closest?.(PREVIEW_TRIGGER_SELECTOR);
    if (!trigger) {
        return;
    }
    if (trigger.closest?.('[data-image-preview-modal]')) {
        return;
    }
    const payload = resolvePreviewPayload(trigger);
    if (!payload) {
        return;
    }
    event.preventDefault?.();
    openImagePreview(payload);
}

function handleDocumentKeydown(event) {
    if (event?.key === 'Escape' && modalEl?.style?.display === 'flex') {
        event.preventDefault?.();
        closeImagePreview();
        return;
    }
    if (event?.key !== 'Enter' && event?.key !== ' ') {
        return;
    }
    const trigger = event?.target?.closest?.(PREVIEW_TRIGGER_SELECTOR);
    if (!trigger) {
        return;
    }
    const payload = resolvePreviewPayload(trigger);
    if (!payload) {
        return;
    }
    event.preventDefault?.();
    openImagePreview(payload);
}

function resolvePreviewPayload(trigger) {
    if (!trigger) {
        return null;
    }
    const src = String(
        trigger.getAttribute?.('data-image-preview-src')
        || trigger.getAttribute?.('src')
        || '',
    ).trim();
    if (!src) {
        return null;
    }
    return {
        src,
        name: String(
            trigger.getAttribute?.('data-image-preview-name')
            || trigger.getAttribute?.('alt')
            || '',
        ).trim(),
        alt: String(trigger.getAttribute?.('alt') || '').trim(),
    };
}

function handlePreviewImageDoubleClick(event) {
    if (modalEl?.style?.display !== 'flex') {
        return;
    }
    event.preventDefault?.();
    if (previewScale > MIN_SCALE) {
        resetPreviewTransform();
        return;
    }
    previewScale = DOUBLE_CLICK_SCALE;
    previewTranslateX = 0;
    previewTranslateY = 0;
    syncPreviewTransform();
}

function handlePreviewImageWheel(event) {
    if (modalEl?.style?.display !== 'flex') {
        return;
    }
    event.preventDefault?.();
    const zoomDirection = Number(event.deltaY || 0) < 0 ? 1 : -1;
    const nextScale = clampScale(
        previewScale + zoomDirection * WHEEL_SCALE_STEP,
    );
    if (nextScale === previewScale) {
        return;
    }
    previewScale = nextScale;
    clampPreviewTranslation();
    syncPreviewTransform();
}

function handlePreviewDragStart(event) {
    if (modalEl?.style?.display !== 'flex' || previewScale <= MIN_SCALE) {
        return;
    }
    if (Number(event.button) !== 0) {
        return;
    }
    event.preventDefault?.();
    previewDragging = true;
    previewDragStartX = Number(event.clientX || 0);
    previewDragStartY = Number(event.clientY || 0);
    previewDragOriginX = previewTranslateX;
    previewDragOriginY = previewTranslateY;
    syncPreviewTransform();
}

function handlePreviewDragMove(event) {
    if (!previewDragging) {
        return;
    }
    previewTranslateX = previewDragOriginX + Number(event.clientX || 0) - previewDragStartX;
    previewTranslateY = previewDragOriginY + Number(event.clientY || 0) - previewDragStartY;
    clampPreviewTranslation();
    syncPreviewTransform();
}

function handlePreviewDragEnd() {
    if (!previewDragging) {
        return;
    }
    previewDragging = false;
    syncPreviewTransform();
}

function resetPreviewTransform() {
    previewScale = MIN_SCALE;
    previewTranslateX = 0;
    previewTranslateY = 0;
    previewDragging = false;
    overlayPointerDown = false;
    syncPreviewTransform();
}

function syncPreviewTransform() {
    if (imageEl) {
        imageEl.style.transform = `translate(${previewTranslateX}px, ${previewTranslateY}px) scale(${previewScale})`;
    }
    if (bodyEl?.dataset) {
        bodyEl.dataset.canPan = previewScale > MIN_SCALE ? 'true' : 'false';
        bodyEl.dataset.dragging = previewDragging ? 'true' : 'false';
    }
}

function clampPreviewTranslation() {
    const bounds = resolvePreviewPanBounds();
    previewTranslateX = clamp(previewTranslateX, -bounds.maxOffsetX, bounds.maxOffsetX);
    previewTranslateY = clamp(previewTranslateY, -bounds.maxOffsetY, bounds.maxOffsetY);
}

function resolvePreviewPanBounds() {
    const canvasWidth = Number(canvasEl?.clientWidth || 0);
    const canvasHeight = Number(canvasEl?.clientHeight || 0);
    const imageWidth = Number(imageEl?.offsetWidth || 0);
    const imageHeight = Number(imageEl?.offsetHeight || 0);
    if (canvasWidth <= 0 || canvasHeight <= 0 || imageWidth <= 0 || imageHeight <= 0) {
        return {
            maxOffsetX: 0,
            maxOffsetY: 0,
        };
    }
    return {
        maxOffsetX: Math.max(0, (imageWidth * previewScale - canvasWidth) / 2),
        maxOffsetY: Math.max(0, (imageHeight * previewScale - canvasHeight) / 2),
    };
}

function clampScale(value) {
    return clamp(value, MIN_SCALE, MAX_SCALE);
}

function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
