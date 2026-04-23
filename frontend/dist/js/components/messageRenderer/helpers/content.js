/**
 * components/messageRenderer/helpers/content.js
 * Shared rich content rendering helpers for message text and tool results.
 */
import { buildWorkspaceImagePreviewUrl } from '../../../core/api/workspaces.js';
import { state } from '../../../core/state.js';
import { t } from '../../../utils/i18n.js';
import { parseMarkdown } from '../../../utils/markdown.js';

const DATA_IMAGE_PREFIX = 'data:image/';
const BASE64_BODY_PATTERN = /^[A-Za-z0-9+/=]+$/;
const IMAGE_PATH_PATTERN = /\.(?:avif|bmp|gif|jpe?g|png|webp)$/i;
const IMAGE_CODE_SPAN_PATTERN = /`([^`\n]+)`/g;
const IMAGE_BARE_PATH_PATTERN =
    /((?:\/|\.{1,2}\/|[A-Za-z]:[\\/])[^"'`\s<>]+?\.(?:avif|bmp|gif|jpe?g|png|webp))/gi;
const TRAILING_PATH_PUNCTUATION_PATTERN = /[),.:;!?\\\]}>，。！？；：）】》]+$/u;

export function renderRichContent(targetEl, source, options = {}) {
    const text = String(source || '');
    const enableWorkspaceImagePreview = options.enableWorkspaceImagePreview !== false;
    const standaloneImageUrl = resolveStandaloneImageDataUrl(text);
    if (standaloneImageUrl) {
        targetEl.replaceChildren(buildImageFigure(standaloneImageUrl));
        return targetEl;
    }

    targetEl.innerHTML = parseMarkdown(text);
    if (enableWorkspaceImagePreview) {
        const previewUrls = resolveWorkspaceImagePreviewUrls(
            text,
            state.currentWorkspaceId || state.currentProjectViewWorkspaceId,
        );
        previewUrls.forEach(previewUrl => {
            targetEl.appendChild(buildImageFigure(previewUrl));
        });
    }
    return targetEl;
}

export function appendStructuredContentPart(targetEl, part, options = {}) {
    if (!targetEl || !part || typeof part !== 'object') {
        return null;
    }
    const kind = String(part.kind || '');
    if (kind === 'text') {
        const textEl = document.createElement('div');
        textEl.className = 'msg-text';
        renderRichContent(textEl, String(part.text || ''), options.richContent || {});
        targetEl.appendChild(textEl);
        return textEl;
    }
    if (kind !== 'media_ref') {
        return null;
    }
    const modality = String(part.modality || '').toLowerCase();
    const url = String(part.url || '').trim();
    const mimeType = String(part.mime_type || '').trim();
    if (!url) {
        return null;
    }
    const mediaEl = buildMediaFigure({
        modality,
        url,
        mimeType,
        name: String(part.name || '').trim(),
    });
    if (!mediaEl) {
        return null;
    }
    targetEl.appendChild(mediaEl);
    return mediaEl;
}

function buildImageFigure(imageDataUrl, name = '') {
    const figureEl = document.createElement('figure');
    figureEl.className = 'msg-image';

    const imageEl = document.createElement('img');
    imageEl.className = 'msg-image-preview';
    imageEl.src = imageDataUrl;
    imageEl.alt = String(name || t('media.preview_alt')).trim();
    imageEl.loading = 'lazy';
    imageEl.decoding = 'async';
    imageEl.tabIndex = 0;
    imageEl.setAttribute('role', 'button');
    imageEl.setAttribute('data-image-preview-trigger', 'true');
    imageEl.setAttribute('data-image-preview-src', imageDataUrl);
    imageEl.setAttribute('data-image-preview-name', String(name || '').trim());
    imageEl.setAttribute('title', t('media.preview_open'));
    figureEl.appendChild(imageEl);
    return figureEl;
}

function buildMediaFigure({ modality, url, mimeType, name }) {
    if (modality === 'image') {
        return buildImageFigure(url, name);
    }
    const figureEl = document.createElement('figure');
    figureEl.className = `msg-media msg-media-${modality || 'file'}`;
    let mediaEl = null;
    if (modality === 'audio') {
        mediaEl = document.createElement('audio');
        mediaEl.controls = true;
        mediaEl.preload = 'metadata';
        mediaEl.src = url;
    } else if (modality === 'video') {
        mediaEl = document.createElement('video');
        mediaEl.controls = true;
        mediaEl.preload = 'metadata';
        mediaEl.src = url;
        mediaEl.playsInline = true;
    } else {
        return null;
    }
    if (mimeType) {
        mediaEl.setAttribute('type', mimeType);
    }
    figureEl.appendChild(mediaEl);
    const safeName = String(name || '').trim();
    if (safeName) {
        const captionEl = document.createElement('figcaption');
        captionEl.textContent = safeName;
        figureEl.appendChild(captionEl);
    }
    return figureEl;
}

function resolveStandaloneImageDataUrl(source) {
    const normalized = String(source || '').trim();
    if (!normalized) return '';

    if (normalized.startsWith(DATA_IMAGE_PREFIX)) {
        const separatorIndex = normalized.indexOf(';base64,');
        if (separatorIndex <= 0) return '';
        const mimeType = normalized.slice(5, separatorIndex);
        const base64Body = normalized.slice(separatorIndex + ';base64,'.length);
        const compactBody = base64Body.replace(/\s+/g, '');
        if (!mimeType || !compactBody || !BASE64_BODY_PATTERN.test(compactBody)) {
            return '';
        }
        return `data:${mimeType};base64,${compactBody}`;
    }

    const compact = normalized.replace(/\s+/g, '');
    if (compact.length < 64 || !BASE64_BODY_PATTERN.test(compact)) {
        return '';
    }
    const mimeType = detectBase64ImageMimeType(compact);
    if (!mimeType) return '';
    return `data:${mimeType};base64,${compact}`;
}

function resolveWorkspaceImagePreviewUrls(source, workspaceId) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    if (!safeWorkspaceId) {
        return [];
    }

    const previewUrls = [];
    const seenPaths = new Set();
    const candidates = [
        ...extractImagePathCandidates(source, IMAGE_CODE_SPAN_PATTERN, 1),
        ...extractImagePathCandidates(source, IMAGE_BARE_PATH_PATTERN, 1),
    ];
    candidates.forEach(candidate => {
        if (seenPaths.has(candidate)) {
            return;
        }
        seenPaths.add(candidate);
        const previewUrl = buildWorkspaceImagePreviewUrl(safeWorkspaceId, candidate);
        if (previewUrl) {
            previewUrls.push(previewUrl);
        }
    });
    return previewUrls;
}

function extractImagePathCandidates(source, pattern, groupIndex) {
    const text = String(source || '');
    if (!text) {
        return [];
    }
    const candidates = [];
    const matches = text.matchAll(pattern);
    for (const match of matches) {
        const normalizedPath = normalizeImagePathCandidate(match[groupIndex]);
        if (normalizedPath) {
            candidates.push(normalizedPath);
        }
    }
    return candidates;
}

function normalizeImagePathCandidate(rawValue) {
    const candidate = String(rawValue || '')
        .trim()
        .replace(TRAILING_PATH_PUNCTUATION_PATTERN, '')
        .replaceAll('\\', '/');
    if (!candidate || !IMAGE_PATH_PATTERN.test(candidate)) {
        return '';
    }
    return candidate;
}

function detectBase64ImageMimeType(base64Text) {
    let binary = '';
    try {
        binary = atob(base64Text.slice(0, 256));
    } catch (error) {
        return '';
    }
    const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
    if (matchesPrefix(bytes, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) {
        return 'image/png';
    }
    if (matchesPrefix(bytes, [0xff, 0xd8, 0xff])) {
        return 'image/jpeg';
    }
    if (matchesAsciiPrefix(bytes, 'GIF87a') || matchesAsciiPrefix(bytes, 'GIF89a')) {
        return 'image/gif';
    }
    if (matchesAsciiPrefix(bytes, 'RIFF') && matchesAsciiPrefix(bytes.slice(8), 'WEBP')) {
        return 'image/webp';
    }
    return '';
}

function matchesPrefix(bytes, prefix) {
    if (!bytes || bytes.length < prefix.length) return false;
    return prefix.every((value, index) => bytes[index] === value);
}

function matchesAsciiPrefix(bytes, prefix) {
    if (!bytes || bytes.length < prefix.length) return false;
    return Array.from(prefix).every((char, index) => bytes[index] === char.charCodeAt(0));
}
