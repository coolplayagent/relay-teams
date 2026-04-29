/**
 * components/messageRenderer/injectionMarker.js
 * Shared inline marker for user/subagent messages injected into a running turn.
 */

const INJECT_ARROW_SVG = '<svg viewBox="0 0 16 16" fill="none"><path d="M4 3.5v3.25a3.75 3.75 0 0 0 3.75 3.75h4.5M10 8.25l2.25 2.25L10 12.75" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const INJECT_FAILED_SVG = '<svg viewBox="0 0 16 16" fill="none"><path d="M8 5v3.25M8 11h.01M2.75 13.25h10.5L8 2.75 2.75 13.25Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

export function renderInjectionMarker(container, rawMessage, options = {}) {
    if (!container || !rawMessage || typeof rawMessage !== 'object') {
        return null;
    }
    const content = injectionContentText(rawMessage);
    if (!content || typeof document === 'undefined') {
        return null;
    }
    const status = String(
        rawMessage.injection_status
        || rawMessage.status
        || rawMessage.payload?.status
        || 'applied',
    ).trim();
    const marker = document.createElement('div');
    marker.className = options.inline === true
        ? 'message-inject-marker is-inline'
        : 'message-inject-marker';
    marker.dataset.status = status || 'applied';
    const injectionId = String(rawMessage.injection_id || rawMessage.message_id || '').trim();
    if (injectionId) {
        marker.dataset.injectionId = injectionId;
    }
    const icon = document.createElement('span');
    icon.className = 'message-inject-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = status === 'failed' ? INJECT_FAILED_SVG : INJECT_ARROW_SVG;
    const text = document.createElement('span');
    text.className = 'message-inject-text';
    text.textContent = content;
    marker.append(icon, text);
    container.appendChild(marker);
    return marker;
}

export function injectionContentText(rawMessage) {
    if (!rawMessage || typeof rawMessage !== 'object') {
        return '';
    }
    const direct = String(rawMessage.content || rawMessage.text || '').trim();
    if (direct) {
        return direct;
    }
    const parts = Array.isArray(rawMessage.content_parts)
        ? rawMessage.content_parts
        : Array.isArray(rawMessage.message?.parts)
            ? rawMessage.message.parts
            : [];
    return parts
        .map(part => String(part?.content || part?.text || '').trim())
        .filter(Boolean)
        .join('\n\n')
        .trim();
}
