/**
 * components/newSessionDraftQuickCards.js
 * Quick prompt and recent action cards for the new session draft view.
 */
import { t } from '../utils/i18n.js';
import { escapeHtml, renderDraftIcon } from './newSessionDraftIcons.js';

const QUICK_START_ITEMS = [
    {
        key: 'code_review',
        icon: 'code',
        promptKey: 'new_session_draft.quick.code_review_prompt',
    },
    {
        key: 'pr_summary',
        icon: 'branch',
        promptKey: 'new_session_draft.quick.pr_summary_prompt',
    },
    {
        key: 'requirements',
        icon: 'flow',
        promptKey: 'new_session_draft.quick.requirements_prompt',
    },
    {
        key: 'tests',
        icon: 'flask',
        promptKey: 'new_session_draft.quick.tests_prompt',
    },
    {
        key: 'debug',
        icon: 'warning',
        promptKey: 'new_session_draft.quick.debug_prompt',
    },
    {
        key: 'automation',
        icon: 'bot',
        promptKey: 'new_session_draft.quick.automation_prompt',
    },
];

export function renderQuickStartCards() {
    return QUICK_START_ITEMS.map(renderQuickStartItem).join('');
}

export function renderRecentCards(recentSession) {
    return `
        ${renderContinueSessionCard(recentSession)}
        <button class="new-session-recent-card" type="button" data-draft-prompt="${escapeHtml(t('new_session_draft.recent.schedule_prompt'))}">
            <span class="new-session-card-icon new-session-card-icon-schedule" aria-hidden="true">${renderDraftIcon('calendar')}</span>
            <span class="new-session-card-copy">
                <strong>${escapeHtml(t('new_session_draft.recent.schedule_title'))}</strong>
                <span>${escapeHtml(t('new_session_draft.recent.schedule_copy'))}</span>
            </span>
            <span class="new-session-card-arrow" aria-hidden="true">›</span>
        </button>
        <button class="new-session-recent-card" type="button" data-draft-open-gateway>
            <span class="new-session-card-icon new-session-card-icon-chat" aria-hidden="true">${renderDraftIcon('chat')}</span>
            <span class="new-session-card-copy">
                <strong>${escapeHtml(t('new_session_draft.recent.im_title'))}</strong>
                <span>${escapeHtml(t('new_session_draft.recent.im_copy'))}</span>
            </span>
            <span class="new-session-card-arrow" aria-hidden="true">›</span>
        </button>
    `;
}

function renderQuickStartItem(item) {
    const title = t(`new_session_draft.quick.${item.key}.title`);
    const copy = t(`new_session_draft.quick.${item.key}.copy`);
    const prompt = t(item.promptKey);
    return `
        <button class="new-session-quick-card new-session-quick-card-${escapeHtml(item.key)}" type="button" data-draft-prompt="${escapeHtml(prompt)}">
            <span class="new-session-card-icon" aria-hidden="true">${renderDraftIcon(item.icon)}</span>
            <span class="new-session-card-copy">
                <strong>${escapeHtml(title)}</strong>
                <span>${escapeHtml(copy)}</span>
            </span>
            <span class="new-session-card-arrow" aria-hidden="true">→</span>
        </button>
    `;
}

function renderContinueSessionCard(session) {
    if (!session) {
        return `
            <button class="new-session-recent-card" type="button" data-draft-prompt="${escapeHtml(t('new_session_draft.recent.continue_prompt'))}">
                <span class="new-session-card-icon new-session-card-icon-clock" aria-hidden="true">${renderDraftIcon('clock')}</span>
                <span class="new-session-card-copy">
                    <strong>${escapeHtml(t('new_session_draft.recent.continue_title'))}</strong>
                    <span>${escapeHtml(t('new_session_draft.recent.continue_empty'))}</span>
                </span>
                <span class="new-session-card-arrow" aria-hidden="true">›</span>
            </button>
        `;
    }
    return `
        <button class="new-session-recent-card" type="button" data-draft-select-session data-session-id="${escapeHtml(session.sessionId)}">
            <span class="new-session-card-icon new-session-card-icon-clock" aria-hidden="true">${renderDraftIcon('clock')}</span>
            <span class="new-session-card-copy">
                <strong>${escapeHtml(t('new_session_draft.recent.continue_title'))}</strong>
                <span>${escapeHtml(session.label || session.sessionId)}</span>
            </span>
            <span class="new-session-card-arrow" aria-hidden="true">›</span>
        </button>
    `;
}
