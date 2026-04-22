/**
 * components/messageRenderer/helpers/prompt.js
 * Shared prompt content normalization and rendering helpers.
 */
import { t } from '../../../utils/i18n.js';
import { appendStructuredContentPart } from './content.js';

export function normalizePromptContentParts(promptPayload) {
    if (Array.isArray(promptPayload)) {
        const normalizedParts = promptPayload
            .map(normalizePromptContentPart)
            .filter(Boolean);
        return normalizedParts.length > 0 ? normalizedParts : null;
    }
    const text = String(promptPayload || '').replace(/\r\n?/g, '\n').trim();
    if (!text) {
        return null;
    }
    return [{ kind: 'text', text }];
}

export function normalizePromptContentPart(part) {
    if (typeof part === 'string') {
        const text = String(part || '').replace(/\r\n?/g, '\n').trim();
        return text ? { kind: 'text', text } : null;
    }
    if (!part || typeof part !== 'object') {
        return null;
    }

    const kind = String(part.kind || '').trim();
    if (kind === 'text') {
        const text = String(part.text || part.content || '').replace(/\r\n?/g, '\n').trim();
        return text ? { kind: 'text', text } : null;
    }
    if (kind === 'inline_media') {
        const mimeType = String(part.mime_type || '').trim();
        const base64Data = String(part.base64_data || '').trim();
        if (!mimeType || !base64Data) {
            return null;
        }
        return {
            kind: 'media_ref',
            modality: String(part.modality || 'image').trim() || 'image',
            mime_type: mimeType,
            url: `data:${mimeType};base64,${base64Data}`,
            name: String(part.name || '').trim(),
        };
    }
    if (kind === 'media_ref') {
        const url = String(part.url || '').trim();
        if (!url) {
            return null;
        }
        return {
            kind: 'media_ref',
            modality: String(part.modality || '').trim(),
            mime_type: String(part.mime_type || '').trim(),
            url,
            name: String(part.name || '').trim(),
        };
    }
    if (kind === 'binary') {
        const mediaType = String(part.media_type || '').trim();
        const data = String(part.data || '').trim();
        if (!mediaType || !data) {
            return null;
        }
        return {
            kind: 'media_ref',
            modality: mediaType.startsWith('audio/')
                ? 'audio'
                : mediaType.startsWith('video/')
                    ? 'video'
                    : 'image',
            mime_type: mediaType,
            url: `data:${mediaType};base64,${data}`,
            name: String(part.name || '').trim(),
        };
    }
    if (kind === 'image-url' || kind === 'audio-url' || kind === 'video-url') {
        const url = String(part.url || '').trim();
        if (!url) {
            return null;
        }
        return {
            kind: 'media_ref',
            modality: kind.replace('-url', ''),
            mime_type: String(part.media_type || '').trim(),
            url,
            name: String(part.name || '').trim(),
        };
    }
    return null;
}

export function summarizePromptContent(promptPayload, options = {}) {
    return summarizePromptContentParts(
        normalizePromptContentParts(promptPayload),
        options,
    );
}

export function summarizePromptContentParts(parts, options = {}) {
    const fragments = [];
    const normalizedParts = Array.isArray(parts) ? parts : [];
    normalizedParts.forEach(part => {
        if (!part || typeof part !== 'object') {
            return;
        }
        if (String(part.kind || '') === 'text') {
            const text = String(part.text || '').replace(/\r\n?/g, '\n').trim();
            if (text) {
                fragments.push(text);
            }
            return;
        }
        if (String(part.kind || '') === 'media_ref') {
            const modality = String(part.modality || 'media').trim() || 'media';
            const name = String(part.name || '').trim() || modality;
            fragments.push(`[${modality}: ${name}]`);
        }
    });
    const summary = fragments.join('\n\n').trim();
    if (summary) {
        return summary;
    }
    return String(options.fallback || '').trim();
}

export function appendPromptContentBlock(contentEl, promptPayload, options = {}) {
    const promptEl = document.createElement('details');
    promptEl.className = String(options.className || 'user-prompt-block');
    promptEl.open = options.open === true;
    promptEl.innerHTML = `
        <summary class="user-prompt-summary">
            <span class="user-prompt-title"></span>
            <span class="user-prompt-preview"></span>
        </summary>
        <div class="user-prompt-body">
            <div class="user-prompt-text"></div>
        </div>
    `;
    updatePromptContentBlock(promptEl, promptPayload, options);
    contentEl.appendChild(promptEl);
    return promptEl;
}

export function updatePromptContentBlock(promptEl, promptPayload, options = {}) {
    const normalizedParts = normalizePromptContentParts(promptPayload);
    const summary = summarizePromptContentParts(normalizedParts, {
        fallback: String(options.fallbackTitle || t('subagent.task_prompt')),
    });
    const lines = summary ? summary.split('\n') : [];
    const title = lines[0] || String(options.fallbackTitle || t('subagent.task_prompt'));
    const preview = lines.length > 1 ? lines.slice(1).join('\n') : title;
    const titleEl = promptEl?.querySelector('.user-prompt-title');
    const previewEl = promptEl?.querySelector('.user-prompt-preview');
    const bodyEl = promptEl?.querySelector('.user-prompt-text');

    if (titleEl) {
        titleEl.textContent = title;
    }
    if (previewEl) {
        previewEl.textContent = preview;
    }
    if (bodyEl) {
        renderPromptContentParts(bodyEl, normalizedParts || [], options);
    }
    return promptEl;
}

export function renderPromptContentParts(targetEl, parts, options = {}) {
    if (!targetEl) {
        return targetEl;
    }
    targetEl.replaceChildren();
    const normalizedParts = Array.isArray(parts) ? parts : [];
    normalizedParts.forEach(part => {
        appendStructuredContentPart(targetEl, part, {
            richContent: {
                enableWorkspaceImagePreview: options.enableWorkspaceImagePreview !== false,
            },
        });
    });
    return targetEl;
}
