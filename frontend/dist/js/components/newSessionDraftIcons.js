/**
 * components/newSessionDraftIcons.js
 * Shared render helpers for the new session draft view.
 */
export function renderDraftIcon(name) {
    const icons = {
        spark: '<svg viewBox="0 0 24 24" fill="none"><path d="M12 3.5l1.6 4.7 4.9 1.8-4.9 1.8L12 16.5l-1.6-4.7-4.9-1.8 4.9-1.8L12 3.5ZM18.5 15.5l.8 2.1 2.2.9-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.9.8-2.1Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
        code: '<svg viewBox="0 0 24 24" fill="none"><path d="M8.5 8l-4 4 4 4M15.5 8l4 4-4 4M13 6.5l-2 11" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        branch: '<svg viewBox="0 0 24 24" fill="none"><path d="M7 5v14M7 7.5h4.5a4 4 0 0 1 4 4V16" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="7" cy="5" r="2" stroke="currentColor" stroke-width="1.7"/><circle cx="7" cy="19" r="2" stroke="currentColor" stroke-width="1.7"/><circle cx="15.5" cy="18" r="2" stroke="currentColor" stroke-width="1.7"/></svg>',
        flow: '<svg viewBox="0 0 24 24" fill="none"><rect x="9" y="3.5" width="6" height="4.5" rx="1.2" stroke="currentColor" stroke-width="1.7"/><rect x="4" y="16" width="6" height="4.5" rx="1.2" stroke="currentColor" stroke-width="1.7"/><rect x="14" y="16" width="6" height="4.5" rx="1.2" stroke="currentColor" stroke-width="1.7"/><path d="M12 8v4.5M7 16v-3.5h10V16" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        flask: '<svg viewBox="0 0 24 24" fill="none"><path d="M9 4h6M10 4v5.4l-4.1 7.2A2.2 2.2 0 0 0 7.8 20h8.4a2.2 2.2 0 0 0 1.9-3.4L14 9.4V4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M8.4 15h7.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        warning: '<svg viewBox="0 0 24 24" fill="none"><path d="M10.5 4.9L3.8 17a2 2 0 0 0 1.8 3h12.8a2 2 0 0 0 1.8-3L13.5 4.9a1.7 1.7 0 0 0-3 0Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M12 9v4M12 16.8v.1" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>',
        bot: '<svg viewBox="0 0 24 24" fill="none"><rect x="5" y="8" width="14" height="10" rx="3" stroke="currentColor" stroke-width="1.7"/><path d="M12 8V4.5M8.7 12.5h.1M15.2 12.5h.1M9.5 16h5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M3.5 12.5v2M20.5 12.5v2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        clock: '<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="7.5" stroke="currentColor" stroke-width="1.7"/><path d="M12 8v4.4l2.8 1.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        calendar: '<svg viewBox="0 0 24 24" fill="none"><path d="M6.5 5.5h11A2.5 2.5 0 0 1 20 8v9.5a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5V8a2.5 2.5 0 0 1 2.5-2.5Z" stroke="currentColor" stroke-width="1.7"/><path d="M8 3.5v4M16 3.5v4M4.5 10h15M8 14h2.5M13.5 14H16" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        chat: '<svg viewBox="0 0 24 24" fill="none"><path d="M5 6.5h14a2 2 0 0 1 2 2V15a2 2 0 0 1-2 2h-6.5l-4 2.8V17H5a2 2 0 0 1-2-2V8.5a2 2 0 0 1 2-2Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M7.5 10h9M7.5 13h5.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        bulb: '<svg viewBox="0 0 24 24" fill="none"><path d="M9.5 18h5M10 21h4M8 13.5a6 6 0 1 1 8 0c-.9.75-1.4 1.75-1.55 3h-4.9c-.15-1.25-.65-2.25-1.55-3Z" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        book: '<svg viewBox="0 0 24 24" fill="none"><path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H20v16H7.5A2.5 2.5 0 0 0 5 21.5v-16Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M5 18.5A2.5 2.5 0 0 1 7.5 16H20M9 7h7M9 10h5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
    };
    return icons[name] || icons.spark;
}

export function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
