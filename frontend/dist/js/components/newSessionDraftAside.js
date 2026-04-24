/**
 * components/newSessionDraftAside.js
 * Right-side guidance panels for the new session draft view.
 */
import { t } from '../utils/i18n.js';
import { escapeHtml, renderDraftIcon } from './newSessionDraftIcons.js';

export function renderSuggestionPanel() {
    const rows = [
        ['1', t('new_session_draft.suggestion.workspace_title'), t('new_session_draft.suggestion.workspace_copy')],
        ['2', t('new_session_draft.suggestion.mode_title'), t('new_session_draft.suggestion.mode_copy')],
        ['3', t('new_session_draft.suggestion.role_title'), t('new_session_draft.suggestion.role_copy')],
        ['4', t('new_session_draft.suggestion.yolo_title'), t('new_session_draft.suggestion.yolo_copy')],
        ['5', t('new_session_draft.suggestion.input_title'), t('new_session_draft.suggestion.input_copy')],
    ];
    return `
        <section class="new-session-side-panel new-session-suggestion-panel">
            <h2>
                <span aria-hidden="true">${renderDraftIcon('bulb')}</span>
                ${escapeHtml(t('new_session_draft.suggestion_title'))}
            </h2>
            <ol class="new-session-suggestion-list">
                ${rows.map(([number, title, copy]) => `
                    <li>
                        <span class="new-session-suggestion-number">${escapeHtml(number)}</span>
                        <span class="new-session-suggestion-copy">
                            <strong>${escapeHtml(title)}</strong>
                            <span>${escapeHtml(copy)}</span>
                        </span>
                    </li>
                `).join('')}
            </ol>
        </section>
    `;
}

export function renderTipsPanel() {
    return `
        <section class="new-session-side-panel new-session-tips-panel">
            <h2>
                <span aria-hidden="true">${renderDraftIcon('book')}</span>
                ${escapeHtml(t('new_session_draft.tips_title'))}
            </h2>
            <ul>
                <li>${escapeHtml(t('new_session_draft.tip.complex'))}</li>
                <li>${escapeHtml(t('new_session_draft.tip.orchestration'))}</li>
                <li>${escapeHtml(t('new_session_draft.tip.mention'))}</li>
                <li>${escapeHtml(t('new_session_draft.tip.subagents'))}</li>
            </ul>
        </section>
    `;
}
