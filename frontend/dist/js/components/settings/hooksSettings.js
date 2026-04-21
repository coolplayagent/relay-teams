/**
 * components/settings/hooksSettings.js
 * Loaded hooks runtime status panel.
 */
import { fetchHookRuntimeView } from '../../core/api.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let latestRuntimeView = null;
let latestErrorMessage = '';
let loadInFlight = false;
let languageBound = false;
let activeHooksLoadRequestId = 0;

export function bindHooksSettingsHandlers() {
    if (!languageBound && typeof document?.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderHooksRuntimePanel();
        });
        languageBound = true;
    }
}

export async function loadHooksSettingsPanel() {
    const requestId = ++activeHooksLoadRequestId;
    loadInFlight = true;
    renderHooksRuntimePanel();
    try {
        const runtimeView = await fetchHookRuntimeView();
        if (requestId !== activeHooksLoadRequestId) {
            return;
        }
        latestRuntimeView = runtimeView;
        latestErrorMessage = '';
    } catch (e) {
        if (requestId !== activeHooksLoadRequestId) {
            return;
        }
        latestRuntimeView = null;
        latestErrorMessage = e?.message || t('settings.hooks.load_failed');
        logError(
            'frontend.hooks_settings.load_failed',
            'Failed to load hooks runtime view',
            errorToPayload(e),
        );
    } finally {
        if (requestId !== activeHooksLoadRequestId) {
            return;
        }
        loadInFlight = false;
        renderHooksRuntimePanel();
    }
}

function renderHooksRuntimePanel() {
    const host = document.getElementById('hooks-runtime-status');
    if (!host) {
        return;
    }
    if (loadInFlight) {
        host.innerHTML = renderLoadingState();
        return;
    }
    if (latestErrorMessage) {
        host.innerHTML = renderEmptyState(
            t('settings.hooks.load_failed'),
            formatMessage('settings.hooks.load_failed_detail', { error: latestErrorMessage }),
        );
        return;
    }
    const loadedHooks = Array.isArray(latestRuntimeView?.loaded_hooks)
        ? latestRuntimeView.loaded_hooks
        : [];
    const sources = Array.isArray(latestRuntimeView?.sources)
        ? latestRuntimeView.sources
        : [];
    if (loadedHooks.length === 0) {
        host.innerHTML = renderEmptyState(
            t('settings.hooks.none'),
            t('settings.hooks.none_copy'),
        );
        return;
    }
    host.innerHTML = `
        <div class="mcp-status-shell">
            <div class="mcp-status-toolbar">
                <div class="mcp-status-toolbar-copy">${escapeHtml(formatMessage('settings.hooks.summary', { count: loadedHooks.length, source_count: sources.length }))}</div>
            </div>
            <div class="mcp-status-list hooks-runtime-list">
                ${loadedHooks.map((hook) => renderHookCard(hook)).join('')}
            </div>
        </div>
    `;
}

function renderHookCard(hook) {
    const trigger = typeof hook?.event_name === 'string' ? hook.event_name : t('settings.hooks.all');
    const handlerType = typeof hook?.handler_type === 'string' ? hook.handler_type : t('settings.hooks.all');
    const matcher = typeof hook?.matcher === 'string' && hook.matcher ? hook.matcher : '*';
    const source = hook?.source && typeof hook.source === 'object' ? hook.source : {};
    const sourceScope = formatSourceScope(source.scope);
    const scopeValue = sourceScope || t('settings.hooks.all');
    const detailRows = buildDetailRows([
        renderDetailItem(t('settings.hooks.trigger'), trigger),
        renderDetailItem(t('settings.hooks.matcher'), matcher),
        renderDetailItem(t('settings.hooks.type'), handlerType),
        renderDetailItem(t('settings.hooks.scope'), scopeValue),
        renderOptionalDetailItem(t('settings.hooks.if_condition'), hook?.if_condition),
        renderOptionalListDetailItem(t('settings.hooks.tool_names'), hook?.tool_names),
        renderOptionalListDetailItem(t('settings.hooks.role_ids'), hook?.role_ids),
        renderOptionalListDetailItem(t('settings.hooks.session_modes'), hook?.session_modes),
        renderOptionalListDetailItem(t('settings.hooks.run_kinds'), hook?.run_kinds),
    ]);
    return `
        <section class="mcp-status-card hooks-runtime-card">
            <div class="mcp-status-card-header">
                <div class="mcp-status-card-heading">
                    <div class="mcp-status-card-name">${escapeHtml(resolveHookName(hook))}</div>
                </div>
            </div>
            <div class="hooks-runtime-detail-list status-list">
                ${detailRows.join('')}
            </div>
        </section>
    `;
}

function buildDetailRows(items) {
    const normalizedItems = items.filter(Boolean);
    const rows = [];
    for (let index = 0; index < normalizedItems.length; index += 2) {
        rows.push(`
            <div class="hooks-runtime-detail-row status-list-row">
                ${normalizedItems[index]}
                ${normalizedItems[index + 1] || ''}
            </div>
        `);
    }
    return rows;
}

function renderDetailItem(label, value) {
    return `
        <div class="hooks-runtime-detail-item status-list-copy">
            <div class="hooks-runtime-detail-label status-list-name">${escapeHtml(label)}</div>
            <div class="hooks-runtime-detail-value status-list-description">${escapeHtml(value || t('settings.hooks.all'))}</div>
        </div>
    `;
}

function renderOptionalDetailItem(label, value) {
    if (typeof value !== 'string' || !value.trim()) {
        return '';
    }
    return renderDetailItem(label, value.trim());
}

function renderOptionalListDetailItem(label, values) {
    if (!Array.isArray(values)) {
        return '';
    }
    const normalizedValues = values
        .filter(value => typeof value === 'string')
        .map(value => value.trim())
        .filter(Boolean);
    if (normalizedValues.length === 0) {
        return '';
    }
    return renderDetailItem(label, normalizedValues.join(', '));
}

function renderLoadingState() {
    return `
        <div class="settings-empty-state settings-empty-state-compact">
            <h4>${escapeHtml(t('settings.hooks.loading'))}</h4>
        </div>
    `;
}

function renderEmptyState(title, copy) {
    return `
        <div class="settings-empty-state">
            <h4>${escapeHtml(title)}</h4>
            <p>${escapeHtml(copy)}</p>
        </div>
    `;
}

function resolveHookName(hook) {
    const name = typeof hook?.name === 'string' ? hook.name.trim() : '';
    return name || t('settings.hooks.unnamed');
}

function formatSourceScope(scope) {
    if (scope === 'project') {
        return t('settings.hooks.scope_project');
    }
    if (scope === 'project_local') {
        return t('settings.hooks.scope_project_local');
    }
    if (scope === 'user') {
        return t('settings.hooks.scope_user');
    }
    return t('settings.hooks.scope_unknown');
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
