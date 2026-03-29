/**
 * components/settings/appearanceSettings.js
 * Appearance settings with real-time CSS variable updates.
 * Uses event delegation on the panel root for robustness.
 */

const STORAGE_KEY = 'agent_teams_appearance';

const DEFAULTS = {
    accent: '',
    background: '',
    foreground: '',
    uiFont: '',
    codeFont: '',
    uiFontSize: 0,
    codeFontSize: 0,
    lineHeight: 0,
    messageDensity: 0,
};

function load() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) return Object.assign({}, DEFAULTS, JSON.parse(raw));
    } catch (e) { /* ignore */ }
    return Object.assign({}, DEFAULTS);
}

function save(config) {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
    } catch (e) { /* ignore */ }
}

/**
 * Set a CSS custom property on html, body, AND as a direct style
 * override so it wins regardless of theme class specificity.
 */
function setVar(name, value) {
    const root = document.documentElement;
    const body = document.body;
    if (root) root.style.setProperty(name, value);
    if (body) body.style.setProperty(name, value);
}

function removeVar(name) {
    const root = document.documentElement;
    const body = document.body;
    if (root) root.style.removeProperty(name);
    if (body) body.style.removeProperty(name);
}

function applyToCSS(config) {
    // Accent color
    if (config.accent) {
        setVar('--primary', config.accent);
        setVar('--primary-hover', lighten(config.accent, 0.15));
    } else {
        removeVar('--primary');
        removeVar('--primary-hover');
    }

    // Background
    if (config.background) {
        setVar('--bg-base', config.background);
        setVar('--bg-surface', config.background);
    } else {
        removeVar('--bg-base');
        removeVar('--bg-surface');
    }

    // Foreground
    if (config.foreground) {
        setVar('--text-primary', config.foreground);
        setVar('--text-msg-content', config.foreground);
    } else {
        removeVar('--text-primary');
        removeVar('--text-msg-content');
    }

    // UI font
    if (config.uiFont) {
        setVar('--font-ui', config.uiFont);
    } else {
        removeVar('--font-ui');
    }

    // Code font
    if (config.codeFont) {
        setVar('--font-mono', config.codeFont);
    } else {
        removeVar('--font-mono');
    }

    // UI font size
    if (config.uiFontSize > 0) {
        setVar('--ui-font-size', config.uiFontSize + 'px');
    } else {
        removeVar('--ui-font-size');
    }

    // Code font size
    if (config.codeFontSize > 0) {
        setVar('--code-font-size', config.codeFontSize + 'px');
    } else {
        removeVar('--code-font-size');
    }

    // Line height
    if (config.lineHeight > 0) {
        setVar('--msg-line-height', (config.lineHeight / 100).toFixed(2));
    } else {
        removeVar('--msg-line-height');
    }

    // Message density / gap
    if (config.messageDensity > 0) {
        setVar('--msg-gap', (config.messageDensity / 100).toFixed(2) + 'rem');
    } else {
        removeVar('--msg-gap');
    }
}

function lighten(hex, amount) {
    var c = String(hex || '').replace('#', '');
    if (c.length !== 6) return hex;
    var num = parseInt(c, 16);
    var r = Math.min(255, ((num >> 16) & 0xff) + Math.round(255 * amount));
    var g = Math.min(255, ((num >> 8) & 0xff) + Math.round(255 * amount));
    var b = Math.min(255, (num & 0xff) + Math.round(255 * amount));
    return '#' + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
}

/** Read all control values from the panel DOM. */
function collectFromPanel() {
    var config = Object.assign({}, DEFAULTS);
    var panel = document.getElementById('appearance-panel');
    if (!panel) return config;

    // Color fields: wrapper div contains color + text inputs
    var colorFields = [
        { id: 'appearance-accent', key: 'accent' },
        { id: 'appearance-background', key: 'background' },
        { id: 'appearance-foreground', key: 'foreground' },
    ];
    for (var i = 0; i < colorFields.length; i++) {
        var f = colorFields[i];
        var wrapper = panel.querySelector('#' + f.id);
        if (!wrapper) continue;
        var txt = wrapper.querySelector('input[type="text"]');
        config[f.key] = txt ? txt.value.trim() : '';
    }

    // Text fields
    var fontEl = panel.querySelector('#appearance-ui-font');
    if (fontEl) config.uiFont = fontEl.value.trim();
    var codeFontEl = panel.querySelector('#appearance-code-font');
    if (codeFontEl) config.codeFont = codeFontEl.value.trim();

    // Range fields
    var rangeFields = [
        { id: 'appearance-ui-font-size', key: 'uiFontSize' },
        { id: 'appearance-code-font-size', key: 'codeFontSize' },
        { id: 'appearance-line-height', key: 'lineHeight' },
        { id: 'appearance-msg-density', key: 'messageDensity' },
    ];
    for (var j = 0; j < rangeFields.length; j++) {
        var rf = rangeFields[j];
        var rangeEl = panel.querySelector('#' + rf.id);
        if (!rangeEl) continue;
        var v = parseInt(rangeEl.value, 10);
        config[rf.key] = isNaN(v) ? 0 : v;
    }

    return config;
}

function applyToPanel(config) {
    var panel = document.getElementById('appearance-panel');
    if (!panel) return;

    // Color fields
    var colorFields = ['accent', 'background', 'foreground'];
    var colorIds = ['appearance-accent', 'appearance-background', 'appearance-foreground'];
    for (var i = 0; i < colorFields.length; i++) {
        var wrapper = panel.querySelector('#' + colorIds[i]);
        if (!wrapper) continue;
        var txt = wrapper.querySelector('input[type="text"]');
        var clr = wrapper.querySelector('input[type="color"]');
        var val = config[colorFields[i]] || '';
        if (txt) txt.value = val;
        if (clr) clr.value = val || '#888888';
    }

    // Text fields
    var fontEl = panel.querySelector('#appearance-ui-font');
    if (fontEl) fontEl.value = config.uiFont || '';
    var codeFontEl = panel.querySelector('#appearance-code-font');
    if (codeFontEl) codeFontEl.value = config.codeFont || '';

    // Range fields
    var rangePairs = [
        ['appearance-ui-font-size', 'uiFontSize', 15],
        ['appearance-code-font-size', 'codeFontSize', 13],
        ['appearance-line-height', 'lineHeight', 148],
        ['appearance-msg-density', 'messageDensity', 85],
    ];
    for (var j = 0; j < rangePairs.length; j++) {
        var id = rangePairs[j][0], key = rangePairs[j][1], def = rangePairs[j][2];
        var el = panel.querySelector('#' + id);
        if (!el) continue;
        el.value = config[key] || def;
        syncRangeDisplay(el, key);
    }
}

function syncRangeDisplay(rangeEl, key) {
    var display = rangeEl.parentElement
        ? rangeEl.parentElement.querySelector('.appearance-range-value')
        : null;
    if (!display) return;
    var v = parseInt(rangeEl.value, 10);
    if (key === 'lineHeight' || key === 'messageDensity') {
        display.textContent = (v / 100).toFixed(2);
    } else {
        display.textContent = v + 'px';
    }
}

function handlePanelEvent(e) {
    var target = e.target;
    if (!target || !target.closest) return;
    var panel = target.closest('#appearance-panel');
    if (!panel) return;

    // Range slider changed
    if (target.type === 'range') {
        var keyMap = {
            'appearance-ui-font-size': 'uiFontSize',
            'appearance-code-font-size': 'codeFontSize',
            'appearance-line-height': 'lineHeight',
            'appearance-msg-density': 'messageDensity',
        };
        var rangeKey = keyMap[target.id];
        if (rangeKey) syncRangeDisplay(target, rangeKey);
        flushToCSS();
        return;
    }

    // Color picker changed - sync to text input
    if (target.type === 'color') {
        var wrapper = target.closest('.appearance-color-field');
        var txt = wrapper ? wrapper.querySelector('input[type="text"]') : null;
        if (txt) txt.value = target.value;
        flushToCSS();
        return;
    }

    // Text input changed (hex color or font name)
    if (target.type === 'text') {
        var colorWrapper = target.closest('.appearance-color-field');
        if (colorWrapper) {
            var clr = colorWrapper.querySelector('input[type="color"]');
            if (clr && /^#[0-9a-fA-F]{6}$/.test(target.value.trim())) {
                clr.value = target.value.trim();
            }
        }
        flushToCSS();
        return;
    }
}

function flushToCSS() {
    var config = collectFromPanel();
    save(config);
    applyToCSS(config);
}

/** Use event delegation -- one listener on document catches everything. */
export function bindAppearanceHandlers() {
    document.addEventListener('input', handlePanelEvent, false);
    document.addEventListener('change', handlePanelEvent, false);

    // Reset button
    var resetBtn = document.getElementById('reset-appearance-btn');
    if (resetBtn) {
        resetBtn.onclick = function () {
            localStorage.removeItem(STORAGE_KEY);
            applyToCSS(DEFAULTS);
            applyToPanel(DEFAULTS);
        };
    }
}

export function loadAppearancePanel() {
    var config = load();
    applyToPanel(config);
}

export function initAppearanceOnStartup() {
    var config = load();
    var doApply = function () { applyToCSS(config); };
    if (document.body) {
        doApply();
    } else {
        document.addEventListener('DOMContentLoaded', doApply);
    }
}
