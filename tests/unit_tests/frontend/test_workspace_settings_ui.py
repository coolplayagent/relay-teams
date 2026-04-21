# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_workspace_settings_panel_loads_and_saves_ssh_profile(
    tmp_path: Path,
) -> None:
    payload = _run_workspace_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    bindWorkspaceSettingsHandlers,
    loadWorkspaceSettingsPanel,
} from "./workspaceSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
globalThis.__mockProfiles = [];

bindWorkspaceSettingsHandlers();
await loadWorkspaceSettingsPanel();

document.getElementById("add-ssh-profile-btn").onclick();
document.getElementById("workspace-ssh-profile-id").value = "prod";
document.getElementById("workspace-ssh-profile-host").value = "prod-alias";
document.getElementById("workspace-ssh-profile-username").value = "deploy";
document.getElementById("workspace-ssh-profile-port").value = "22";
document.getElementById("workspace-ssh-profile-shell").value = "/bin/bash";
document.getElementById("workspace-ssh-profile-timeout").value = "15";
document.getElementById("workspace-ssh-profile-password").value = "secret";
document.getElementById("workspace-ssh-profile-private-key-name").value = "id_ed25519";
document.getElementById("workspace-ssh-profile-private-key").value = "-----BEGIN KEY-----\\ncontent\\n-----END KEY-----";

await document.getElementById("save-ssh-profile-btn").onclick();

console.log(JSON.stringify({
    notifications,
    savePayload: globalThis.__saveSshProfilePayload,
    listHtml: document.getElementById("workspace-ssh-profile-list").innerHTML,
    addDisplay: document.getElementById("add-ssh-profile-btn").style.display,
    editorDisplay: document.getElementById("workspace-ssh-profile-editor").style.display,
}));
""".strip(),
    )

    assert payload["savePayload"] == {
        "sshProfileId": "prod",
        "config": {
            "host": "prod-alias",
            "username": "deploy",
            "password": "secret",
            "port": 22,
            "remote_shell": "/bin/bash",
            "connect_timeout_seconds": 15,
            "private_key": "-----BEGIN KEY-----\ncontent\n-----END KEY-----",
            "private_key_name": "id_ed25519",
        },
    }
    assert "prod" in str(payload["listHtml"])
    assert "prod-alias" in str(payload["listHtml"])
    assert payload["addDisplay"] == "inline-flex"
    assert payload["editorDisplay"] == "none"
    assert payload["notifications"] == [
        {
            "title": "SSH Profile Saved",
            "message": "Saved profile prod.",
            "tone": "success",
        }
    ]


def test_workspace_settings_panel_prefills_and_deletes_profile(
    tmp_path: Path,
) -> None:
    payload = _run_workspace_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    bindWorkspaceSettingsHandlers,
    loadWorkspaceSettingsPanel,
} from "./workspaceSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
globalThis.__mockProfiles = [
    {
        ssh_profile_id: "prod",
        host: "prod-alias",
        username: "deploy",
        port: 22,
        remote_shell: "/bin/bash",
        connect_timeout_seconds: 15,
        has_password: true,
        has_private_key: true,
        private_key_name: "id_ed25519",
    },
];

bindWorkspaceSettingsHandlers();
await loadWorkspaceSettingsPanel();

const editButton = document.getElementById("workspace-ssh-profile-list")
    .querySelectorAll("[data-workspace-ssh-profile-edit]")[0];
editButton?.onclick?.();

const prefilled = {
    sshProfileId: document.getElementById("workspace-ssh-profile-id").value,
    host: document.getElementById("workspace-ssh-profile-host").value,
    username: document.getElementById("workspace-ssh-profile-username").value,
    password: document.getElementById("workspace-ssh-profile-password").value,
    port: document.getElementById("workspace-ssh-profile-port").value,
    remoteShell: document.getElementById("workspace-ssh-profile-shell").value,
    timeout: document.getElementById("workspace-ssh-profile-timeout").value,
    privateKeyName: document.getElementById("workspace-ssh-profile-private-key-name").value,
    privateKey: document.getElementById("workspace-ssh-profile-private-key").value,
    authState: document.getElementById("workspace-ssh-profile-auth-state").textContent,
};

await document.getElementById("delete-ssh-profile-btn").onclick();

console.log(JSON.stringify({
    notifications,
    deleteProfileId: globalThis.__deleteSshProfileId,
    prefilled,
    listHtml: document.getElementById("workspace-ssh-profile-list").innerHTML,
}));
""".strip(),
    )

    assert payload["prefilled"] == {
        "sshProfileId": "prod",
        "host": "prod-alias",
        "username": "deploy",
        "password": "",
        "port": "22",
        "remoteShell": "/bin/bash",
        "timeout": "15",
        "privateKeyName": "id_ed25519",
        "privateKey": "",
        "authState": 'Stored password will be kept unless you enter a new one. Stored private key "id_ed25519" will be kept unless you paste or import a new one.',
    }
    assert payload["deleteProfileId"] == "prod"
    assert "No SSH profiles configured" in str(payload["listHtml"])
    assert payload["notifications"] == [
        {
            "title": "SSH Profile Deleted",
            "message": "Deleted profile prod.",
            "tone": "success",
        }
    ]


def test_workspace_settings_panel_tests_saved_and_draft_profiles(
    tmp_path: Path,
) -> None:
    payload = _run_workspace_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    bindWorkspaceSettingsHandlers,
    loadWorkspaceSettingsPanel,
} from "./workspaceSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
globalThis.__mockProfiles = [
    {
        ssh_profile_id: "prod",
        host: "prod-alias",
        username: "deploy",
        port: 22,
        remote_shell: "/bin/bash",
        connect_timeout_seconds: 12,
        has_password: false,
        has_private_key: false,
        private_key_name: null,
    },
];
globalThis.__mockProbeResponse = {
    ok: true,
    latency_ms: 64,
    diagnostics: {
        binary_available: true,
        host_reachable: true,
        used_password: false,
        used_private_key: false,
        used_system_config: true,
        exit_code: 0,
    },
};

bindWorkspaceSettingsHandlers();
await loadWorkspaceSettingsPanel();

const testButton = document.getElementById("workspace-ssh-profile-list")
    .querySelectorAll("[data-workspace-ssh-profile-test]")[0];
await testButton?.onclick?.();
const savedProbePayload = globalThis.__probeSshProfilePayload;
const savedListHtml = document.getElementById("workspace-ssh-profile-list").innerHTML;

document.getElementById("add-ssh-profile-btn").onclick();
document.getElementById("workspace-ssh-profile-id").value = "staging";
document.getElementById("workspace-ssh-profile-host").value = "staging-alias";
document.getElementById("workspace-ssh-profile-username").value = "ops";
document.getElementById("workspace-ssh-profile-port").value = "2222";
document.getElementById("workspace-ssh-profile-shell").value = "/bin/bash";
document.getElementById("workspace-ssh-profile-timeout").value = "9";
document.getElementById("workspace-ssh-profile-password").value = "secret";
document.getElementById("workspace-ssh-profile-private-key-name").value = "id_ed25519";
document.getElementById("workspace-ssh-profile-private-key").value = "-----BEGIN KEY-----\\ncontent\\n-----END KEY-----";

await document.getElementById("test-ssh-profile-btn").onclick();

console.log(JSON.stringify({
    savedProbePayload,
    savedListHtml,
    draftProbePayload: globalThis.__probeSshProfilePayload,
    draftStatusText: document.getElementById("workspace-ssh-profile-probe-status").textContent,
    draftStatusDisplay: document.getElementById("workspace-ssh-profile-probe-status").style.display,
    draftStatusClass: document.getElementById("workspace-ssh-profile-probe-status").className,
    draftButtonText: document.getElementById("test-ssh-profile-btn").textContent,
}));
""".strip(),
    )

    assert payload["savedProbePayload"] == {
        "ssh_profile_id": "prod",
        "timeout_ms": 12000,
    }
    assert "Connected in 64ms" in str(payload["savedListHtml"])
    assert payload["draftProbePayload"] == {
        "ssh_profile_id": "staging",
        "override": {
            "host": "staging-alias",
            "username": "ops",
            "port": 2222,
            "remote_shell": "/bin/bash",
            "connect_timeout_seconds": 9,
            "password": "secret",
            "private_key": "-----BEGIN KEY-----\ncontent\n-----END KEY-----",
            "private_key_name": "id_ed25519",
        },
        "timeout_ms": 9000,
    }
    assert payload["draftStatusText"] == "Connected in 64ms"
    assert payload["draftStatusDisplay"] == "block"
    assert payload["draftStatusClass"] == (
        "profile-probe-status probe-status probe-status-success"
    )
    assert payload["draftButtonText"] == "Test"


def test_workspace_password_toggle_reveals_saved_password_and_preserves_on_save(
    tmp_path: Path,
) -> None:
    payload = _run_workspace_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    bindWorkspaceSettingsHandlers,
    loadWorkspaceSettingsPanel,
} from "./workspaceSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
globalThis.__mockProfiles = [
    {
        ssh_profile_id: "prod",
        host: "prod-alias",
        username: "deploy",
        has_password: true,
        has_private_key: false,
        private_key_name: null,
    },
];
globalThis.__mockProfilePasswords = { prod: "secret" };

bindWorkspaceSettingsHandlers();
await loadWorkspaceSettingsPanel();

const editButton = document.getElementById("workspace-ssh-profile-list")
    .querySelectorAll("[data-workspace-ssh-profile-edit]")[0];
editButton?.onclick?.();

const initial = {
    passwordValue: document.getElementById("workspace-ssh-profile-password").value,
    passwordType: document.getElementById("workspace-ssh-profile-password").type,
    passwordPlaceholder: document.getElementById("workspace-ssh-profile-password").placeholder,
    toggleDisplay: document.getElementById("toggle-workspace-ssh-profile-password-btn").style.display,
    toggleTitle: document.getElementById("toggle-workspace-ssh-profile-password-btn").title,
};

await document.getElementById("toggle-workspace-ssh-profile-password-btn").onclick();
const revealed = {
    passwordValue: document.getElementById("workspace-ssh-profile-password").value,
    passwordType: document.getElementById("workspace-ssh-profile-password").type,
    passwordPlaceholder: document.getElementById("workspace-ssh-profile-password").placeholder,
    toggleDisplay: document.getElementById("toggle-workspace-ssh-profile-password-btn").style.display,
    toggleTitle: document.getElementById("toggle-workspace-ssh-profile-password-btn").title,
    authState: document.getElementById("workspace-ssh-profile-auth-state").textContent,
};

await document.getElementById("toggle-workspace-ssh-profile-password-btn").onclick();
await document.getElementById("save-ssh-profile-btn").onclick();

console.log(JSON.stringify({
    initial,
    revealed,
    revealCalls: globalThis.__revealSshProfilePasswordCalls,
    savePayload: globalThis.__saveSshProfilePayload,
    notifications,
}));
""".strip(),
    )

    assert payload["initial"] == {
        "passwordValue": "",
        "passwordType": "password",
        "passwordPlaceholder": "************",
        "toggleDisplay": "inline-flex",
        "toggleTitle": "Show password",
    }
    assert payload["revealed"] == {
        "passwordValue": "secret",
        "passwordType": "text",
        "passwordPlaceholder": "",
        "toggleDisplay": "inline-flex",
        "toggleTitle": "Hide password",
        "authState": "Stored password will be kept unless you enter a new one.",
    }
    assert payload["revealCalls"] == 1
    assert payload["savePayload"] == {
        "sshProfileId": "prod",
        "config": {
            "host": "prod-alias",
            "username": "deploy",
            "port": None,
            "remote_shell": None,
            "connect_timeout_seconds": None,
        },
    }
    assert payload["notifications"] == [
        {
            "title": "SSH Profile Saved",
            "message": "Saved profile prod.",
            "tone": "success",
        }
    ]


def _run_workspace_settings_script(
    tmp_path: Path, runner_source: str
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "workspaceSettings.js"
    )
    module_under_test_path = tmp_path / "workspaceSettings.mjs"
    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
export async function fetchSshProfiles() {
    return globalThis.__mockProfiles || [];
}

export async function saveSshProfile(sshProfileId, config) {
    globalThis.__saveSshProfilePayload = { sshProfileId, config };
    const profiles = Array.isArray(globalThis.__mockProfiles) ? [...globalThis.__mockProfiles] : [];
    const existing = profiles.find(profile => profile?.ssh_profile_id === sshProfileId) || null;
    const hasPassword = typeof config?.password === "string" ? config.password.trim().length > 0 : Boolean(existing?.has_password);
    const hasPrivateKey = typeof config?.private_key === "string" ? config.private_key.trim().length > 0 : Boolean(existing?.has_private_key);
    const nextRecord = {
        ssh_profile_id: sshProfileId,
        host: config?.host || "",
        username: config?.username || null,
        port: config?.port || null,
        remote_shell: config?.remote_shell || null,
        connect_timeout_seconds: config?.connect_timeout_seconds || null,
        has_password: hasPassword,
        has_private_key: hasPrivateKey,
        private_key_name: hasPrivateKey ? (config?.private_key_name || existing?.private_key_name || null) : null,
    };
    const nextProfiles = profiles.filter(profile => profile?.ssh_profile_id !== sshProfileId);
    nextProfiles.push(nextRecord);
    globalThis.__mockProfiles = nextProfiles;
    return nextRecord;
}

export async function revealSshProfilePassword(sshProfileId) {
    globalThis.__revealSshProfilePasswordCalls += 1;
    const passwords = globalThis.__mockProfilePasswords || {};
    return {
        password: passwords[sshProfileId] || null,
    };
}

export async function probeSshProfileConnection(payload) {
    globalThis.__probeSshProfilePayload = payload;
    return globalThis.__mockProbeResponse || {
        ok: true,
        latency_ms: 42,
        diagnostics: {
            binary_available: true,
            host_reachable: true,
            used_password: false,
            used_private_key: false,
            used_system_config: true,
            exit_code: 0,
        },
    };
}

export async function deleteSshProfile(sshProfileId) {
    globalThis.__deleteSshProfileId = sshProfileId;
    globalThis.__mockProfiles = (globalThis.__mockProfiles || []).filter(
        profile => profile?.ssh_profile_id !== sshProfileId,
    );
    return { status: "ok" };
}
""".strip(),
        encoding="utf-8",
    )
    mock_feedback_path.write_text(
        """
export async function showConfirmDialog() {
    return true;
}

export function showToast(payload = {}) {
    globalThis.__notifications.push(payload);
}
""".strip(),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
const translations = {
    "settings.action.edit": "Edit",
    "settings.action.test": "Test",
    "settings.action.delete": "Delete",
    "settings.action.cancel": "Cancel",
    "settings.workspace.empty_title": "No SSH profiles configured",
    "settings.workspace.empty_copy": "Create a reusable SSH profile, then reference it from one or more workspace mounts.",
    "settings.workspace.profile_chip": "SSH",
    "settings.workspace.no_host": "No host",
    "settings.workspace.shell_default": "System shell",
    "settings.workspace.timeout_default": "Default timeout",
    "settings.workspace.timeout_value": "{value}s timeout",
    "settings.workspace.add_profile": "Add SSH Profile",
    "settings.workspace.edit_profile": "Edit SSH Profile",
    "settings.workspace.password": "Password",
    "settings.workspace.password_placeholder": "Optional password",
    "settings.workspace.private_key": "Private Key",
    "settings.workspace.private_key_placeholder": "Paste a private key or import one from a file",
    "settings.workspace.private_key_name": "Imported Key File",
    "settings.workspace.private_key_name_placeholder": "Optional key filename",
    "settings.workspace.private_key_import": "Import Private Key",
    "settings.workspace.private_key_inline": "pasted key",
    "settings.workspace.private_key_import_failed_title": "Private Key Import Failed",
    "settings.workspace.private_key_import_failed_detail": "Failed to import private key: {error}",
    "settings.workspace.password_reveal_failed_title": "Password Reveal Failed",
    "settings.workspace.password_reveal_failed_detail": "Failed to reveal SSH profile password: {error}",
    "settings.workspace.auth_method_password": "Password",
    "settings.workspace.auth_method_private_key": "Private key",
    "settings.workspace.auth_method_private_key_named": "Private key: {name}",
    "settings.workspace.auth_method_system": "System auth",
    "settings.workspace.auth_state_password": "Stored password will be kept unless you enter a new one.",
    "settings.workspace.auth_state_private_key": "Stored private key will be kept unless you paste or import a new one.",
    "settings.workspace.auth_state_private_key_named": "Stored private key \\"{name}\\" will be kept unless you paste or import a new one.",
    "settings.workspace.auth_state_new_password": "A new password will be saved when you click Save.",
    "settings.workspace.auth_state_new_private_key": "Private key \\"{name}\\" will replace the stored key when you click Save.",
    "settings.workspace.auth_state_system": "If password and private key are empty, Agent Teams falls back to your system SSH configuration.",
    "settings.workspace.saved_title": "SSH Profile Saved",
    "settings.workspace.saved_detail": "Saved profile {ssh_profile_id}.",
    "settings.workspace.save_failed_title": "Save Failed",
    "settings.workspace.save_failed_detail": "Failed to save SSH profile: {error}",
    "settings.workspace.delete_title": "Delete SSH Profile",
    "settings.workspace.delete_message": "Delete SSH profile {ssh_profile_id}?",
    "settings.workspace.deleted_title": "SSH Profile Deleted",
    "settings.workspace.deleted_detail": "Deleted profile {ssh_profile_id}.",
    "settings.workspace.delete_failed_title": "Delete Failed",
    "settings.workspace.delete_failed_detail": "Failed to delete SSH profile: {error}",
    "settings.workspace.load_failed_title": "Load Failed",
    "settings.workspace.load_failed_detail": "Failed to load SSH profiles: {error}",
    "settings.workspace.validation_failed_title": "Validation Failed",
    "settings.workspace.profile_id_required": "Profile ID is required.",
    "settings.workspace.host_required": "Host is required.",
    "settings.workspace.testing": "Testing connection...",
    "settings.workspace.probe_success": "Connected in {latency_ms}ms",
    "settings.workspace.connection_failed": "Connection failed: {reason}",
    "settings.workspace.probe_failed": "Probe failed: {error}",
    "settings.workspace.unknown": "Unknown error",
    "settings.proxy.show_password": "Show password",
    "settings.proxy.hide_password": "Hide password",
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
        encoding="utf-8",
    )
    mock_logger_path.write_text(
        """
export function errorToPayload(error = null) {
    return { message: String(error?.message || error || "") };
}

export function logError() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../../core/api.js", "./mockApi.mjs")
        .replace("../../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
        .replace("../../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
function createButton(attributes = {{}}) {{
    const attributeStore = new Map(Object.entries(attributes));
    return {{
        onclick: null,
        style: {{}},
        getAttribute(name) {{
            return attributeStore.get(name) || null;
        }},
        setAttribute(name, value) {{
            attributeStore.set(name, String(value));
        }},
    }};
}}

function parseButtons(html, attrName) {{
    const escapedAttrName = attrName.replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&");
    const pattern = new RegExp(`${{escapedAttrName}}="([^"]+)"`, "g");
    const matches = [];
    let match = pattern.exec(html);
    while (match) {{
        const attributes = {{}};
        attributes[attrName] = match[1];
        matches.push(createButton(attributes));
        match = pattern.exec(html);
    }}
    return matches;
}}

function createHtmlElement() {{
    let html = "";
    const cache = new Map();
    return {{
        style: {{}},
        onclick: null,
        get innerHTML() {{
            return html;
        }},
        set innerHTML(value) {{
            html = String(value || "");
            cache.clear();
        }},
        querySelectorAll(selector) {{
            if (selector === "[data-workspace-ssh-profile-edit]") {{
                if (!cache.has(selector)) {{
                    cache.set(selector, parseButtons(html, "data-workspace-ssh-profile-edit"));
                }}
                return cache.get(selector);
            }}
            if (selector === "[data-workspace-ssh-profile-delete]") {{
                if (!cache.has(selector)) {{
                    cache.set(selector, parseButtons(html, "data-workspace-ssh-profile-delete"));
                }}
                return cache.get(selector);
            }}
            if (selector === "[data-workspace-ssh-profile-test]") {{
                if (!cache.has(selector)) {{
                    cache.set(selector, parseButtons(html, "data-workspace-ssh-profile-test"));
                }}
                return cache.get(selector);
            }}
            return [];
        }},
    }};
}}

function createInput() {{
    return {{
        value: "",
        style: {{}},
        oninput: null,
        onchange: null,
        type: "text",
        placeholder: "",
        focus() {{
            return undefined;
        }},
    }};
}}

function createFileInput() {{
    return {{
        value: "",
        files: [],
        style: {{}},
        onchange: null,
        click() {{
            return undefined;
        }},
    }};
}}

function createElements() {{
    return new Map([
        ["workspace-ssh-profile-list", createHtmlElement()],
        ["workspace-ssh-profile-editor", createHtmlElement()],
        ["workspace-ssh-profile-editor-title", {{ textContent: "", style: {{}} }}],
        ["workspace-ssh-profile-auth-state", {{ textContent: "", style: {{}} }}],
        ["workspace-ssh-profile-probe-status", {{ textContent: "", className: "", style: {{}} }}],
        ["workspace-ssh-profile-id", createInput()],
        ["workspace-ssh-profile-host", createInput()],
        ["workspace-ssh-profile-username", createInput()],
        ["workspace-ssh-profile-password", createInput()],
        ["workspace-ssh-profile-port", createInput()],
        ["workspace-ssh-profile-shell", createInput()],
        ["workspace-ssh-profile-timeout", createInput()],
        ["workspace-ssh-profile-private-key-name", createInput()],
        ["workspace-ssh-profile-private-key", createInput()],
        ["workspace-ssh-profile-private-key-file", createFileInput()],
        ["add-ssh-profile-btn", {{ onclick: null, style: {{}} }}],
        ["test-ssh-profile-btn", {{ onclick: null, style: {{}}, textContent: "", disabled: false }}],
        ["save-ssh-profile-btn", {{ onclick: null, style: {{}} }}],
        ["cancel-ssh-profile-btn", {{ onclick: null, style: {{}} }}],
        ["delete-ssh-profile-btn", {{ onclick: null, style: {{}} }}],
        ["workspace-ssh-profile-import-private-key-btn", {{ onclick: null, style: {{}} }}],
        ["toggle-workspace-ssh-profile-password-btn", createButton()],
    ]);
}}

function installGlobals(elements, notifications) {{
    globalThis.__notifications = notifications;
    globalThis.__revealSshProfilePasswordCalls = 0;
    globalThis.document = {{
        getElementById(id) {{
            const element = elements.get(id);
            if (!element) {{
                throw new Error(`Missing element: ${{id}}`);
            }}
            return element;
        }},
        querySelectorAll() {{
            return [];
        }},
    }};
}}

{runner_source}
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
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
