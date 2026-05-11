/**
 * components/settings/pluginsSettings.js
 * Plugins settings panel.
 */
import {
    configurePlugin,
    deletePlugin,
    disablePlugin,
    enablePlugin,
    fetchPluginMarketplace,
    fetchPluginsRuntime,
    installPlugin,
    updatePlugin,
    validatePlugin,
} from '../../core/api.js';
import { showFormDialog, showTextInputDialog, showToast } from '../../utils/feedback.js';
import { formatMessage, t, translateDocument } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const CONFIGURED_SECRET_VALUE = '<configured>';
const CLAUDE_MARKETPLACE_NAME = 'claude-plugins-official';
const CLAUDE_MARKETPLACE_SOURCE = 'anthropics/claude-plugins-official';
const MUTABLE_SCOPES = new Set(['user', 'project', 'project_local']);
const COMPONENT_FIELDS = [
    ['skill_sources', 'Skills'],
    ['role_sources', 'Roles'],
    ['command_sources', 'Commands'],
    ['hook_sources', 'Hooks'],
    ['mcp_sources', 'MCP'],
    ['monitor_sources', 'Monitors'],
    ['settings_sources', 'Settings'],
];

let activeLoadRequestId = 0;
let pluginsRegistry = { plugins: [], diagnostics: [] };
let selectedPluginKey = '';
let pluginPanelMode = 'list';
let validationRegistry = null;
let handlersBound = false;
let installDraft = defaultInstallDraft();
let marketplaceBrowser = defaultMarketplaceBrowser();

export function bindPluginsSettingsHandlers() {
    const root = document.getElementById('plugins-settings-root');
    if (!root || handlersBound) {
        return;
    }
    handlersBound = true;

    bindActionButton('refresh-plugins-btn', () => {
        void loadPluginsSettingsPanel({ force: true });
    });
    bindActionButton('validate-plugin-btn', () => {
        pluginPanelMode = 'validate';
        validationRegistry = null;
        renderPluginsPanel();
        syncPluginsSettingsActions();
    });
    bindActionButton('install-plugin-btn', () => {
        pluginPanelMode = 'install';
        validationRegistry = null;
        installDraft = defaultInstallDraft();
        marketplaceBrowser = defaultMarketplaceBrowser();
        renderPluginsPanel();
        syncPluginsSettingsActions();
    });

    root.addEventListener('input', event => {
        const target = event.target;
        if (!(target instanceof HTMLInputElement)) {
            return;
        }
        if (target.form?.id === 'plugin-install-form') {
            updateInstallDraft(target);
        }
    });

    root.addEventListener('change', event => {
        const target = event.target;
        if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLSelectElement)) {
            return;
        }
        if (target.form?.id === 'plugin-install-form') {
            updateInstallDraft(target);
            if (target.name === 'marketplace_plugin') {
                installDraft.version = '';
                renderPluginsPanel();
            } else if (target.name === 'source_kind' || target.name === 'marketplace_provider') {
                renderPluginsPanel();
            }
        } else if (target.form?.id === 'plugin-config-form') {
            target.setAttribute('data-plugin-config-dirty', 'true');
        }
    });

    root.addEventListener('click', event => {
        const button = event.target?.closest?.('[data-plugin-action]');
        if (!(button instanceof HTMLButtonElement)) {
            return;
        }
        const action = String(button.getAttribute('data-plugin-action') || '').trim();
        const plugin = findPluginByKey(button.getAttribute('data-plugin-key'));
        void handlePluginAction(action, plugin, button);
    });

    root.addEventListener('submit', event => {
        event.preventDefault();
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) {
            return;
        }
        if (form.id === 'plugin-install-form') {
            void submitPluginInstall(form);
        } else if (form.id === 'plugin-validate-form') {
            void submitPluginValidate(form);
        } else if (form.id === 'plugin-config-form') {
            const plugin = findPluginByKey(form.getAttribute('data-plugin-key'));
            void submitPluginConfigure(form, plugin);
        }
    });
}

export async function loadPluginsSettingsPanel(options = {}) {
    const root = document.getElementById('plugins-settings-root');
    if (!root) {
        return;
    }
    bindPluginsSettingsHandlers();
    const requestId = ++activeLoadRequestId;
    if (!pluginsRegistry.plugins.length || options.force === true) {
        root.innerHTML = renderLoading();
    }
    try {
        const registry = normalizeRegistry(await fetchPluginsRuntime());
        if (requestId !== activeLoadRequestId) {
            return;
        }
        pluginsRegistry = registry;
        ensureSelectedPlugin();
        if (pluginPanelMode !== 'configure') {
            pluginPanelMode = 'list';
        }
        renderPluginsPanel();
    } catch (error) {
        logError('frontend.settings.plugins.load_failed', 'Failed to load plugins settings panel', errorToPayload(error));
        root.innerHTML = `
            <div class="plugins-empty-state">
                <h5>${escapeHtml(t('settings.plugins.load_failed'))}</h5>
                <p>${escapeHtml(String(error?.message || t('settings.plugins.load_failed_copy')))}</p>
            </div>
        `;
    } finally {
        syncPluginsSettingsActions();
    }
}

export function syncPluginsSettingsActions() {
    const refreshBtn = document.getElementById('refresh-plugins-btn');
    const validateBtn = document.getElementById('validate-plugin-btn');
    const installBtn = document.getElementById('install-plugin-btn');
    const active = document.getElementById('plugins-panel')?.classList.contains('active') === true;
    [refreshBtn, validateBtn, installBtn].forEach(button => {
        if (!button) return;
        button.style.display = active ? 'inline-flex' : 'none';
    });
    if (active && pluginPanelMode !== 'list') {
        if (validateBtn) validateBtn.style.display = 'none';
        if (installBtn) installBtn.style.display = 'none';
    }
    if (active && pluginPanelMode === 'list' && pluginsRegistry.plugins.length === 0) {
        if (refreshBtn) refreshBtn.style.display = 'none';
        if (validateBtn) validateBtn.style.display = 'none';
        if (installBtn) installBtn.style.display = 'inline-flex';
    }
}

function renderPluginsPanel() {
    const root = document.getElementById('plugins-settings-root');
    if (!root) {
        return;
    }
    ensureSelectedPlugin();
    root.innerHTML = `
        <div class="plugins-shell">
            ${renderToolbar()}
            ${pluginPanelMode === 'install' ? renderInstallForm() : ''}
            ${pluginPanelMode === 'validate' ? renderValidateForm() : ''}
            ${pluginPanelMode === 'configure' ? renderConfigureForm(findPluginByKey(selectedPluginKey)) : ''}
            ${renderLayout(pluginsRegistry.plugins)}
        </div>
    `;
    translateDocument(root);
}

function renderToolbar() {
    const plugins = pluginsRegistry.plugins;
    const enabledCount = plugins.filter(plugin => plugin.enabled === true).length;
    const diagnosticsCount = pluginsRegistry.diagnostics.length;
    return `
        <div class="plugins-toolbar">
            <div class="plugins-toolbar-stats">
                <span>${escapeHtml(formatMessage('settings.plugins.total_loaded', { count: plugins.length }))}</span>
                <span>${escapeHtml(formatMessage('settings.plugins.enabled_count', { count: enabledCount }))}</span>
                <span>${escapeHtml(formatMessage('settings.plugins.diagnostics_count', { count: diagnosticsCount }))}</span>
            </div>
        </div>
    `;
}

function renderLayout(plugins) {
    if (!pluginsRegistry.plugins.length) {
        return `
            <div class="plugins-empty-state">
                <h5>${escapeHtml(t('settings.plugins.empty'))}</h5>
                <p>${escapeHtml(t('settings.plugins.empty_copy'))}</p>
            </div>
        `;
    }
    const selected = findPluginByKey(selectedPluginKey) || plugins[0] || pluginsRegistry.plugins[0];
    return `
        <div class="plugins-layout">
            <div class="plugins-list" role="list">
                ${plugins.map(renderPluginRow).join('') || renderNoMatches()}
            </div>
            <div class="plugins-detail-panel">
                ${selected ? renderPluginDetail(selected) : renderSelectedEmpty()}
            </div>
        </div>
    `;
}

function renderPluginRow(plugin) {
    const key = pluginKey(plugin);
    const selectedClass = key === selectedPluginKey ? ' is-selected' : '';
    const diagnosticsCount = diagnosticsForPlugin(plugin).length;
    return `
        <button type="button" class="plugin-row${selectedClass}" data-plugin-action="select" data-plugin-key="${escapeHtml(key)}" role="listitem">
            <span class="plugin-row-main">
                <span class="plugin-row-title">${escapeHtml(plugin.name || t('settings.plugins.unknown'))}</span>
                <span class="plugin-row-meta">
                    ${escapeHtml(plugin.version || t('settings.plugins.no_version'))}
                    <span aria-hidden="true">/</span>
                    ${escapeHtml(scopeLabel(plugin.scope))}
                </span>
            </span>
            <span class="plugin-row-side">
                <span class="plugin-status-pill ${plugin.enabled ? 'is-enabled' : 'is-disabled'}">${escapeHtml(plugin.enabled ? t('settings.plugins.enabled') : t('settings.plugins.disabled'))}</span>
                ${diagnosticsCount ? `<span class="plugin-diagnostics-pill">${escapeHtml(String(diagnosticsCount))}</span>` : ''}
            </span>
            <span class="plugin-row-components">${escapeHtml(componentSummary(plugin))}</span>
        </button>
    `;
}

function renderPluginDetail(plugin) {
    const manifest = plugin.manifest || {};
    const diagnostics = diagnosticsForPlugin(plugin);
    const dependencies = Array.isArray(manifest.dependencies) ? manifest.dependencies : [];
    const mutable = MUTABLE_SCOPES.has(String(plugin.scope || ''));
    const userConfigFields = Object.entries(manifest.user_config || {});
    return `
        <div class="plugin-detail-head">
            <div class="plugin-detail-title">
                <h5>${escapeHtml(plugin.name || t('settings.plugins.unknown'))}</h5>
                <p>${escapeHtml(manifest.description || t('settings.plugins.no_description'))}</p>
            </div>
            <span class="plugin-status-pill ${plugin.enabled ? 'is-enabled' : 'is-disabled'}">${escapeHtml(plugin.enabled ? t('settings.plugins.enabled') : t('settings.plugins.disabled'))}</span>
        </div>
        <div class="plugin-detail-actions">
            ${userConfigFields.length ? `<button class="secondary-btn section-action-btn" type="button" data-plugin-action="configure" data-plugin-key="${escapeHtml(pluginKey(plugin))}">${escapeHtml(t('settings.plugins.configure'))}</button>` : ''}
            <button class="secondary-btn section-action-btn" type="button" data-plugin-action="toggle" data-plugin-key="${escapeHtml(pluginKey(plugin))}" ${mutable ? '' : 'disabled'}>${escapeHtml(plugin.enabled ? t('settings.plugins.disable') : t('settings.plugins.enable'))}</button>
            <button class="secondary-btn section-action-btn" type="button" data-plugin-action="update" data-plugin-key="${escapeHtml(pluginKey(plugin))}" ${mutable ? '' : 'disabled'}>${escapeHtml(t('settings.plugins.update'))}</button>
            <button class="secondary-btn section-action-btn" type="button" data-plugin-action="remove" data-plugin-key="${escapeHtml(pluginKey(plugin))}" ${mutable ? '' : 'disabled'}>${escapeHtml(t('settings.plugins.remove'))}</button>
        </div>
        <div class="plugin-detail-grid">
            ${renderDetailItem(t('settings.plugins.scope'), scopeLabel(plugin.scope))}
            ${renderDetailItem(t('settings.plugins.version'), plugin.version || t('settings.plugins.no_version'))}
            ${renderDetailItem(t('settings.plugins.root_dir'), plugin.root_dir || t('settings.plugins.no_path'))}
            ${renderDetailItem(t('settings.plugins.manifest'), plugin.manifest_path || t('settings.plugins.no_path'))}
        </div>
        ${renderComponents(plugin)}
        ${renderUserConfigSummary(plugin, userConfigFields)}
        ${renderDependencySummary(dependencies)}
        ${renderDiagnostics(diagnostics)}
    `;
}

function renderInstallForm() {
    const marketplaceMode = installDraft.source_kind === 'marketplace';
    const claudeMarketplaceMode = marketplaceMode && installDraft.marketplace_provider === 'claude';
    const gitMode = installDraft.source_kind === 'git';
    const marketplacePlugins = Array.isArray(marketplaceBrowser.plugins) ? marketplaceBrowser.plugins : [];
    const selectedMarketplacePlugin = selectedMarketplaceEntry();
    const versions = Array.isArray(selectedMarketplacePlugin?.versions) ? selectedMarketplacePlugin.versions : [];
    const selectedVersion = selectedMarketplaceVersion();
    const selectedUnsupported = Boolean(marketplaceMode && versionUnsupportedReason(selectedVersion));
    return `
        <form class="plugins-editor-panel" id="plugin-install-form">
            <div class="proxy-form-section-header"><h5>${escapeHtml(t('settings.plugins.install_title'))}</h5></div>
            <div class="plugins-form-grid">
                <label class="plugins-form-field plugins-form-field-wide">
                    <span>${escapeHtml(t('settings.plugins.source_path'))}</span>
                    <input type="text" name="source" value="${escapeHtml(installDraft.source)}" placeholder="${escapeHtml(t(marketplaceMode ? 'settings.plugins.marketplace_plugin_placeholder' : 'settings.plugins.source_placeholder'))}" required spellcheck="false" />
                </label>
                <label class="plugins-form-field">
                    <span>${escapeHtml(t('settings.plugins.source_type'))}</span>
                    <select name="source_kind">
                        <option value="local" ${installDraft.source_kind === 'local' ? 'selected' : ''}>${escapeHtml(t('settings.plugins.source_type_local'))}</option>
                        <option value="git" ${installDraft.source_kind === 'git' ? 'selected' : ''}>${escapeHtml(t('settings.plugins.source_type_git'))}</option>
                        <option value="marketplace" ${installDraft.source_kind === 'marketplace' ? 'selected' : ''}>${escapeHtml(t('settings.plugins.source_type_marketplace'))}</option>
                    </select>
                </label>
                <label class="plugins-form-field">
                    <span>${escapeHtml(t('settings.plugins.scope'))}</span>
                    <select name="scope">
                        <option value="user" ${installDraft.scope === 'user' ? 'selected' : ''}>${escapeHtml(scopeLabel('user'))}</option>
                        <option value="project" ${installDraft.scope === 'project' ? 'selected' : ''}>${escapeHtml(scopeLabel('project'))}</option>
                        <option value="project_local" ${installDraft.scope === 'project_local' ? 'selected' : ''}>${escapeHtml(scopeLabel('project_local'))}</option>
                    </select>
                </label>
                ${marketplaceMode ? `
                    <label class="plugins-form-field">
                        <span>${escapeHtml(t('settings.plugins.marketplace_provider'))}</span>
                        <select name="marketplace_provider">
                            <option value="relay" ${installDraft.marketplace_provider !== 'claude' ? 'selected' : ''}>${escapeHtml(t('settings.plugins.marketplace_provider_relay'))}</option>
                            <option value="claude" ${claudeMarketplaceMode ? 'selected' : ''}>${escapeHtml(t('settings.plugins.marketplace_provider_claude'))}</option>
                        </select>
                    </label>
                    <label class="plugins-form-field plugins-form-field-with-action">
                        <span>${escapeHtml(t(claudeMarketplaceMode ? 'settings.plugins.marketplace_name' : 'settings.plugins.marketplace_path'))}</span>
                        <span class="plugins-input-row">
                            <input type="text" name="marketplace" value="${escapeHtml(installDraft.marketplace)}" placeholder="${escapeHtml(t(claudeMarketplaceMode ? 'settings.plugins.claude_marketplace_placeholder' : 'settings.plugins.marketplace_placeholder'))}" spellcheck="false" />
                            <button class="secondary-btn section-action-btn" type="button" data-plugin-action="load-marketplace">${escapeHtml(t('settings.plugins.load_marketplace'))}</button>
                        </span>
                    </label>
                    ${claudeMarketplaceMode ? `
                        <label class="plugins-form-field plugins-form-field-wide">
                            <span>${escapeHtml(t('settings.plugins.marketplace_source'))}</span>
                            <input type="text" name="marketplace_source" value="${escapeHtml(installDraft.marketplace_source)}" placeholder="${escapeHtml(t('settings.plugins.claude_marketplace_source_placeholder'))}" spellcheck="false" />
                        </label>
                    ` : ''}
                    <label class="plugins-form-field">
                        <span>${escapeHtml(t('settings.plugins.version'))}</span>
                        ${versions.length
                            ? renderMarketplaceVersionSelect(versions)
                            : `<input type="text" name="version" value="${escapeHtml(installDraft.version)}" placeholder="${escapeHtml(t('settings.plugins.version_placeholder'))}" spellcheck="false" />`}
                    </label>
                    ${renderMarketplacePluginSelect(marketplacePlugins)}
                ` : ''}
                ${gitMode ? `
                    <label class="plugins-form-field">
                        <span>${escapeHtml(t('settings.plugins.git_ref'))}</span>
                        <input type="text" name="source_ref" value="${escapeHtml(installDraft.source_ref)}" placeholder="${escapeHtml(t('settings.plugins.git_ref_placeholder'))}" spellcheck="false" />
                        <small>${escapeHtml(t('settings.plugins.git_ref_help'))}</small>
                    </label>
                ` : ''}
            </div>
            ${marketplaceMode ? renderMarketplaceStatus() : ''}
            ${marketplaceMode ? renderSelectedMarketplacePluginDescription(selectedMarketplacePlugin) : ''}
            ${marketplaceMode ? renderMarketplaceVersionDetails(selectedVersion) : ''}
            <div class="plugins-editor-actions">
                <button class="secondary-btn section-action-btn" type="button" data-plugin-action="cancel-editor">${escapeHtml(t('settings.action.cancel'))}</button>
                ${marketplaceMode ? '' : `<button class="secondary-btn section-action-btn" type="button" data-plugin-action="validate-install-source">${escapeHtml(t('settings.plugins.validate_source'))}</button>`}
                <button class="primary-btn section-action-btn" type="submit" ${selectedUnsupported ? 'disabled' : ''}>${escapeHtml(t('settings.action.save'))}</button>
            </div>
            ${validationRegistry ? renderValidationResult(validationRegistry) : ''}
        </form>
    `;
}

function renderMarketplacePluginSelect(plugins) {
    if (!plugins.length) {
        return '';
    }
    return `
        <label class="plugins-form-field plugins-form-field-wide">
            <span>${escapeHtml(t('settings.plugins.marketplace_plugin'))}</span>
            <select name="marketplace_plugin">
                ${plugins.map(plugin => {
                    const name = String(plugin?.name || '').trim();
                    const selected = installDraft.marketplace_plugin === name ? 'selected' : '';
                    const latest = String(plugin?.latest || '').trim();
                    const unsupported = !installableMarketplaceVersions(plugin).length;
                    const label = latest ? `${name} @ ${latest}` : name;
                    return `<option value="${escapeHtml(name)}" ${selected}>${escapeHtml(unsupported ? `${label} - ${t('settings.plugins.unsupported')}` : label)}</option>`;
                }).join('')}
            </select>
        </label>
    `;
}

function renderSelectedMarketplacePluginDescription(plugin) {
    const description = String(plugin?.description || '').trim();
    if (!description) {
        return '';
    }
    return `
        <section class="plugin-detail-section">
            <h6>${escapeHtml(t('settings.plugins.plugin_description'))}</h6>
            <p class="plugins-muted">${escapeHtml(description)}</p>
        </section>
    `;
}

function renderMarketplaceVersionSelect(versions) {
    return `
        <select name="version">
            <option value="">${escapeHtml(t('settings.plugins.version_latest'))}</option>
            ${versions.map(version => {
                const value = String(version?.version || '').trim();
                const selected = installDraft.version === value ? 'selected' : '';
                const unsupported = Boolean(versionUnsupportedReason(version));
                return `<option value="${escapeHtml(value)}" ${selected}>${escapeHtml(unsupported ? `${value} - ${t('settings.plugins.unsupported')}` : value)}</option>`;
            }).join('')}
        </select>
    `;
}

function renderMarketplaceStatus() {
    if (marketplaceBrowser.error) {
        return `<div class="plugin-validation-result has-diagnostics"><strong>${escapeHtml(t('settings.plugins.marketplace_load_failed'))}</strong><span>${escapeHtml(marketplaceBrowser.error)}</span></div>`;
    }
    if (marketplaceBrowser.loading) {
        return `<div class="plugin-validation-result"><strong>${escapeHtml(t('settings.plugins.marketplace_loading'))}</strong><span>${escapeHtml(marketplaceBrowser.path)}</span></div>`;
    }
    if (!marketplaceBrowser.plugins.length) {
        return '';
    }
    const unsupportedCount = marketplaceBrowser.plugins.filter(plugin => !installableMarketplaceVersions(plugin).length).length;
    const detail = unsupportedCount
        ? formatMessage('settings.plugins.marketplace_loaded_with_unsupported', { count: unsupportedCount })
        : marketplaceBrowser.path;
    return `<div class="plugin-validation-result"><strong>${escapeHtml(formatMessage('settings.plugins.marketplace_loaded', { count: marketplaceBrowser.plugins.length }))}</strong><span>${escapeHtml(detail)}</span></div>`;
}

function renderMarketplaceVersionDetails(version) {
    if (!version) {
        return '';
    }
    const source = version.source || {};
    const dependencies = Array.isArray(version.dependencies) ? version.dependencies : [];
    const warnings = versionWarnings(version);
    const unsupportedReason = versionUnsupportedReason(version);
    return `
        <section class="plugin-detail-section">
            <h6>${escapeHtml(t('settings.plugins.marketplace_version_details'))}</h6>
            <div class="plugin-detail-grid">
                ${renderDetailItem(t('settings.plugins.source_type'), source.kind || t('settings.plugins.unknown'))}
                ${renderDetailItem(t('settings.plugins.source_path'), source.value || t('settings.plugins.no_path'))}
                ${source.ref ? renderDetailItem(t('settings.plugins.git_ref'), source.ref) : ''}
                ${version.sha256 ? renderDetailItem('SHA-256', version.sha256) : ''}
            </div>
            ${unsupportedReason ? `<div class="plugin-validation-result has-diagnostics"><strong>${escapeHtml(t('settings.plugins.unsupported'))}</strong><span>${escapeHtml(unsupportedReason)}</span></div>` : ''}
            ${warnings.length ? `<div class="plugin-validation-result has-diagnostics"><strong>${escapeHtml(t('settings.plugins.marketplace_warnings'))}</strong>${warnings.map(warning => `<span>${escapeHtml(warning)}</span>`).join('')}</div>` : ''}
            ${dependencies.length ? `
                <div class="plugin-chip-row">
                    ${dependencies.map(dependency => `<span class="plugin-chip">${escapeHtml(dependencyLabel(dependency))}</span>`).join('')}
                </div>
            ` : ''}
        </section>
    `;
}

function renderValidateForm() {
    return `
        <form class="plugins-editor-panel" id="plugin-validate-form">
            <div class="proxy-form-section-header"><h5>${escapeHtml(t('settings.plugins.validate_title'))}</h5></div>
            <label class="plugins-form-field">
                <span>${escapeHtml(t('settings.plugins.source_path'))}</span>
                <input type="text" name="path" placeholder="${escapeHtml(t('settings.plugins.source_placeholder'))}" required spellcheck="false" />
            </label>
            <div class="plugins-editor-actions">
                <button class="secondary-btn section-action-btn" type="button" data-plugin-action="cancel-editor">${escapeHtml(t('settings.action.cancel'))}</button>
                <button class="primary-btn section-action-btn" type="submit">${escapeHtml(t('settings.plugins.validate'))}</button>
            </div>
            ${validationRegistry ? renderValidationResult(validationRegistry) : ''}
        </form>
    `;
}

function renderConfigureForm(plugin) {
    if (!plugin) {
        return '';
    }
    const fields = Object.entries(plugin.manifest?.user_config || {});
    return `
        <form class="plugins-editor-panel" id="plugin-config-form" data-plugin-key="${escapeHtml(pluginKey(plugin))}">
            <div class="proxy-form-section-header"><h5>${escapeHtml(formatMessage('settings.plugins.configure_title', { name: plugin.name }))}</h5></div>
            <div class="plugins-form-grid">
                ${fields.map(([name, field]) => renderConfigField(plugin, name, field)).join('')}
            </div>
            <div class="plugins-editor-actions">
                <button class="secondary-btn section-action-btn" type="button" data-plugin-action="cancel-editor">${escapeHtml(t('settings.action.cancel'))}</button>
                <button class="primary-btn section-action-btn" type="submit">${escapeHtml(t('settings.action.save'))}</button>
            </div>
        </form>
    `;
}

function renderConfigField(plugin, name, field) {
    const value = plugin.user_config?.[name] ?? field?.default ?? '';
    const sensitive = field?.sensitive === true;
    const required = field?.required === true;
    const title = field?.title || name;
    const description = field?.description || '';
    const fieldType = String(field?.type || 'string').trim().toLowerCase();
    const booleanField = ['boolean', 'bool'].includes(fieldType);
    const jsonField = ['array', 'list', 'object', 'dict', 'json', 'any'].includes(fieldType);
    const inputType = sensitive ? 'password' : (['number', 'integer', 'int', 'float'].includes(fieldType) ? 'number' : 'text');
    const configured = sensitive && value === CONFIGURED_SECRET_VALUE;
    const placeholder = sensitive && value === CONFIGURED_SECRET_VALUE
        ? t('settings.plugins.configured')
        : '';
    if (booleanField) {
        return `
            <label class="plugins-checkbox-field">
                <input
                    type="checkbox"
                    name="${escapeHtml(name)}"
                    ${value === true || configured ? 'checked' : ''}
                    data-plugin-config-sensitive="${sensitive ? 'true' : 'false'}"
                    data-plugin-config-required="${required ? 'true' : 'false'}"
                    data-plugin-config-configured="${configured ? 'true' : 'false'}"
                    data-plugin-config-dirty="false"
                    data-plugin-config-type="${escapeHtml(fieldType)}"
                />
                <span>
                    ${escapeHtml(title)}
                    ${required ? `<em>${escapeHtml(t('settings.plugins.required'))}</em>` : ''}
                    ${configured ? `<em>${escapeHtml(t('settings.plugins.configured'))}</em>` : ''}
                </span>
                ${description ? `<small>${escapeHtml(description)}</small>` : ''}
            </label>
        `;
    }
    if (jsonField) {
        return `
            <label class="plugins-form-field plugins-form-field-wide">
                <span>
                    ${escapeHtml(title)}
                    ${required ? `<em>${escapeHtml(t('settings.plugins.required'))}</em>` : ''}
                    ${configured ? `<em>${escapeHtml(t('settings.plugins.configured'))}</em>` : ''}
                </span>
                <textarea
                    name="${escapeHtml(name)}"
                    rows="5"
                    placeholder="${escapeHtml(placeholder)}"
                    data-plugin-config-sensitive="${sensitive ? 'true' : 'false'}"
                    data-plugin-config-required="${required ? 'true' : 'false'}"
                    data-plugin-config-configured="${configured ? 'true' : 'false'}"
                    data-plugin-config-type="${escapeHtml(fieldType)}"
                    spellcheck="false"
                >${configured ? '' : escapeHtml(formatPluginConfigJsonValue(value))}</textarea>
                ${description ? `<small>${escapeHtml(description)}</small>` : ''}
            </label>
        `;
    }
    return `
        <label class="plugins-form-field">
            <span>
                ${escapeHtml(title)}
                ${required ? `<em>${escapeHtml(t('settings.plugins.required'))}</em>` : ''}
                ${sensitive ? `<em>${escapeHtml(t('settings.plugins.sensitive'))}</em>` : ''}
            </span>
            <input
                type="${escapeHtml(inputType)}"
                name="${escapeHtml(name)}"
                value="${sensitive ? '' : escapeHtml(value)}"
                placeholder="${escapeHtml(placeholder)}"
                data-plugin-config-sensitive="${sensitive ? 'true' : 'false'}"
                data-plugin-config-required="${required ? 'true' : 'false'}"
                data-plugin-config-configured="${sensitive && value === CONFIGURED_SECRET_VALUE ? 'true' : 'false'}"
                data-plugin-config-type="${escapeHtml(fieldType)}"
                autocomplete="${sensitive ? 'new-password' : 'off'}"
                spellcheck="false"
            />
            ${description ? `<small>${escapeHtml(description)}</small>` : ''}
        </label>
    `;
}

function renderComponents(plugin) {
    const rows = COMPONENT_FIELDS.map(([field, label]) => {
        const count = componentCount(plugin, field);
        return `<div class="plugin-component-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(count))}</strong></div>`;
    }).join('');
    return `
        <section class="plugin-detail-section">
            <h6>${escapeHtml(t('settings.plugins.components'))}</h6>
            <div class="plugin-component-grid">${rows}</div>
        </section>
    `;
}

function renderUserConfigSummary(plugin, fields) {
    if (!fields.length) {
        return `
            <section class="plugin-detail-section">
                <h6>${escapeHtml(t('settings.plugins.user_config'))}</h6>
                <p class="plugins-muted">${escapeHtml(t('settings.plugins.no_user_config'))}</p>
            </section>
        `;
    }
    return `
        <section class="plugin-detail-section">
            <h6>${escapeHtml(t('settings.plugins.user_config'))}</h6>
            <div class="plugin-config-summary">
                ${fields.map(([name, field]) => {
                    const value = plugin.user_config?.[name];
                    const configured = value === CONFIGURED_SECRET_VALUE || value !== undefined;
                    return `
                        <div class="plugin-config-row">
                            <span>${escapeHtml(field?.title || name)}</span>
                            <strong>${escapeHtml(configured ? t('settings.plugins.configured') : t('settings.plugins.not_configured'))}</strong>
                        </div>
                    `;
                }).join('')}
            </div>
        </section>
    `;
}

function renderDependencySummary(dependencies) {
    if (!dependencies.length) {
        return '';
    }
    return `
        <section class="plugin-detail-section">
            <h6>${escapeHtml(t('settings.plugins.dependencies'))}</h6>
            <div class="plugin-chip-row">
                ${dependencies.map(dependency => `<span class="plugin-chip">${escapeHtml(dependencyLabel(dependency))}</span>`).join('')}
            </div>
        </section>
    `;
}

function renderDiagnostics(diagnostics) {
    return `
        <section class="plugin-detail-section">
            <h6>${escapeHtml(t('settings.plugins.diagnostics'))}</h6>
            ${diagnostics.length
                ? `<div class="plugin-diagnostics-list">${diagnostics.map(renderDiagnostic).join('')}</div>`
                : `<p class="plugins-muted">${escapeHtml(t('settings.plugins.no_diagnostics'))}</p>`}
        </section>
    `;
}

function renderDiagnostic(diagnostic) {
    return `
        <div class="plugin-diagnostic-row">
            <strong>${escapeHtml(diagnostic.severity || diagnostic.level || t('settings.plugins.diagnostic'))}</strong>
            <span>${escapeHtml(diagnostic.message || diagnostic.detail || JSON.stringify(diagnostic))}</span>
        </div>
    `;
}

function renderValidationResult(registry) {
    const normalized = normalizeRegistry(registry);
    const pluginNames = normalized.plugins.map(plugin => plugin.name).filter(Boolean).join(', ');
    const hasErrors = normalized.diagnostics.length > 0;
    return `
        <div class="plugin-validation-result ${hasErrors ? 'has-diagnostics' : ''}">
            <strong>${escapeHtml(hasErrors ? t('settings.plugins.validation_with_diagnostics') : t('settings.plugins.validation_ok'))}</strong>
            <span>${escapeHtml(pluginNames || t('settings.plugins.validation_no_plugins'))}</span>
            ${normalized.diagnostics.length ? `<div class="plugin-diagnostics-list">${normalized.diagnostics.map(renderDiagnostic).join('')}</div>` : ''}
        </div>
    `;
}

async function handlePluginAction(action, plugin, button) {
    if (action === 'select') {
        selectedPluginKey = button.getAttribute('data-plugin-key') || '';
        pluginPanelMode = 'list';
        renderPluginsPanel();
        syncPluginsSettingsActions();
        return;
    }
    if (action === 'cancel-editor') {
        pluginPanelMode = 'list';
        validationRegistry = null;
        renderPluginsPanel();
        syncPluginsSettingsActions();
        return;
    }
    if (action === 'validate-install-source') {
        const form = button.closest('form');
        if (form instanceof HTMLFormElement) {
            await submitPluginValidatePath(form, 'source');
        }
        return;
    }
    if (action === 'load-marketplace') {
        const form = button.closest('form');
        if (form instanceof HTMLFormElement) {
            await loadMarketplaceForInstall(form);
        }
        return;
    }
    if (!plugin) {
        return;
    }
    if (action === 'configure') {
        selectedPluginKey = pluginKey(plugin);
        pluginPanelMode = 'configure';
        renderPluginsPanel();
        syncPluginsSettingsActions();
    } else if (action === 'toggle') {
        await togglePlugin(plugin);
    } else if (action === 'update') {
        await promptAndUpdatePlugin(plugin);
    } else if (action === 'remove') {
        await confirmAndDeletePlugin(plugin);
    }
}

async function submitPluginInstall(form) {
    syncInstallDraftFromForm(form);
    const payload = {
        source: installDraft.source,
        scope: installDraft.scope || 'user',
        enabled: true,
    };
    const sourceKind = installDraft.source_kind || 'local';
    const marketplaceMode = sourceKind === 'marketplace';
    const claudeMarketplaceMode = marketplaceMode && installDraft.marketplace_provider === 'claude';
    const marketplace = installDraft.marketplace;
    const version = installDraft.version;
    if (marketplaceMode) {
        payload.source = installDraft.marketplace_plugin || payload.source;
        payload.marketplace = marketplace;
        payload.version = version || null;
        if (claudeMarketplaceMode) {
            payload.marketplace_provider = 'claude';
            payload.marketplace_source = installDraft.marketplace_source;
            payload.marketplace_ref = installDraft.marketplace_ref;
        }
    } else {
        payload.source_kind = sourceKind;
        if (sourceKind === 'git' && installDraft.source_ref) {
            payload.source_ref = installDraft.source_ref;
        }
    }
    if (!payload.source) {
        showToast({ tone: 'warning', message: t('settings.plugins.source_required') });
        return;
    }
    if (marketplaceMode && !payload.marketplace) {
        showToast({ tone: 'warning', message: t('settings.plugins.marketplace_required') });
        return;
    }
    if (claudeMarketplaceMode && !installDraft.marketplace_source) {
        showToast({ tone: 'warning', message: t('settings.plugins.marketplace_source_required') });
        return;
    }
    const unsupportedReason = versionUnsupportedReason(selectedMarketplaceVersion());
    if (marketplaceMode && unsupportedReason) {
        showToast({ tone: 'warning', message: unsupportedReason });
        return;
    }
    try {
        await installPlugin(payload);
        showToast({ tone: 'success', message: t('settings.plugins.installed') });
        pluginPanelMode = 'list';
        await loadPluginsSettingsPanel({ force: true });
    } catch (error) {
        showToast({ tone: 'error', message: String(error?.message || t('settings.plugins.install_failed')) });
    }
}

async function loadMarketplaceForInstall(form) {
    syncInstallDraftFromForm(form);
    const claudeMarketplaceMode = installDraft.source_kind === 'marketplace' && installDraft.marketplace_provider === 'claude';
    if (!installDraft.marketplace) {
        showToast({ tone: 'warning', message: t('settings.plugins.marketplace_required') });
        return;
    }
    if (claudeMarketplaceMode && !installDraft.marketplace_source) {
        showToast({ tone: 'warning', message: t('settings.plugins.marketplace_source_required') });
        return;
    }
    marketplaceBrowser = {
        ...defaultMarketplaceBrowser(),
        path: installDraft.marketplace,
        loading: true,
    };
    renderPluginsPanel();
    try {
        const marketplace = await fetchPluginMarketplace(
            installDraft.marketplace,
            claudeMarketplaceMode
                ? {
                    marketplace_provider: 'claude',
                    marketplace_source: installDraft.marketplace_source,
                    marketplace_ref: installDraft.marketplace_ref,
                    refresh: true,
                }
                : {},
        );
        const plugins = Array.isArray(marketplace?.plugins) ? marketplace.plugins : [];
        marketplaceBrowser = {
            path: installDraft.marketplace,
            plugins,
            error: '',
            loading: false,
        };
        const currentSelection = plugins.find(plugin => plugin?.name === installDraft.marketplace_plugin);
        const selected = currentSelection || plugins[0] || null;
        installDraft.marketplace_plugin = String(selected?.name || '').trim();
        installDraft.source = installDraft.marketplace_plugin || installDraft.source;
        installDraft.version = '';
        renderPluginsPanel();
        showToast({ tone: 'success', message: formatMessage('settings.plugins.marketplace_loaded', { count: plugins.length }) });
    } catch (error) {
        marketplaceBrowser = {
            path: installDraft.marketplace,
            plugins: [],
            error: String(error?.message || t('settings.plugins.marketplace_load_failed')),
            loading: false,
        };
        renderPluginsPanel();
        showToast({ tone: 'error', message: marketplaceBrowser.error });
    }
}

async function submitPluginValidate(form) {
    await submitPluginValidatePath(form, 'path');
}

async function submitPluginValidatePath(form, fieldName) {
    const path = readFormValue(form, fieldName);
    if (!path) {
        showToast({ tone: 'warning', message: t('settings.plugins.source_required') });
        return;
    }
    try {
        validationRegistry = await validatePlugin(path);
        renderPluginsPanel();
        showToast({ tone: 'success', message: t('settings.plugins.validated') });
    } catch (error) {
        showToast({ tone: 'error', message: String(error?.message || t('settings.plugins.validate_failed')) });
    }
}

async function submitPluginConfigure(form, plugin) {
    if (!plugin) {
        return;
    }
    const userConfig = {};
    for (const input of Array.from(form.querySelectorAll('input[name], textarea[name]'))) {
        const key = String(input.getAttribute('name') || '').trim();
        const fieldType = String(input.getAttribute('data-plugin-config-type') || 'string').trim().toLowerCase();
        let value;
        try {
            value = readPluginConfigInputValue(input, fieldType);
        } catch (error) {
            showToast({ tone: 'warning', message: String(error?.message || t('settings.plugins.invalid_json_config')) });
            return;
        }
        const sensitive = input.getAttribute('data-plugin-config-sensitive') === 'true';
        const required = input.getAttribute('data-plugin-config-required') === 'true';
        const configured = input.getAttribute('data-plugin-config-configured') === 'true';
        if (isUnchangedConfiguredSensitiveCheckbox(input, sensitive, configured)) {
            continue;
        }
        if (isEmptyPluginConfigValue(value) && sensitive && configured) {
            continue;
        }
        if (isEmptyPluginConfigValue(value) && required) {
            showToast({ tone: 'warning', message: formatMessage('settings.plugins.field_required', { field: key }) });
            return;
        }
        if (isEmptyPluginConfigValue(value)) {
            if (hasPluginUserConfigValue(plugin, key)) {
                userConfig[key] = value;
            }
            continue;
        }
        userConfig[key] = value;
    }
    try {
        await configurePlugin(plugin.name, { scope: plugin.scope, user_config: userConfig });
        showToast({ tone: 'success', message: t('settings.plugins.saved') });
        pluginPanelMode = 'list';
        await loadPluginsSettingsPanel({ force: true });
    } catch (error) {
        showToast({ tone: 'error', message: String(error?.message || t('settings.plugins.save_failed')) });
    }
}

async function togglePlugin(plugin) {
    try {
        if (plugin.enabled) {
            await disablePlugin(plugin.name, plugin.scope);
            showToast({ tone: 'success', message: t('settings.plugins.disabled_title') });
        } else {
            await enablePlugin(plugin.name, plugin.scope);
            showToast({ tone: 'success', message: t('settings.plugins.enabled_title') });
        }
        await loadPluginsSettingsPanel({ force: true });
    } catch (error) {
        showToast({ tone: 'error', message: String(error?.message || t('settings.plugins.toggle_failed')) });
    }
}

async function promptAndUpdatePlugin(plugin) {
    if (plugin?.source?.kind === 'marketplace' && plugin?.source?.marketplace) {
        await promptAndUpdateMarketplacePlugin(plugin);
        return;
    }
    const version = await showTextInputDialog({
        title: t('settings.plugins.update_title'),
        message: t('settings.plugins.update_version_prompt'),
        placeholder: t('settings.plugins.version_placeholder'),
        confirmLabel: t('settings.plugins.update'),
    });
    if (version === null || version === false) {
        return;
    }
    try {
        await updatePlugin(plugin.name, { scope: plugin.scope, version: String(version || '').trim() || null });
        showToast({ tone: 'success', message: t('settings.plugins.updated') });
        await loadPluginsSettingsPanel({ force: true });
    } catch (error) {
        showToast({ tone: 'error', message: String(error?.message || t('settings.plugins.update_failed')) });
    }
}

async function promptAndUpdateMarketplacePlugin(plugin) {
    try {
        const marketplace = await fetchPluginMarketplace(
            plugin.source.marketplace,
            pluginMarketplaceRequestOptions(plugin),
        );
        const entry = (Array.isArray(marketplace?.plugins) ? marketplace.plugins : [])
            .find(item => item?.name === plugin.name);
        const allVersions = Array.isArray(entry?.versions) ? entry.versions : [];
        if (!allVersions.length) {
            await updatePlugin(plugin.name, { scope: plugin.scope, version: null });
            showToast({ tone: 'success', message: t('settings.plugins.updated') });
            await loadPluginsSettingsPanel({ force: true });
            return;
        }
        const versions = installableMarketplaceVersions(entry);
        if (!versions.length) {
            showToast({ tone: 'warning', message: t('settings.plugins.unsupported') });
            return;
        }
        const latestVersion = marketplacePreferredVersion(entry, allVersions);
        const latestInstallable = !latestVersion || !versionUnsupportedReason(latestVersion);
        const result = await showFormDialog({
            title: t('settings.plugins.update_title'),
            message: t('settings.plugins.update_version_prompt'),
            confirmLabel: t('settings.plugins.update'),
            fields: [
                {
                    id: 'version',
                    label: t('settings.plugins.version'),
                    type: 'select',
                    value: '',
                    options: [
                        ...(latestInstallable
                            ? [{ value: '', label: t('settings.plugins.version_latest') }]
                            : []),
                        ...versions.map(version => {
                            const value = String(version?.version || '').trim();
                            return { value, label: value };
                        }).filter(option => option.value),
                    ],
                },
            ],
        });
        if (!result) {
            return;
        }
        await updatePlugin(plugin.name, {
            scope: plugin.scope,
            version: String(result.version || '').trim() || null,
        });
        showToast({ tone: 'success', message: t('settings.plugins.updated') });
        await loadPluginsSettingsPanel({ force: true });
    } catch (error) {
        showToast({ tone: 'error', message: String(error?.message || t('settings.plugins.update_failed')) });
    }
}

function pluginMarketplaceRequestOptions(plugin) {
    const provider = plugin?.source?.marketplace_provider;
    if (provider !== 'claude') {
        return {};
    }
    return {
        marketplace_provider: provider,
        marketplace_source: plugin?.source?.marketplace_source || '',
        marketplace_ref: plugin?.source?.marketplace_ref || '',
        refresh: true,
    };
}

async function confirmAndDeletePlugin(plugin) {
    const result = await showFormDialog({
        title: t('settings.plugins.remove'),
        message: formatMessage('settings.plugins.delete_confirm', { name: plugin.name, scope: scopeLabel(plugin.scope) }),
        confirmLabel: t('settings.plugins.remove'),
        fields: [
            {
                id: 'prune',
                label: t('settings.plugins.prune_installed_copies'),
                type: 'checkbox',
                value: false,
                description: t('settings.plugins.prune_installed_copies_help'),
            },
        ],
    });
    if (!result) {
        return;
    }
    try {
        await deletePlugin(plugin.name, plugin.scope, result.prune === true);
        showToast({ tone: 'success', message: t('settings.plugins.deleted') });
        selectedPluginKey = '';
        await loadPluginsSettingsPanel({ force: true });
    } catch (error) {
        showToast({ tone: 'error', message: String(error?.message || t('settings.plugins.delete_failed')) });
    }
}

function normalizeRegistry(payload) {
    return {
        plugins: Array.isArray(payload?.plugins) ? payload.plugins : [],
        diagnostics: Array.isArray(payload?.diagnostics) ? payload.diagnostics : [],
    };
}

function defaultInstallDraft() {
    return {
        source: '',
        source_kind: 'local',
        source_ref: '',
        scope: 'user',
        marketplace: '',
        marketplace_provider: 'relay',
        marketplace_source: '',
        marketplace_ref: '',
        marketplace_plugin: '',
        version: '',
        enabled: true,
    };
}

function defaultMarketplaceBrowser() {
    return {
        path: '',
        plugins: [],
        error: '',
        loading: false,
    };
}

function syncInstallDraftFromForm(form) {
    for (const element of Array.from(form.elements)) {
        if (element instanceof HTMLInputElement || element instanceof HTMLSelectElement) {
            updateInstallDraft(element);
        }
    }
}

function updateInstallDraft(element) {
    const name = String(element.name || '').trim();
    if (!name || !(name in installDraft)) {
        return;
    }
    if (element instanceof HTMLInputElement && element.type === 'checkbox') {
        installDraft[name] = element.checked;
        return;
    }
    installDraft[name] = String(element.value || '').trim();
    if (name === 'source_kind' && installDraft.source_kind === 'marketplace') {
        installDraft.marketplace_provider = installDraft.marketplace_provider || 'relay';
    }
    if (name === 'marketplace_provider' && installDraft.marketplace_provider === 'relay') {
        if (installDraft.marketplace === CLAUDE_MARKETPLACE_NAME) {
            installDraft.marketplace = '';
        }
        if (installDraft.marketplace_source === CLAUDE_MARKETPLACE_SOURCE) {
            installDraft.marketplace_source = '';
        }
        installDraft.marketplace_ref = '';
    }
    if (name === 'marketplace_provider' && installDraft.marketplace_provider === 'claude') {
        installDraft.marketplace = installDraft.marketplace || CLAUDE_MARKETPLACE_NAME;
        installDraft.marketplace_source = installDraft.marketplace_source || CLAUDE_MARKETPLACE_SOURCE;
    }
    if (name === 'marketplace_plugin') {
        installDraft.source = installDraft.marketplace_plugin;
    }
}

function readPluginConfigInputValue(input, fieldType) {
    if (input instanceof HTMLInputElement && input.type === 'checkbox') {
        return input.checked;
    }
    const rawValue = String(input.value || '');
    const value = rawValue.trim();
    if (['number', 'float'].includes(fieldType)) {
        return value ? Number(value) : '';
    }
    if (['integer', 'int'].includes(fieldType)) {
        if (!value) {
            return '';
        }
        const parsed = Number(value);
        if (!Number.isInteger(parsed)) {
            throw new Error(t('settings.plugins.invalid_integer_config'));
        }
        return parsed;
    }
    if (['array', 'list', 'object', 'dict', 'json', 'any'].includes(fieldType)) {
        if (!value) {
            return '';
        }
        const parsed = JSON.parse(value);
        if (['array', 'list'].includes(fieldType) && !Array.isArray(parsed)) {
            throw new Error(t('settings.plugins.invalid_array_config'));
        }
        if (['object', 'dict'].includes(fieldType) && (Array.isArray(parsed) || parsed === null || typeof parsed !== 'object')) {
            throw new Error(t('settings.plugins.invalid_object_config'));
        }
        return parsed;
    }
    return rawValue;
}

function isEmptyPluginConfigValue(value) {
    return value === '';
}

function hasPluginUserConfigValue(plugin, key) {
    return Object.prototype.hasOwnProperty.call(plugin?.user_config || {}, key);
}

function isUnchangedConfiguredSensitiveCheckbox(input, sensitive, configured) {
    return input instanceof HTMLInputElement
        && input.type === 'checkbox'
        && sensitive
        && configured
        && input.getAttribute('data-plugin-config-dirty') !== 'true';
}

function selectedMarketplaceEntry() {
    const selectedName = String(installDraft.marketplace_plugin || '').trim();
    return marketplaceBrowser.plugins.find(plugin => plugin?.name === selectedName) || null;
}

function selectedMarketplaceVersion() {
    const entry = selectedMarketplaceEntry();
    const versions = Array.isArray(entry?.versions) ? entry.versions : [];
    if (!versions.length) {
        return null;
    }
    const selectedVersion = String(installDraft.version || '').trim();
    if (selectedVersion) {
        return versions.find(version => String(version?.version || '').trim() === selectedVersion) || null;
    }
    return marketplacePreferredVersion(entry, versions);
}

function installableMarketplaceVersions(plugin) {
    const versions = Array.isArray(plugin?.versions) ? plugin.versions : [];
    return versions.filter(version => !versionUnsupportedReason(version));
}

function marketplacePreferredVersion(entry, versions) {
    const latest = String(entry?.latest || '').trim();
    if (latest) {
        return versions.find(version => String(version?.version || '').trim() === latest) || semanticLatestMarketplaceVersion(versions);
    }
    return semanticLatestMarketplaceVersion(versions);
}

function versionUnsupportedReason(version) {
    if (!version) {
        return '';
    }
    const reason = String(version?.unsupported_reason || '').trim();
    if (reason) {
        return reason;
    }
    return version?.source?.kind === 'unsupported'
        ? t('settings.plugins.unsupported_source')
        : '';
}

function versionWarnings(version) {
    return Array.isArray(version?.warnings)
        ? version.warnings.map(warning => String(warning || '').trim()).filter(Boolean)
        : [];
}

function semanticLatestMarketplaceVersion(versions) {
    return versions.reduce((selected, candidate) => {
        if (!selected) {
            return candidate;
        }
        return compareMarketplaceVersions(candidate?.version, selected?.version) > 0 ? candidate : selected;
    }, null);
}

function compareMarketplaceVersions(left, right) {
    const leftKey = marketplaceVersionSortKey(left);
    const rightKey = marketplaceVersionSortKey(right);
    const baseComparison = compareMarketplaceVersionParts(leftKey.base, rightKey.base);
    if (baseComparison !== 0) {
        return baseComparison;
    }
    if (leftKey.stable !== rightKey.stable) {
        return leftKey.stable - rightKey.stable;
    }
    return compareMarketplaceVersionParts(leftKey.prerelease, rightKey.prerelease);
}

function marketplaceVersionSortKey(version) {
    const [base, prerelease = ''] = String(version || '').trim().toLowerCase().split('-', 2);
    return {
        base: marketplaceVersionParts(base),
        stable: prerelease ? 0 : 1,
        prerelease: marketplaceVersionParts(prerelease),
    };
}

function marketplaceVersionParts(version) {
    return Array.from(String(version || '').matchAll(/\d+|[a-z]+/g)).map(match => {
        const token = match[0];
        return /^\d+$/.test(token)
            ? { kind: 0, value: Number(token) }
            : { kind: 1, value: token };
    });
}

function compareMarketplaceVersionParts(left, right) {
    const length = Math.max(left.length, right.length);
    for (let index = 0; index < length; index += 1) {
        const leftPart = left[index] || { kind: 0, value: '' };
        const rightPart = right[index] || { kind: 0, value: '' };
        if (leftPart.kind !== rightPart.kind) {
            return leftPart.kind - rightPart.kind;
        }
        if (leftPart.value > rightPart.value) {
            return 1;
        }
        if (leftPart.value < rightPart.value) {
            return -1;
        }
    }
    return 0;
}

function ensureSelectedPlugin() {
    if (selectedPluginKey && findPluginByKey(selectedPluginKey)) {
        return;
    }
    selectedPluginKey = pluginsRegistry.plugins.length ? pluginKey(pluginsRegistry.plugins[0]) : '';
}

function findPluginByKey(key) {
    const normalizedKey = String(key || '').trim();
    return pluginsRegistry.plugins.find(plugin => pluginKey(plugin) === normalizedKey) || null;
}

function pluginKey(plugin) {
    return `${String(plugin?.scope || '').trim()}:${String(plugin?.name || '').trim()}`;
}

function diagnosticsForPlugin(plugin) {
    return pluginsRegistry.diagnostics.filter(diagnostic => {
        const pluginName = String(diagnostic.plugin_name || diagnostic.plugin || diagnostic.name || '').trim();
        const scope = String(diagnostic.scope || '').trim();
        return !pluginName || pluginName === plugin.name
            ? !scope || scope === plugin.scope
            : false;
    });
}

function componentSummary(plugin) {
    const counts = COMPONENT_FIELDS
        .map(([field, label]) => [label, componentCount(plugin, field)])
        .filter(([, count]) => count > 0)
        .map(([label, count]) => `${label} ${count}`);
    return counts.length ? counts.join(' / ') : t('settings.plugins.no_components');
}

function componentCount(plugin, field) {
    const countField = {
        skill_sources: 'skills',
        role_sources: 'roles',
        command_sources: 'commands',
        hook_sources: 'hooks',
        mcp_sources: 'mcp_servers',
        monitor_sources: 'monitors',
        settings_sources: 'settings',
    }[field];
    const count = Number(plugin.component_counts?.[countField]);
    if (Number.isFinite(count)) {
        return count;
    }
    return Array.isArray(plugin[field]) ? plugin[field].length : 0;
}

function dependencyLabel(dependency) {
    if (typeof dependency === 'string') {
        return dependency;
    }
    const name = String(dependency?.name || '').trim();
    const version = String(dependency?.version || '').trim();
    return version ? `${name}@${version}` : name;
}

function formatPluginConfigJsonValue(value) {
    if (value === undefined || value === null || value === '') {
        return '';
    }
    if (typeof value === 'string') {
        return JSON.stringify(value);
    }
    return JSON.stringify(value, null, 2);
}

function scopeLabel(scope) {
    const key = `settings.plugins.scope_${String(scope || 'unknown').replaceAll('-', '_')}`;
    return t(key) === key ? String(scope || t('settings.plugins.unknown')) : t(key);
}

function renderDetailItem(label, value) {
    return `
        <div class="plugin-detail-item">
            <span>${escapeHtml(label)}</span>
            <strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong>
        </div>
    `;
}

function renderNoMatches() {
    return `<div class="plugins-list-empty">${escapeHtml(t('settings.plugins.no_matches'))}</div>`;
}

function renderSelectedEmpty() {
    return `<div class="plugins-empty-state"><h5>${escapeHtml(t('settings.plugins.selected_empty'))}</h5></div>`;
}

function renderLoading() {
    return `<div class="plugins-empty-state"><h5>${escapeHtml(t('settings.plugins.loading'))}</h5></div>`;
}

function readFormValue(form, fieldName) {
    const element = form.elements[fieldName];
    if (element instanceof HTMLInputElement || element instanceof HTMLSelectElement) {
        return String(element.value || '').trim();
    }
    return '';
}

function bindActionButton(id, handler) {
    const button = document.getElementById(id);
    if (button) {
        button.onclick = handler;
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}
