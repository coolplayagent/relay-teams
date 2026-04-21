# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from .css_helpers import load_components_css


def test_image_preview_is_wired_into_bootstrap_and_message_rendering() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    bootstrap_script = (
        repo_root / "frontend" / "dist" / "js" / "app" / "bootstrap.js"
    ).read_text(encoding="utf-8")
    preview_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "imagePreview.js"
    ).read_text(encoding="utf-8")
    content_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers"
        / "content.js"
    ).read_text(encoding="utf-8")
    prompt_script = (
        repo_root / "frontend" / "dist" / "js" / "app" / "prompt.js"
    ).read_text(encoding="utf-8")
    i18n_script = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "i18n.js"
    ).read_text(encoding="utf-8")
    components_css = load_components_css()
    layout_css = (repo_root / "frontend" / "dist" / "css" / "layout.css").read_text(
        encoding="utf-8"
    )

    assert (
        'import { initializeImagePreview } from "../components/imagePreview.js";'
        in bootstrap_script
    )
    assert "initializeImagePreview();" in bootstrap_script
    assert (
        "const PREVIEW_TRIGGER_SELECTOR = '[data-image-preview-trigger=\"true\"]';"
        in preview_script
    )
    assert "export function openImagePreview(payload = {}) {" in preview_script
    assert "export function closeImagePreview() {" in preview_script
    assert "document.addEventListener('click', handleDocumentClick);" in preview_script
    assert (
        "document.addEventListener('keydown', handleDocumentKeydown);" in preview_script
    )
    assert (
        "document.addEventListener('mousemove', handlePreviewDragMove);"
        in preview_script
    )
    assert (
        "document.addEventListener('mouseup', handlePreviewDragEnd);" in preview_script
    )
    assert "let overlayPointerDown = false;" in preview_script
    assert "function handlePreviewImageDoubleClick(event) {" in preview_script
    assert "function handlePreviewImageWheel(event) {" in preview_script
    assert "function handlePreviewDragStart(event) {" in preview_script
    assert "function handlePreviewDragMove(event) {" in preview_script
    assert "function handlePreviewDragEnd() {" in preview_script
    assert "function resetPreviewTransform() {" in preview_script
    assert "modalEl?.addEventListener('pointerdown', event => {" in preview_script
    assert "overlayPointerDown = event.target === modalEl;" in preview_script
    assert "const shouldClose = overlayPointerDown" in preview_script
    assert "&& event.target === modalEl" in preview_script
    assert "previewScale = DOUBLE_CLICK_SCALE;" in preview_script
    assert "previewScale + zoomDirection * WHEEL_SCALE_STEP" in preview_script
    assert "metaEl.textContent = '';" in preview_script
    assert "metaEl.hidden = true;" in preview_script
    assert "nextImageUrl.startsWith('data:')" not in preview_script
    assert "image-preview-modal-canvas" in preview_script
    assert (
        "bodyEl.dataset.canPan = previewScale > MIN_SCALE ? 'true' : 'false';"
        in preview_script
    )
    assert (
        "bodyEl.dataset.dragging = previewDragging ? 'true' : 'false';"
        in preview_script
    )
    assert "data-image-preview-trigger', 'true'" in content_script
    assert "data-image-preview-src', imageDataUrl" in content_script
    assert "data-image-preview-name', String(name || '').trim()" in content_script
    assert 'data-image-preview-trigger="true"' in prompt_script
    assert (
        'data-image-preview-src="${escapeHtml(attachment.previewUrl)}"' in prompt_script
    )
    assert 'data-image-preview-name="${escapeHtml(attachment.name)}"' in prompt_script
    assert "'media.preview_title': 'Image Preview'," in i18n_script
    assert "'media.preview_title': '图片预览'," in i18n_script
    assert ".image-preview-modal {" in components_css
    assert ".image-preview-modal .image-preview-modal-content {" in components_css
    assert '.image-preview-modal-body[data-can-pan="true"] {' in components_css
    assert '.image-preview-modal-body[data-dragging="true"] {' in components_css
    assert ".image-preview-modal-canvas {" in components_css
    assert ".image-preview-modal-image {" in components_css
    assert ".image-preview-modal-meta[hidden] {" in components_css
    assert '.msg-image-preview[data-image-preview-trigger="true"] {' in components_css
    assert "cursor: zoom-in;" in layout_css
    assert "width: min(1240px, 96vw);" in components_css
    assert "height: min(90vh, 960px);" in components_css
    assert "max-height: min(90vh, 960px);" in components_css
    assert "min-height: 760px;" in components_css
    assert "grid-template-rows: auto minmax(0, 1fr);" in components_css
    assert "width: min(100%, 28rem);" in components_css
    assert "max-height: 20rem;" in components_css
