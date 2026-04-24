/**
 * components/newSessionDraftView.js
 * Page shell for the new session draft landing page.
 */
import { t } from '../utils/i18n.js';
import { renderSuggestionPanel, renderTipsPanel } from './newSessionDraftAside.js';
import { escapeHtml, renderDraftIcon } from './newSessionDraftIcons.js';
import { renderQuickStartCards, renderRecentCards } from './newSessionDraftQuickCards.js';

export function renderNewSessionDraftView(recentSession) {
    return `
        <section class="new-session-draft-page" aria-label="${escapeHtml(t('new_session_draft.title'))}">
            <div class="new-session-draft-main">
                <div class="new-session-draft-priority">
                    <div class="new-session-draft-hero">
                        <div class="new-session-draft-spark" aria-hidden="true">
                            ${renderDraftIcon('spark')}
                        </div>
                        <h1>${escapeHtml(t('new_session_draft.hero_title'))}</h1>
                        <p>${escapeHtml(t('new_session_draft.hero_copy'))}</p>
                    </div>
                    <div id="new-session-draft-composer-slot" class="new-session-draft-composer-slot"></div>
                </div>
                <div class="new-session-draft-secondary">
                    <div class="new-session-section-head">
                        <h2>${escapeHtml(t('new_session_draft.quick_title'))}</h2>
                    </div>
                    <div class="new-session-quick-grid">
                        ${renderQuickStartCards()}
                    </div>
                </div>
                <div class="new-session-draft-tertiary">
                    <div class="new-session-section-head new-session-section-head-recent">
                        <h2>${escapeHtml(t('new_session_draft.recent_title'))}</h2>
                    </div>
                    <div class="new-session-recent-grid">
                        ${renderRecentCards(recentSession)}
                    </div>
                </div>
            </div>
            <aside class="new-session-draft-aside" aria-label="${escapeHtml(t('new_session_draft.suggestion_title'))}">
                ${renderSuggestionPanel()}
                ${renderTipsPanel()}
            </aside>
        </section>
    `;
}
