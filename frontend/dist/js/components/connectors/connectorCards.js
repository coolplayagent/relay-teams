/**
 * components/connectors/connectorCards.js
 * Renders the built-in connector card wall.
 */
import { formatMessage, t } from '../../utils/i18n.js';

const CONNECTOR_ICON_BY_PROVIDER = Object.freeze({
    github: '/assets/connectors/github.svg',
    discord: '/assets/connectors/discord.svg',
    feishu: '/assets/connectors/feishu.svg',
    wechat: '/assets/connectors/wechat.svg',
    xiaoluban: '/assets/connectors/xiaoluban.svg',
});

const CONNECTOR_GROUP_BY_PROVIDER = Object.freeze({
    github: 'official',
    discord: 'internal',
    feishu: 'internal',
    wechat: 'internal',
    xiaoluban: 'internal',
});

const CONNECTOR_GROUP_LABEL_KEYS = Object.freeze({
    official: 'feature.connectors.group.official',
    internal: 'feature.connectors.group.internal',
});

const STATUS_LABEL_KEYS = Object.freeze({
    connected: 'feature.connectors.status.connected',
    needs_config: 'feature.connectors.status.needs_config',
    disabled: 'feature.connectors.status.disabled',
    error: 'feature.connectors.status.error',
});

const PROVIDER_NAME_KEYS = Object.freeze({
    github: 'feature.connectors.provider.github.name',
    discord: 'feature.connectors.provider.discord.name',
    feishu: 'feature.connectors.provider.feishu.name',
    wechat: 'feature.connectors.provider.wechat.name',
    xiaoluban: 'feature.connectors.provider.xiaoluban.name',
});

const PROVIDER_DESCRIPTION_KEYS = Object.freeze({
    github: 'feature.connectors.provider.github.description',
    discord: 'feature.connectors.provider.discord.description',
    feishu: 'feature.connectors.provider.feishu.description',
    wechat: 'feature.connectors.provider.wechat.description',
    xiaoluban: 'feature.connectors.provider.xiaoluban.description',
});

const CAPABILITY_LABEL_KEYS = Object.freeze({
    repositories: 'feature.connectors.capability.repositories',
    issues: 'feature.connectors.capability.issues',
    pull_requests: 'feature.connectors.capability.pull_requests',
    actions: 'feature.connectors.capability.actions',
    messages: 'feature.connectors.capability.messages',
    mentions: 'feature.connectors.capability.mentions',
    bot_events: 'feature.connectors.capability.bot_events',
    direct_messages: 'feature.connectors.capability.direct_messages',
    group_messages: 'feature.connectors.capability.group_messages',
    file_messages: 'feature.connectors.capability.file_messages',
    im_forwarding: 'feature.connectors.capability.im_forwarding',
    notifications: 'feature.connectors.capability.notifications',
});

const CONNECT_ACTION_LABEL_KEYS = Object.freeze({
    discord: 'feature.connectors.action.connect_discord',
    feishu: 'feature.connectors.action.connect_feishu',
    wechat: 'feature.connectors.action.connect_wechat',
    xiaoluban: 'feature.connectors.action.connect_xiaoluban',
});

export function renderConnectorsCardPageMarkup({
    connectorsResponse,
    searchQuery = '',
    statusFilter = 'all',
} = {}) {
    const items = Array.isArray(connectorsResponse?.items)
        ? connectorsResponse.items
        : [];
    const filteredItems = filterConnectorItems(items, { searchQuery, statusFilter });
    const grouped = groupConnectorItems(filteredItems);
    const summary = connectorsResponse?.summary && typeof connectorsResponse.summary === 'object'
        ? connectorsResponse.summary
        : {};
    return `
        <div class="feature-page connectors-page">
            <section class="connectors-hero">
                <div class="connectors-title-block">
                    <h2>${escapeHtml(t('feature.gateway.title'))}</h2>
                    <p>${escapeHtml(t('feature.gateway.subtitle'))}</p>
                </div>
                <div class="connectors-summary" aria-label="${escapeHtml(t('feature.gateway.summary'))}">
                    ${renderSummaryChip(t('feature.connectors.summary.connected'), summary.connected || 0, 'connected')}
                    ${renderSummaryChip(t('feature.connectors.summary.pending'), summary.needs_config || 0, 'pending')}
                    ${renderSummaryChip(t('feature.connectors.summary.error'), summary.error || 0, 'error')}
                </div>
            </section>
            <section class="connectors-toolbar" aria-label="${escapeHtml(t('feature.gateway.filters'))}">
                <label class="connectors-search">
                    <span class="connectors-search-icon" aria-hidden="true">⌕</span>
                    <input type="search" value="${escapeHtml(searchQuery)}" placeholder="${escapeHtml(t('feature.gateway.search_placeholder'))}" data-connectors-search>
                </label>
                <div class="connectors-tabs" role="tablist">
                    ${renderFilterButton('all', t('feature.connectors.filter.all'), statusFilter)}
                    ${renderFilterButton('connected', t('feature.connectors.filter.connected'), statusFilter)}
                    ${renderFilterButton('unconnected', t('feature.connectors.filter.unconnected'), statusFilter)}
                </div>
            </section>
            ${renderConnectorGroup('official', grouped.official)}
            ${renderConnectorGroup('internal', grouped.internal)}
            ${filteredItems.length === 0 ? renderEmptyState() : ''}
        </div>
    `;
}

export function renderConnectorConfigModalMarkup({
    item,
    accountManagementMarkup = '',
} = {}) {
    if (!item) {
        return '';
    }
    const provider = String(item.provider || item.connector_id || '').trim();
    const status = String(item.status || 'needs_config').trim();
    return `
        <div class="modal gateway-feature-modal connectors-config-modal" data-connector-modal>
            <div class="modal-content gateway-feature-modal-content connectors-config-modal-content" role="dialog" aria-modal="true" aria-labelledby="connector-config-modal-title">
                <div class="modal-header gateway-feature-modal-header">
                    <div class="gateway-feature-modal-heading connectors-config-heading">
                        <img src="${escapeHtml(CONNECTOR_ICON_BY_PROVIDER[provider] || '')}" alt="" aria-hidden="true">
                        <div>
                            <h3 id="connector-config-modal-title">${escapeHtml(formatProviderName(item))}</h3>
                            <p>${escapeHtml(formatProviderDescription(item))}</p>
                        </div>
                    </div>
                    <button class="icon-btn" type="button" aria-label="${escapeHtml(t('settings.action.cancel'))}" data-connector-modal-close>
                        <span aria-hidden="true">×</span>
                    </button>
                </div>
                <div class="gateway-feature-modal-body connectors-config-body">
                    <div class="connectors-config-status-row">
                        <span class="connectors-status-dot is-${escapeHtml(status)}"></span>
                        <strong>${escapeHtml(formatStatus(status))}</strong>
                        <span>${escapeHtml(formatAccountSummary(item))}</span>
                    </div>
                    <dl class="connectors-config-details">
                        <div><dt>${escapeHtml(t('feature.connectors.detail.auth_type'))}</dt><dd>${escapeHtml(formatAuthType(item.auth_type))}</dd></div>
                        <div><dt>${escapeHtml(t('feature.connectors.detail.last_activity'))}</dt><dd>${escapeHtml(formatDateTime(item.last_activity_at))}</dd></div>
                        <div><dt>${escapeHtml(t('feature.connectors.detail.capabilities'))}</dt><dd>${escapeHtml(formatCapabilities(item.capabilities))}</dd></div>
                    </dl>
                    ${item.last_error ? `<p class="connectors-error-copy">${escapeHtml(item.last_error)}</p>` : ''}
                    ${accountManagementMarkup || ''}
                </div>
                <div class="connectors-config-actions">
                    <button class="primary-btn" type="button" data-connector-configure="${escapeHtml(provider)}">${escapeHtml(formatConnectActionLabel(provider, status))}</button>
                </div>
            </div>
        </div>
    `;
}

function renderSummaryChip(label, value, tone) {
    return `
        <span class="connectors-summary-chip is-${escapeHtml(tone)}">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
        </span>
    `;
}

function renderFilterButton(value, label, activeValue) {
    const active = value === activeValue;
    return `
        <button class="connectors-tab${active ? ' is-active' : ''}" type="button" role="tab" aria-selected="${active ? 'true' : 'false'}" data-connectors-filter="${escapeHtml(value)}">
            ${escapeHtml(label)}
        </button>
    `;
}

function renderConnectorGroup(groupKey, items) {
    if (!Array.isArray(items) || items.length === 0) {
        return '';
    }
    return `
        <section class="connectors-section">
            <h3>${escapeHtml(t(CONNECTOR_GROUP_LABEL_KEYS[groupKey] || groupKey))}</h3>
            <div class="connectors-card-grid">
                ${items.map(renderConnectorCard).join('')}
            </div>
        </section>
    `;
}

function renderConnectorCard(item) {
    const provider = String(item?.provider || item?.connector_id || '').trim();
    const status = String(item?.status || 'needs_config').trim();
    const accountCount = Number(item?.account_count || 0);
    return `
        <article class="connectors-card" data-connector-card="${escapeHtml(provider)}">
            <button class="connectors-card-menu" type="button" aria-label="${escapeHtml(t('feature.connectors.action.manage'))}" data-connector-manage="${escapeHtml(provider)}">...</button>
            <div class="connectors-card-main">
                <img class="connectors-card-icon" src="${escapeHtml(CONNECTOR_ICON_BY_PROVIDER[provider] || '')}" alt="" aria-hidden="true">
                <div class="connectors-card-title">
                    <h4>${escapeHtml(formatProviderName(item))}</h4>
                    <p>${escapeHtml(formatProviderDescription(item))}</p>
                </div>
            </div>
            <div class="connectors-card-footer">
                <span class="connectors-card-status">
                    <span class="connectors-status-dot is-${escapeHtml(status)}"></span>
                    ${escapeHtml(formatStatus(status))}
                </span>
                <button class="connectors-card-action" type="button" data-connector-open="${escapeHtml(provider)}">
                    ${escapeHtml(formatCardActionLabel(provider, status, accountCount))}
                </button>
            </div>
        </article>
    `;
}

function formatCardActionLabel(provider, status, accountCount) {
    if (provider === 'github') {
        return status === 'connected'
            ? t('feature.connectors.action.configure')
            : t('feature.connectors.action.connect');
    }
    if (accountCount > 0) {
        return t('feature.connectors.action.manage');
    }
    return t('feature.connectors.action.connect');
}

function formatConnectActionLabel(provider, status) {
    const key = CONNECT_ACTION_LABEL_KEYS[provider] || '';
    if (key) {
        return t(key);
    }
    return status === 'connected'
        ? t('feature.connectors.action.configure')
        : t('feature.connectors.action.connect');
}

function renderEmptyState() {
    return `
        <div class="connectors-empty">
            <h3>${escapeHtml(t('feature.connectors.empty.title'))}</h3>
            <p>${escapeHtml(t('feature.connectors.empty.copy'))}</p>
        </div>
    `;
}

function filterConnectorItems(items, { searchQuery, statusFilter }) {
    const normalizedQuery = String(searchQuery || '').trim().toLowerCase();
    return items.filter(item => {
        const status = String(item?.status || '').trim();
        if (statusFilter === 'connected' && status !== 'connected') {
            return false;
        }
        if (statusFilter === 'unconnected' && status === 'connected') {
            return false;
        }
        if (!normalizedQuery) {
            return true;
        }
        const haystack = [
            item?.display_name,
            item?.description,
            item?.provider,
            ...(Array.isArray(item?.capabilities) ? item.capabilities : []),
        ].join(' ').toLowerCase();
        return haystack.includes(normalizedQuery);
    });
}

function groupConnectorItems(items) {
    return items.reduce(
        (result, item) => {
            const provider = String(item?.provider || '').trim();
            const groupKey = CONNECTOR_GROUP_BY_PROVIDER[provider] || 'internal';
            result[groupKey].push(item);
            return result;
        },
        { official: [], internal: [] },
    );
}

function formatAccountSummary(item) {
    const accountCount = Number(item?.account_count || 0);
    const enabledCount = Number(item?.enabled_count || 0);
    return formatMessage('feature.connectors.account_summary', {
        enabled: enabledCount,
        total: accountCount,
    });
}

function formatAuthType(authType) {
    const value = String(authType || '').trim();
    if (value === 'api_token') {
        return 'API Token';
    }
    if (value === 'api_key') {
        return 'API Key';
    }
    if (value === 'qr_login') {
        return t('feature.connectors.auth.qr_login');
    }
    return value || t('feature.connectors.value.not_configured');
}

function formatDateTime(value) {
    if (!value) {
        return t('feature.connectors.value.none');
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return String(value);
    }
    return date.toLocaleString();
}

function formatCapabilities(capabilities) {
    const items = Array.isArray(capabilities) ? capabilities : [];
    if (items.length === 0) {
        return t('feature.connectors.value.none');
    }
    return items
        .map(item => t(CAPABILITY_LABEL_KEYS[String(item || '').trim()] || String(item || '').trim()))
        .join(', ');
}

function formatProviderName(item) {
    const provider = String(item?.provider || item?.connector_id || '').trim();
    return t(PROVIDER_NAME_KEYS[provider] || '') || String(item?.display_name || provider);
}

function formatProviderDescription(item) {
    const provider = String(item?.provider || item?.connector_id || '').trim();
    const key = PROVIDER_DESCRIPTION_KEYS[provider] || '';
    const translated = key ? t(key) : '';
    return translated && translated !== key ? translated : String(item?.description || '');
}

function formatStatus(status) {
    const key = STATUS_LABEL_KEYS[String(status || '').trim()] || '';
    return key ? t(key) : String(status || '');
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
