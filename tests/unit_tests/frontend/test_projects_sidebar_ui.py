# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

import json
import subprocess


def test_projects_sidebar_groups_sessions_and_supports_project_actions(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    handleNewProjectClick,
    loadProjects,
    setSelectSessionHandler,
    toggleProjectSortMode,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());
setSelectSessionHandler(async (sessionId) => {
    globalThis.__selectedSessionIds.push(sessionId);
});

await loadProjects();
const projectsList = document.getElementById("projects-list");
const projectCards = projectsList.children.filter(child => child.className === "project-card");
const firstProject = projectCards[0];
const secondProject = projectCards[1];

const initialSessionCount = firstProject.querySelectorAll(".session-item").length;
const initialProjectExpanded = firstProject.querySelector(".project-toggle").getAttribute("aria-expanded");
const initialFirstProjectTitle = firstProject.querySelector(".project-title").textContent;
const initialSecondProjectTitle = secondProject.querySelector(".project-title").textContent;
const initialFirstSessionLabel = firstProject.querySelectorAll(".session-id")[0].textContent;
const initialVisibilityLabel = firstProject.querySelector(".project-session-visibility-btn").textContent;

firstProject.querySelector(".project-session-visibility-btn").onclick();
await flushTasks();
const expandedSessionProject = projectsList.children.filter(child => child.className === "project-card")[0];
const expandedSessionCount = expandedSessionProject.querySelectorAll(".session-item").length;
const expandedVisibilityLabel = expandedSessionProject.querySelector(".project-session-visibility-btn").textContent;

expandedSessionProject.querySelector(".project-session-visibility-btn").onclick();
await flushTasks();
const recollapsedProject = projectsList.children.filter(child => child.className === "project-card")[0];
const recollapsedSessionCount = recollapsedProject.querySelectorAll(".session-item").length;

recollapsedProject.querySelector(".project-toggle").onclick();
await flushTasks();
const collapsedProject = projectsList.children.filter(child => child.className === "project-card")[0];
const collapsedProjectExpanded = collapsedProject.querySelector(".project-toggle").getAttribute("aria-expanded");

collapsedProject.querySelectorAll(".project-new-session-btn")[0].onclick();
await flushTasks();

await handleNewProjectClick();
await flushTasks();
const finalFirstProjectTitle = projectsList.children.filter(child => child.className === "project-card")[0].querySelector(".project-title").textContent;

toggleProjectSortMode();
await flushTasks();
const sortedFirstProjectTitle = projectsList.children.filter(child => child.className === "project-card")[0].querySelector(".project-title").textContent;

console.log(JSON.stringify({
    initialProjectCount: 2,
    initialSessionCount,
    initialProjectExpanded,
    collapsedProjectExpanded,
    initialVisibilityLabel,
    expandedSessionCount,
    expandedVisibilityLabel,
    recollapsedSessionCount,
    createdSessionWorkspaceIds: globalThis.__createdSessionWorkspaceIds,
    selectedSessionIds: globalThis.__selectedSessionIds,
    finalProjectCount: projectsList.children.filter(child => child.className === "project-card").length,
    initialFirstProjectTitle,
    initialSecondProjectTitle,
    initialFirstSessionLabel,
    finalFirstProjectTitle,
    sortedFirstProjectTitle,
}));
""".strip(),
    )

    assert payload["initialProjectCount"] == 2
    assert payload["initialSessionCount"] == 10
    assert payload["initialProjectExpanded"] == "true"
    assert payload["collapsedProjectExpanded"] == "false"
    assert payload["initialVisibilityLabel"] == "Show all (11)"
    assert payload["expandedSessionCount"] == 11
    assert payload["expandedVisibilityLabel"] == "Collapse"
    assert payload["recollapsedSessionCount"] == 10
    assert payload["createdSessionWorkspaceIds"] == ["alpha-project", "gamma-project"]
    assert payload["selectedSessionIds"] == ["session-new-1", "session-new-2"]
    assert payload["finalProjectCount"] == 3
    assert payload["initialFirstProjectTitle"] == "Alpha Project"
    assert payload["initialSecondProjectTitle"] == "Beta Project"
    assert payload["initialFirstSessionLabel"] == "session-11"
    assert payload["finalFirstProjectTitle"] == "Gamma Project"
    assert payload["sortedFirstProjectTitle"] == "Alpha Project"


def test_projects_sidebar_renames_session_from_sidebar_action(tmp_path: Path) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());

await loadProjects();
const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const renameButton = firstProject.querySelectorAll(".session-rename-btn")[4];
renameButton.onclick({ stopPropagation() {} });
await flushTasks();
await flushTasks();

const refreshedFirstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const renamedLabel = refreshedFirstProject.querySelectorAll(".session-id")[4].textContent;

console.log(JSON.stringify({
    renameCalls: globalThis.__renameCalls,
    renamedLabel,
}));
""".strip(),
    )

    assert payload["renameCalls"] == [
        {
            "sessionId": "session-7",
            "metadata": {"title": "Renamed Session"},
        }
    ]
    assert payload["renamedLabel"] == "Renamed Session"


def test_projects_sidebar_marks_im_sessions_with_icon_and_im_class(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());

await loadProjects();
const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const firstSession = firstProject.querySelectorAll(".session-item")[0];
const firstSessionLabel = firstProject.querySelectorAll(".session-id")[0].textContent;
const iconCount = firstProject.querySelectorAll(".session-source-icon").length;

console.log(JSON.stringify({
    firstSessionClassName: firstSession.className,
    firstSessionLabel,
    iconCount,
}));
""".strip(),
        mock_api_source="""
const workspaces = [
    {
        workspace_id: "alpha-project",
        root_path: "/work/Alpha Project",
        updated_at: "2026-03-14T10:00:00Z",
        profile: {
            file_scope: {
                backend: "project",
            },
        },
    },
];

const sessions = [
    {
        session_id: "session-im",
        workspace_id: "alpha-project",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        metadata: {
            title: "feishu_bot · Release Updates",
            source_kind: "im",
            source_icon: "im",
        },
    },
    {
        session_id: "session-plain",
        workspace_id: "alpha-project",
        updated_at: "2026-03-14T10:10:00Z",
        pending_tool_approval_count: 0,
    },
];

export async function fetchWorkspaces() {
    return workspaces;
}

export async function fetchSessions() {
    return sessions;
}

export async function fetchAutomationProjects() {
    return [];
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function startNewSession() {
    throw new Error("not used");
}

export async function updateSession() {
    return { status: "ok" };
}

export async function pickWorkspace() {
    throw new Error("not used");
}

export async function forkWorkspace() {
    throw new Error("not used");
}

export async function deleteSession() {
    return undefined;
}

export async function deleteWorkspace() {
    return { status: "ok" };
}

export async function createAutomationProject() {
    throw new Error("not used");
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function disableAutomationProject() {
    return { status: "ok" };
}

export async function enableAutomationProject() {
    return { status: "ok" };
}

export async function runAutomationProject() {
    throw new Error("not used");
}
""".strip(),
    )

    assert "session-item-im" in str(payload["firstSessionClassName"])
    assert str(payload["firstSessionLabel"]).startswith("feishu_bot")
    assert str(payload["firstSessionLabel"]).endswith("Release Updates")
    assert payload["iconCount"] == 1


def test_projects_sidebar_creates_automation_project_with_feishu_binding(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    handleNewAutomationProjectClick,
    setSelectSessionHandler,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());
setSelectSessionHandler(async () => {});
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

await handleNewAutomationProjectClick();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    createPayload: globalThis.__createAutomationPayload,
    formOptions: globalThis.__showFormDialogCalls[0],
    runCalls: globalThis.__runAutomationProjectCalls,
}));
""".strip(),
        mock_api_source="""
const workspaces = [
    {
        workspace_id: "alpha-project",
        root_path: "/work/Alpha Project",
        updated_at: "2026-03-14T10:00:00Z",
        profile: {
            file_scope: {
                backend: "project",
            },
        },
    },
];

export async function fetchWorkspaces() {
    return workspaces;
}

export async function fetchSessions() {
    return [];
}

export async function fetchAutomationProjects() {
    return [];
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

export async function startNewSession() {
    throw new Error("not used");
}

export async function updateSession() {
    return { status: "ok" };
}

export async function pickWorkspace() {
    throw new Error("not used");
}

export async function forkWorkspace() {
    throw new Error("not used");
}

export async function deleteSession() {
    return undefined;
}

export async function deleteWorkspace() {
    return { status: "ok" };
}

export async function createAutomationProject(payload) {
    globalThis.__createAutomationPayload = payload;
    return {
        automation_project_id: "aut_created",
        workspace_id: "alpha-project",
        status: "enabled",
    };
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function disableAutomationProject() {
    return { status: "ok" };
}

export async function enableAutomationProject() {
    return { status: "ok" };
}

export async function runAutomationProject(projectId) {
    globalThis.__runAutomationProjectCalls = [projectId];
    return { session_id: "session-automation-1" };
}
""".strip(),
    )

    create_payload = cast(dict[str, object], payload["createPayload"])
    delivery_binding = cast(dict[str, object], create_payload["delivery_binding"])
    delivery_events = cast(list[object], create_payload["delivery_events"])
    form_options = cast(dict[str, object], payload["formOptions"])
    binding_options = cast(
        list[dict[str, object]],
        next(
            cast(list[dict[str, object]], field["options"])
            for field in cast(list[dict[str, object]], form_options["fields"])
            if field["id"] == "delivery_binding_key"
        ),
    )

    assert delivery_binding["trigger_id"] == "trg_feishu"
    assert delivery_binding["chat_id"] == "oc_123"
    assert delivery_events == [
        "started",
        "completed",
        "failed",
    ]
    assert binding_options[1]["label"] == "feishu_main - Release Updates"
    assert binding_options[1]["description"] == "Feishu Main - group"
    assert payload["runCalls"] == ["aut_created"]


def test_projects_sidebar_forks_project_and_can_keep_worktree_on_remove(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());
globalThis.__confirmDialogResponses = [true, false];

await loadProjects();
let projectsList = document.getElementById("projects-list");
let firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
firstProject.querySelector(".project-options-btn").onclick({ stopPropagation() {} });
await flushTasks();
projectsList = document.getElementById("projects-list");
firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
firstProject.querySelector(".project-fork-btn").onclick({ stopPropagation() {} });
await flushTasks();
await flushTasks();
projectsList = document.getElementById("projects-list");
const forkedProject = projectsList.children.filter(child => child.className === "project-card")[0];
forkedProject.querySelector(".project-options-btn").onclick({ stopPropagation() {} });
await flushTasks();
projectsList = document.getElementById("projects-list");
const openForkedProject = projectsList.children.filter(child => child.className === "project-card")[0];
openForkedProject.querySelector(".project-remove-btn").onclick({ stopPropagation() {} });
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    forkCalls: globalThis.__forkCalls,
    deleteWorkspaceCalls: globalThis.__deleteWorkspaceCalls,
    createdSessionWorkspaceIds: globalThis.__createdSessionWorkspaceIds,
    confirmDialogTitles: globalThis.__confirmDialogCalls.map(item => item.title),
}));
""".strip(),
    )

    assert payload["forkCalls"] == [
        {
            "workspaceId": "alpha-project",
            "name": "Alpha Project Fork",
        }
    ]
    assert payload["deleteWorkspaceCalls"] == [
        {
            "workspaceId": "alpha-project-fork",
            "options": {"removeWorktree": False},
        }
    ]
    assert payload["createdSessionWorkspaceIds"] == ["alpha-project-fork"]
    assert payload["confirmDialogTitles"] == [
        "Remove Workspace",
        "Remove Project Worktree",
    ]


def test_projects_sidebar_opens_project_workspace_view_from_title_click(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());

await loadProjects();
const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
firstProject.querySelector(".project-title-btn").onclick({ stopPropagation() {} });
await flushTasks();

console.log(JSON.stringify({
    openedWorkspaceIds: globalThis.__openedWorkspaceIds,
}));
""".strip(),
    )

    assert payload["openedWorkspaceIds"] == ["alpha-project"]


def test_projects_sidebar_marks_selected_project_after_title_click(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());

await loadProjects();
let projectsList = document.getElementById("projects-list");
let firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
firstProject.querySelector(".project-title-btn").onclick({ stopPropagation() {} });
await flushTasks();
await loadProjects();

projectsList = document.getElementById("projects-list");
firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const firstProjectTitleButton = firstProject.querySelector(".project-title-btn");
const activeSessionCount = firstProject.querySelectorAll(".session-item").filter(item => item.className.includes("active")).length;

console.log(JSON.stringify({
    className: firstProjectTitleButton.className,
    ariaCurrent: firstProjectTitleButton.getAttribute("aria-current"),
    activeSessionCount,
}));
""".strip(),
    )

    assert "is-active" in str(payload["className"])
    assert payload["ariaCurrent"] == "page"
    assert payload["activeSessionCount"] == 1


def test_projects_sidebar_hover_hint_preserves_project_action_space() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")

    actions_start = components_css.index(".projects-list .project-actions {")
    actions_end = components_css.index(
        ".projects-list .project-row:hover .project-actions,",
        actions_start,
    )
    actions_rule = components_css[actions_start:actions_end]

    hint_start = components_css.index(".projects-list .project-path-hint {")
    hint_end = components_css.index(
        ".projects-list .project-row:hover + .project-path-hint,",
        hint_start,
    )
    hint_rule = components_css[hint_start:hint_end]

    assert "position: relative;" in actions_rule
    assert "z-index: 8;" in actions_rule
    assert "right: 3.15rem;" in hint_rule
    assert "width: auto;" in hint_rule
    assert "min-width: 0;" in hint_rule
    assert "max-width: none;" in hint_rule
    assert "overflow: hidden;" in hint_rule
    assert "white-space: nowrap;" in hint_rule
    assert "text-overflow: ellipsis;" in hint_rule
    assert "z-index: 6;" in hint_rule


def test_projects_sidebar_uses_root_path_basename_for_long_git_worktree_title(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());

await loadProjects();
const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];

console.log(JSON.stringify({
    projectTitle: firstProject.querySelector(".project-title").textContent,
}));
""".strip(),
        mock_api_source="""
const workspaces = [
    {
        workspace_id: "C:/Users/yex/Documents/workspace/agent-teams",
        root_path: "C:/Users/yex/Documents/workspace/agent-teams",
        updated_at: "2026-03-14T10:00:00Z",
        profile: {
            file_scope: {
                backend: "git_worktree",
            },
        },
    },
];

const sessions = [];

export async function fetchWorkspaces() {
    return workspaces;
}

export async function fetchSessions() {
    return sessions;
}

export async function fetchAutomationProjects() {
    return [];
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function startNewSession(workspaceId) {
    globalThis.__createdSessionWorkspaceIds.push(workspaceId);
    return {
        session_id: "session-new-1",
        workspace_id: workspaceId,
        updated_at: "2026-03-14T11:00:00Z",
        pending_tool_approval_count: 0,
    };
}

export async function updateSession() {
    return { status: "ok" };
}

export async function pickWorkspace() {
    throw new Error("not used");
}

export async function forkWorkspace() {
    throw new Error("not used");
}

export async function deleteSession() {
    return undefined;
}

export async function deleteWorkspace() {
    return { status: "ok" };
}

export async function createAutomationProject() {
    throw new Error("not used");
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function disableAutomationProject() {
    return { status: "ok" };
}

export async function enableAutomationProject() {
    return { status: "ok" };
}

export async function runAutomationProject() {
    throw new Error("not used");
}
""".strip(),
    )

    assert payload["projectTitle"] == "agent-teams"


def _run_sidebar_script(
    tmp_path: Path,
    runner_source: str,
    mock_api_source: str | None = None,
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "components" / "sidebar.js"

    module_under_test_path = tmp_path / "sidebar.mjs"
    mock_dom_path = tmp_path / "mockDom.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    mock_api_path = tmp_path / "mockApi.mjs"
    mock_state_path = tmp_path / "mockState.mjs"
    mock_project_view_path = tmp_path / "mockProjectView.mjs"
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

function parseElements(source, selector) {
    const results = [];
    const patterns = {
        ".project-toggle": /class="project-toggle"[^>]*aria-expanded="([^"]+)"[^>]*>/g,
        ".project-title-btn": /class="([^"]*project-title-btn[^"]*)"[^>]*aria-current="([^"]+)"[^>]*>/g,
        ".project-options-btn": /class="([^"]*project-options-btn[^"]*)"[^>]*>/g,
        ".project-new-session-btn": /class="([^"]*project-new-session-btn[^"]*)"[^>]*>/g,
            ".project-fork-btn": /class="[^"]*project-fork-btn[^"]*"[^>]*>/g,
            ".project-remove-btn": /class="[^"]*project-remove-btn[^"]*"[^>]*>/g,
        ".project-session-visibility-btn": /class="project-session-visibility-btn"[^>]*>([\s\S]*?)<\/button>/g,
        ".session-rename-btn": /class="session-rename-btn"[^>]*data-session-id="([^"]+)"[^>]*data-session-metadata="([^"]*)"[^>]*>/g,
        ".session-delete-btn": /class="session-delete-btn"[^>]*data-session-id="([^"]+)"[^>]*>/g,
        ".session-item": /class="([^"]*session-item[^"]*)"[^>]*data-session-id="([^"]+)"[^>]*data-workspace-id="([^"]+)"[^>]*>/g,
        ".session-source-icon": /class="session-source-icon"[^>]*>/g,
        ".project-title": /class="project-title"[^>]*>([\s\S]*?)<\/span>/g,
        ".session-id": /class="session-id"[^>]*>([\s\S]*?)<\/span>\s*<span class="session-meta"/g,
    };
    const pattern = patterns[selector];
    if (!pattern) {
        return results;
    }
    let match = pattern.exec(source);
    while (match) {
        if (selector === ".project-toggle") {
            results.push(createNode({ attributes: { "aria-expanded": match[1] } }));
        } else if (selector === ".project-title-btn") {
            results.push(createNode({
                className: match[1],
                attributes: {
                    "aria-current": match[2],
                },
            }));
        } else if (selector === ".project-options-btn") {
            results.push(createNode({ className: match[1] }));
        } else if (selector === ".project-new-session-btn") {
            results.push(createNode({ className: match[1] }));
        } else if (selector === ".project-fork-btn") {
            results.push(createNode());
        } else if (selector === ".project-remove-btn") {
            results.push(createNode());
        } else if (selector === ".project-session-visibility-btn") {
            results.push(createNode({ textContent: match[1].replace(/<[^>]+>/g, "").trim() }));
        } else if (selector === ".session-rename-btn") {
            results.push(createNode({
                attributes: {
                    "data-session-id": match[1],
                    "data-session-metadata": decodeHtmlAttribute(match[2]),
                },
            }));
        } else if (selector === ".session-delete-btn") {
            results.push(createNode({ attributes: { "data-session-id": match[1] } }));
        } else if (selector === ".session-item") {
            results.push(createNode({
                className: match[1],
                attributes: {
                    "data-session-id": match[2],
                    "data-workspace-id": match[3],
                },
            }));
        } else if (selector === ".session-source-icon") {
            results.push(createNode());
        } else if (selector === ".project-title") {
            results.push(createNode({ textContent: match[1].replace(/<[^>]+>/g, "").trim() }));
        } else if (selector === ".session-id") {
            results.push(createNode({ textContent: match[1].replace(/<[^>]+>/g, "").trim() }));
        }
        match = pattern.exec(source);
    }
    return results;
}

function createNode({ className = "", textContent = "", attributes = {} } = {}) {
    const attributeStore = new Map(Object.entries(attributes));
    return {
        className,
        textContent,
        onclick: null,
        onkeydown: null,
        style: {},
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

function createCardElement() {
    let html = "";
    const cache = new Map();
    return {
        className: "",
        style: {},
        children: [],
        setAttribute() {
            return undefined;
        },
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
                cache.set(selector, parseElements(html, selector));
            }
            return cache.get(selector);
        },
    };
}

function createContainerElement() {
    let html = "";
    return {
        className: "",
        style: {},
        children: [],
        get innerHTML() {
            return html;
        },
        set innerHTML(value) {
            html = String(value);
            if (!html) {
                this.children = [];
            }
        },
        appendChild(child) {
            this.children.push(child);
            return child;
        },
        querySelector(selector) {
            return this.querySelectorAll(selector)[0] || null;
        },
        querySelectorAll(selector) {
            if (!selector.startsWith(".")) {
                return [];
            }
            const className = selector.slice(1);
            return this.children.filter(child => {
                const childClassName = String(child.className || "");
                return childClassName.split(/\s+/).includes(className);
            });
        },
    };
}

function createBasicElement() {
    return {
        style: {},
        innerHTML: "",
        textContent: "",
        children: [],
    };
}

export function createDomEnvironment() {
    const elements = new Map([
        ["projects-list", createContainerElement()],
        ["rounds-list", createBasicElement()],
        ["back-btn", createBasicElement()],
        ["chat-messages", createBasicElement()],
    ]);

    return {
        getElementById(id) {
            const element = elements.get(id);
            if (!element) {
                throw new Error(`Missing element: ${id}`);
            }
            return element;
        },
        createElement() {
            return createCardElement();
        },
        querySelector(selector) {
            if (selector === ".session-item") {
                const projectsList = elements.get("projects-list");
                for (const child of projectsList.children) {
                    const item = child.querySelector(".session-item");
                    if (item) {
                        return item;
                    }
                }
                return null;
            }
            return null;
        },
    };
}

export function installGlobals(documentEnv) {
    globalThis.document = documentEnv;
    els.projectsList = documentEnv.getElementById("projects-list");
    els.roundsList = documentEnv.getElementById("rounds-list");
    els.backBtn = documentEnv.getElementById("back-btn");
    els.chatMessages = documentEnv.getElementById("chat-messages");
}

export async function flushTasks() {
    await Promise.resolve();
    await new Promise(resolve => setTimeout(resolve, 0));
}
""".strip(),
        encoding="utf-8",
    )

    mock_feedback_path.write_text(
        """
export async function showConfirmDialog(options = {}) {
    globalThis.__confirmDialogCalls.push(options);
    if (Array.isArray(globalThis.__confirmDialogResponses) && globalThis.__confirmDialogResponses.length > 0) {
        return globalThis.__confirmDialogResponses.shift();
    }
    return true;
}

export async function showFormDialog(options = {}) {
    globalThis.__showFormDialogCalls.push(options);
    return globalThis.__showFormDialogResult ?? null;
}

export async function showTextInputDialog(options = {}) {
    if (options.title === "Rename Session") {
        return "Renamed Session";
    }
    if (options.title === "Fork Project") {
        return "Alpha Project Fork";
    }
    return "/work/Gamma Project";
}
""".strip(),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
const translations = {
    "sidebar.project": "Project",
    "sidebar.sort_name": "Sort by name",
    "sidebar.sort_recent": "Sort by recent",
    "sidebar.new_project": "New project",
    "sidebar.fork": "Fork",
    "sidebar.remove": "Remove",
    "sidebar.fork_project": "Fork Project",
    "sidebar.fork_project_message": "Enter the name for the forked project.",
    "sidebar.fork_project_placeholder": "Forked project name",
    "sidebar.remove_workspace": "Remove Workspace",
    "sidebar.remove_workspace_message": "Remove workspace {workspace}? This will also delete its sessions from the sidebar.",
    "sidebar.remove_project_worktree": "Remove Project Worktree",
    "sidebar.remove_project_worktree_message": "Delete the git worktree for {workspace} too? Choose Cancel to keep the worktree on disk.",
    "sidebar.delete_worktree": "Delete Worktree",
    "sidebar.keep_worktree": "Keep Worktree",
    "sidebar.no_projects_title": "No projects yet",
    "sidebar.no_projects_copy": "Add a project below to attach a workspace and start sessions.",
    "sidebar.workspace": "Workspace",
    "sidebar.project_options": "Project options",
    "sidebar.new_session": "New session",
    "sidebar.no_sessions": "No sessions yet",
    "settings.action.cancel": "Cancel",
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
        encoding="utf-8",
    )

    mock_logger_path.write_text(
        """
export function sysLog(message) {
    globalThis.__logs.push(String(message));
}
""".strip(),
        encoding="utf-8",
    )

    default_mock_api_source = """
const workspaces = [
    {
        workspace_id: "alpha-project",
        root_path: "/work/Alpha Project",
        updated_at: "2026-03-14T10:00:00Z",
        profile: {
            file_scope: {
                backend: "project",
            },
        },
    },
    {
        workspace_id: "beta-project",
        root_path: "/work/Beta Project",
        updated_at: "2026-03-13T10:00:00Z",
        profile: {
            file_scope: {
                backend: "project",
            },
        },
    },
];

const sessions = [
    { session_id: "session-11", workspace_id: "alpha-project", updated_at: "2026-03-14T10:11:00Z", pending_tool_approval_count: 0 },
    { session_id: "session-10", workspace_id: "alpha-project", updated_at: "2026-03-14T10:10:00Z", pending_tool_approval_count: 0 },
    { session_id: "session-9", workspace_id: "alpha-project", updated_at: "2026-03-14T10:09:00Z", pending_tool_approval_count: 0 },
    { session_id: "session-8", workspace_id: "alpha-project", updated_at: "2026-03-14T10:08:00Z", pending_tool_approval_count: 0 },
    { session_id: "session-7", workspace_id: "alpha-project", updated_at: "2026-03-14T10:07:00Z", pending_tool_approval_count: 0, metadata: { title: "Reply to greeting" } },
    { session_id: "session-6", workspace_id: "alpha-project", updated_at: "2026-03-14T10:06:00Z", pending_tool_approval_count: 0 },
    { session_id: "session-5", workspace_id: "alpha-project", updated_at: "2026-03-14T10:05:00Z", pending_tool_approval_count: 0 },
    { session_id: "session-4", workspace_id: "alpha-project", updated_at: "2026-03-14T10:04:00Z", pending_tool_approval_count: 0 },
    { session_id: "session-3", workspace_id: "alpha-project", updated_at: "2026-03-14T10:03:00Z", pending_tool_approval_count: 0 },
    { session_id: "session-2", workspace_id: "alpha-project", updated_at: "2026-03-14T10:02:00Z", pending_tool_approval_count: 0 },
    { session_id: "session-1", workspace_id: "alpha-project", updated_at: "2026-03-14T10:01:00Z", pending_tool_approval_count: 0 },
    { session_id: "beta-1", workspace_id: "beta-project", updated_at: "2026-03-13T10:01:00Z", pending_tool_approval_count: 0 },
];

export async function fetchWorkspaces() {
    return workspaces;
}

export async function fetchSessions() {
    return sessions;
}

export async function fetchAutomationProjects() {
    return [];
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function startNewSession(workspaceId) {
    globalThis.__createdSessionWorkspaceIds.push(workspaceId);
    const suffix = globalThis.__createdSessionWorkspaceIds.length;
    const session = {
        session_id: `session-new-${suffix}`,
        workspace_id: workspaceId,
        updated_at: "2026-03-14T11:00:00Z",
        pending_tool_approval_count: 0,
    };
    sessions.unshift(session);
    return session;
}

export async function updateSession(sessionId, metadata) {
    globalThis.__renameCalls.push({ sessionId, metadata });
    const session = sessions.find(item => item.session_id === sessionId);
    if (session) {
        session.metadata = metadata;
    }
    return { status: "ok" };
}

export async function pickWorkspace(rootPath = null) {
    if (rootPath === null) {
        const error = new Error("Native directory picker is unavailable");
        error.status = 503;
        error.detail = "Native directory picker is unavailable";
        throw error;
    }
    workspaces.push({
        workspace_id: "gamma-project",
        root_path: rootPath,
        updated_at: "2026-03-14T12:00:00Z",
        profile: {
            file_scope: {
                backend: "project",
            },
        },
    });
    return {
        workspace: workspaces[2],
    };
}

export async function forkWorkspace(workspaceId, name) {
    globalThis.__forkCalls.push({ workspaceId, name });
    workspaces.unshift({
        workspace_id: "alpha-project-fork",
        root_path: "/worktrees/alpha-project-fork",
        updated_at: "2026-03-14T12:30:00Z",
        profile: {
            file_scope: {
                backend: "git_worktree",
            },
        },
    });
    return workspaces[0];
}

export async function deleteSession() {
    return undefined;
}

export async function deleteWorkspace(workspaceId, options = {}) {
    globalThis.__deleteWorkspaceCalls.push({ workspaceId, options });
    return { status: "ok" };
}

export async function createAutomationProject() {
    throw new Error("not used");
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function disableAutomationProject() {
    return { status: "ok" };
}

export async function enableAutomationProject() {
    return { status: "ok" };
}

export async function runAutomationProject() {
    throw new Error("not used");
}
""".strip()
    mock_api_path.write_text(
        (mock_api_source or default_mock_api_source),
        encoding="utf-8",
    )

    mock_state_path.write_text(
        """
export const state = {
    currentSessionId: "session-7",
    currentWorkspaceId: "alpha-project",
    currentMainView: "session",
    currentProjectViewWorkspaceId: null,
};
""".strip(),
        encoding="utf-8",
    )

    mock_project_view_path.write_text(
        """
import { state } from "./mockState.mjs";

export async function openWorkspaceProjectView(workspace) {
    globalThis.__openedWorkspaceIds.push(workspace.workspace_id);
    state.currentMainView = "project";
    state.currentProjectViewWorkspaceId = workspace.workspace_id;
    state.currentWorkspaceId = workspace.workspace_id;
}

export async function openAutomationProjectView(project) {
    globalThis.__openedAutomationProjectIds.push(project.automation_project_id);
    state.currentMainView = "project";
}

export function hideProjectView() {
    globalThis.__hideProjectViewCalls += 1;
    state.currentMainView = "session";
    state.currentProjectViewWorkspaceId = null;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("./projectView.js", "./mockProjectView.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
import {{ createDomEnvironment, flushTasks, installGlobals }} from "./mockDom.mjs";

globalThis.__logs = [];
globalThis.__confirmDialogCalls = [];
globalThis.__confirmDialogResponses = [];
globalThis.__createdSessionWorkspaceIds = [];
globalThis.__deleteWorkspaceCalls = [];
globalThis.__forkCalls = [];
globalThis.__renameCalls = [];
globalThis.__selectedSessionIds = [];
globalThis.__openedWorkspaceIds = [];
globalThis.__openedAutomationProjectIds = [];
globalThis.__hideProjectViewCalls = 0;
globalThis.__showFormDialogResult = null;
globalThis.__showFormDialogCalls = [];
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
            "Node runner failed:\\n"
            f"STDOUT:\\n{completed.stdout}\\n"
            f"STDERR:\\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
