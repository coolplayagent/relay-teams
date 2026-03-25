from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_project_view_opens_progressively_and_reuses_cached_tree_and_diff(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    hideProjectView,
    initializeProjectView,
    openWorkspaceProjectView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

initializeProjectView();
await openWorkspaceProjectView({ workspace_id: "alpha-project" });

const initialHtml = els.projectViewContent.innerHTML;

await flushTasks();
await flushTasks();
const initialToggle = els.projectViewContent.querySelector(".workspace-tree-toggle");
const initialExpanded = initialToggle?.getAttribute("aria-expanded");
const diffLoadedHtml = els.projectViewContent.innerHTML;

initialToggle?.onclick?.();
await flushTasks();
const expandedToggle = els.projectViewContent.querySelector(".workspace-tree-toggle");
const expandedState = expandedToggle?.getAttribute("aria-expanded");
const fileEntry = els.projectViewContent.querySelector(".workspace-tree-file");
fileEntry?.onclick?.();
await flushTasks();
const selectedHtml = els.projectViewContent.innerHTML;
const selectedFileEntry = els.projectViewContent.querySelector(".workspace-tree-file");
const selectedDiffCard = els.projectViewContent.querySelector(".workspace-diff-card");

hideProjectView();
await openWorkspaceProjectView({ workspace_id: "alpha-project" });
const reopenedHtml = els.projectViewContent.innerHTML;
const reopenedSummary = els.projectViewSummary.textContent;
const reopenedToggle = els.projectViewContent.querySelector(".workspace-tree-toggle");
reopenedToggle?.onclick?.();
const collapsedToggle = els.projectViewContent.querySelector(".workspace-tree-toggle");
const collapsedState = collapsedToggle?.getAttribute("aria-expanded");
const collapsedHtml = els.projectViewContent.innerHTML;
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    projectViewDisplay: els.projectView.style.display,
    chatContainerDisplay: els.chatContainer.style.display,
    initialExpanded,
    expandedState,
    collapsedState,
    initialHasNestedFile: initialHtml.includes('data-tree-file-path="src/main.py"'),
    initialHasTreeIcons: diffLoadedHtml.includes("workspace-tree-icon"),
    initialShowsDiffLoading: initialHtml.includes("Loading changes"),
    diffLoadedHasCard: diffLoadedHtml.includes("workspace-diff-card"),
    diffLoadedHasDetail: diffLoadedHtml.includes("changed file"),
    selectedFileEntryPressed: selectedFileEntry?.getAttribute("aria-pressed"),
    selectedDiffClassName: selectedDiffCard?.getAttribute("class"),
    expandedHasNestedFile: selectedHtml.includes('data-tree-file-path="src/main.py"'),
    collapsedHasNestedFile: collapsedHtml.includes('data-tree-file-path="src/main.py"'),
    reopenedHasNestedFile: reopenedHtml.includes('data-tree-file-path="src/main.py"'),
    reopenedHasDetail: reopenedHtml.includes("changed file"),
    reopenedSummary,
    snapshotRequests: globalThis.__snapshotRequests,
    diffRequests: globalThis.__diffRequests,
    diffFileRequests: globalThis.__diffFileRequests,
    treeRequests: globalThis.__treeRequests,
}));
""".strip(),
    )

    assert payload["projectViewDisplay"] == "block"
    assert payload["chatContainerDisplay"] == "none"
    assert payload["initialExpanded"] == "false"
    assert payload["expandedState"] == "true"
    assert payload["collapsedState"] == "false"
    assert payload["initialHasNestedFile"] is False
    assert payload["initialHasTreeIcons"] is True
    assert payload["initialShowsDiffLoading"] is True
    assert payload["diffLoadedHasCard"] is True
    assert payload["diffLoadedHasDetail"] is True
    assert payload["selectedFileEntryPressed"] == "true"
    assert "is-selected" in str(payload["selectedDiffClassName"])
    assert payload["expandedHasNestedFile"] is True
    assert payload["collapsedHasNestedFile"] is False
    assert payload["reopenedHasNestedFile"] is True
    assert payload["reopenedHasDetail"] is True
    assert payload["reopenedSummary"] == "1 changed files"
    assert payload["snapshotRequests"] == ["alpha-project", "alpha-project"]
    assert payload["diffRequests"] == ["alpha-project", "alpha-project"]
    assert payload["diffFileRequests"] == ["src/main.py"]
    assert payload["treeRequests"] == ["src"]


def test_project_view_updates_automation_project_with_feishu_binding(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openAutomationProjectView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

globalThis.__showFormDialogResult = {
    display_name: "Daily Briefing",
    workspace_id: "alpha-project",
    prompt: "Summarize the latest project changes.",
    cron_expression: "0 9 * * *",
    timezone: "UTC",
    enabled: true,
    delivery_binding_key: "trg_feishu::tenant-1::oc_123",
    delivery_event_started: true,
    delivery_event_completed: true,
    delivery_event_failed: true,
};

initializeProjectView();
await openAutomationProjectView({ automation_project_id: "aut_1", workspace_id: "alpha-project" });
await flushTasks();
await flushTasks();

const editButton = document.querySelector("[data-automation-edit]");
editButton?.onclick?.();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    contentHtml: els.projectViewContent.innerHTML,
    formOptions: globalThis.__showFormDialogCalls[0],
    updatePayload: globalThis.__updatedAutomationPayload,
}));
""".strip(),
        mock_api_source="""
export async function disableAutomationProject() {
    return { status: "disabled" };
}

export async function enableAutomationProject() {
    return { status: "enabled" };
}

export async function fetchAutomationProject() {
    return {
        automation_project_id: "aut_1",
        name: "daily-briefing",
        display_name: "Daily Briefing",
        status: "enabled",
        workspace_id: "alpha-project",
        prompt: "Summarize the latest project changes.",
        schedule_mode: "cron",
        cron_expression: "0 9 * * *",
        timezone: "UTC",
        delivery_binding: {
            provider: "feishu",
            trigger_id: "trg_feishu",
            tenant_key: "tenant-1",
            chat_id: "oc_123",
            chat_type: "group",
            source_label: "Release Updates",
        },
        delivery_events: ["started"],
        next_run_at: "2026-03-14T09:00:00Z",
    };
}

export async function fetchAutomationFeishuBindings() {
    return [
        {
            provider: "feishu",
            trigger_id: "trg_feishu",
            trigger_name: "Feishu Main",
            tenant_key: "tenant-1",
            chat_id: "oc_123",
            chat_type: "group",
            source_label: "Release Updates",
            session_id: "session-im-1",
            session_title: "feishu_main - Release Updates",
            updated_at: "2026-03-14T10:00:00Z",
        },
    ];
}

export async function fetchAutomationProjectSessions() {
    return [];
}

export async function fetchWorkspaces() {
    return [
        {
            workspace_id: "alpha-project",
            root_path: "/work/alpha-project",
        },
    ];
}

export async function fetchWorkspaceSnapshot() {
    throw new Error("not used");
}

export async function fetchWorkspaceTree() {
    throw new Error("not used");
}

export async function fetchWorkspaceDiffs() {
    throw new Error("not used");
}

export async function fetchWorkspaceDiffFile() {
    throw new Error("not used");
}

export async function runAutomationProject() {
    return { status: "ok" };
}

export async function updateAutomationProject(_automationProjectId, payload) {
    globalThis.__updatedAutomationPayload = payload;
    return { status: "ok" };
}
""".strip(),
    )

    assert payload["updatePayload"]["delivery_binding"]["trigger_id"] == "trg_feishu"
    assert payload["updatePayload"]["delivery_binding"]["chat_id"] == "oc_123"
    assert payload["updatePayload"]["delivery_events"] == [
        "started",
        "completed",
        "failed",
    ]
    bindingField = next(
        field
        for field in payload["formOptions"]["fields"]
        if field["id"] == "delivery_binding_key"
    )
    assert bindingField["options"][1]["label"] == "feishu_main - Release Updates"
    assert bindingField["options"][1]["description"] == "Feishu Main - group"
    assert "feishu_main - Release Updates" in str(payload["contentHtml"])


def _run_project_view_script(
    tmp_path: Path,
    runner_source: str,
    mock_api_source: str | None = None,
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "projectView.js"
    )

    module_under_test_path = tmp_path / "projectView.mjs"
    mock_dom_path = tmp_path / "mockDom.mjs"
    mock_api_path = tmp_path / "mockApi.mjs"
    mock_state_path = tmp_path / "mockState.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_agent_panel_path = tmp_path / "mockAgentPanel.mjs"
    mock_navigator_path = tmp_path / "mockNavigator.mjs"
    mock_subagent_rail_path = tmp_path / "mockSubagentRail.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_dom_path.write_text(
        r"""
export const els = {};

function decodeHtmlAttribute(value) {
    return String(value)
        .replaceAll("&quot;", '"')
        .replaceAll("&#39;", "'")
        .replaceAll("&lt;", "<")
        .replaceAll("&gt;", ">")
        .replaceAll("&amp;", "&");
}

function createBasicElement() {
    const attributeStore = new Map();
    return {
        style: {},
        textContent: "",
        innerHTML: "",
        onclick: null,
        onkeydown: null,
        classList: {
            remove() {
                return undefined;
            },
        },
        setAttribute(name, value) {
            attributeStore.set(name, String(value));
        },
        getAttribute(name) {
            return attributeStore.get(name) || null;
        },
    };
}

function createTreeNode(attributes = {}) {
    const attributeStore = new Map(Object.entries(attributes));
    return {
        onclick: null,
        onkeydown: null,
        addEventListener(name, handler) {
            if (name === "click") {
                this.onclick = handler;
            }
            if (name === "keydown") {
                this.onkeydown = handler;
            }
        },
        setAttribute(name, value) {
            attributeStore.set(name, String(value));
        },
        getAttribute(name) {
            return attributeStore.get(name) || null;
        },
    };
}

function parseNodes(source, selector) {
    const patterns = {
        ".workspace-tree-toggle": /class="workspace-tree-toggle"[\s\S]*?data-tree-toggle-path="([^"]+)"[\s\S]*?aria-expanded="([^"]+)"/g,
        ".workspace-tree-file": /class="([^"]*workspace-tree-file[^"]*)"[\s\S]*?data-tree-file-path="([^"]+)"[\s\S]*?aria-pressed="([^"]+)"/g,
        ".workspace-diff-card": /class="([^"]*workspace-diff-card[^"]*)"[\s\S]*?data-diff-path="([^"]*)"/g,
        "[data-automation-edit]": /data-automation-edit/g,
    };
    const pattern = patterns[selector];
    const results = [];
    if (!pattern) {
        return results;
    }
    let match = pattern.exec(source);
    while (match) {
        if (selector === ".workspace-tree-toggle") {
            results.push(createTreeNode({
                class: "workspace-tree-toggle",
                "data-tree-toggle-path": decodeHtmlAttribute(match[1]),
                "aria-expanded": match[2],
            }));
        } else if (selector === ".workspace-tree-file") {
            results.push(createTreeNode({
                class: match[1],
                "data-tree-file-path": decodeHtmlAttribute(match[2]),
                "aria-pressed": match[3],
            }));
        } else if (selector === ".workspace-diff-card") {
            results.push(createTreeNode({
                class: match[1],
                "data-diff-path": decodeHtmlAttribute(match[2]),
            }));
        } else if (selector === "[data-automation-edit]") {
            results.push(createTreeNode({}));
        }
        match = pattern.exec(source);
    }
    return results;
}

function createHtmlElement() {
    let html = "";
    const cache = new Map();
    return {
        style: {},
        textContent: "",
        onclick: null,
        onkeydown: null,
        get innerHTML() {
            return html;
        },
        set innerHTML(value) {
            html = String(value);
            cache.clear();
        },
        querySelector(selector) {
            return this.querySelectorAll(selector)[0] || null;
        },
        querySelectorAll(selector) {
            if (!cache.has(selector)) {
                cache.set(selector, parseNodes(html, selector));
            }
            return cache.get(selector);
        },
    };
}

export function createDomEnvironment() {
    const elements = new Map([
        ["project-view", createBasicElement()],
        ["project-view-title", createBasicElement()],
        ["project-view-summary", createBasicElement()],
        ["project-view-toolbar-actions", createHtmlElement()],
        ["project-view-content", createHtmlElement()],
        ["project-view-reload", createBasicElement()],
        ["project-view-close", createBasicElement()],
        ["chat-container", createBasicElement()],
        ["observability-view", createBasicElement()],
        ["observability-btn", createBasicElement()],
    ]);

    return {
        body: {
            classList: {
                remove() {
                    return undefined;
                },
            },
        },
        addEventListener() {
            return undefined;
        },
        dispatchEvent() {
            return undefined;
        },
        querySelector(selector) {
            const toolbar = elements.get("project-view-toolbar-actions");
            const toolbarMatch = toolbar?.querySelector(selector);
            if (toolbarMatch) {
                return toolbarMatch;
            }
            const content = elements.get("project-view-content");
            return content?.querySelector(selector) || null;
        },
        getElementById(id) {
            const element = elements.get(id);
            if (!element) {
                throw new Error(`Missing element: ${id}`);
            }
            return element;
        },
    };
}

export function installGlobals(documentEnv) {
    globalThis.document = documentEnv;
    els.projectView = documentEnv.getElementById("project-view");
    els.projectViewTitle = documentEnv.getElementById("project-view-title");
    els.projectViewSummary = documentEnv.getElementById("project-view-summary");
    els.projectViewToolbarActions = documentEnv.getElementById("project-view-toolbar-actions");
    els.projectViewContent = documentEnv.getElementById("project-view-content");
    els.projectViewReloadBtn = documentEnv.getElementById("project-view-reload");
    els.projectViewCloseBtn = documentEnv.getElementById("project-view-close");
    els.chatContainer = documentEnv.getElementById("chat-container");
}

export async function flushTasks() {
    await Promise.resolve();
    await new Promise(resolve => setTimeout(resolve, 0));
    await Promise.resolve();
}
""".strip(),
        encoding="utf-8",
    )

    default_mock_api_source = """
export async function disableAutomationProject() {
    return { status: "disabled" };
}

export async function enableAutomationProject() {
    return { status: "enabled" };
}

export async function fetchAutomationProject() {
    return null;
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function fetchAutomationProjectSessions() {
    return [];
}

export async function fetchWorkspaces() {
    return [];
}

export async function fetchWorkspaceSnapshot(workspaceId) {
    await new Promise(resolve => setTimeout(resolve, 0));
    globalThis.__snapshotRequests.push(workspaceId);
    return {
        workspace_id: "alpha-project",
        root_path: "/work/alpha-project",
        tree: {
            name: ".",
            path: ".",
            kind: "directory",
            has_children: true,
            children: [
                {
                    name: "src",
                    path: "src",
                    kind: "directory",
                    has_children: true,
                    children: [],
                },
                {
                    name: "docs",
                    path: "docs",
                    kind: "directory",
                    has_children: false,
                    children: [],
                },
            ],
        },
    };
}

export async function fetchWorkspaceTree(workspaceId, path) {
    globalThis.__treeRequests.push(path);
    return {
        workspace_id: workspaceId,
        directory_path: path,
        children: [
            {
                name: "main.py",
                path: "src/main.py",
                kind: "file",
                has_children: false,
                children: [],
            },
        ],
    };
}

export async function fetchWorkspaceDiffs(workspaceId) {
    await new Promise(resolve => setTimeout(resolve, 0));
    globalThis.__diffRequests.push(workspaceId);
    return {
        workspace_id: workspaceId,
        root_path: "/work/alpha-project",
        is_git_repository: true,
        git_root_path: "/work/alpha-project",
        diff_message: null,
        diff_files: [
            {
                path: "src/main.py",
                change_type: "modified",
            },
        ],
    };
}

export async function fetchWorkspaceDiffFile(workspaceId, path) {
    await new Promise(resolve => setTimeout(resolve, 0));
    globalThis.__diffFileRequests.push(path);
    return {
        workspace_id: workspaceId,
        path,
        change_type: "modified",
        diff: "changed file",
        is_binary: false,
    };
}

export async function runAutomationProject() {
    return { status: "ok" };
}

export async function updateAutomationProject() {
    return { status: "ok" };
}
""".strip()
    mock_api_path.write_text(
        mock_api_source or default_mock_api_source,
        encoding="utf-8",
    )

    mock_state_path.write_text(
        """
export const state = {
    currentMainView: "session",
    currentProjectViewWorkspaceId: null,
    currentWorkspaceId: null,
};
""".strip(),
        encoding="utf-8",
    )

    mock_i18n_path.write_text(
        """
const translations = {
    "workspace_view.title": "{workspace} Project",
    "workspace_view.tree": "Files",
    "workspace_view.diffs": "Changes",
    "workspace_view.reload": "Reload",
    "workspace_view.back": "Back",
    "workspace_view.loading": "Loading project snapshot...",
    "workspace_view.loading_tree": "Loading files...",
    "workspace_view.loading_directory": "Loading folder...",
    "workspace_view.loading_diffs": "Loading changes...",
    "workspace_view.loading_diff": "Loading diff...",
    "workspace_view.load_failed": "Load failed",
    "workspace_view.empty_tree": "Empty tree",
    "workspace_view.no_diffs": "No diffs",
    "workspace_view.not_git_repository": "Not a git repository",
    "workspace_view.binary_diff": "Binary diff",
    "workspace_view.empty_diff": "Empty diff",
    "workspace_view.diff_summary": "{count} changed files",
    "workspace_view.change.modified": "Modified",
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
        encoding="utf-8",
    )

    mock_logger_path.write_text(
        """
export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    mock_feedback_path.write_text(
        """
export async function showFormDialog(options = {}) {
    globalThis.__showFormDialogCalls.push(options);
    return globalThis.__showFormDialogResult ?? null;
}
""".strip(),
        encoding="utf-8",
    )
    mock_agent_panel_path.write_text(
        """
export function clearAllPanels() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    mock_navigator_path.write_text(
        """
export function hideRoundNavigator() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    mock_subagent_rail_path.write_text(
        """
export function setSubagentRailExpanded() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
        .replace("./agentPanel.js", "./mockAgentPanel.mjs")
        .replace("./rounds/navigator.js", "./mockNavigator.mjs")
        .replace("./subagentRail.js", "./mockSubagentRail.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
import {{ createDomEnvironment, installGlobals }} from "./mockDom.mjs";

globalThis.__snapshotRequests = [];
globalThis.__diffRequests = [];
globalThis.__diffFileRequests = [];
globalThis.__treeRequests = [];
globalThis.__showFormDialogResult = null;
globalThis.__showFormDialogCalls = [];
globalThis.CustomEvent = class CustomEvent {{
    constructor(type, init = {{}}) {{
        this.type = type;
        this.detail = init.detail;
    }}
}};
installGlobals(createDomEnvironment());

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
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
