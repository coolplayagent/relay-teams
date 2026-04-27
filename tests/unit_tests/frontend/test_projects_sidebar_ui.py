# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

import json
import subprocess

from .css_helpers import load_components_css


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
    openedNewSessionDraftWorkspaceIds: globalThis.__openedNewSessionDraftWorkspaceIds,
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
    assert payload["createdSessionWorkspaceIds"] == []
    assert payload["openedNewSessionDraftWorkspaceIds"] == [
        "alpha-project",
        "gamma-project",
    ]
    assert payload["selectedSessionIds"] == []
    assert payload["finalProjectCount"] == 3
    assert payload["initialFirstProjectTitle"] == "Alpha Project"
    assert payload["initialSecondProjectTitle"] == "Beta Project"
    assert payload["initialFirstSessionLabel"] == "New conversation"
    assert payload["finalFirstProjectTitle"] == "Gamma Project"
    assert payload["sortedFirstProjectTitle"] == "Alpha Project"


def test_projects_sidebar_renders_session_run_status_indicators(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        mock_api_source="""
const workspaces = [
    {
        workspace_id: "alpha-project",
        root_path: "/work/Alpha Project",
        updated_at: "2026-03-14T10:00:00Z",
        profile: { file_scope: { backend: "project" } },
    },
];

const sessions = [
    {
        session_id: "session-running",
        workspace_id: "alpha-project",
        updated_at: "2026-03-14T10:04:00Z",
        metadata: { title: "Running task" },
        has_active_run: true,
        active_run_status: "running",
    },
    {
        session_id: "session-queued",
        workspace_id: "alpha-project",
        updated_at: "2026-03-14T10:03:30Z",
        metadata: { title: "Queued task" },
        has_active_run: true,
        active_run_status: "queued",
    },
    {
        session_id: "session-stopped",
        workspace_id: "alpha-project",
        updated_at: "2026-03-14T10:03:20Z",
        metadata: { title: "Stopped task" },
        has_active_run: true,
        active_run_status: "stopped",
        has_unread_terminal_run: true,
        latest_terminal_run_status: "stopped",
    },
    {
        session_id: "session-failed",
        workspace_id: "alpha-project",
        updated_at: "2026-03-14T10:03:10Z",
        metadata: { title: "Failed task" },
        has_active_run: true,
        active_run_status: "failed",
        has_unread_terminal_run: true,
        latest_terminal_run_status: "failed",
    },
    {
        session_id: "session-unread",
        workspace_id: "alpha-project",
        updated_at: "2026-03-14T10:03:00Z",
        metadata: { title: "Finished task" },
        has_unread_terminal_run: true,
        latest_terminal_run_status: "completed",
    },
    {
        session_id: "session-7",
        workspace_id: "alpha-project",
        updated_at: "2026-03-14T10:02:00Z",
        metadata: { title: "Current task" },
        has_unread_terminal_run: true,
        latest_terminal_run_status: "failed",
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

export async function deleteSession() {
    return { status: "ok" };
}

export async function deleteWorkspace() {
    return { status: "ok" };
}

export async function forkWorkspace() {
    return {};
}

export async function pickWorkspace() {
    return { workspace_id: "alpha-project" };
}

export async function createAutomationProject() {
    return {};
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
""".strip(),
        runner_source="""
import { loadProjects } from "./sidebar.mjs";

installGlobals(createDomEnvironment());
await loadProjects();

const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const items = firstProject.querySelectorAll(".session-item").map(item => ({
    sessionId: item.getAttribute("data-session-id"),
    className: item.className,
}));

console.log(JSON.stringify({ items }));
""".strip(),
    )

    classes = {
        str(item["sessionId"]): str(item["className"])
        for item in cast(list[dict[str, object]], payload["items"])
    }
    assert "has-run-indicator-running" in classes["session-running"]
    assert "has-run-indicator-running" in classes["session-queued"]
    assert "has-run-indicator-stopped" in classes["session-stopped"]
    assert "has-run-indicator-running" not in classes["session-stopped"]
    assert "has-run-indicator-failed" in classes["session-failed"]
    assert "has-run-indicator-running" not in classes["session-failed"]
    assert "has-run-indicator-unread" in classes["session-unread"]
    assert "active" in classes["session-7"]
    assert "has-run-indicator-unread" not in classes["session-7"]


def test_projects_sidebar_renders_2000_sessions_without_full_dom_expansion(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        mock_api_source="""
const workspaces = [
    {
        workspace_id: "alpha-project",
        root_path: "/work/Alpha Project",
        updated_at: "2026-03-14T10:00:00Z",
        profile: { file_scope: { backend: "project" } },
    },
];

const sessions = Array.from({ length: 2000 }, (_, index) => ({
    session_id: `session-${String(index).padStart(4, "0")}`,
    workspace_id: "alpha-project",
    updated_at: new Date(Date.UTC(2026, 2, 14, 10, 0, index % 60)).toISOString(),
    metadata: { title: `Session title ${index}` },
    pending_tool_approval_count: 0,
}));

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

export async function deleteSession() {
    return { status: "ok" };
}

export async function deleteWorkspace() {
    return { status: "ok" };
}

export async function forkWorkspace() {
    return {};
}

export async function pickWorkspace() {
    return { workspace_id: "alpha-project" };
}

export async function createAutomationProject() {
    return {};
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
""".strip(),
        runner_source="""
import { loadProjects } from "./sidebar.mjs";

installGlobals(createDomEnvironment());
const started = performance.now();
await loadProjects();
const elapsedMs = performance.now() - started;

const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const visibleSessionCount = firstProject.querySelectorAll(".session-item").length;
const visibilityLabel = firstProject.querySelector(".project-session-visibility-btn").textContent;

console.log(JSON.stringify({
    elapsedMs,
    visibleSessionCount,
    visibilityLabel,
}));
""".strip(),
    )

    assert payload["visibleSessionCount"] == 10
    assert payload["visibilityLabel"] == "Show all (2000)"
    elapsed_ms = payload["elapsedMs"]
    assert isinstance(elapsed_ms, int | float)
    assert elapsed_ms < 300


def test_projects_sidebar_clears_unread_indicator_immediately_on_session_click(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        mock_api_source="""
const workspaces = [
    {
        workspace_id: "alpha-project",
        root_path: "/work/Alpha Project",
        updated_at: "2026-03-14T10:00:00Z",
        profile: { file_scope: { backend: "project" } },
    },
];

const sessions = [
    {
        session_id: "session-unread",
        workspace_id: "alpha-project",
        updated_at: "2026-03-14T10:03:00Z",
        metadata: { title: "Finished task" },
        has_unread_terminal_run: true,
        latest_terminal_run_status: "completed",
    },
    {
        session_id: "session-7",
        workspace_id: "alpha-project",
        updated_at: "2026-03-14T10:02:00Z",
        metadata: { title: "Current task" },
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

export async function deleteSession() {
    return { status: "ok" };
}

export async function deleteWorkspace() {
    return { status: "ok" };
}

export async function forkWorkspace() {
    return {};
}

export async function pickWorkspace() {
    return { workspace_id: "alpha-project" };
}

export async function createAutomationProject() {
    return {};
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
""".strip(),
        runner_source="""
import {
    loadProjects,
    setSelectSessionHandler,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());
setSelectSessionHandler(async (sessionId) => {
    globalThis.__selectedSessionIds.push(sessionId);
    await new Promise(resolve => setTimeout(resolve, 20));
});

await loadProjects();
const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const unreadItem = firstProject.querySelectorAll(".session-item")
    .find(item => item.getAttribute("data-session-id") === "session-unread");

unreadItem.onclick();
const immediateClassName = unreadItem.className;

console.log(JSON.stringify({
    immediateClassName,
    selectedSessionIds: globalThis.__selectedSessionIds,
}));
""".strip(),
    )

    class_name = str(payload["immediateClassName"])
    assert "active" in class_name
    assert "has-run-indicator-unread" not in class_name
    assert "session-run-indicator-viewed" in class_name
    assert payload["selectedSessionIds"] == ["session-unread"]


def test_projects_sidebar_keeps_latest_rapid_session_click_active(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
    setSelectSessionHandler,
} from "./sidebar.mjs";
import { state } from "./mockState.mjs";

installGlobals(createDomEnvironment());
setSelectSessionHandler(async (sessionId) => {
    globalThis.__selectedSessionIds.push(sessionId);
    await new Promise(resolve => setTimeout(resolve, sessionId === "session-11" ? 20 : 0));
});

await loadProjects();
const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const items = firstProject.querySelectorAll(".session-item");
const firstItem = items.find(item => item.getAttribute("data-session-id") === "session-11");
const secondItem = items.find(item => item.getAttribute("data-session-id") === "session-10");

firstItem.onclick();
secondItem.onclick();
const immediateFirstClassName = firstItem.className;
const immediateSecondClassName = secondItem.className;
await flushTasks();
await new Promise(resolve => setTimeout(resolve, 30));
await flushTasks();

console.log(JSON.stringify({
    immediateFirstClassName,
    immediateSecondClassName,
    finalFirstClassName: firstItem.className,
    finalSecondClassName: secondItem.className,
    selectedSessionIds: globalThis.__selectedSessionIds,
    currentSessionId: state.currentSessionId,
}));
""".strip(),
    )

    assert "active" not in str(payload["immediateFirstClassName"]).split()
    assert "active" in str(payload["immediateSecondClassName"]).split()
    assert "active" not in str(payload["finalFirstClassName"]).split()
    assert "active" in str(payload["finalSecondClassName"]).split()
    assert payload["selectedSessionIds"] == ["session-11", "session-10"]
    assert payload["currentSessionId"] == "session-10"


def test_projects_sidebar_session_activation_animation_ignores_old_timeout(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
    setSelectSessionHandler,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());
setSelectSessionHandler(async () => {});

await loadProjects();
const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const item = firstProject.querySelectorAll(".session-item")
    .find(candidate => candidate.getAttribute("data-session-id") === "session-11");

const originalSetTimeout = globalThis.setTimeout;
const scheduledTimeouts = [];
globalThis.setTimeout = (callback, delay) => {
    scheduledTimeouts.push({ callback, delay });
    return scheduledTimeouts.length;
};

item.onclick();
const afterFirstClick = item.className;
item.onclick();
const afterSecondClick = item.className;
scheduledTimeouts[0].callback();
const afterOldTimeout = item.className;
scheduledTimeouts[1].callback();
const afterNewTimeout = item.className;
globalThis.setTimeout = originalSetTimeout;

console.log(JSON.stringify({
    afterFirstClick,
    afterSecondClick,
    afterOldTimeout,
    afterNewTimeout,
    timeoutDelays: scheduledTimeouts.map(item => item.delay),
}));
""".strip(),
    )

    assert "session-item-activating" in str(payload["afterFirstClick"]).split()
    assert "session-item-activating" in str(payload["afterSecondClick"]).split()
    assert "session-item-activating" in str(payload["afterOldTimeout"]).split()
    assert "session-item-activating" not in str(payload["afterNewTimeout"]).split()
    timeout_delays = cast(list[int], payload["timeoutDelays"])
    assert timeout_delays[-2:] == [140, 140]


def test_projects_sidebar_new_session_keeps_session_visibility_collapsed_and_declares_animations(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
    setSelectSessionHandler,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());
setSelectSessionHandler(async (sessionId) => {
    globalThis.__selectedSessionIds.push(sessionId);
});

await loadProjects();
const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];

firstProject.querySelector(".project-session-visibility-btn").onclick();
await flushTasks();
const expandedProject = projectsList.children.filter(child => child.className === "project-card")[0];
expandedProject.querySelector(".project-session-visibility-btn").onclick();
await flushTasks();
const recollapsedProject = projectsList.children.filter(child => child.className === "project-card")[0];
const beforeCount = recollapsedProject.querySelectorAll(".session-item").length;
const beforeVisibilityLabel = recollapsedProject.querySelector(".project-session-visibility-btn").textContent;

recollapsedProject.querySelectorAll(".project-new-session-btn")[0].onclick();
await flushTasks();
await flushTasks();

const refreshedProject = projectsList.children.filter(child => child.className === "project-card")[0];
const afterCount = refreshedProject.querySelectorAll(".session-item").length;
const afterVisibilityLabel = refreshedProject.querySelector(".project-session-visibility-btn").textContent;

console.log(JSON.stringify({
    beforeCount,
    beforeVisibilityLabel,
    afterCount,
    afterVisibilityLabel,
    openedNewSessionDraftWorkspaceIds: globalThis.__openedNewSessionDraftWorkspaceIds,
    selectedSessionIds: globalThis.__selectedSessionIds,
}));
""".strip(),
    )

    repo_root = Path(__file__).resolve().parents[3]
    sidebar_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sidebar.js"
    ).read_text(encoding="utf-8")
    components_base_css = load_components_css()

    assert payload["beforeCount"] == 10
    assert payload["beforeVisibilityLabel"] == "Show all (11)"
    assert payload["afterCount"] == 10
    assert payload["afterVisibilityLabel"] == "Show all (11)"
    assert payload["openedNewSessionDraftWorkspaceIds"] == ["alpha-project"]
    assert payload["selectedSessionIds"] == []
    assert (
        "expandedProjectSessionIds.add(groupKey('workspace', targetWorkspaceId));"
        not in sidebar_script
    )
    assert "let pendingSessionAnimation = null;" in sidebar_script
    assert "function animateSessionItem(item, animation) {" in sidebar_script
    assert "openNewSessionDraftFromSidebar(projectId);" in sidebar_script
    assert "animateSessionItem(sessionItem, 'removing');" in sidebar_script
    assert (
        "optimisticActivateSession(sessionId, { animate: true, item: button, updateState: false });"
        in sidebar_script
    )
    assert "await selectSessionById(sessionId, selectionToken);" in sidebar_script
    assert (
        "void selectSessionById(sessionId, selectionToken).catch(error => {"
        in sidebar_script
    )
    assert (
        "document.addEventListener('agent-teams-session-selected', () => void loadProjects());"
        not in sidebar_script
    )
    assert "agent-teams-session-activated" in sidebar_script
    assert "void markSelectedSessionTerminalViewed(sessionId, selectionSignal);" in (
        repo_root / "frontend" / "dist" / "js" / "app" / "session.js"
    ).read_text(encoding="utf-8")
    assert ".session-item-entering {" in components_base_css
    assert ".session-item-removing {" in components_base_css
    assert ".session-item-activating {" in components_base_css
    assert "@keyframes sessionItemEnter {" in components_base_css
    assert "@keyframes sessionItemRemove {" in components_base_css
    assert "@keyframes sessionItemActivate {" in components_base_css
    assert "pendingSessionVisibilityAnimation" in sidebar_script
    assert "SESSION_VISIBILITY_ANIMATED_ITEM_LIMIT = 24" in sidebar_script
    assert "session-entry-visible-entering" in sidebar_script
    assert "session-entry-collapsing" in sidebar_script
    assert "button.scrollIntoView?.({ block: 'nearest' });" in (
        repo_root / "frontend" / "dist" / "js" / "components" / "sessionSearch.js"
    ).read_text(encoding="utf-8")
    assert (
        "function renderProjectsWorkspaceShell(toolbar, contentNodes)" in sidebar_script
    )
    assert "els.projectsList.style.display = 'flex';" in sidebar_script
    assert "syncSidebarStickyOffsets" not in sidebar_script
    assert "--sidebar-feature-sticky-height" not in components_base_css
    assert ".projects-workspace-shell {" in components_base_css
    assert "flex: 1 1 0;" in components_base_css
    assert ".projects-workspace-scroll {" in components_base_css
    assert "scrollbar-width: thin;" in components_base_css
    assert "overscroll-behavior: contain;" in components_base_css
    assert (
        ".projects-workspace-scroll::-webkit-scrollbar-thumb {" in components_base_css
    )
    assert ".projects-list::-webkit-scrollbar-thumb {" not in components_base_css
    assert ".home-feature-section {\n    position: sticky;" not in components_base_css
    assert ".projects-toolbar {\n    position: sticky;" not in components_base_css
    assert ".project-session-list.is-visibility-expanding" in components_base_css
    assert ".project-session-list.is-visibility-collapsing" in components_base_css
    assert (
        ".project-session-list.is-visibility-height-collapsing" in components_base_css
    )
    assert "projectSessionVisibilitySettle" not in components_base_css
    assert "@keyframes projectSessionVisibilityEnter {" in components_base_css
    assert "@keyframes projectSessionVisibilityExit {" in components_base_css
    assert ".session-search-root {" in components_base_css
    assert ".session-search-result {" in components_base_css
    assert ".session-search-mark {" in components_base_css
    assert (
        "background: color-mix(in srgb, var(--bg-surface) 96%, transparent);"
        in components_base_css
    )
    assert "font-size: 0.84rem;" in components_base_css
    assert (
        ".session-search-results.is-animated .session-search-result {"
        in components_base_css
    )
    assert "@keyframes sessionSearchResultEnter {" in components_base_css
    assert "has_unread_terminal_run" in sidebar_script
    assert "has-run-indicator-${indicatorType}" in sidebar_script
    assert ".session-run-indicator-running" in components_base_css
    assert ".session-run-indicator-unread" in components_base_css
    assert ".session-run-indicator-stopped" in components_base_css
    assert ".session-run-indicator-failed" in components_base_css
    assert ".session-item.active.has-run-indicator-unread" in components_base_css
    assert "@keyframes sessionRunIndicatorSpin {" in components_base_css
    assert "@media (prefers-reduced-motion: reduce)" in components_base_css
    assert 'class="session-label-text" title=' in sidebar_script


def test_projects_sidebar_render_signature_does_not_recurse_serialized_markup() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    sidebar_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sidebar.js"
    ).read_text(encoding="utf-8")

    assert "const serializedContent = innerHtml || textContent;" in sidebar_script
    assert (
        "const childSignature = serializedContent\n"
        "        ? ''\n"
        "        : Array.from(node.children || []).map(renderNodeSignature).join('::');"
    ) in sidebar_script
    assert (
        "`${className}::${innerHtml}::${textContent}::${childSignature}`"
        not in sidebar_script
    )


def test_session_search_filters_and_highlights_results(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sessionSearch.js"
    )
    module_path = tmp_path / "sessionSearch.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    runner_path = tmp_path / "runner.mjs"
    module_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            "../utils/i18n.js",
            "./mockI18n.mjs",
        ),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
const translations = {
    "sidebar.untitled_session": "New conversation",
    "sidebar.project": "Project",
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
import {
    buildSessionSearchResults,
    highlightSessionSearchText,
} from "./sessionSearch.mjs";

const entries = [
    {
        sessionId: "hidden-42",
        title: "Review <agent> permissions",
        projectLabel: "agent-teams",
        updatedAtMs: 3,
        groupKey: "workspace:agent-teams",
    },
    {
        sessionId: "session-2",
        title: "Build workspace client",
        projectLabel: "workspace",
        updatedAtMs: 2,
        groupKey: "workspace:workspace",
    },
    {
        sessionId: "session-3",
        title: "Other task",
        projectLabel: "agent-teams",
        updatedAtMs: 1,
        groupKey: "workspace:agent-teams",
    },
];

const manyEntries = Array.from({ length: 24 }, (_, index) => ({
    sessionId: `bulk-${index}`,
    title: `Bulk session ${index}`,
    projectLabel: "agent-teams",
    updatedAtMs: index,
    groupKey: "workspace:agent-teams",
}));
const largeEntries = Array.from({ length: 2000 }, (_, index) => ({
    sessionId: `large-${index}`,
    title: index === 1999 ? "Needle session 1999" : `Large session ${index}`,
    projectLabel: index % 2 === 0 ? "agent-teams" : "workspace",
    updatedAtMs: index,
    groupKey: "workspace:agent-teams",
}));

const agentResults = buildSessionSearchResults(entries, "agent");
const projectResults = buildSessionSearchResults(entries, "workspace");
const idResults = buildSessionSearchResults(entries, "hidden-42");
const recentResults = buildSessionSearchResults(entries, "");
const noMatches = buildSessionSearchResults(entries, "missing");
const manyResults = buildSessionSearchResults(manyEntries, "bulk");
const largeStarted = performance.now();
const largeResults = buildSessionSearchResults(largeEntries, "needle");
const largeElapsedMs = performance.now() - largeStarted;
const highlighted = highlightSessionSearchText("Review <agent> permissions", "agent");

console.log(JSON.stringify({
    agentResultIds: agentResults.map(item => item.sessionId),
    firstTitleHtml: agentResults[0].titleHtml,
    projectResultIds: projectResults.map(item => item.sessionId),
    idResultIds: idResults.map(item => item.sessionId),
    recentResultIds: recentResults.map(item => item.sessionId),
    noMatchCount: noMatches.length,
    manyResultCount: manyResults.length,
    manyShortcutCount: manyResults.filter(item => item.shortcut).length,
    ninthShortcut: manyResults[8]?.shortcut || "",
    tenthShortcut: manyResults[9]?.shortcut || "",
    largeFirstResultId: largeResults[0]?.sessionId || "",
    largeElapsedMs,
    highlighted,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=3,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)

    assert payload["agentResultIds"] == ["hidden-42", "session-3"]
    assert (
        payload["firstTitleHtml"]
        == 'Review &lt;<mark class="session-search-mark">agent</mark>&gt; permissions'
    )
    assert payload["projectResultIds"] == ["session-2"]
    assert payload["idResultIds"] == ["hidden-42"]
    assert payload["recentResultIds"] == ["hidden-42", "session-2", "session-3"]
    assert payload["noMatchCount"] == 0
    assert payload["manyResultCount"] == 20
    assert payload["manyShortcutCount"] == 9
    assert payload["ninthShortcut"]
    assert payload["tenthShortcut"] == ""
    assert payload["largeFirstResultId"] == "large-1999"
    large_elapsed_ms = payload["largeElapsedMs"]
    assert isinstance(large_elapsed_ms, int | float)
    assert large_elapsed_ms < 80
    assert (
        payload["highlighted"]
        == 'Review &lt;<mark class="session-search-mark">agent</mark>&gt; permissions'
    )
    source_text = source_path.read_text(encoding="utf-8")
    css_text = (
        repo_root / "frontend" / "dist" / "css" / "components" / "session-search.css"
    ).read_text(encoding="utf-8")
    assert "updateActiveResultVisuals();" in source_text
    assert "let selecting = false;" in source_text
    assert "setSearchSelecting(true, activeIndex);" in source_text
    assert "await selectHandler(result);\n        closeSessionSearch();" in source_text
    assert 'class="session-search-result-marker" aria-hidden="true">-' in source_text
    assert "session-search-result-icon" not in source_text
    assert '<svg viewBox="0 0 24 24"' not in source_text
    assert "const shortcutClass = result.shortcut" in source_text
    assert "const shortcutHtml = result.shortcut" in source_text
    assert "session-search-shortcut is-empty" in source_text
    assert 'title="${escapeHtml(result.title)}"' in source_text
    assert 'title="${escapeHtml(result.projectLabel)}"' in source_text
    assert (
        "grid-template-columns: 0.7rem minmax(0, 1fr) minmax(5.25rem, 6.75rem) 2.55rem;"
        in css_text
    )
    assert ".session-search-result.has-shortcut {" not in css_text
    assert ".session-search-results::-webkit-scrollbar {" in css_text
    assert "scrollbar-width: none;" in css_text
    assert ".session-search-result.is-selecting {" in css_text
    assert ".session-search-shortcut.is-empty {" in css_text
    assert "minmax(5.8rem, 9rem)" not in css_text
    assert ".session-search-results.is-animated .session-search-result {" in css_text
    assert ".session-search-results.is-searching" not in css_text


def test_projects_sidebar_renders_normal_mode_subagents_as_child_sessions(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

globalThis.__sessionSubagentSessionsMap = {
    "session-1": [
        {
            sessionId: "session-1",
            instanceId: "inst-sub-1",
            roleId: "Explorer",
            runId: "subagent_run_1",
            title: "Explore history",
            updatedAt: "2026-03-14T10:12:00Z",
        },
        {
            sessionId: "session-1",
            instanceId: "inst-sub-2",
            roleId: "Crafter",
            runId: "subagent_run_2",
            title: "Draft summary",
            updatedAt: "2026-03-14T10:13:00Z",
        },
    ],
};
globalThis.__expandedSubagentSessionIds.add("session-1");
globalThis.__activeSubagentSession = {
    sessionId: "session-1",
    instanceId: "inst-sub-2",
};

await loadProjects();
const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const toggle = firstProject.querySelector(".session-subagents-toggle");
toggle.onclick({ preventDefault() {}, stopPropagation() {} });
const childItems = firstProject.querySelectorAll(".session-subagent-item");
childItems[1].onclick({ preventDefault() {}, stopPropagation() {} });

console.log(JSON.stringify({
    toggleCalls: globalThis.__toggleSubagentSessionListCalls,
    childCount: childItems.length,
    firstChildLabel: childItems[0].textContent,
    secondChildActive: childItems[1].className.includes("active"),
    dispatchedEvents: globalThis.__documentDispatches,
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
        session_id: "session-1",
        workspace_id: "alpha-project",
        session_mode: "normal",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        metadata: { title: "Root session" },
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

    repo_root = Path(__file__).resolve().parents[3]
    sidebar_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sidebar.js"
    ).read_text(encoding="utf-8")
    components_css = load_components_css()

    assert payload["toggleCalls"] == ["session-1"]
    assert payload["childCount"] == 2
    assert payload["firstChildLabel"] == "Explorer - inst-sub"
    assert payload["secondChildActive"] is True
    assert "session-subagent-tree" not in sidebar_script
    assert ".projects-list .session-subagent-list::before {" in components_css
    dispatched_events = cast(list[object], payload["dispatchedEvents"])
    assert dispatched_events[-1] == {
        "type": "agent-teams-select-subagent-session",
        "detail": {
            "sessionId": "session-1",
            "subagent": {
                "instanceId": "inst-sub-2",
                "roleId": "Crafter",
                "runId": "subagent_run_2",
                "title": "Draft summary",
            },
        },
    }


def test_projects_sidebar_subagent_children_keep_state_for_transition_classes(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

globalThis.__sessionSubagentSessionsMap = {
    "session-1": [
        {
            sessionId: "session-1",
            instanceId: "inst-sub-1",
            roleId: "Explorer",
            runId: "subagent_run_1",
            title: "Explore history",
            updatedAt: "2026-03-14T10:12:00Z",
        },
        {
            sessionId: "session-1",
            instanceId: "inst-sub-2",
            roleId: "Crafter",
            runId: "subagent_run_2",
            title: "Draft summary",
            updatedAt: "2026-03-14T10:13:00Z",
        },
    ],
};
globalThis.__expandedSubagentSessionIds.add("session-1");

await loadProjects();

let projectsList = document.getElementById("projects-list");
let firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const firstRenderList = firstProject.querySelector(".session-subagent-list");

globalThis.__expandedSubagentSessionIds.delete("session-1");
await loadProjects();
await flushTasks();

projectsList = document.getElementById("projects-list");
firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const collapsedRenderList = firstProject.querySelector(".session-subagent-list");

globalThis.__expandedSubagentSessionIds.add("session-1");
await loadProjects();
await flushTasks();

projectsList = document.getElementById("projects-list");
firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const reopenRenderList = firstProject.querySelector(".session-subagent-list");

console.log(JSON.stringify({
    firstRenderClass: firstRenderList.className,
    collapsedRenderClass: collapsedRenderList.className,
    collapsedAria: collapsedRenderList.getAttribute("aria-hidden"),
    reopenedRenderClass: reopenRenderList.className,
    collapsedChildCount: firstProject.querySelectorAll(".session-subagent-item").length,
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
        session_id: "session-1",
        workspace_id: "alpha-project",
        session_mode: "normal",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        metadata: { title: "Root session" },
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

    repo_root = Path(__file__).resolve().parents[3]
    sidebar_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sidebar.js"
    ).read_text(encoding="utf-8")
    components_css = load_components_css()

    assert "is-expanded" in str(payload["firstRenderClass"])
    assert "is-collapsed" in str(payload["collapsedRenderClass"])
    assert payload["collapsedAria"] == "true"
    assert "is-expanded" in str(payload["reopenedRenderClass"])
    assert payload["collapsedChildCount"] == 2
    assert ".session-subagent-list.is-collapsed" in components_css
    assert ".session-subagent-list.is-expanded" in components_css
    assert "function syncSubagentSessionListVisualState" in sidebar_script


def test_projects_sidebar_uses_session_summary_for_subagent_toggle_without_prefetch(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

globalThis.__sessionSubagentFetchResults = {
    "session-1": [
        {
            sessionId: "session-1",
            instanceId: "inst-sub-1",
            roleId: "Explorer",
            runId: "subagent_run_1",
            title: "Explore history",
            updatedAt: "2026-03-14T10:12:00Z",
        },
    ],
    "session-2": [],
};

await loadProjects();

const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const toggles = firstProject.querySelectorAll(".session-subagents-toggle");

console.log(JSON.stringify({
    ensureCalls: globalThis.__ensureSessionSubagentsCalls,
    toggleCount: toggles.length,
    firstToggleSessionId: toggles[0]?.getAttribute("data-session-id") || null,
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
        session_id: "session-1",
        workspace_id: "alpha-project",
        session_mode: "normal",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        subagent_session_count: 1,
        metadata: { title: "Root session 1" },
    },
    {
        session_id: "session-2",
        workspace_id: "alpha-project",
        session_mode: "normal",
        updated_at: "2026-03-14T10:10:00Z",
        pending_tool_approval_count: 0,
        subagent_session_count: 0,
        metadata: { title: "Root session 2" },
    },
    {
        session_id: "session-3",
        workspace_id: "alpha-project",
        session_mode: "orchestration",
        updated_at: "2026-03-14T10:09:00Z",
        pending_tool_approval_count: 0,
        metadata: { title: "Coordinator session" },
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

    repo_root = Path(__file__).resolve().parents[3]
    sidebar_script = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sidebar.js"
    ).read_text(encoding="utf-8")

    ensure_calls = cast(list[str], payload["ensureCalls"])
    assert ensure_calls == []
    assert payload["toggleCount"] == 1
    assert payload["firstToggleSessionId"] == "session-1"
    assert (
        "${renderSubagentToggle(session)}\n"
        '                                            <span class="session-id">'
    ) in sidebar_script


def test_projects_sidebar_hides_stale_summary_toggle_after_empty_subagent_list_load(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

globalThis.__sessionSubagentSessionsMap = {
    "session-1": [],
};
globalThis.__expandedSubagentSessionIds.add("session-1");

await loadProjects();

const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const toggles = firstProject.querySelectorAll(".session-subagents-toggle");

console.log(JSON.stringify({
    toggleCount: toggles.length,
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
        session_id: "session-1",
        workspace_id: "alpha-project",
        session_mode: "normal",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        subagent_session_count: 1,
        metadata: { title: "Root session 1" },
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

    assert payload["toggleCount"] == 0


def test_projects_sidebar_deletes_subagent_child_session_and_returns_to_parent(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
    setSelectSessionHandler,
} from "./sidebar.mjs";
import { state } from "./mockState.mjs";

globalThis.__sessionSubagentSessionsMap = {
    "session-1": [
        {
            sessionId: "session-1",
            instanceId: "inst-sub-1",
            roleId: "Explorer",
            runId: "subagent_run_1",
            title: "Explore history",
            updatedAt: "2026-03-14T10:12:00Z",
        },
    ],
};
globalThis.__expandedSubagentSessionIds.add("session-1");
globalThis.__activeSubagentSession = {
    sessionId: "session-1",
    instanceId: "inst-sub-1",
};
state.currentSessionId = "session-1";

setSelectSessionHandler(async (sessionId) => {
    globalThis.__selectedSessionIds.push(sessionId);
});

await loadProjects();
const projectsList = document.getElementById("projects-list");
const firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
const deleteButton = firstProject.querySelector(".session-subagent-delete-btn");
deleteButton.onclick({ stopPropagation() {} });
await flushTasks();
await new Promise(resolve => setTimeout(resolve, 220));
await flushTasks();

console.log(JSON.stringify({
    deleteCalls: globalThis.__deleteSubagentCalls,
    closedRunIds: globalThis.__closedSubagentRunIds,
    selectedSessionIds: globalThis.__selectedSessionIds,
    remainingChildCount: (globalThis.__sessionSubagentSessionsMap["session-1"] || []).length,
    activeSubagentSession: globalThis.__activeSubagentSession,
    confirmCalls: globalThis.__confirmDialogCalls,
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
        session_id: "session-1",
        workspace_id: "alpha-project",
        session_mode: "normal",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        metadata: { title: "Root session" },
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

export async function deleteSessionSubagent(sessionId, instanceId) {
    globalThis.__deleteSubagentCalls.push({ sessionId, instanceId });
    return { status: "ok" };
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

    assert payload["deleteCalls"] == [
        {
            "sessionId": "session-1",
            "instanceId": "inst-sub-1",
        }
    ]
    assert payload["closedRunIds"] == ["subagent_run_1"]
    assert payload["selectedSessionIds"] == ["session-1"]
    assert payload["remainingChildCount"] == 0
    assert payload["activeSubagentSession"] is None
    confirm_calls = cast(list[dict[str, object]], payload["confirmCalls"])
    assert confirm_calls[0]["tone"] == "warning"


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
            "metadata": {"title": "Renamed Session", "title_source": "manual"},
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


def test_projects_sidebar_marks_automation_sessions_with_icon_and_automation_class(
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
        session_id: "session-automation",
        workspace_id: "alpha-project",
        project_kind: "automation",
        project_id: "aut_123",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        metadata: {
            title: "Daily Briefing run 2026-03-14 18:30",
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

    assert "session-item-automation" in str(payload["firstSessionClassName"])
    assert payload["firstSessionLabel"] == "Daily Briefing run 2026-03-14 18:30"
    assert payload["iconCount"] == 1


def test_projects_sidebar_shows_both_automation_and_im_session_indicators(
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
const iconCount = firstProject.querySelectorAll(".session-source-icon").length;

console.log(JSON.stringify({
    firstSessionClassName: firstSession.className,
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
        session_id: "session-bound-im",
        workspace_id: "alpha-project",
        project_kind: "workspace",
        project_id: "alpha-project",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        metadata: {
            title: "Release Updates",
            source_kind: "im",
        },
    },
];

export async function fetchWorkspaces() {
    return workspaces;
}

export async function fetchSessions() {
    return sessions;
}

export async function fetchAutomationProjects() {
    return [
        {
            automation_project_id: "aut_123",
            display_name: "Daily Briefing",
            name: "daily-briefing",
            status: "enabled",
            workspace_id: "alpha-project",
            delivery_binding: {
                provider: "feishu",
                trigger_id: "trg_feishu",
                tenant_key: "tenant-1",
                chat_id: "oc_123",
                session_id: "session-bound-im",
                chat_type: "dm",
            },
        },
    ];
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
    assert "session-item-automation" in str(payload["firstSessionClassName"])
    assert payload["iconCount"] == 2


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
globalThis.__requestAutomationProjectInputResult = {
    name: "daily-briefing",
    display_name: "Daily Briefing",
    workspace_id: "alpha-project",
    prompt: "Summarize the latest project changes.",
    cron_expression: "0 9 * * *",
    schedule_mode: "cron",
    timezone: "Asia/Shanghai",
    enabled: true,
    delivery_binding: {
        provider: "feishu",
        trigger_id: "trg_feishu",
        tenant_key: "tenant-1",
        chat_id: "oc_123",
        session_id: "session-im-1",
        chat_type: "group",
        source_label: "Release Updates",
    },
    delivery_events: ["started", "completed", "failed"],
};

await handleNewAutomationProjectClick();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    createPayload: globalThis.__createAutomationPayload,
    requestAutomationCalls: globalThis.__requestAutomationProjectInputCalls,
    runCalls: globalThis.__runAutomationProjectCalls || null,
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
    assert delivery_binding["trigger_id"] == "trg_feishu"
    assert delivery_binding["chat_id"] == "oc_123"
    assert delivery_binding["session_id"] == "session-im-1"
    assert delivery_events == [
        "started",
        "completed",
        "failed",
    ]
    assert create_payload["timezone"] == "Asia/Shanghai"
    assert payload["requestAutomationCalls"] == [{}]
    assert payload["runCalls"] is None


def test_projects_sidebar_renders_feature_navigation_ahead_of_workspace_cards(
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
const children = document.getElementById("projects-list").children;
const featureSection = children[0];
const projectCards = children.filter(child => String(child.className || "").includes("project-card"));
const featureItems = featureSection.querySelectorAll(".home-feature-item");

console.log(JSON.stringify({
    firstChildClassName: featureSection?.className || "",
    featureCount: featureItems.length,
    featureIds: featureItems.map(item => item.getAttribute("data-feature-id")),
    workspaceCardCount: projectCards.length,
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
        session_id: "session-im-1",
        workspace_id: "alpha-project",
        project_kind: "workspace",
        project_id: "alpha-project",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        metadata: {
            title: "feishu_main - Release Updates",
            source_kind: "im",
        },
    },
];

export async function fetchWorkspaces() {
    return workspaces;
}

export async function fetchSessions() {
    return sessions;
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

    assert payload["firstChildClassName"] == "home-feature-section"
    assert payload["featureCount"] == 3
    assert payload["featureIds"] == ["skills", "automation", "gateway"]
    assert payload["workspaceCardCount"] == 1


def test_projects_sidebar_uses_distinct_feature_icon_variants() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    sidebar_source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sidebar.js"
    ).read_text(encoding="utf-8")

    assert "home-feature-icon-svg-skills" in sidebar_source
    assert "home-feature-icon-svg-automation" in sidebar_source
    assert "home-feature-icon-svg-gateway" in sidebar_source


def test_projects_sidebar_opens_home_feature_views_from_feature_navigation(
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
const featureSection = document.getElementById("projects-list").children[0];
const featureItems = featureSection.querySelectorAll(".home-feature-item");
featureItems[0]?.onclick?.();
featureItems[1]?.onclick?.();
featureItems[2]?.onclick?.();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    openedSkillsFeatureCount: globalThis.__openedSkillsFeatureCount || 0,
    openedAutomationProjectIds: globalThis.__openedAutomationProjectIds,
    openedGatewayFeatureCount: globalThis.__openedGatewayFeatureCount || 0,
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
""".strip(),
    )

    assert payload["openedSkillsFeatureCount"] == 1
    assert payload["openedAutomationProjectIds"] == [""]
    assert payload["openedGatewayFeatureCount"] == 1


def test_projects_sidebar_primary_new_session_opens_draft_for_multiple_workspaces(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
    setSelectSessionHandler,
} from "./sidebar.mjs";
import { state } from "./mockState.mjs";

installGlobals(createDomEnvironment());
setSelectSessionHandler(async (sessionId) => {
    globalThis.__selectedSessionIds.push(sessionId);
});
state.currentWorkspaceId = null;
globalThis.__showFormDialogResult = { workspace_id: "beta-project" };

await loadProjects();
const featureSection = document.getElementById("projects-list").children[0];
featureSection.querySelector(".home-new-session-btn").onclick();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    createdSessionWorkspaceIds: globalThis.__createdSessionWorkspaceIds,
    openedNewSessionDraftWorkspaceIds: globalThis.__openedNewSessionDraftWorkspaceIds,
    selectedSessionIds: globalThis.__selectedSessionIds,
    showFormDialogTitles: globalThis.__showFormDialogCalls.map(item => item.title),
}));
""".strip(),
    )

    assert payload["createdSessionWorkspaceIds"] == []
    assert payload["openedNewSessionDraftWorkspaceIds"] == [""]
    assert payload["selectedSessionIds"] == []
    assert payload["showFormDialogTitles"] == []


def test_projects_sidebar_new_session_detaches_active_stream_without_session(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";
import { state } from "./mockState.mjs";

installGlobals(createDomEnvironment());
state.currentSessionId = null;
state.currentMainView = "project";
state.currentProjectViewWorkspaceId = "alpha-project";
state.activeEventSource = { close() {} };

await loadProjects();
const featureSection = document.getElementById("projects-list").children[0];
featureSection.querySelector(".home-new-session-btn").onclick();
await flushTasks();

console.log(JSON.stringify({
    detachStreamCalls: globalThis.__detachStreamCalls || 0,
    openedNewSessionDraftWorkspaceIds: globalThis.__openedNewSessionDraftWorkspaceIds,
    activeEventSource: state.activeEventSource,
}));
""".strip(),
    )

    assert payload["detachStreamCalls"] == 1
    assert payload["openedNewSessionDraftWorkspaceIds"] == ["alpha-project"]
    assert payload["activeEventSource"] is None


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
globalThis.__showFormDialogResult = { remove_directory: false };

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
    openedNewSessionDraftWorkspaceIds: globalThis.__openedNewSessionDraftWorkspaceIds,
    confirmDialogCalls: globalThis.__confirmDialogCalls,
    showFormDialogCalls: globalThis.__showFormDialogCalls.map(item => ({
        title: item.title,
        fieldId: item.fields?.[0]?.id || null,
        fieldLabel: item.fields?.[0]?.label || null,
    })),
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
            "options": {"removeDirectory": False},
        }
    ]
    assert payload["createdSessionWorkspaceIds"] == []
    assert payload["openedNewSessionDraftWorkspaceIds"] == ["alpha-project-fork"]
    assert payload["confirmDialogCalls"] == []
    assert payload["showFormDialogCalls"] == [
        {
            "title": "Remove Workspace",
            "fieldId": "remove_directory",
            "fieldLabel": "Also delete git worktree",
        }
    ]


def test_projects_sidebar_uses_workspace_id_titles_for_duplicate_workspace_paths(
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
const projectCards = projectsList.children.filter(child => child.className === "project-card");

console.log(JSON.stringify({
    projectTitles: projectCards.map(card => card.querySelector(".project-title").textContent),
}));
""".strip(),
        mock_api_source="""
const workspaces = [
    {
        workspace_id: "ui-multi-mount-demo",
        root_path: "/opt/workspace/agent-teams-main",
        updated_at: "2026-03-14T11:00:00Z",
        profile: {
            file_scope: {
                backend: "project",
            },
        },
    },
    {
        workspace_id: "agent-teams-main",
        root_path: "/opt/workspace/agent-teams-main",
        updated_at: "2026-03-14T10:00:00Z",
        profile: {
            file_scope: {
                backend: "project",
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

export async function createAutomationProject() {
    throw new Error("not used");
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function deleteSession() {
    return undefined;
}

export async function deleteWorkspace() {
    return { status: "ok" };
}

export async function disableAutomationProject() {
    return { status: "ok" };
}

export async function enableAutomationProject() {
    return { status: "ok" };
}

export async function forkWorkspace(workspaceId, name) {
    globalThis.__forkCalls.push({ workspaceId, name });
    return {
        workspace_id: `${workspaceId}-fork`,
        root_path: `/worktrees/${workspaceId}-fork`,
        updated_at: "2026-03-14T12:30:00Z",
        profile: {
            file_scope: {
                backend: "git_worktree",
            },
        },
    };
}

export async function pickWorkspace() {
    throw new Error("not used");
}

export async function startNewSession(workspaceId) {
    globalThis.__createdSessionWorkspaceIds.push(workspaceId);
    return {
        session_id: `session-new-${globalThis.__createdSessionWorkspaceIds.length}`,
        workspace_id: workspaceId,
        updated_at: "2026-03-14T11:00:00Z",
        pending_tool_approval_count: 0,
    };
}

export async function updateSession() {
    return { status: "ok" };
}
""".strip(),
    )

    assert payload["projectTitles"] == [
        "ui-multi-mount-demo",
        "agent-teams-main",
    ]


def test_projects_sidebar_can_keep_directory_when_removing_workspace(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());
globalThis.__showFormDialogResult = { remove_directory: false };

await loadProjects();
let projectsList = document.getElementById("projects-list");
let firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
firstProject.querySelector(".project-options-btn").onclick({ stopPropagation() {} });
await flushTasks();
projectsList = document.getElementById("projects-list");
firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
firstProject.querySelector(".project-remove-btn").onclick({ stopPropagation() {} });
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    deleteWorkspaceCalls: globalThis.__deleteWorkspaceCalls,
    confirmDialogCalls: globalThis.__confirmDialogCalls,
    showFormDialogCalls: globalThis.__showFormDialogCalls.map(item => ({
        title: item.title,
        fieldId: item.fields?.[0]?.id || null,
        fieldLabel: item.fields?.[0]?.label || null,
    })),
}));
""".strip(),
    )

    assert payload["deleteWorkspaceCalls"] == [
        {
            "workspaceId": "alpha-project",
            "options": {"removeDirectory": False},
        }
    ]
    assert payload["confirmDialogCalls"] == []
    assert payload["showFormDialogCalls"] == [
        {
            "title": "Remove Workspace",
            "fieldId": "remove_directory",
            "fieldLabel": "Also delete directory",
        }
    ]


def test_projects_sidebar_can_delete_directory_when_removing_workspace(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
} from "./sidebar.mjs";

installGlobals(createDomEnvironment());
globalThis.__showFormDialogResult = { remove_directory: true };

await loadProjects();
let projectsList = document.getElementById("projects-list");
let firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
firstProject.querySelector(".project-options-btn").onclick({ stopPropagation() {} });
await flushTasks();
projectsList = document.getElementById("projects-list");
firstProject = projectsList.children.filter(child => child.className === "project-card")[0];
firstProject.querySelector(".project-remove-btn").onclick({ stopPropagation() {} });
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    deleteWorkspaceCalls: globalThis.__deleteWorkspaceCalls,
}));
""".strip(),
    )

    assert payload["deleteWorkspaceCalls"] == [
        {
            "workspaceId": "alpha-project",
            "options": {"removeDirectory": True},
        }
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


def test_projects_sidebar_defers_passive_refresh_while_hovering(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
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
        session_id: "session-1",
        workspace_id: "alpha-project",
        updated_at: "2026-03-14T10:01:00Z",
        pending_tool_approval_count: 0,
    },
];

globalThis.__fetchCounts = {
    workspaces: 0,
    sessions: 0,
    automationProjects: 0,
};

export async function fetchWorkspaces() {
    globalThis.__fetchCounts.workspaces += 1;
    return workspaces;
}

export async function fetchSessions() {
    globalThis.__fetchCounts.sessions += 1;
    return sessions;
}

export async function fetchAutomationProjects() {
    globalThis.__fetchCounts.automationProjects += 1;
    return [];
}

export async function startNewSession() {
    throw new Error("not used");
}

export async function updateSession() {
    throw new Error("not used");
}

export async function pickWorkspace() {
    throw new Error("not used");
}

export async function forkWorkspace() {
    throw new Error("not used");
}

export async function deleteSession() {
    throw new Error("not used");
}

export async function deleteWorkspace() {
    throw new Error("not used");
}

export async function createAutomationProject() {
    throw new Error("not used");
}

export async function deleteAutomationProject() {
    throw new Error("not used");
}

export async function disableAutomationProject() {
    throw new Error("not used");
}

export async function enableAutomationProject() {
    throw new Error("not used");
}
""".strip(),
        runner_source="""
import {
    loadProjects,
    scheduleSessionsRefresh,
} from "./sidebar.mjs";

await loadProjects();
const initialCounts = { ...globalThis.__fetchCounts };

globalThis.__projectsListHover = true;
scheduleSessionsRefresh(0);
await new Promise(resolve => setTimeout(resolve, 80));
const hoveredCounts = { ...globalThis.__fetchCounts };

globalThis.__projectsListHover = false;
await new Promise(resolve => setTimeout(resolve, 320));
const settledCounts = { ...globalThis.__fetchCounts };

console.log(JSON.stringify({
    initialCounts,
    hoveredCounts,
    settledCounts,
}));
""".strip(),
    )

    assert payload["initialCounts"] == {
        "workspaces": 1,
        "sessions": 1,
        "automationProjects": 1,
    }
    assert payload["hoveredCounts"] == payload["initialCounts"]
    assert payload["settledCounts"] == {
        "workspaces": 2,
        "sessions": 2,
        "automationProjects": 2,
    }


def test_projects_sidebar_force_refreshes_subagent_events_even_when_hovering(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import { loadProjects } from "./sidebar.mjs";

await loadProjects();

const initialCounts = { ...globalThis.__fetchCounts };
globalThis.__projectsListHover = true;
document.dispatchEvent({
    type: "agent-teams-subagent-sessions-changed",
    detail: { forceRefresh: true },
});
await new Promise(resolve => setTimeout(resolve, 160));

const refreshedCounts = { ...globalThis.__fetchCounts };

console.log(JSON.stringify({
    initialCounts,
    refreshedCounts,
    sessionForceRefreshes: globalThis.__sessionForceRefreshes,
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
        session_id: "session-1",
        workspace_id: "alpha-project",
        session_mode: "normal",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        metadata: { title: "Root session" },
    },
];

globalThis.__fetchCounts = {
    workspaces: 0,
    sessions: 0,
    automationProjects: 0,
};
globalThis.__sessionForceRefreshes = [];

export async function fetchWorkspaces() {
    globalThis.__fetchCounts.workspaces += 1;
    return workspaces;
}

export async function fetchSessions(options = {}) {
    globalThis.__fetchCounts.sessions += 1;
    globalThis.__sessionForceRefreshes.push(options.forceRefresh === true);
    return sessions;
}

export async function fetchAutomationProjects() {
    globalThis.__fetchCounts.automationProjects += 1;
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

    assert payload["initialCounts"] == {
        "workspaces": 1,
        "sessions": 1,
        "automationProjects": 1,
    }
    assert payload["refreshedCounts"] == {
        "workspaces": 2,
        "sessions": 2,
        "automationProjects": 2,
    }
    assert payload["sessionForceRefreshes"] == [False, True]


def test_projects_sidebar_preserves_forced_refresh_across_debounce(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    loadProjects,
    scheduleSessionsRefresh,
} from "./sidebar.mjs";

await loadProjects();
scheduleSessionsRefresh(40, { forceRefresh: true });
scheduleSessionsRefresh(0);
await new Promise(resolve => setTimeout(resolve, 90));

console.log(JSON.stringify({
    fetchCounts: globalThis.__fetchCounts,
    sessionForceRefreshes: globalThis.__sessionForceRefreshes,
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
        session_id: "session-1",
        workspace_id: "alpha-project",
        session_mode: "normal",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        metadata: { title: "Root session" },
    },
];

globalThis.__fetchCounts = {
    workspaces: 0,
    sessions: 0,
    automationProjects: 0,
};
globalThis.__sessionForceRefreshes = [];

export async function fetchWorkspaces() {
    globalThis.__fetchCounts.workspaces += 1;
    return workspaces;
}

export async function fetchSessions(options = {}) {
    globalThis.__fetchCounts.sessions += 1;
    globalThis.__sessionForceRefreshes.push(options.forceRefresh === true);
    return sessions;
}

export async function fetchAutomationProjects() {
    globalThis.__fetchCounts.automationProjects += 1;
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

    assert payload["fetchCounts"] == {
        "workspaces": 2,
        "sessions": 2,
        "automationProjects": 2,
    }
    assert payload["sessionForceRefreshes"] == [False, True]


def test_projects_sidebar_defers_subagent_events_without_force_refresh_while_hovering(
    tmp_path: Path,
) -> None:
    payload = _run_sidebar_script(
        tmp_path=tmp_path,
        runner_source="""
import { loadProjects } from "./sidebar.mjs";

await loadProjects();

const initialCounts = { ...globalThis.__fetchCounts };
globalThis.__projectsListHover = true;
document.dispatchEvent({
    type: "agent-teams-subagent-sessions-changed",
});
await new Promise(resolve => setTimeout(resolve, 180));

const deferredCounts = { ...globalThis.__fetchCounts };

console.log(JSON.stringify({
    initialCounts,
    deferredCounts,
}));
process.exit(0);""".strip(),
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
        session_id: "session-1",
        workspace_id: "alpha-project",
        session_mode: "normal",
        updated_at: "2026-03-14T10:11:00Z",
        pending_tool_approval_count: 0,
        metadata: { title: "Root session" },
    },
];

globalThis.__fetchCounts = {
    workspaces: 0,
    sessions: 0,
    automationProjects: 0,
};

export async function fetchWorkspaces() {
    globalThis.__fetchCounts.workspaces += 1;
    return workspaces;
}

export async function fetchSessions() {
    globalThis.__fetchCounts.sessions += 1;
    return sessions;
}

export async function fetchAutomationProjects() {
    globalThis.__fetchCounts.automationProjects += 1;
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
}""".strip(),
    )

    assert payload["initialCounts"] == {
        "workspaces": 1,
        "sessions": 1,
        "automationProjects": 1,
    }
    assert payload["deferredCounts"] == {
        "workspaces": 1,
        "sessions": 1,
        "automationProjects": 1,
    }


def test_projects_sidebar_hover_hint_preserves_project_action_space() -> None:
    components_css = load_components_css()

    actions_start = components_css.index(".projects-list .project-actions {")
    actions_end = components_css.index(
        ".projects-list .project-row:hover .project-actions,",
        actions_start,
    )
    actions_rule = components_css[actions_start:actions_end]

    hint_start = components_css.index(".projects-list .project-path-hint {")
    hint_end = components_css.index(
        ".projects-list .project-path-hint.is-measuring",
        hint_start,
    )
    hint_rule = components_css[hint_start:hint_end]

    assert "position: relative;" in actions_rule
    assert "z-index: 8;" in actions_rule
    assert "position: fixed;" in hint_rule
    assert "width: auto;" in hint_rule
    assert "min-width: 0;" in hint_rule
    assert "max-width: none;" in hint_rule
    assert "overflow: hidden;" in hint_rule
    assert "white-space: nowrap;" in hint_rule
    assert "text-overflow: clip;" in hint_rule
    assert "transform: translateY(-2px);" in hint_rule
    assert "z-index: 1200;" in hint_rule
    assert ".project-path-hint.is-visible" in components_css

    sidebar_script = (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "sidebar.js"
    ).read_text(encoding="utf-8")
    assert "function bindProjectPathHint(card)" in sidebar_script
    assert "function showProjectPathHint(row, hint)" in sidebar_script
    assert "viewportWidth - left - 12" in sidebar_script
    assert "Number(rowRect.width || 0) - 28" not in sidebar_script


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
    session_search_source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "sessionSearch.js"
    )

    module_under_test_path = tmp_path / "sidebar.mjs"
    session_search_module_path = tmp_path / "sessionSearch.mjs"
    mock_dom_path = tmp_path / "mockDom.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    mock_api_path = tmp_path / "mockApi.mjs"
    mock_state_path = tmp_path / "mockState.mjs"
    mock_stream_path = tmp_path / "mockStream.mjs"
    mock_submission_path = tmp_path / "mockSubmission.mjs"
    mock_recovery_path = tmp_path / "mockRecovery.mjs"
    mock_message_renderer_path = tmp_path / "mockMessageRenderer.mjs"
    mock_agent_panel_path = tmp_path / "mockAgentPanel.mjs"
    mock_context_indicators_path = tmp_path / "mockContextIndicators.mjs"
    mock_project_view_path = tmp_path / "mockProjectView.mjs"
    mock_subagent_sessions_path = tmp_path / "mockSubagentSessions.mjs"
    mock_new_session_draft_path = tmp_path / "mockNewSessionDraft.mjs"
    mock_rounds_timeline_path = tmp_path / "mockRoundsTimeline.mjs"
    mock_session_debug_badge_path = tmp_path / "mockSessionDebugBadge.mjs"
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
            ".home-feature-item": /class="([^"]*home-feature-item[^"]*)"[^>]*data-feature-id="([^"]+)"[^>]*>/g,
            ".home-new-session-btn": /class="([^"]*home-new-session-btn[^"]*)"[^>]*>/g,
            ".home-session-search-btn": /class="([^"]*home-session-search-btn[^"]*)"[^>]*>/g,
            ".project-toggle": /class="project-toggle"[^>]*aria-expanded="([^"]+)"[^>]*>/g,
        ".project-title-btn": /class="([^"]*project-title-btn[^"]*)"[^>]*aria-current="([^"]+)"[^>]*>/g,
        ".project-options-btn": /class="([^"]*project-options-btn[^"]*)"[^>]*>/g,
        ".project-new-session-btn": /class="([^"]*project-new-session-btn[^"]*)"[^>]*>/g,
            ".project-fork-btn": /class="[^"]*project-fork-btn[^"]*"[^>]*>/g,
            ".project-remove-btn": /class="[^"]*project-remove-btn[^"]*"[^>]*>/g,
        ".project-session-visibility-btn": /class="project-session-visibility-btn"[^>]*>([\s\S]*?)<\/button>/g,
            ".session-subagents-toggle": /class="session-subagents-toggle"[^>]*data-session-id="([^"]+)"[^>]*aria-expanded="([^"]+)"[^>]*>/g,
            ".session-subagent-list": /class="([^"]*session-subagent-list[^"]*)"[^>]*data-session-id="([^"]+)"[^>]*aria-hidden="([^"]+)"[^>]*>/g,
            ".session-subagent-item": /class="([^"]*session-subagent-item[^"]*)"[^>]*data-session-id="([^"]+)"[^>]*data-subagent-instance-id="([^"]+)"[^>]*data-subagent-role-id="([^"]+)"[^>]*data-subagent-run-id="([^"]+)"[^>]*data-subagent-title="([^"]*)"[^>]*>[\s\S]*?<span class="session-label-text"[^>]*>([\s\S]*?)<\/span>/g,
        ".session-subagent-delete-btn": /class="([^"]*session-subagent-delete-btn[^"]*)"[^>]*data-session-id="([^"]+)"[^>]*data-subagent-instance-id="([^"]+)"[^>]*data-subagent-run-id="([^"]+)"[^>]*data-subagent-label="([^"]*)"[^>]*>/g,
        ".session-subagent-empty": /class="session-subagent-empty"[^>]*>([\s\S]*?)<\/div>/g,
        ".session-rename-btn": /class="session-rename-btn"[^>]*data-session-id="([^"]+)"[^>]*data-session-metadata="([^"]*)"[^>]*>/g,
        ".session-delete-btn": /class="session-delete-btn"[^>]*data-session-id="([^"]+)"[^>]*>/g,
        ".session-item": /class="([^"]*session-item[^"]*)"[^>]*data-session-id="([^"]+)"[^>]*data-workspace-id="([^"]+)"[^>]*>/g,
        ".session-source-icon": /class="[^"]*session-source-icon[^"]*"[^>]*>/g,
        ".project-title": /class="project-title"[^>]*>([\s\S]*?)<\/span>/g,
        ".session-id": /class="session-id"[^>]*>([\s\S]*?)<\/span>\s*<\/span>\s*<span class="session-meta"/g,
    };
    const pattern = patterns[selector];
    if (!pattern) {
        return results;
    }
    let match = pattern.exec(source);
    while (match) {
        if (selector === ".home-feature-item") {
            results.push(createNode({
                className: match[1],
                attributes: {
                    "data-feature-id": match[2],
                },
            }));
        } else if (selector === ".home-new-session-btn") {
            results.push(createNode({ className: match[1] }));
        } else if (selector === ".home-session-search-btn") {
            results.push(createNode({ className: match[1] }));
        } else if (selector === ".project-toggle") {
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
        } else if (selector === ".session-subagents-toggle") {
            results.push(createNode({
                attributes: {
                    "data-session-id": match[1],
                    "aria-expanded": match[2],
                },
            }));
        } else if (selector === ".session-subagent-list") {
            results.push(createNode({
                className: match[1],
                attributes: {
                    "data-session-id": match[2],
                    "aria-hidden": match[3],
                },
            }));
        } else if (selector === ".session-subagent-item") {
            results.push(createNode({
                className: match[1],
                textContent: match[7].replace(/<[^>]+>/g, "").trim(),
                attributes: {
                    "data-session-id": match[2],
                    "data-subagent-instance-id": match[3],
                    "data-subagent-role-id": match[4],
                    "data-subagent-run-id": match[5],
                    "data-subagent-title": decodeHtmlAttribute(match[6]),
                },
            }));
        } else if (selector === ".session-subagent-delete-btn") {
            results.push(createNode({
                className: match[1],
                attributes: {
                    "data-session-id": match[2],
                    "data-subagent-instance-id": match[3],
                    "data-subagent-run-id": match[4],
                    "data-subagent-label": decodeHtmlAttribute(match[5]),
                },
            }));
        } else if (selector === ".session-subagent-empty") {
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
        classList: {
            contains(name) {
                return String(className || "").split(/\s+/).includes(name);
            },
            toggle(name, force) {
                const current = new Set(String(className || "").split(/\s+/).filter(Boolean));
                const shouldAdd = force ?? !current.has(name);
                if (shouldAdd) {
                    current.add(name);
                } else {
                    current.delete(name);
                }
                className = Array.from(current).join(" ");
                this.value = className;
                return shouldAdd;
            },
            value: className,
        },
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
        appendChild(child) {
            this.children.push(child);
            cache.clear();
            return child;
        },
        querySelector(selector) {
            return this.querySelectorAll(selector)[0] || null;
        },
        querySelectorAll(selector) {
            if (!selector.startsWith(".")) {
                return [];
            }
            if (!cache.has(selector)) {
                const className = selector.slice(1);
                const directMatches = this.children.filter(child => {
                    const childClassName = String(child.className || "");
                    return childClassName.split(/\s+/).includes(className);
                });
                const childMatches = this.children.flatMap(child => (
                    typeof child.querySelectorAll === "function"
                        ? child.querySelectorAll(selector)
                        : []
                ));
                cache.set(selector, [
                    ...parseElements(html, selector),
                    ...directMatches,
                    ...childMatches,
                ]);
            }
            return cache.get(selector);
        },
    };
}

function collectFlattenedProjectChildren(child) {
    const childClassName = String(child?.className || "");
    const own = childClassName.split(/\s+/).includes("project-card") ? [child] : [];
    const nested = Array.isArray(child?.children)
        ? child.children.flatMap(collectFlattenedProjectChildren)
        : [];
    return [...own, ...nested];
}

function createContainerElement() {
    let html = "";
    return {
        className: "",
        style: {},
        children: [],
        matches(selector) {
            return selector === ":hover" ? !!globalThis.__projectsListHover : false;
        },
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
            if (String(child?.className || "").split(/\s+/).includes("projects-workspace-shell")) {
                this.children.push(...collectFlattenedProjectChildren(child));
            }
            return child;
        },
        contains(target) {
            return target === this || this.children.includes(target);
        },
        querySelector(selector) {
            if (selector === ":hover") {
                return globalThis.__projectsListHover ? this : null;
            }
            return this.querySelectorAll(selector)[0] || null;
        },
        querySelectorAll(selector) {
            if (!selector.startsWith(".")) {
                return [];
            }
            const className = selector.slice(1);
            const directMatches = this.children.filter(child => {
                const childClassName = String(child.className || "");
                return childClassName.split(/\s+/).includes(className);
            });
            const childMatches = this.children.flatMap(child => (
                typeof child.querySelectorAll === "function"
                    ? child.querySelectorAll(selector)
                    : []
            ));
            return [...directMatches, ...childMatches];
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
    const listeners = new Map();
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
        get activeElement() {
            return globalThis.__documentActiveElement || null;
        },
        addEventListener(name, handler) {
            if (!listeners.has(name)) {
                listeners.set(name, []);
            }
            listeners.get(name).push(handler);
        },
        dispatchEvent(event) {
            const handlers = listeners.get(event.type) || [];
            for (const handler of handlers) {
                handler(event);
            }
            globalThis.__documentDispatches = globalThis.__documentDispatches || [];
            globalThis.__documentDispatches.push({
                type: event.type,
                detail: event.detail,
            });
            return true;
        },
        querySelectorAll() {
            return [];
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
    "sidebar.new_session_primary": "New conversation",
    "sidebar.feature_navigation": "Feature navigation",
    "sidebar.feature_skills": "Skills",
    "sidebar.feature_automation": "Automation",
    "sidebar.feature_gateway": "IM Gateway",
    "sidebar.select_workspace_for_session": "Choose a workspace for the new session.",
    "sidebar.new_automation": "New automation",
    "sidebar.fork": "Fork",
    "sidebar.remove": "Remove",
    "sidebar.collapse": "Collapse",
    "sidebar.show_all": "Show all ({count})",
    "sidebar.fork_project": "Fork Project",
    "sidebar.fork_project_message": "Enter the name for the forked project.",
    "sidebar.fork_project_placeholder": "Forked project name",
    "sidebar.remove_workspace": "Remove Workspace",
    "sidebar.remove_workspace_message": "Remove workspace {workspace}? This will also delete its sessions from the sidebar.",
    "sidebar.remove_workspace_delete_directory_label": "Also delete directory",
    "sidebar.remove_workspace_delete_directory_message": "Leave unchecked to remove only the workspace record and keep files on disk.",
    "sidebar.remove_workspace_delete_worktree_label": "Also delete git worktree",
    "sidebar.remove_workspace_delete_worktree_message": "Leave unchecked to remove only the workspace record and keep the worktree on disk.",
    "sidebar.error.loading_projects": "Failed to load projects: {error}",
    "sidebar.rename_session_title": "Rename Session",
    "sidebar.rename_session_message": "Enter a new name for this session.",
    "sidebar.session_name_placeholder": "Session name",
    "sidebar.untitled_session": "New conversation",
    "sidebar.search_conversations": "Search",
    "sidebar.search_conversations_title": "Search conversations",
    "sidebar.search_placeholder": "Search conversations",
    "sidebar.search_recent": "Recent conversations",
    "sidebar.search_results": "Matching conversations",
    "sidebar.search_no_matches": "No matches",
    "sidebar.log.queued_bound_session": "Queued automation run in bound IM session: {session_id}",
    "sidebar.no_projects_title": "No projects yet",
    "sidebar.no_projects_copy": "Add a project below to attach a workspace and start sessions.",
    "sidebar.workspace": "Workspace",
        "sidebar.project_options": "Project options",
        "sidebar.new_session": "New session",
    "sidebar.no_sessions": "No sessions yet",
    "sidebar.subagent_sessions_toggle": "Toggle subagent sessions",
    "sidebar.subagent_sessions_loading": "Loading subagent sessions...",
    "sidebar.subagent_sessions_empty": "No subagent sessions",
    "sidebar.error.selecting_session": "Failed to select session: {error}",
    "settings.action.cancel": "Cancel",
    };

export function t(key) {
    return translations[key] || key;
}

export function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (message, [name, value]) => message.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
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
    resolved_mock_api_source = mock_api_source or default_mock_api_source
    if (
        "export async function fetchAutomationProjects()"
        not in resolved_mock_api_source
    ):
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function fetchAutomationProjects() {\n"
            "    return [];\n"
            "}\n"
        )
    if "export async function deleteSessionSubagent(" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function deleteSessionSubagent(sessionId, instanceId) {\n"
            "    globalThis.__deleteSubagentCalls.push({ sessionId, instanceId });\n"
            '    return { status: "ok" };\n'
            "}\n"
        )
    mock_api_path.write_text(
        resolved_mock_api_source,
        encoding="utf-8",
    )

    mock_state_path.write_text(
        """
export const state = {
    currentSessionId: "session-7",
    currentWorkspaceId: "alpha-project",
    currentMainView: "session",
    currentProjectViewWorkspaceId: null,
    currentFeatureViewId: null,
    activeSubagentSession: null,
    activeEventSource: null,
    pendingNewSessionActive: false,
    pendingNewSessionWorkspaceId: null,
};
""".strip(),
        encoding="utf-8",
    )
    mock_stream_path.write_text(
        """
import { state } from "./mockState.mjs";

export function detachActiveStreamForSessionSwitch() {
    globalThis.__detachStreamCalls = (globalThis.__detachStreamCalls || 0) + 1;
    state.activeEventSource = null;
}

export function detachNormalModeSubagentStreamsForSessionSwitch() {
    globalThis.__detachSubagentStreamCalls = (globalThis.__detachSubagentStreamCalls || 0) + 1;
}

export function closeNormalModeSubagentStream(runId) {
    globalThis.__closedSubagentRunIds = globalThis.__closedSubagentRunIds || [];
    globalThis.__closedSubagentRunIds.push(runId);
}
""".strip(),
        encoding="utf-8",
    )
    mock_submission_path.write_text(
        """
export function detachForegroundSubmission() {
    globalThis.__detachForegroundSubmissionCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_recovery_path.write_text(
        """
export function clearSessionRecovery() {
    globalThis.__clearSessionRecoveryCalls = (globalThis.__clearSessionRecoveryCalls || 0) + 1;
}

export function stopSessionContinuity(sessionId) {
    globalThis.__stoppedSessionContinuity = globalThis.__stoppedSessionContinuity || [];
    globalThis.__stoppedSessionContinuity.push(sessionId);
}
""".strip(),
        encoding="utf-8",
    )
    mock_message_renderer_path.write_text(
        """
export function clearAllStreamState() {
    globalThis.__clearAllStreamStateCalls = (globalThis.__clearAllStreamStateCalls || 0) + 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_agent_panel_path.write_text(
        """
export function clearAllPanels() {
    globalThis.__clearAllPanelsCalls = (globalThis.__clearAllPanelsCalls || 0) + 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_context_indicators_path.write_text(
        """
export function clearContextIndicators() {
    globalThis.__clearContextIndicatorsCalls = (globalThis.__clearContextIndicatorsCalls || 0) + 1;
}
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

export async function openAutomationHomeView(projectId = "") {
    globalThis.__openedAutomationProjectIds.push(projectId || "");
    state.currentMainView = "project";
}

export async function requestAutomationProjectInput(project = {}) {
    globalThis.__requestAutomationProjectInputCalls.push(project);
    return globalThis.__requestAutomationProjectInputResult ?? null;
}

export async function openImFeatureView() {
    globalThis.__openedGatewayFeatureCount = (globalThis.__openedGatewayFeatureCount || 0) + 1;
    state.currentMainView = "project";
}

export async function openSkillsFeatureView() {
    globalThis.__openedSkillsFeatureCount = (globalThis.__openedSkillsFeatureCount || 0) + 1;
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
    mock_subagent_sessions_path.write_text(
        """
function readRows(sessionId) {
    const rows = globalThis.__sessionSubagentSessionsMap?.[sessionId];
    return Array.isArray(rows) ? rows : [];
}

export function buildSubagentSessionLabel(record) {
    const roleId = String(record?.roleId || record?.role_id || "Agent").trim();
    const instanceId = String(record?.instanceId || record?.instance_id || "unknown").trim();
    return `${roleId} - ${instanceId.slice(0, 8) || 'unknown'}`;
}

export function getActiveSubagentSession() {
    return globalThis.__activeSubagentSession || null;
}

export function getSessionSubagentSessions(sessionId) {
    return readRows(String(sessionId || "").trim());
}

export function hasLoadedSessionSubagents(sessionId) {
    const safeSessionId = String(sessionId || "").trim();
    return Object.prototype.hasOwnProperty.call(
        globalThis.__sessionSubagentSessionsMap || {},
        safeSessionId,
    );
}

export async function ensureSessionSubagents(sessionId) {
    const safeSessionId = String(sessionId || "").trim();
    if (!safeSessionId) {
        return [];
    }
    globalThis.__ensureSessionSubagentsCalls.push(safeSessionId);
    const nextRows = globalThis.__sessionSubagentFetchResults?.[safeSessionId];
    if (Array.isArray(nextRows)) {
        globalThis.__sessionSubagentSessionsMap[safeSessionId] = nextRows;
    }
    return readRows(safeSessionId);
}

export function isSubagentSessionListExpanded(sessionId) {
    return !!globalThis.__expandedSubagentSessionIds?.has(String(sessionId || "").trim());
}

export function isSubagentSessionListLoading(sessionId) {
    return !!globalThis.__loadingSubagentSessionIds?.has(String(sessionId || "").trim());
}

export function toggleSubagentSessionList(sessionId) {
    const safeSessionId = String(sessionId || "").trim();
    if (!safeSessionId) {
        return;
    }
    globalThis.__toggleSubagentSessionListCalls.push(safeSessionId);
    if (globalThis.__expandedSubagentSessionIds.has(safeSessionId)) {
        globalThis.__expandedSubagentSessionIds.delete(safeSessionId);
    } else {
        globalThis.__expandedSubagentSessionIds.add(safeSessionId);
    }
}

export function removeSessionSubagent(sessionId, instanceId) {
    const safeSessionId = String(sessionId || "").trim();
    const safeInstanceId = String(instanceId || "").trim();
    const rows = readRows(safeSessionId);
    const removed = rows.find(item => item.instanceId === safeInstanceId) || null;
    globalThis.__sessionSubagentSessionsMap[safeSessionId] = rows.filter(
        item => item.instanceId !== safeInstanceId,
    );
    if (
        globalThis.__activeSubagentSession
        && globalThis.__activeSubagentSession.sessionId === safeSessionId
        && globalThis.__activeSubagentSession.instanceId === safeInstanceId
    ) {
        globalThis.__activeSubagentSession = null;
    }
    return removed;
}
""".strip(),
        encoding="utf-8",
    )

    mock_new_session_draft_path.write_text(
        """
import { state } from "./mockState.mjs";

export function clearNewSessionDraft() {
    globalThis.__clearNewSessionDraftCalls += 1;
    state.pendingNewSessionActive = false;
    state.pendingNewSessionWorkspaceId = null;
}

export function openNewSessionDraft(workspaceId = "") {
    const safeWorkspaceId = String(workspaceId || "").trim();
    globalThis.__openedNewSessionDraftWorkspaceIds.push(safeWorkspaceId);
    state.pendingNewSessionActive = true;
    state.pendingNewSessionWorkspaceId = safeWorkspaceId;
    state.currentSessionId = null;
    state.currentMainView = "new-session-draft";
    state.currentFeatureViewId = null;
    if (safeWorkspaceId) {
        state.currentWorkspaceId = safeWorkspaceId;
    }
}
""".strip(),
        encoding="utf-8",
    )

    mock_rounds_timeline_path.write_text(
        """
export function clearSessionTimeline() {
    globalThis.__clearSessionTimelineCalls += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_session_debug_badge_path.write_text(
        """
export function syncSessionDebugBadge() {
    globalThis.__syncSessionDebugBadgeCalls = (globalThis.__syncSessionDebugBadgeCalls || 0) + 1;
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
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("../core/submission.js", "./mockSubmission.mjs")
        .replace("../app/recovery.js", "./mockRecovery.mjs")
        .replace("./messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("./agentPanel.js", "./mockAgentPanel.mjs")
        .replace("./contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("./projectView.js", "./mockProjectView.mjs")
        .replace("./subagentSessions.js", "./mockSubagentSessions.mjs")
        .replace("./newSessionDraft.js", "./mockNewSessionDraft.mjs")
        .replace("./rounds/timeline.js", "./mockRoundsTimeline.mjs")
        .replace("./sessionSearch.js", "./sessionSearch.mjs")
        .replace("./sessionDebugBadge.js", "./mockSessionDebugBadge.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    session_search_module_path.write_text(
        session_search_source_path.read_text(encoding="utf-8").replace(
            "../utils/i18n.js",
            "./mockI18n.mjs",
        ),
        encoding="utf-8",
    )

    runner_path.write_text(
        f"""
import {{ createDomEnvironment, flushTasks, installGlobals }} from "./mockDom.mjs";

globalThis.__logs = [];
globalThis.__confirmDialogCalls = [];
globalThis.__confirmDialogResponses = [];
globalThis.__createdSessionWorkspaceIds = [];
globalThis.__openedNewSessionDraftWorkspaceIds = [];
globalThis.__deleteSubagentCalls = [];
globalThis.__deleteWorkspaceCalls = [];
globalThis.__forkCalls = [];
globalThis.__renameCalls = [];
globalThis.__selectedSessionIds = [];
globalThis.__openedWorkspaceIds = [];
globalThis.__openedAutomationProjectIds = [];
globalThis.__hideProjectViewCalls = 0;
globalThis.__clearNewSessionDraftCalls = 0;
globalThis.__clearSessionTimelineCalls = 0;
globalThis.__detachForegroundSubmissionCalls = 0;
globalThis.__showFormDialogResult = null;
globalThis.__showFormDialogCalls = [];
globalThis.__requestAutomationProjectInputResult = null;
globalThis.__requestAutomationProjectInputCalls = [];
globalThis.__toggleSubagentSessionListCalls = [];
globalThis.__sessionSubagentSessionsMap = {{}};
globalThis.__sessionSubagentFetchResults = {{}};
globalThis.__ensureSessionSubagentsCalls = [];
globalThis.__expandedSubagentSessionIds = new Set();
globalThis.__loadingSubagentSessionIds = new Set();
globalThis.__activeSubagentSession = null;
globalThis.__closedSubagentRunIds = [];
globalThis.__documentDispatches = [];
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
        timeout=3,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\\n"
            f"STDOUT:\\n{completed.stdout}\\n"
            f"STDERR:\\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
