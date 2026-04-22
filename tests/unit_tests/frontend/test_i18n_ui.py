# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_i18n_module_switches_between_english_and_chinese(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "utils" / "i18n.js"
    mock_api_path = tmp_path / "mockApi.mjs"
    module_under_test_path = tmp_path / "i18n.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
export async function fetchUiLanguageSettings() {
    return { language: "zh-CN" };
}

export async function saveUiLanguageSettings(payload) {
    globalThis.__savedPayloads.push(payload);
    return { status: "ok" };
}
""".strip(),
        encoding="utf-8",
    )

    module_under_test_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            "../core/api.js", "./mockApi.mjs"
        ),
        encoding="utf-8",
    )

    runner_path.write_text(
        """
function createElement(dataset = {}) {
    return {
        dataset,
        textContent: '',
        attributes: {},
        setAttribute(name, value) {
            this.attributes[name] = String(value);
            this[name] = String(value);
        },
    };
}

const languageButton = createElement();
const backendLabel = createElement({ i18n: 'sidebar.backend_checking' });
const promptInput = createElement({ i18nPlaceholder: 'composer.placeholder' });
const settingsButton = createElement({ i18nTitle: 'topbar.settings_title', i18nAriaLabel: 'topbar.settings_title' });

globalThis.__savedPayloads = [];
globalThis.localStorage = {
    _values: new Map(),
    getItem(key) {
        return this._values.has(key) ? this._values.get(key) : null;
    },
    setItem(key, value) {
        this._values.set(key, String(value));
    },
};
    Object.defineProperty(globalThis, 'navigator', {
        value: { language: 'en-US' },
        configurable: true,
    });
globalThis.CustomEvent = class CustomEvent {
    constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail || null;
    }
};
globalThis.document = {
    documentElement: { lang: 'en-US' },
    getElementById(id) {
        if (id === 'language-toggle-btn') {
            return languageButton;
        }
        return null;
    },
    querySelectorAll(selector) {
        if (selector === '[data-i18n]') {
            return [backendLabel];
        }
        if (selector === '[data-i18n-title]') {
            return [settingsButton];
        }
        if (selector === '[data-i18n-placeholder]') {
            return [promptInput];
        }
        if (selector === '[data-i18n-aria-label]') {
            return [settingsButton];
        }
        return [];
    },
    dispatchEvent() {
        return true;
    },
};

const { getCurrentLanguage, initializeLanguage, toggleLanguage, t } = await import('./i18n.mjs');

await initializeLanguage();
const afterInit = {
    language: getCurrentLanguage(),
    htmlLang: document.documentElement.lang,
    buttonText: languageButton.textContent,
    buttonTitle: languageButton.title,
    backendLabel: backendLabel.textContent,
    placeholder: promptInput.placeholder,
    settingsTitle: settingsButton.title,
    computerToolGroup: t('settings.roles.tool_group.computer.name'),
    webToolGroup: t('settings.roles.tool_group.web.name'),
    fallbackSection: t('settings.model.fallback_section'),
    fallbackStrategy: t('settings.model.fallback_strategy'),
    fallbackDisabled: t('settings.model.fallback_disabled'),
    fallbackSameProviderPolicy: t('settings.model.fallback_policy.same_provider_then_other_provider'),
};

await toggleLanguage();
const afterToggle = {
    language: getCurrentLanguage(),
    htmlLang: document.documentElement.lang,
    buttonText: languageButton.textContent,
    backendLabel: backendLabel.textContent,
    placeholder: promptInput.placeholder,
    computerToolGroup: t('settings.roles.tool_group.computer.name'),
    webToolGroup: t('settings.roles.tool_group.web.name'),
    fallbackSection: t('settings.model.fallback_section'),
    fallbackStrategy: t('settings.model.fallback_strategy'),
    fallbackDisabled: t('settings.model.fallback_disabled'),
    fallbackSameProviderPolicy: t('settings.model.fallback_policy.same_provider_then_other_provider'),
    savedPayloads: globalThis.__savedPayloads,
};

console.log(JSON.stringify({ afterInit, afterToggle }));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert payload["afterInit"] == {
        "language": "zh-CN",
        "htmlLang": "zh-CN",
        "buttonText": "中文",
        "buttonTitle": "切换语言",
        "backendLabel": "正在检查后端...",
        "placeholder": "你希望这些代理帮你做什么？",
        "settingsTitle": "设置",
        "computerToolGroup": "Computer Use",
        "webToolGroup": "Web",
        "fallbackSection": "回退",
        "fallbackStrategy": "回退策略",
        "fallbackDisabled": "未启用回退",
        "fallbackSameProviderPolicy": "先同提供商，再其他提供商",
    }
    assert payload["afterToggle"] == {
        "language": "en-US",
        "htmlLang": "en-US",
        "buttonText": "EN",
        "backendLabel": "Checking backend...",
        "placeholder": "What would you like the agents to do?",
        "computerToolGroup": "Computer Use",
        "webToolGroup": "Web",
        "fallbackSection": "Fallback",
        "fallbackStrategy": "Fallback Strategy",
        "fallbackDisabled": "Fallback disabled",
        "fallbackSameProviderPolicy": "Same Provider Then Other Provider",
        "savedPayloads": [{"language": "en-US"}],
    }
