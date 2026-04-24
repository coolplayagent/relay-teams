# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_commands_settings_panel_renders_catalog_creates_and_edits_command(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/settings/commandsSettings.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "commands_settings"
    temp_dir.mkdir()
    (temp_dir / "commandsSettings.mjs").write_text(
        source.replace("../../core/api.js", "./mockApi.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
        .replace("../../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../../utils/logger.js", "./mockLogger.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockApi.mjs").write_text(
        """
export async function fetchCommandCatalog() {
    globalThis.__fetchCatalogCalls = (globalThis.__fetchCatalogCalls || 0) + 1;
    return {
        app_commands: [
                {
                    name: "global",
                    aliases: ["g"],
                description: "Global command",
                argument_hint: "",
                allowed_modes: ["normal"],
                scope: "app",
                source_path: "C:/config/commands/global.md",
                template: "Global {{args}}",
            },
        ],
        workspaces: [
            {
                workspace_id: "workspace-1",
                root_path: "C:/repo",
                commands: [
                        {
                            name: "opsx:propose",
                            aliases: ["opsx/propose"],
                        description: "Create an OpenSpec proposal",
                        argument_hint: "<change-id>",
                        allowed_modes: ["normal"],
                        scope: "project",
                        source_path: "C:/repo/.claude/commands/opsx/propose.md",
                        template: "Propose {{args}}",
                    },
                ],
            },
            {
                workspace_id: "workspace-2",
                root_path: "C:/other",
                commands: [],
            },
            {
                workspace_id: "read-only",
                root_path: "C:/readonly",
                can_create_commands: false,
                commands: [],
            },
            {
                workspace_id: "remote-only",
                root_path: "",
                commands: [],
            },
        ],
    };
}

export async function createCommand(payload) {
    globalThis.__createdCommandPayload = payload;
    return { command: { name: payload.name } };
}

export async function updateCommand(payload) {
    globalThis.__updatedCommandPayload = payload;
    return { command: { name: payload.name } };
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return {
        "settings.commands.count_one": "1 command",
        "settings.commands.count_many": "{count} commands",
        "settings.commands.workspace_count_one": "1 workspace",
        "settings.commands.workspace_count_many": "{count} workspace",
        "settings.commands.search_placeholder": "Search command or workspace",
        "settings.commands.no_matches": "No matching commands",
        "settings.commands.refresh": "Refresh",
        "settings.commands.loading": "Loading commands...",
        "settings.commands.empty": "No commands discovered",
        "settings.commands.empty_copy": "No available commands were found.",
        "settings.commands.empty_hint": "Add a command or refresh.",
        "settings.commands.global_group": "Global commands",
        "settings.commands.workspace_empty": "No project commands in this workspace.",
        "settings.commands.global_empty": "No global commands.",
        "settings.commands.scope": "Scope",
        "settings.commands.scope_project": "Project",
        "settings.commands.scope_global": "Global",
        "settings.commands.workspace": "Workspace",
        "settings.commands.no_workspaces": "No workspaces",
        "settings.commands.source": "Directory",
        "settings.commands.source_claude": "Claude",
        "settings.commands.source_codex": "Codex",
        "settings.commands.source_opencode": "OpenCode",
        "settings.commands.source_relay_teams": "Relay Teams",
        "settings.commands.name": "Command name",
        "settings.commands.path": "File path",
        "settings.commands.description": "Description",
        "settings.commands.argument_hint": "Argument hint",
        "settings.commands.allowed_modes": "Allowed modes",
        "settings.commands.aliases": "Aliases (optional)",
        "settings.commands.alias_label": "alias",
        "settings.commands.no_aliases": "No aliases",
        "settings.commands.table_command": "Command",
        "settings.commands.table_description": "Description",
        "settings.commands.table_argument": "Argument",
        "settings.commands.table_scope": "Scope",
        "settings.commands.table_source_path": "Source path",
        "settings.commands.table_actions": "Actions",
        "settings.commands.expand_hint": "Click to expand",
        "settings.commands.collapse_hint": "Click to collapse",
        "settings.commands.template": "Prompt template",
        "settings.commands.source_path": "Source path",
        "settings.commands.source_meta": "Source info:",
        "settings.commands.edit": "Edit",
        "settings.commands.preview": "Preview",
        "settings.commands.copy_path": "Copy path",
        "settings.commands.copy_path_done": "Path Copied",
        "settings.commands.copy_path_failed": "Failed to copy path",
        "settings.commands.no_description": "No description",
        "settings.commands.editor_create_title": "Add Command",
        "settings.commands.editor_create_copy": "Choose a directory, then save the command file.",
        "settings.commands.editor_edit_title": "Edit Command",
        "settings.commands.editor_edit_copy": "Saving overwrites the current source path file without moving it.",
        "settings.commands.created": "Created",
        "settings.commands.created_copy": "Created copy",
        "settings.commands.updated": "Updated",
        "settings.commands.updated_copy": "Updated copy",
        "settings.commands.create_failed": "Create failed",
        "settings.commands.update_failed": "Update failed",
        "settings.commands.save_failed_copy": "Save failed",
        "settings.commands.workspace_required": "Workspace required",
        "settings.commands.source_path_required": "Source path required",
        "settings.action.cancel": "Cancel",
        "settings.action.save": "Save",
    }[key] || key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockFeedback.mjs").write_text(
        """
export function showToast(payload) {
    globalThis.__toasts = [...(globalThis.__toasts || []), payload];
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockLogger.mjs").write_text(
        """
export function errorToPayload(error) {
    return { message: String(error?.message || "") };
}

export function logError() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    runner = """
const elements = new Map();
function element(id) {
    if (!elements.has(id)) {
        elements.set(id, {
            id,
            value: "",
            innerHTML: "",
            textContent: "",
            onclick: null,
            onchange: null,
            oninput: null,
            style: { display: "" },
        });
    }
    return elements.get(id);
}
globalThis.document = {
    getElementById(id) {
        return element(id);
    },
    dispatchEvent(event) {
        globalThis.__documentEvents = [...(globalThis.__documentEvents || []), event.type];
    },
};

const {
    bindCommandsSettingsHandlers,
    loadCommandsSettingsPanel,
} = await import("./commandsSettings.mjs");
await loadCommandsSettingsPanel();
const catalogHtml = element("commands-status").innerHTML;
element("command-search-input").value = "opsx";
element("command-search-input").oninput();
const searchHtml = element("commands-status").innerHTML;
element("command-search-input").value = "";
element("command-search-input").oninput();
element("toggle-command-group-workspace-workspace-1").onclick();
const expandedCatalogHtml = element("commands-status").innerHTML;

element("edit-command-workspace-0-0").onclick();
const editHtml = element("commands-status").innerHTML;
element("command-description-input").value = "Updated proposal command";
element("command-template-input").value = "Updated {{args}}";
element("preview-command-btn").onclick();
const previewText = element("command-preview-output").textContent;
await element("save-command-btn").onclick();

element("add-command-btn").onclick();
const createHtml = element("commands-status").innerHTML;
bindCommandsSettingsHandlers();
element("command-workspace-input").value = "workspace-1";
element("command-name-input").value = "opsx:review";
element("command-name-input").oninput();
element("command-description-input").value = "Review an OpenSpec change";
element("command-argument-hint-input").value = "<change-id>";
element("command-template-input").value = "Review {{args}}";
const addButtonDisplayInCreate = element("add-command-btn").style.display;
const saveButtonDisplayInCreate = element("save-command-btn").style.display;
const cancelButtonDisplayInCreate = element("cancel-command-btn").style.display;
const previewButtonDisplayInCreate = element("preview-command-btn").style.display;
await element("save-command-btn").onclick();

console.log(JSON.stringify({
    catalogHtml,
    searchHtml,
    expandedCatalogHtml,
    editHtml,
    createHtml,
    suggestedPath: element("command-path-input").value,
    createdPayload: globalThis.__createdCommandPayload,
    updatedPayload: globalThis.__updatedCommandPayload,
    previewText,
    fetchCalls: globalThis.__fetchCatalogCalls,
    toastCount: (globalThis.__toasts || []).length,
    addButtonDisplayInCreate,
    saveButtonDisplayInCreate,
    cancelButtonDisplayInCreate,
    previewButtonDisplayInCreate,
    documentEvents: globalThis.__documentEvents || [],
}));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    payload = json.loads(result.stdout)
    catalog_html = str(payload["catalogHtml"])
    search_html = str(payload["searchHtml"])
    expanded_catalog_html = str(payload["expandedCatalogHtml"])
    edit_html = str(payload["editHtml"])
    create_html = str(payload["createHtml"])
    assert "Global commands" in catalog_html
    assert "/global" in catalog_html
    assert "Search command or workspace" in catalog_html
    assert "Command" in catalog_html
    assert "Description" in catalog_html
    assert "Argument" in catalog_html
    assert "Scope" in catalog_html
    assert "Source path" in catalog_html
    assert "Actions" in catalog_html
    assert "workspace-1" in catalog_html
    assert "workspace-2" in catalog_html
    assert "read-only" in catalog_html
    assert "remote-only" in catalog_html
    assert "Click to expand" in catalog_html
    assert "/opsx:propose" not in catalog_html
    assert "/opsx:propose" in search_html
    assert "alias /opsx/propose" in search_html
    assert "/opsx:propose" in expanded_catalog_html
    assert "alias /opsx/propose" in expanded_catalog_html
    assert "No project commands in this workspace." in catalog_html
    assert "Edit" in catalog_html
    assert "C:/repo/.claude/commands/opsx/propose.md" in edit_html
    assert "Edit Command" in edit_html
    assert "Aliases (optional)" in edit_html
    assert "command-editor-actions" not in edit_html
    assert "Create command" not in edit_html
    assert create_html.index("Relay Teams") < create_html.index("Claude")
    assert "read-only" not in create_html
    assert "remote-only" not in create_html
    assert payload["addButtonDisplayInCreate"] == "none"
    assert payload["saveButtonDisplayInCreate"] == "inline-flex"
    assert payload["cancelButtonDisplayInCreate"] == "inline-flex"
    assert payload["previewButtonDisplayInCreate"] == "inline-flex"
    assert "aliases: [opsx/propose]" in str(payload["previewText"])
    assert "Updated {{args}}" in str(payload["previewText"])
    assert payload["suggestedPath"] == "opsx/review.md"
    assert payload["updatedPayload"] == {
        "source_path": "C:/repo/.claude/commands/opsx/propose.md",
        "name": "opsx:propose",
        "aliases": ["opsx/propose"],
        "description": "Updated proposal command",
        "argument_hint": "<change-id>",
        "allowed_modes": ["normal"],
        "template": "Updated {{args}}",
    }
    assert payload["createdPayload"] == {
        "scope": "project",
        "workspace_id": "workspace-1",
        "source": "relay_teams",
        "relative_path": "opsx/review.md",
        "name": "opsx:review",
        "aliases": [],
        "description": "Review an OpenSpec change",
        "argument_hint": "<change-id>",
        "allowed_modes": ["normal"],
        "template": "Review {{args}}",
    }
    assert payload["fetchCalls"] == 3
    assert payload["toastCount"] == 2
    assert payload["documentEvents"] == [
        "agent-teams-commands-updated",
        "agent-teams-commands-updated",
    ]


def test_commands_settings_ignores_stale_load_failures(tmp_path: Path) -> None:
    source = Path("frontend/dist/js/components/settings/commandsSettings.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "commands_settings_stale"
    temp_dir.mkdir()
    (temp_dir / "commandsSettings.mjs").write_text(
        source.replace("../../core/api.js", "./mockApi.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
        .replace("../../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../../utils/logger.js", "./mockLogger.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockApi.mjs").write_text(
        """
export async function fetchCommandCatalog() {
    const next = globalThis.__catalogQueue.shift();
    return next();
}

export async function createCommand() {
    return {};
}

export async function updateCommand() {
    return {};
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function t(key, params = {}) {
    return String(key).replace("{count}", String(params.count || ""));
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockFeedback.mjs").write_text(
        """
export function showToast() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockLogger.mjs").write_text(
        """
export function errorToPayload(error) {
    return { message: String(error?.message || "") };
}

export function logError() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    runner = """
const elements = new Map();
function element(id) {
    if (!elements.has(id)) {
        elements.set(id, {
            id,
            value: "",
            innerHTML: "",
            textContent: "",
            onclick: null,
            onchange: null,
            oninput: null,
            style: { display: "" },
        });
    }
    return elements.get(id);
}
globalThis.document = {
    getElementById(id) {
        return element(id);
    },
    dispatchEvent() {
        return undefined;
    },
};

const { loadCommandsSettingsPanel } = await import("./commandsSettings.mjs");
let rejectStale;
let resolveCurrent;
globalThis.__catalogQueue = [
    () => new Promise((resolve, reject) => {
        rejectStale = reject;
    }),
    () => new Promise((resolve) => {
        resolveCurrent = resolve;
    }),
];
const staleLoad = loadCommandsSettingsPanel();
const currentLoad = loadCommandsSettingsPanel();
resolveCurrent({
    app_commands: [
        {
            name: "current",
            aliases: [],
            description: "Current command",
            argument_hint: "",
            allowed_modes: ["normal"],
            scope: "app",
            source_path: "/config/commands/current.md",
            template: "Current",
        },
    ],
    workspaces: [],
});
await currentLoad;
rejectStale(new Error("stale load failed"));
await staleLoad;

console.log(JSON.stringify({
    html: element("commands-status").innerHTML,
}));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    payload = json.loads(result.stdout)
    html = str(payload["html"])
    assert "current" in html
    assert "stale load failed" not in html
    assert "settings.commands.load_failed" not in html


def test_commands_settings_reports_refresh_failure_after_successful_save(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/components/settings/commandsSettings.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "commands_settings_refresh_failure"
    temp_dir.mkdir()
    (temp_dir / "commandsSettings.mjs").write_text(
        source.replace("../../core/api.js", "./mockApi.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
        .replace("../../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../../utils/logger.js", "./mockLogger.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockApi.mjs").write_text(
        """
export async function fetchCommandCatalog() {
    globalThis.__fetchCatalogCalls = (globalThis.__fetchCatalogCalls || 0) + 1;
    if (globalThis.__fetchCatalogCalls === 1) {
        return {
            app_commands: [],
            workspaces: [
                {
                    workspace_id: "workspace-1",
                    root_path: "C:/repo",
                    commands: [],
                },
            ],
        };
    }
    throw new Error("refresh failed");
}

export async function createCommand(payload) {
    globalThis.__createdCommandPayload = payload;
    return { command: { name: payload.name } };
}

export async function updateCommand() {
    return {};
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return {
        "settings.commands.load_failed": "Load failed",
        "settings.commands.load_failed_copy": "Could not reload catalog",
        "settings.commands.created": "Created",
        "settings.commands.created_copy": "Created copy",
        "settings.commands.create_failed": "Create failed",
        "settings.commands.save_failed_copy": "Save failed",
        "settings.commands.workspace_required": "Workspace required",
        "settings.commands.scope": "Scope",
        "settings.commands.scope_project": "Project",
        "settings.commands.scope_global": "Global",
        "settings.commands.workspace": "Workspace",
        "settings.commands.no_workspaces": "No workspaces",
        "settings.commands.source": "Directory",
        "settings.commands.source_claude": "Claude",
        "settings.commands.source_codex": "Codex",
        "settings.commands.source_opencode": "OpenCode",
        "settings.commands.source_relay_teams": "Relay Teams",
        "settings.commands.name": "Command name",
        "settings.commands.path": "File path",
        "settings.commands.description": "Description",
        "settings.commands.argument_hint": "Argument hint",
        "settings.commands.allowed_modes": "Allowed modes",
        "settings.commands.aliases": "Aliases (optional)",
        "settings.commands.template": "Prompt template",
        "settings.commands.preview": "Preview",
        "settings.action.cancel": "Cancel",
        "settings.action.save": "Save",
    }[key] || key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockFeedback.mjs").write_text(
        """
export function showToast(payload) {
    globalThis.__toasts = [...(globalThis.__toasts || []), payload];
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockLogger.mjs").write_text(
        """
export function errorToPayload(error) {
    return { message: String(error?.message || "") };
}

export function logError() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    runner = """
const elements = new Map();
function element(id) {
    if (!elements.has(id)) {
        elements.set(id, {
            id,
            value: "",
            innerHTML: "",
            textContent: "",
            onclick: null,
            onchange: null,
            oninput: null,
            style: { display: "" },
        });
    }
    return elements.get(id);
}
globalThis.document = {
    getElementById(id) {
        return element(id);
    },
    dispatchEvent(event) {
        globalThis.__documentEvents = [...(globalThis.__documentEvents || []), event.type];
    },
};

const {
    bindCommandsSettingsHandlers,
    loadCommandsSettingsPanel,
} = await import("./commandsSettings.mjs");
await loadCommandsSettingsPanel();
element("add-command-btn").onclick();
bindCommandsSettingsHandlers();
element("command-workspace-input").value = "workspace-1";
element("command-name-input").value = "opsx:review";
element("command-name-input").oninput();
element("command-template-input").value = "Review {{args}}";
element("save-command-btn").onclick();
await Promise.resolve();
await new Promise(resolve => setTimeout(resolve, 0));

console.log(JSON.stringify({
    createdPayload: globalThis.__createdCommandPayload,
    toasts: globalThis.__toasts || [],
    documentEvents: globalThis.__documentEvents || [],
}));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["createdPayload"] == {
        "scope": "project",
        "workspace_id": "workspace-1",
        "source": "relay_teams",
        "relative_path": "opsx/review.md",
        "name": "opsx:review",
        "aliases": [],
        "description": "",
        "argument_hint": "",
        "allowed_modes": ["normal"],
        "template": "Review {{args}}",
    }
    assert payload["toasts"] == [
        {
            "title": "Created",
            "message": "Created copy",
            "tone": "success",
        },
        {
            "title": "Load failed",
            "message": "refresh failed",
            "tone": "warning",
        },
    ]
    assert payload["documentEvents"] == ["agent-teams-commands-updated"]
