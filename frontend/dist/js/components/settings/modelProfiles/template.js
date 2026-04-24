/**
 * components/settings/modelProfiles/template.js
 * Markup for the model profile settings panel.
 */

export function renderModelProfilesPanelMarkup() {
    return `
        <div class="settings-panel" id="model-panel" style="display:none;">
            <div class="settings-section settings-section-model">
                <div class="settings-content-stack settings-model-stack">
                    <div class="profiles-list" id="profiles-list"></div>
                    <div class="profile-editor model-profile-page" id="profile-editor" style="display:none;">
                        <form class="profile-editor-form model-profile-wizard" id="profile-editor-form" autocomplete="off">
                            <div class="model-profile-top">
                                <div class="form-group model-profile-name-field">
                                    <label for="profile-name" data-i18n="settings.model.profile_name">Profile Name</label>
                                    <input type="text" id="profile-name" placeholder="e.g., default, kimi" data-i18n-placeholder="settings.model.profile_name_placeholder" autocomplete="off">
                                </div>
                                <label class="profile-default-row profile-default-row-prominent" for="profile-is-default">
                                    <input type="checkbox" id="profile-is-default">
                                    <span data-i18n="settings.model.default_model_action">Set as default model</span>
                                </label>
                            </div>
                            <h4 class="model-profile-editor-title" id="profile-editor-title"></h4>

                            <section class="model-profile-step is-open" data-profile-step="model">
                                <button class="model-profile-step-header" type="button" data-profile-step-toggle="model">
                                    <span class="model-profile-step-index">1</span>
                                    <span class="model-profile-step-title" data-i18n="settings.model.step_provider_model">Model Provider and Model</span>
                                    <span class="model-profile-step-summary" id="profile-model-summary"></span>
                                </button>
                                <div class="model-profile-step-body">
                                    <div class="model-provider-choice-grid model-provider-choice-grid-four" id="profile-provider-options">
                                        <button class="model-provider-choice is-active" id="profile-provider-external-btn" type="button" data-provider-mode="external" data-provider-value="openai_compatible">
                                            <span class="model-provider-choice-icon" aria-hidden="true">
                                                <svg viewBox="0 0 24 24" fill="none" class="icon-sm">
                                                    <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.7"></circle>
                                                    <path d="M3 12h18M12 3c2 2.4 3 5.4 3 9s-1 6.6-3 9M12 3c-2 2.4-3 5.4-3 9s1 6.6 3 9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"></path>
                                                </svg>
                                            </span>
                                            <span>
                                                <strong data-i18n="settings.model.provider_external">Model Marketplace</strong>
                                                <span data-i18n="settings.model.provider_external_copy">Choose provider and model from the marketplace</span>
                                            </span>
                                        </button>
                                        <button class="model-provider-choice" id="profile-provider-maas-btn" type="button" data-provider-mode="maas" data-provider-value="maas">
                                            <span class="model-provider-choice-icon" aria-hidden="true">
                                                <svg viewBox="0 0 24 24" fill="none" class="icon-sm">
                                                    <path d="M7 18h10a4 4 0 0 0 .4-7.98A5.5 5.5 0 0 0 6.83 8.2 4.8 4.8 0 0 0 7 18Z" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"></path>
                                                </svg>
                                            </span>
                                            <span>
                                                <strong data-i18n="settings.model.provider_maas">MaaS Model</strong>
                                                <span data-i18n="settings.model.provider_maas_copy">Hosted model service platform</span>
                                            </span>
                                        </button>
                                        <button class="model-provider-choice" id="profile-provider-codeagent-btn" type="button" data-provider-mode="codeagent" data-provider-value="codeagent">
                                            <span class="model-provider-choice-icon" aria-hidden="true">
                                                <svg viewBox="0 0 24 24" fill="none" class="icon-sm">
                                                    <path d="M7 6.5 12 4l5 2.5v5.75c0 3.03-1.93 5.73-5 7.75-3.07-2.02-5-4.72-5-7.75V6.5Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"></path>
                                                    <path d="M9.5 11.5 11 13l3.5-3.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"></path>
                                                </svg>
                                            </span>
                                            <span>
                                                <strong data-i18n="settings.model.provider_codeagent">CodeAgent Model</strong>
                                                <span data-i18n="settings.model.provider_codeagent_copy">Use CodeAgent models with SSO sign-in</span>
                                            </span>
                                        </button>
                                        <button class="model-provider-choice" id="profile-provider-custom-btn" type="button" data-provider-mode="custom" data-provider-value="openai_compatible">
                                            <span class="model-provider-choice-icon" aria-hidden="true">
                                                <svg viewBox="0 0 24 24" fill="none" class="icon-sm">
                                                    <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v11a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5v-11Z" stroke="currentColor" stroke-width="1.7"></path>
                                                    <path d="M8 9h8M8 13h5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"></path>
                                                </svg>
                                            </span>
                                            <span>
                                                <strong data-i18n="settings.model.provider_custom">Custom Model</strong>
                                                <span data-i18n="settings.model.provider_custom_copy">Enter endpoint and model id manually</span>
                                            </span>
                                        </button>
                                    </div>
                                    <div class="form-group model-hidden-field">
                                        <label for="profile-provider" data-i18n="settings.model.provider">Provider</label>
                                        <select id="profile-provider" aria-hidden="true" tabindex="-1">
                                            <option value="openai_compatible">openai_compatible</option>
                                            <option value="maas">maas</option>
                                            <option value="codeagent">codeagent</option>
                                        </select>
                                    </div>
                                    <div class="form-group model-profile-collapsible-field" id="profile-base-url-fields" style="display:none;">
                                        <label for="profile-base-url" data-i18n="settings.model.base_url">Base URL</label>
                                        <input type="text" id="profile-base-url" placeholder="Only fill this when your provider uses a custom endpoint, e.g. https://api.openai.com/v1" data-i18n-placeholder="settings.model.custom_base_url_placeholder" autocomplete="url">
                                    </div>
                                    <div class="model-catalog-panel" id="model-catalog-panel" style="display:none;">
                                        <div class="model-catalog-header">
                                            <div>
                                                <h4 data-i18n="settings.model.catalog_title">Model Catalog</h4>
                                                <p id="model-catalog-status" class="model-catalog-status probe-status probe-status-probing" data-i18n="settings.model.catalog_loading">Loading model catalog...</p>
                                            </div>
                                            <button class="settings-inline-action settings-list-action" id="refresh-model-catalog-btn" type="button" data-i18n="settings.model.catalog_refresh">Refresh</button>
                                        </div>
                                        <div class="model-catalog-search-row">
                                            <div class="model-catalog-picker-field">
                                                <label for="model-catalog-provider-search" data-i18n="settings.model.provider">Provider</label>
                                                <div class="model-catalog-search-field">
                                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                        <circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"></circle>
                                                        <path d="m16.5 16.5 3.5 3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"></path>
                                                    </svg>
                                                    <input type="search" id="model-catalog-provider-search" placeholder="Search providers" data-i18n-placeholder="settings.model.catalog_provider_search" autocomplete="off">
                                                </div>
                                                <div class="model-catalog-provider-list" id="model-catalog-provider-list"></div>
                                            </div>
                                            <div class="model-catalog-picker-field">
                                                <label for="model-catalog-model-search" data-i18n="settings.model.model">Model</label>
                                                <div class="model-catalog-search-field">
                                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                        <circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"></circle>
                                                        <path d="m16.5 16.5 3.5 3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"></path>
                                                    </svg>
                                                    <input type="search" id="model-catalog-model-search" placeholder="Search models" data-i18n-placeholder="settings.model.catalog_model_search" autocomplete="off">
                                                </div>
                                                <div class="model-catalog-model-list" id="model-catalog-model-list"></div>
                                            </div>
                                        </div>
                                    </div>
                                    <div id="profile-model-field-home">
                                        <div class="form-group form-group-inline-action model-profile-collapsible-field" id="profile-model-group" style="display:none;">
                                            <label for="profile-model" data-i18n="settings.model.model">Model</label>
                                            <div class="secure-input-row profile-model-input-row">
                                                <input type="text" id="profile-model" placeholder="Enter a model id manually, e.g. gpt-4o-mini" data-i18n-placeholder="settings.model.custom_model_placeholder" autocomplete="off" spellcheck="false">
                                                <button class="secure-input-btn profile-model-menu-btn" id="open-profile-model-menu-btn" type="button" title="Show Models" aria-label="Show Models">
                                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                        <path d="m7 10 5 5 5-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                                    </svg>
                                                </button>
                                                <button class="secure-input-btn profile-discovery-btn" id="fetch-profile-models-btn" type="button" title="Fetch Models" aria-label="Fetch Models">
                                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                        <path d="M20 12a8 8 0 1 1-2.34-5.66" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                                        <path d="M20 4v6h-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                                    </svg>
                                                </button>
                                                <div class="profile-model-menu" id="profile-model-menu" style="display:none;"></div>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="profile-model-discovery-status" id="profile-model-discovery-status" style="display:none;"></div>
                                    <div class="profile-credentials-row model-api-key-row" id="profile-primary-credentials-row">
                                        <div class="form-group model-api-key-group" id="profile-api-key-group">
                                            <label for="profile-api-key" data-i18n="settings.model.api_key">API Key</label>
                                            <div class="secure-input-row">
                                                <input type="password" id="profile-api-key" placeholder="sk-..." autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false">
                                                <button class="secure-input-btn" id="toggle-profile-api-key-btn" type="button" title="Show API key" aria-label="Show API key" style="display:none;">
                                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                        <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
                                                        <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"></circle>
                                                    </svg>
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="profile-credentials-row" id="profile-maas-auth-fields" style="display:none;">
                                        <div class="form-group">
                                            <label for="profile-maas-username">MAAS Username</label>
                                            <input type="text" id="profile-maas-username" placeholder="username" autocomplete="username">
                                        </div>
                                        <div class="form-group">
                                            <label for="profile-maas-password">MAAS Password</label>
                                            <div class="secure-input-row">
                                                <input type="password" id="profile-maas-password" placeholder="password" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false">
                                                <button class="secure-input-btn" id="toggle-profile-maas-password-btn" type="button" title="Show password" aria-label="Show password" style="display:none;">
                                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                        <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
                                                        <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"></circle>
                                                    </svg>
                                                </button>
                                            </div>
                                        </div>
                                        <div class="form-group-span-2" id="profile-maas-model-slot"></div>
                                    </div>
                                    <div class="profile-credentials-row profile-codeagent-credentials-row" id="profile-codeagent-auth-fields" style="display:none;">
                                        <div class="form-group form-group-inline-action profile-codeagent-sso-group">
                                            <label for="profile-codeagent-login-status">CodeAgent SSO</label>
                                            <div class="codeagent-sso-control">
                                                <button class="settings-inline-action settings-list-action codeagent-sso-login-btn" type="button" id="profile-codeagent-login-status" data-i18n="settings.model.codeagent_sign_in_sso" data-i18n-title="settings.model.codeagent_sign_in_sso" data-i18n-aria-label="settings.model.codeagent_sign_in_sso" aria-controls="profile-codeagent-login-status-message">Sign in with SSO</button>
                                            </div>
                                            <div class="codeagent-sso-status-message" id="profile-codeagent-login-status-message" role="status" aria-live="polite" style="display:none;"></div>
                                        </div>
                                        <div class="form-group-span-2" id="profile-codeagent-model-slot"></div>
                                    </div>
                                </div>
                            </section>

                            <section class="model-profile-step" data-profile-step="advanced">
                                <button class="model-profile-step-header" type="button" data-profile-step-toggle="advanced">
                                    <span class="model-profile-step-index">2</span>
                                    <span class="model-profile-step-title" data-i18n="settings.model.step_advanced">Advanced Options</span>
                                    <span class="model-profile-step-summary" id="profile-advanced-summary"></span>
                                </button>
                                <div class="model-profile-step-body">
                                    <div class="form-row">
                                        <div class="form-group">
                                            <label for="profile-image-capability" data-i18n="settings.model.image_capability">Image Input</label>
                                            <select id="profile-image-capability">
                                                <option value="follow_detection" data-i18n="settings.model.image_capability_follow">Follow detection</option>
                                                <option value="supported" data-i18n="settings.model.image_capability_supported">Supports image input</option>
                                                <option value="unsupported" data-i18n="settings.model.image_capability_unsupported">Text only</option>
                                            </select>
                                        </div>
                                        <div class="form-group">
                                            <label for="profile-temperature" data-i18n="settings.model.temperature">Temperature</label>
                                            <input type="number" id="profile-temperature" value="0.7" step="0.1" min="0" max="2" autocomplete="off">
                                        </div>
                                        <div class="form-group">
                                            <label for="profile-top-p" data-i18n="settings.model.top_p">Top P</label>
                                            <input type="number" id="profile-top-p" value="1.0" step="0.1" min="0" max="1" autocomplete="off">
                                        </div>
                                        <div class="form-group">
                                            <label for="profile-max-tokens" data-i18n="settings.model.max_output_tokens">Max Output Tokens</label>
                                            <input type="number" id="profile-max-tokens" value="" min="1" autocomplete="off" placeholder="Optional" data-i18n-placeholder="settings.model.optional">
                                        </div>
                                        <div class="form-group">
                                            <label for="profile-context-window" data-i18n="settings.model.context_window">Context Window</label>
                                            <input type="number" id="profile-context-window" value="" min="1" autocomplete="off" placeholder="Optional" data-i18n-placeholder="settings.model.optional">
                                        </div>
                                        <div class="form-group">
                                            <label for="profile-connect-timeout" data-i18n="settings.model.connect_timeout">Connect Timeout (s)</label>
                                            <input type="number" id="profile-connect-timeout" value="15" step="1" min="1" max="300" autocomplete="off">
                                        </div>
                                        <div class="form-group">
                                            <label for="profile-ssl-verify" data-i18n="settings.proxy.default_ssl">SSL Verification</label>
                                            <select id="profile-ssl-verify">
                                                <option value="" data-i18n="settings.proxy.inherit_default">Inherit Default</option>
                                                <option value="true" data-i18n="settings.proxy.verify">Verify</option>
                                                <option value="false" data-i18n="settings.proxy.skip_verify">Skip Verify</option>
                                            </select>
                                        </div>
                                    </div>
                                </div>
                            </section>

                            <section class="model-profile-step" data-profile-step="fallback">
                                <button class="model-profile-step-header" type="button" data-profile-step-toggle="fallback">
                                    <span class="model-profile-step-index">3</span>
                                    <span class="model-profile-step-title" data-i18n="settings.model.step_fallback">Fallback Strategy</span>
                                    <span class="model-profile-step-summary" id="profile-fallback-summary"></span>
                                </button>
                                <div class="model-profile-step-body">
                                    <div class="form-row model-profile-fallback-row">
                                        <div class="form-group">
                                            <label for="profile-fallback-policy" data-i18n="settings.model.fallback_strategy">Fallback Strategy</label>
                                            <select id="profile-fallback-policy"></select>
                                        </div>
                                        <div class="form-group">
                                            <label for="profile-fallback-priority" data-i18n="settings.model.fallback_priority">Fallback Priority</label>
                                            <input type="number" id="profile-fallback-priority" value="0" min="0" max="1000000" autocomplete="off">
                                        </div>
                                    </div>
                                </div>
                            </section>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    `;
}
