# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

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


def _run_sidebar_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "components" / "sidebar.js"

    module_under_test_path = tmp_path / "sidebar.mjs"
    mock_dom_path = tmp_path / "mockDom.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    mock_api_path = tmp_path / "mockApi.mjs"
    mock_state_path = tmp_path / "mockState.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_dom_path.write_text(
        """
export const els = {};

function parseElements(source, selector) {
    const results = [];
    const patterns = {
        ".project-toggle": /class="project-toggle"[^>]*aria-expanded="([^"]+)"[^>]*>/g,
        ".project-new-session-btn": /class="([^"]*project-new-session-btn[^"]*)"[^>]*>/g,
        ".project-session-visibility-btn": /class="project-session-visibility-btn"[^>]*>([\\s\\S]*?)<\\/button>/g,
        ".session-delete-btn": /class="session-delete-btn"[^>]*data-session-id="([^"]+)"[^>]*>/g,
        ".session-item": /class="([^"]*session-item[^"]*)"[^>]*data-session-id="([^"]+)"[^>]*data-workspace-id="([^"]+)"[^>]*>/g,
        ".project-title": /class="project-title"[^>]*>([\\s\\S]*?)<\\/span>/g,
        ".session-id": /class="session-id"[^>]*>([\\s\\S]*?)<\\/span>/g,
    };
    const pattern = patterns[selector];
    if (!pattern) {
        return results;
    }
    let match = pattern.exec(source);
    while (match) {
        if (selector === ".project-toggle") {
            results.push(createNode({ attributes: { "aria-expanded": match[1] } }));
        } else if (selector === ".project-new-session-btn") {
            results.push(createNode({ className: match[1] }));
        } else if (selector === ".project-session-visibility-btn") {
            results.push(createNode({ textContent: match[1].replace(/<[^>]+>/g, "").trim() }));
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
export async function showConfirmDialog() {
    return true;
}

export async function showTextInputDialog() {
    return "/work/Gamma Project";
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

    mock_api_path.write_text(
        """
const workspaces = [
    {
        workspace_id: "alpha-project",
        root_path: "/work/Alpha Project",
        updated_at: "2026-03-14T10:00:00Z",
    },
    {
        workspace_id: "beta-project",
        root_path: "/work/Beta Project",
        updated_at: "2026-03-13T10:00:00Z",
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
    });
    return {
        workspace: workspaces[2],
    };
}

export async function deleteSession() {
    return undefined;
}

export async function deleteWorkspace() {
    return { status: "ok" };
}
""".strip(),
        encoding="utf-8",
    )

    mock_state_path.write_text(
        """
export const state = {
    currentSessionId: "session-7",
    currentWorkspaceId: "alpha-project",
};
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../core/state.js", "./mockState.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
import {{ createDomEnvironment, flushTasks, installGlobals }} from "./mockDom.mjs";

globalThis.__logs = [];
globalThis.__createdSessionWorkspaceIds = [];
globalThis.__selectedSessionIds = [];
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
