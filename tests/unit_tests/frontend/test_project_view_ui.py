from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import cast

from .css_helpers import load_components_css


def _merge_mock_api_source(base_source: str, override_source: str) -> str:
    merged_source = base_source
    for block in re.split(
        r"(?=^export async function )", override_source, flags=re.MULTILINE
    ):
        stripped_block = block.strip()
        if not stripped_block:
            continue
        export_match = re.match(r"export async function (\w+)\s*\(", stripped_block)
        if export_match is None:
            merged_source = f"{merged_source}\n\n{stripped_block}"
            continue
        export_name = export_match.group(1)
        export_pattern = re.compile(
            rf"export async function {export_name}\s*\([^)]*\)\s*\{{[\s\S]*?\n\}}",
            flags=re.MULTILINE,
        )
        if export_pattern.search(merged_source):
            merged_source = export_pattern.sub(
                lambda _match: stripped_block,
                merged_source,
                count=1,
            )
        else:
            merged_source = f"{merged_source}\n\n{stripped_block}"
    return merged_source


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
    assert payload["diffRequests"] == [
        {"workspaceId": "alpha-project", "mount": None},
        {"workspaceId": "alpha-project", "mount": "default"},
    ]
    assert payload["diffFileRequests"] == [
        {"workspaceId": "alpha-project", "path": "src/main.py", "mount": "default"},
    ]
    assert payload["treeRequests"] == [
        {"workspaceId": "alpha-project", "path": "src", "mount": "default"},
    ]


def test_project_view_opens_workspace_root_from_header(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openWorkspaceProjectView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

initializeProjectView();
await openWorkspaceProjectView({ workspace_id: "alpha-project" });
await flushTasks();
await flushTasks();

const openRootButton = els.projectViewContent.querySelector("[data-open-workspace-root]");
openRootButton?.onclick?.();

console.log(JSON.stringify({
    contentHtml: els.projectViewContent.innerHTML,
    openWorkspaceRootCalls: globalThis.__openWorkspaceRootCalls,
    toastCalls: globalThis.__toastCalls || [],
}));
""".strip(),
    )

    assert "data-open-workspace-root" in str(payload["contentHtml"])
    assert "/work/alpha-project" in str(payload["contentHtml"])
    assert payload["openWorkspaceRootCalls"] == [
        {"workspaceId": "alpha-project", "mount": "default"},
    ]
    assert payload["toastCalls"] == []


def test_project_view_renders_multi_mount_workspace_and_switches_active_mount(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        mock_api_source="""
export async function fetchWorkspaceSnapshot(workspaceId) {
    await new Promise(resolve => setTimeout(resolve, 0));
    globalThis.__snapshotRequests.push(workspaceId);
    return {
        workspace_id: workspaceId,
        default_mount_name: "app",
        default_mount_root: "/work/app",
        tree: {
            name: workspaceId,
            path: ".",
            kind: "directory",
            has_children: true,
            children: [
                {
                    name: "app",
                    path: "app",
                    kind: "directory",
                    has_children: true,
                    children: [],
                },
                {
                    name: "ops",
                    path: "ops",
                    kind: "directory",
                    has_children: true,
                    children: [],
                },
            ],
        },
    };
}

export async function openWorkspaceRoot(workspaceId, mount = null) {
    globalThis.__openWorkspaceRootCalls.push({ workspaceId, mount });
    return { status: "ok" };
}

export async function fetchWorkspaceTree(workspaceId, path, mount = null) {
    globalThis.__treeRequests.push({ workspaceId, path, mount });
    if (mount === "ops") {
        return {
            workspace_id: workspaceId,
            mount_name: "ops",
            directory_path: path,
            children: [
                {
                    name: "deploy.yaml",
                    path: "deploy.yaml",
                    kind: "file",
                    has_children: false,
                    children: [],
                },
            ],
        };
    }
    return {
        workspace_id: workspaceId,
        mount_name: "app",
        directory_path: path,
        children: [
            {
                name: "src",
                path: "src",
                kind: "directory",
                has_children: true,
                children: [],
            },
        ],
    };
}

export async function fetchWorkspaceDiffs(workspaceId, mount = null) {
    await new Promise(resolve => setTimeout(resolve, 0));
    globalThis.__diffRequests.push({ workspaceId, mount });
    if (mount === "ops") {
        return {
            workspace_id: workspaceId,
            mount_name: "ops",
            root_path: "/srv/ops",
            is_git_repository: false,
            git_root_path: null,
            diff_message: "Workspace mount does not support diff: ops",
            diff_files: [],
        };
    }
    return {
        workspace_id: workspaceId,
        mount_name: "app",
        root_path: "/work/app",
        is_git_repository: true,
        git_root_path: "/work/app",
        diff_message: null,
        diff_files: [
            {
                path: "src/main.py",
                change_type: "modified",
            },
        ],
    };
}

export async function fetchWorkspaceDiffFile(workspaceId, path, mount = null) {
    globalThis.__diffFileRequests.push({ workspaceId, path, mount });
    return {
        workspace_id: workspaceId,
        mount_name: mount || "app",
        path,
        change_type: "modified",
        diff: `diff for ${mount || "app"}:${path}`,
        is_binary: false,
    };
}
""".strip(),
        runner_source="""
import {
    initializeProjectView,
    openWorkspaceProjectView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

initializeProjectView();
await openWorkspaceProjectView({
    workspace_id: "alpha-project",
    default_mount_name: "app",
    mounts: [
        {
            mount_name: "app",
            provider: "local",
            provider_config: { root_path: "/work/app" },
        },
        {
            mount_name: "ops",
            provider: "ssh",
            provider_config: { ssh_profile_id: "prod", remote_root: "/srv/ops" },
        },
    ],
});
await flushTasks();
await flushTasks();
await flushTasks();
await flushTasks();

const initialHtml = els.projectViewContent.innerHTML;
els.projectViewContent.querySelector("[data-open-workspace-root]")?.onclick?.();

const opsMountButton = Array.from(
    els.projectViewContent.querySelectorAll("[data-workspace-mount]"),
).find(node => node?.getAttribute?.("data-workspace-mount") === "ops");
opsMountButton?.onclick?.();
await flushTasks();
await flushTasks();
await flushTasks();
await flushTasks();

const switchedHtml = els.projectViewContent.innerHTML;
console.log(JSON.stringify({
    initialHtml,
    switchedHtml,
    opsMountActive: /data-workspace-mount="ops"[\\s\\S]*?aria-pressed="true"/.test(switchedHtml),
    snapshotRequests: globalThis.__snapshotRequests,
    diffRequests: globalThis.__diffRequests,
    diffFileRequests: globalThis.__diffFileRequests,
    treeRequests: globalThis.__treeRequests,
    openWorkspaceRootCalls: globalThis.__openWorkspaceRootCalls,
}));
""".strip(),
    )

    assert 'data-workspace-mount="app"' in str(payload["initialHtml"])
    assert 'data-workspace-mount="ops"' in str(payload["initialHtml"])
    assert "SSH profile: prod" in str(payload["initialHtml"])
    assert "/work/app" in str(payload["initialHtml"])
    assert payload["openWorkspaceRootCalls"] == [
        {"workspaceId": "alpha-project", "mount": "app"},
    ]
    assert payload["opsMountActive"] is True
    assert "/srv/ops" in str(payload["switchedHtml"])
    assert "deploy.yaml" in str(payload["switchedHtml"])
    assert "Workspace mount does not support diff: ops" in str(payload["switchedHtml"])
    assert "data-open-workspace-root" not in str(payload["switchedHtml"])
    assert payload["snapshotRequests"] == ["alpha-project"]
    assert payload["diffRequests"] == [
        {"workspaceId": "alpha-project", "mount": "app"},
        {"workspaceId": "alpha-project", "mount": "ops"},
    ]
    assert payload["treeRequests"] == [
        {"workspaceId": "alpha-project", "path": ".", "mount": "app"},
        {"workspaceId": "alpha-project", "path": ".", "mount": "ops"},
    ]
    assert payload["diffFileRequests"] == [
        {"workspaceId": "alpha-project", "path": "src/main.py", "mount": "app"},
    ]


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

initializeProjectView();
await openAutomationProjectView({ automation_project_id: "aut_1", workspace_id: "alpha-project" });
await flushTasks();
await flushTasks();

const editButton = document.querySelector("[data-automation-edit]");
editButton?.onclick?.();
await flushTasks();
await flushTasks();

document.getElementById("automation-editor-display-name-input").value = "Friday Briefing";
document.getElementById("automation-editor-prompt-input").value = "Summarize the latest project changes.";
document.getElementById("automation-editor-timezone-input").value = "Asia/Shanghai";
document.getElementById("automation-editor-delivery-binding-input").value = "trg_feishu::tenant-1::oc_123::session-im-1";
document.querySelector("[data-automation-editor-binding]")?.onchange?.({
    target: document.getElementById("automation-editor-delivery-binding-input"),
});
await flushTasks();
await flushTasks();
document.getElementById("automation-editor-schedule-kind-input").value = "weekly";
document.querySelector("[data-automation-editor-schedule-kind]")?.onchange?.({
    target: document.getElementById("automation-editor-schedule-kind-input"),
});
await flushTasks();
await flushTasks();
document.getElementById("automation-editor-time-input").value = "18:30";
document.getElementById("automation-editor-weekday-input").value = "5";
document.getElementById("automation-editor-delivery-started-input").checked = true;
document.getElementById("automation-editor-delivery-completed-input").checked = true;
document.getElementById("automation-editor-delivery-failed-input").checked = true;
const modalHtmlBeforeSave = globalThis.__bodyChildren.map(node => node.innerHTML).join("\\n");
document.querySelector("[data-automation-editor-save]")?.onclick?.();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    contentHtml: els.projectViewContent.innerHTML,
    modalHtml: modalHtmlBeforeSave,
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

export async function createAutomationProject() {
    return { automation_project_id: "aut_new" };
}

export async function deleteAutomationProject() {
    return { status: "ok" };
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
            session_id: "session-im-1",
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

export async function fetchAutomationProjects() {
    return [{ automation_project_id: "aut_1", display_name: "Daily Briefing", name: "daily-briefing", status: "enabled", workspace_id: "alpha-project" }];
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

export async function fetchConfigStatus() {
    return { skills: { skills: [] } };
}

export async function fetchOrchestrationConfig() {
    return { presets: [] };
}

export async function fetchRoleConfigOptions() {
    return { normal_mode_roles: [] };
}

export async function fetchTriggers() {
    return [];
}

export async function fetchWeChatGatewayAccounts() {
    return [];
}

export async function fetchGitHubTriggerAccounts() {
    return globalThis.__mockGitHubAccounts || [];
}

export async function fetchGitHubRepoSubscriptions() {
    return globalThis.__mockGitHubRepos || [];
}

export async function fetchGitHubAccountRepositories() {
    return globalThis.__mockGitHubAvailableRepos || [];
}

export async function fetchGitHubTriggerRules() {
    return globalThis.__mockGitHubRules || [];
}

export async function reloadSkillsConfig() {
    return { status: "ok" };
}

export async function createTrigger() {
    return { status: "ok" };
}

export async function updateTrigger() {
    return { status: "ok" };
}

export async function deleteTrigger() {
    return { status: "ok" };
}

export async function enableTrigger() {
    return { status: "ok" };
}

export async function disableTrigger() {
    return { status: "ok" };
}

export async function startWeChatGatewayLogin() {
    return { session_key: "wechat-login-1", qr_code_url: "https://example.test/qr.png" };
}

export async function waitWeChatGatewayLogin() {
    return { connected: true };
}

export async function updateWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function enableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function disableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function deleteWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function updateAutomationProject(_automationProjectId, payload) {
    globalThis.__updatedAutomationPayload = payload;
    return { status: "ok" };
}
""".strip(),
    )

    update_payload = cast(dict[str, object], payload["updatePayload"])
    delivery_binding = cast(dict[str, object], update_payload["delivery_binding"])
    delivery_events = cast(list[object], update_payload["delivery_events"])
    assert delivery_binding["trigger_id"] == "trg_feishu"
    assert delivery_binding["chat_id"] == "oc_123"
    assert delivery_binding["session_id"] == "session-im-1"
    assert delivery_events == [
        "started",
        "completed",
        "failed",
    ]
    assert update_payload["display_name"] == "Friday Briefing"
    assert update_payload["cron_expression"] == "30 18 * * 5"
    assert update_payload["timezone"] == "Asia/Shanghai"
    assert "automation-editor-modal-title" in str(payload["modalHtml"])
    assert "feishu_main - Release Updates" in str(payload["contentHtml"])


def test_project_view_renders_github_automation_section_and_access_panel(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openAutomationGitHubView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

globalThis.__mockGitHubAccounts = [
    {
        account_id: "ghta_1",
        name: "github-main",
        display_name: "GitHub Main",
        status: "enabled",
        token_configured: true,
        webhook_secret_configured: true,
    },
];
globalThis.__mockGitHubRepos = [
    {
        repo_subscription_id: "ghrs_1",
        account_id: "ghta_1",
        owner: "octocat",
        repo_name: "Hello-World",
        full_name: "octocat/Hello-World",
        callback_url: "https://example.com/github/webhook",
        webhook_status: "registered",
        enabled: true,
        subscribed_events: ["pull_request"],
    },
];
globalThis.__mockGitHubRules = [
    {
        trigger_rule_id: "trg_1",
        repo_subscription_id: "ghrs_1",
        name: "pr-opened",
        enabled: true,
        match_config: {
            event_name: "pull_request",
            actions: ["opened"],
        },
    },
];

initializeProjectView();
await openAutomationGitHubView("access");
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    contentHtml: els.projectViewContent.innerHTML,
    toolbarHtml: els.projectViewToolbarActions.innerHTML,
    summary: els.projectViewSummary.textContent,
    bindCalls: globalThis.__githubSettingsBindCalls || 0,
    loadCalls: globalThis.__githubSettingsLoadCalls || 0,
}));
""".strip(),
    )

    assert "GitHub access panel" in str(payload["contentHtml"])
    assert "octocat/Hello-World" in str(payload["contentHtml"])
    assert "Subscribed Events: pull_request" in str(payload["contentHtml"])
    assert 'data-automation-section="github"' in str(payload["toolbarHtml"])
    assert payload["summary"] == "1 accounts · 1 repos · 1 rules"
    assert payload["bindCalls"] == 1
    assert payload["loadCalls"] == 1


def test_project_view_repo_detail_shows_full_github_rule_configuration(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openAutomationGitHubView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

globalThis.__mockGitHubAccounts = [
    {
        account_id: "ghta_1",
        name: "github-main",
        display_name: "GitHub Main",
        status: "enabled",
        token_configured: true,
        webhook_secret_configured: true,
    },
];
globalThis.__mockGitHubRepos = [
    {
        repo_subscription_id: "ghrs_1",
        account_id: "ghta_1",
        owner: "octocat",
        repo_name: "Hello-World",
        full_name: "octocat/Hello-World",
        callback_url: "https://example.com/github/webhook",
        webhook_status: "registered",
        enabled: true,
        subscribed_events: ["pull_request"],
    },
];
globalThis.__mockGitHubRules = [
    {
        trigger_rule_id: "trg_1",
        repo_subscription_id: "ghrs_1",
        name: "pr-opened",
        enabled: true,
        match_config: {
            event_name: "pull_request",
            actions: ["opened", "edited"],
            draft_pr: false,
            base_branches: ["main", "release/*"],
        },
        dispatch_config: {
            target_type: "run_template",
            run_template: {
                workspace_id: "rule-workspace",
                prompt_template: "Review the PR\\nand summarize impact.",
            },
        },
    },
];

initializeProjectView();
await openAutomationGitHubView("repo:ghrs_1");
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    contentHtml: els.projectViewContent.innerHTML,
}));
""".strip(),
    )

    assert "Workspace ID" in str(payload["contentHtml"])
    assert "rule-workspace" in str(payload["contentHtml"])
    assert "Subscribed Event" in str(payload["contentHtml"])
    assert "Pull Request" in str(payload["contentHtml"])
    assert "Actions" in str(payload["contentHtml"])
    assert "opened, edited" in str(payload["contentHtml"])
    assert "Draft Pull Request" in str(payload["contentHtml"])
    assert "Ready for review only" in str(payload["contentHtml"])
    assert "Base Branches" in str(payload["contentHtml"])
    assert "main, release/*" in str(payload["contentHtml"])
    assert "Task Prompt" in str(payload["contentHtml"])
    assert "Review the PR" in str(payload["contentHtml"])
    assert "summarize impact." in str(payload["contentHtml"])
    assert "Open Webhooks" in str(payload["contentHtml"])
    assert "https://github.com/octocat/Hello-World/settings/hooks" in str(
        payload["contentHtml"]
    )


def test_project_view_github_account_dialog_uses_secure_fields(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openAutomationGitHubView,
} from "./projectView.mjs";
import { flushTasks } from "./mockDom.mjs";

globalThis.__mockGitHubAccounts = [
    {
        account_id: "ghta_1",
        name: "github-main",
        display_name: "GitHub Main",
        status: "enabled",
        token_configured: true,
        webhook_secret_configured: true,
    },
];

initializeProjectView();
await openAutomationGitHubView("account:ghta_1");
await flushTasks();
await flushTasks();

const editButton = document.querySelector('[data-github-account-edit]');
editButton?.onclick?.();
await flushTasks();
await flushTasks();

const dialogCall = globalThis.__showFormDialogCalls.at(-1) || {};
const fields = Array.isArray(dialogCall.fields) ? dialogCall.fields : [];
    const secureFields = fields
        .filter(field => field.id === "token" || field.id === "webhook_secret")
        .map(field => ({
            id: field.id,
            type: field.type || "text",
            allowEmptyReveal: field.allowEmptyReveal === true,
            showLabel: field.showLabel || "",
            hideLabel: field.hideLabel || "",
        }));

console.log(JSON.stringify({
    buttonFound: Boolean(editButton),
    secureFields,
}));
""".strip(),
    )

    assert payload["buttonFound"] is True
    assert payload["secureFields"] == [
        {
            "id": "token",
            "type": "password",
            "allowEmptyReveal": True,
            "showLabel": "Show GitHub token",
            "hideLabel": "Hide GitHub token",
        },
        {
            "id": "webhook_secret",
            "type": "password",
            "allowEmptyReveal": True,
            "showLabel": "Show Webhook Secret",
            "hideLabel": "Hide Webhook Secret",
        },
    ]


def test_project_view_new_github_account_dialog_allows_empty_reveal(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openAutomationGitHubView,
} from "./projectView.mjs";
import { flushTasks } from "./mockDom.mjs";

globalThis.__mockGitHubAccounts = [];

initializeProjectView();
await openAutomationGitHubView();
await flushTasks();
await flushTasks();

const createButton = document.querySelector('[data-github-account-create]');
createButton?.onclick?.();
await flushTasks();
await flushTasks();

const dialogCall = globalThis.__showFormDialogCalls.at(-1) || {};
const fields = Array.isArray(dialogCall.fields) ? dialogCall.fields : [];
const secureFields = fields
    .filter(field => field.id === "token" || field.id === "webhook_secret")
    .map(field => ({
        id: field.id,
        type: field.type || "text",
        allowEmptyReveal: field.allowEmptyReveal === true,
    }));

console.log(JSON.stringify({
    buttonFound: Boolean(createButton),
    secureFields,
}));
""".strip(),
    )

    assert payload["buttonFound"] is True
    assert payload["secureFields"] == [
        {
            "id": "token",
            "type": "password",
            "allowEmptyReveal": True,
        },
        {
            "id": "webhook_secret",
            "type": "password",
            "allowEmptyReveal": True,
        },
    ]


def test_project_view_edits_github_account_with_inline_submit_handler(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openAutomationGitHubView,
} from "./projectView.mjs";
import { flushTasks } from "./mockDom.mjs";

globalThis.__mockGitHubAccounts = [
    {
        account_id: "ghta_1",
        name: "github-main",
        display_name: "GitHub Main",
        status: "enabled",
        token_configured: true,
        webhook_secret_configured: true,
    },
];
globalThis.__showFormDialogResult = {
    name: "github-main",
    display_name: "GitHub Main",
    token: "",
    clear_token: false,
    webhook_secret: "",
    clear_webhook_secret: false,
    enabled: true,
};

initializeProjectView();
await openAutomationGitHubView("account:ghta_1");
await flushTasks();
await flushTasks();

const editButton = document.querySelector('[data-github-account-edit]');
editButton?.onclick?.();
await flushTasks();
await flushTasks();

const dialogCall = globalThis.__showFormDialogCalls.at(-1) || {};

console.log(JSON.stringify({
    buttonFound: Boolean(editButton),
    submitHandlerType: typeof dialogCall.submitHandler,
    updatedPayload: globalThis.__updatedGitHubAccountPayload || null,
    toastCalls: globalThis.__toastCalls || [],
}));
""".strip(),
    )

    assert payload["buttonFound"] is True
    assert payload["submitHandlerType"] == "function"
    assert payload["updatedPayload"] == {
        "accountId": "ghta_1",
        "payload": {
            "name": "github-main",
            "display_name": "GitHub Main",
            "enabled": True,
        },
    }
    assert payload["toastCalls"] == [
        {
            "title": "Saved",
            "message": "GitHub Main",
            "tone": "success",
        }
    ]


def test_project_view_creates_github_repo_from_repository_dropdown(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openAutomationGitHubView,
} from "./projectView.mjs";
import { flushTasks } from "./mockDom.mjs";

globalThis.__mockGitHubAccounts = [
    {
        account_id: "ghta_1",
        name: "github-main",
        display_name: "GitHub Main",
        status: "enabled",
        token_configured: true,
        webhook_secret_configured: true,
    },
];
globalThis.__mockGitHubRepos = [];
globalThis.__mockGitHubRules = [];
globalThis.__mockGitHubAvailableRepos = [
    {
        owner: "octocat",
        repo_name: "Hello-World",
        full_name: "octocat/Hello-World",
        default_branch: "main",
        private: false,
    },
];

initializeProjectView();
await openAutomationGitHubView("account:ghta_1");
await flushTasks();
await flushTasks();

globalThis.__showFormDialogResult = {
    full_name: "octocat/Hello-World",
    enabled: true,
};

const button = document.querySelector('[data-github-repo-create]');
button?.onclick?.();
await flushTasks();
await flushTasks();

const dialogCall = globalThis.__showFormDialogCalls.at(-1) || {};
const fields = Array.isArray(dialogCall.fields) ? dialogCall.fields : [];

console.log(JSON.stringify({
    buttonFound: Boolean(button),
    buttonValue: button?.getAttribute?.("data-github-repo-create") || "",
    createdPayload: globalThis.__createdGitHubRepoPayload || null,
    fieldIds: fields.map(field => field.id),
    fieldTypes: fields.map(field => field.type || "text"),
    firstFieldOptions: fields[0]?.options || [],
    toastCalls: globalThis.__toastCalls || [],
}));
""".strip(),
    )

    assert payload["buttonFound"] is True
    assert payload["buttonValue"] == "ghta_1"
    assert payload["createdPayload"] == {
        "account_id": "ghta_1",
        "owner": "octocat",
        "repo_name": "Hello-World",
        "enabled": True,
    }
    assert payload["fieldIds"] == ["full_name", "enabled"]
    assert payload["fieldTypes"] == ["select", "checkbox"]
    assert payload["firstFieldOptions"] == [
        {"value": "", "label": "Select a repository"},
        {"value": "octocat/Hello-World", "label": "octocat/Hello-World"},
    ]


def test_project_view_github_rule_dialog_exposes_subscribed_event_field() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "projectView.js"
    ).read_text(encoding="utf-8")

    assert "function getGitHubRuleEventOptions()" in source
    assert "id: 'workspace_id'" in source
    assert "description: t('feature.automation.github_rule_workspace_copy')" in source
    assert "id: 'event_name'" in source
    assert "label: t('feature.automation.github_event_subscription')" in source
    assert "description: t('feature.automation.github_event_copy')" in source
    assert "options: getGitHubRuleEventOptions()" in source
    assert "label: t('feature.automation.github_rule_name')" in source
    assert "id: 'actions'" in source
    assert "type: 'multiselect'" in source
    assert "options: getGitHubRuleActionOptions()" in source
    assert "placeholder: t('feature.automation.github_actions_placeholder')" in source
    assert "description: t('feature.automation.github_actions_copy')" in source
    assert "id: 'draft_pr'" in source
    assert "options: getGitHubDraftPrOptions()" in source
    assert "description: t('feature.automation.github_draft_pr_copy')" in source
    assert "id: 'head_branches'" not in source
    assert "id: 'comment_on_completion'" not in source
    assert "id: 'completion_comment_template'" not in source
    assert "id: 'labels_any'" not in source
    assert "id: 'labels_all'" not in source
    assert "id: 'label_match_mode'" not in source
    assert "id: 'labels'" not in source
    assert "id: 'sender_allow'" not in source
    assert "id: 'sender_deny'" not in source
    assert "id: 'paths_any'" not in source
    assert "id: 'paths_ignore'" not in source


def test_project_view_github_rule_edit_dialog_preserves_event_selection_controls() -> (
    None
):
    repo_root = Path(__file__).resolve().parents[3]
    i18n_source = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "i18n.js"
    ).read_text(encoding="utf-8")

    assert (
        "'feature.automation.github_event_subscription': 'Subscribed Event'"
        in i18n_source
    )
    assert (
        "'feature.automation.github_event_copy': 'Select the GitHub webhook event for this rule. The repository subscribed events are derived automatically from enabled rules.'"
        in i18n_source
    )
    assert (
        "'feature.automation.github_event_pull_request': 'Pull Request'" in i18n_source
    )
    assert "'feature.automation.github_event_issues': 'Issues'" in i18n_source
    assert "'feature.automation.github_rule_name': 'Rule Name'" in i18n_source
    assert (
        "'feature.automation.github_actions_copy': 'Select one or more GitHub actions."
        in i18n_source
    )
    assert "'feature.automation.github_draft_pr': 'Draft Pull Request'" in i18n_source
    assert (
        "'feature.automation.github_rule_workspace_summary': 'Workspace: {workspace}'"
        in i18n_source
    )
    assert "review_requested" in i18n_source


def test_project_view_github_rule_payload_clears_pr_only_filters_for_issue_rules() -> (
    None
):
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "projectView.js"
    ).read_text(encoding="utf-8")

    assert "draft_pr: normalizeGitHubDraftPrValue(values.draft_pr)," in source
    assert (
        "base_branches: normalizeCommaSeparatedValues(values.base_branches)," in source
    )
    assert (
        "const selectedActions = normalizeCommaSeparatedValues(values.actions);"
        in source
    )
    assert "actions: selectedActions," in source
    assert (
        "head_branches: normalizeCommaSeparatedValues(values.head_branches),"
        not in source
    )
    assert "comment_on_completion" not in source
    assert "completion_comment_template" not in source
    assert "labels_any:" not in source
    assert "labels_all:" not in source
    assert "sender_allow:" not in source
    assert "sender_deny:" not in source
    assert "paths_any:" not in source
    assert "paths_ignore:" not in source


def test_feedback_form_dialog_supports_multiselect_fields() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "feedback.js"
    ).read_text(encoding="utf-8")

    assert "fieldType === 'multiselect'" in source
    assert 'data-feedback-form-type="multiselect"' in source
    assert "data-feedback-multiselect-option" in source
    assert "bindMultiselectControls(hosts.dialogRoot);" in source


def test_feedback_form_dialog_supports_inline_submit_errors() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "feedback.js"
    ).read_text(encoding="utf-8")

    assert "submitHandler = null" in source
    assert "typeof activeDialog?.submitHandler === 'function'" in source
    assert "feedback-dialog-submit-error" in source
    assert "setDialogSubmittingState({" in source
    assert "submitError.hidden = false;" in source


def test_project_view_updates_local_github_rule_state_after_mutations() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "projectView.js"
    ).read_text(encoding="utf-8")

    assert "function upsertGitHubRuleInState(rule)" in source
    assert "function removeGitHubRuleFromState(triggerRuleId)" in source
    assert "upsertGitHubRuleInState(created);" in source
    assert "upsertGitHubRuleInState(updated);" in source
    assert "removeGitHubRuleFromState(rule.trigger_rule_id);" in source
    assert "renderAutomationHomeView();" in source


def test_project_view_preserves_disabled_one_shot_automation_on_edit(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openAutomationProjectView,
} from "./projectView.mjs";
import { flushTasks } from "./mockDom.mjs";

initializeProjectView();
await openAutomationProjectView({ automation_project_id: "aut_1", workspace_id: "alpha-project" });
await flushTasks();
await flushTasks();

document.querySelector("[data-automation-edit]")?.onclick?.();
await flushTasks();
await flushTasks();

document.getElementById("automation-editor-prompt-input").value = "Run once and stay disabled.";
document.querySelector("[data-automation-editor-save]")?.onclick?.();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
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

export async function createAutomationProject() {
    return { automation_project_id: "aut_new" };
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function fetchAutomationProject() {
    return {
        automation_project_id: "aut_1",
        name: "one-shot-briefing",
        display_name: "One-shot Briefing",
        status: "disabled",
        workspace_id: "alpha-project",
        prompt: "Run once and stay disabled.",
        schedule_mode: "one_shot",
        cron_expression: null,
        run_at: "2026-03-14T09:30:00.000Z",
        timezone: "Asia/Shanghai",
        delivery_events: [],
    };
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function fetchAutomationProjectSessions() {
    return [];
}

export async function fetchAutomationProjects() {
    return [{ automation_project_id: "aut_1", display_name: "One-shot Briefing", name: "one-shot-briefing", status: "disabled", workspace_id: "alpha-project" }];
}

export async function fetchWorkspaces() {
    return [{ workspace_id: "alpha-project", root_path: "/work/alpha-project" }];
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

export async function fetchConfigStatus() {
    return { skills: { skills: [] } };
}

export async function fetchOrchestrationConfig() {
    return { presets: [] };
}

export async function fetchRoleConfigOptions() {
    return { normal_mode_roles: [] };
}

export async function fetchTriggers() {
    return [];
}

export async function fetchWeChatGatewayAccounts() {
    return [];
}

export async function reloadSkillsConfig() {
    return { status: "ok" };
}

export async function createTrigger() {
    return { status: "ok" };
}

export async function updateTrigger() {
    return { status: "ok" };
}

export async function deleteTrigger() {
    return { status: "ok" };
}

export async function enableTrigger() {
    return { status: "ok" };
}

export async function disableTrigger() {
    return { status: "ok" };
}

export async function startWeChatGatewayLogin() {
    return { session_key: "wechat-login-1", qr_code_url: "https://example.test/qr.png" };
}

export async function waitWeChatGatewayLogin() {
    return { connected: true };
}

export async function updateWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function enableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function disableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function deleteWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function updateAutomationProject(_automationProjectId, payload) {
    globalThis.__updatedAutomationPayload = payload;
    return { status: "ok" };
}
""".strip(),
    )

    update_payload = cast(dict[str, object], payload["updatePayload"])
    assert update_payload["enabled"] is False
    assert update_payload["schedule_mode"] == "one_shot"
    assert update_payload["cron_expression"] is None
    assert update_payload["run_at"] == "2026-03-14T09:30:00.000Z"


def test_project_view_keeps_automation_view_for_reused_bound_session_run(
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

initializeProjectView();
await openAutomationProjectView({ automation_project_id: "aut_1", workspace_id: "alpha-project" });
await flushTasks();
await flushTasks();

const runButton = document.querySelector("[data-automation-run]");
runButton?.onclick?.();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    dispatchedEvents: globalThis.__dispatchedEvents,
    logs: globalThis.__logs,
    projectViewSummary: els.projectViewSummary.textContent,
}));
""".strip(),
        mock_api_source="""
export async function disableAutomationProject() {
    return { status: "disabled" };
}

export async function enableAutomationProject() {
    return { status: "enabled" };
}

export async function createAutomationProject() {
    return { automation_project_id: "aut_new" };
}

export async function deleteAutomationProject() {
    return { status: "ok" };
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
        last_session_id: "session-im-1",
        next_run_at: "2026-03-14T09:00:00Z",
    };
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function fetchAutomationProjectSessions() {
    return [
        {
            session_id: "session-im-1",
            workspace_id: "alpha-project",
            project_kind: "workspace",
            project_id: "alpha-project",
            metadata: { title: "feishu_main - Release Updates" },
            updated_at: "2026-03-14T10:00:00Z",
        },
    ];
}

export async function fetchAutomationProjects() {
    return [{ automation_project_id: "aut_1", display_name: "Daily Briefing", name: "daily-briefing", status: "enabled", workspace_id: "alpha-project" }];
}

export async function fetchWorkspaces() {
    return [{ workspace_id: "alpha-project", root_path: "/work/alpha-project" }];
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
    return {
        automation_project_id: "aut_1",
        session_id: "session-im-1",
        run_id: "run-1",
        queued: false,
        reused_bound_session: true,
    };
}

export async function fetchConfigStatus() {
    return { skills: { skills: [] } };
}

export async function fetchOrchestrationConfig() {
    return { presets: [] };
}

export async function fetchRoleConfigOptions() {
    return { normal_mode_roles: [] };
}

export async function fetchTriggers() {
    return [];
}

export async function fetchWeChatGatewayAccounts() {
    return [];
}

export async function reloadSkillsConfig() {
    return { status: "ok" };
}

export async function createTrigger() {
    return { status: "ok" };
}

export async function updateTrigger() {
    return { status: "ok" };
}

export async function deleteTrigger() {
    return { status: "ok" };
}

export async function enableTrigger() {
    return { status: "ok" };
}

export async function disableTrigger() {
    return { status: "ok" };
}

export async function startWeChatGatewayLogin() {
    return { session_key: "wechat-login-1", qr_code_url: "https://example.test/qr.png" };
}

export async function waitWeChatGatewayLogin() {
    return { connected: true };
}

export async function updateWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function enableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function disableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function deleteWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function updateAutomationProject() {
    return { status: "ok" };
}
""".strip(),
    )

    assert payload["dispatchedEvents"] == [
        {"type": "agent-teams-projects-changed", "detail": None}
    ]
    assert payload["logs"] == [
        "Started automation run in bound IM session: session-im-1"
    ]
    assert "1 " in str(payload["projectViewSummary"])


def test_project_view_keeps_feature_page_visible_after_manual_automation_run(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openAutomationHomeView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

initializeProjectView();
await openAutomationHomeView("aut_1");
await flushTasks();
await flushTasks();

const runButton = document.querySelector("[data-automation-run]");
runButton?.onclick?.();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    dispatchedEvents: globalThis.__dispatchedEvents,
    logs: globalThis.__logs,
    projectViewTitle: els.projectViewTitle.textContent,
    contentHtml: els.projectViewContent.innerHTML,
}));
""".strip(),
        mock_api_source="""
let projectSessions = [
    {
        session_id: "session-old-1",
        workspace_id: "alpha-project",
        project_kind: "workspace",
        project_id: "alpha-project",
        metadata: { title: "Old run" },
        updated_at: "2026-03-14T09:00:00Z",
    },
];

export async function disableAutomationProject() {
    return { status: "disabled" };
}

export async function enableAutomationProject() {
    return { status: "enabled" };
}

export async function createAutomationProject() {
    return { automation_project_id: "aut_new" };
}

export async function deleteAutomationProject() {
    return { status: "ok" };
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
        next_run_at: "2026-03-14T09:00:00Z",
    };
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function fetchAutomationProjectSessions() {
    return projectSessions;
}

export async function fetchAutomationProjects() {
    return [{ automation_project_id: "aut_1", display_name: "Daily Briefing", name: "daily-briefing", status: "enabled", workspace_id: "alpha-project" }];
}

export async function fetchWorkspaces() {
    return [{ workspace_id: "alpha-project", root_path: "/work/alpha-project" }];
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
    projectSessions = [
        {
            session_id: "session-new-1",
            workspace_id: "alpha-project",
            project_kind: "workspace",
            project_id: "alpha-project",
            metadata: { title: "Manual Run" },
            updated_at: "2026-03-14T10:00:00Z",
        },
        ...projectSessions,
    ];
    return {
        automation_project_id: "aut_1",
        session_id: "session-new-1",
        run_id: "run-2",
        queued: false,
        reused_bound_session: false,
    };
}

export async function fetchConfigStatus() {
    return { skills: { skills: [] } };
}

export async function fetchOrchestrationConfig() {
    return { presets: [] };
}

export async function fetchRoleConfigOptions() {
    return { normal_mode_roles: [] };
}

export async function fetchTriggers() {
    return [];
}

export async function fetchWeChatGatewayAccounts() {
    return [];
}

export async function reloadSkillsConfig() {
    return { status: "ok" };
}

export async function createTrigger() {
    return { status: "ok" };
}

export async function updateTrigger() {
    return { status: "ok" };
}

export async function deleteTrigger() {
    return { status: "ok" };
}

export async function enableTrigger() {
    return { status: "ok" };
}

export async function disableTrigger() {
    return { status: "ok" };
}

export async function startWeChatGatewayLogin() {
    return { session_key: "wechat-login-1", qr_code_url: "https://example.test/qr.png" };
}

export async function waitWeChatGatewayLogin() {
    return { connected: true };
}

export async function updateWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function enableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function disableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function deleteWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function updateAutomationProject() {
    return { status: "ok" };
}
""".strip(),
    )

    dispatched_events = cast(list[dict[str, object]], payload["dispatchedEvents"])
    logs = cast(list[object], payload["logs"])
    dispatched_event_types = [str(entry["type"]) for entry in dispatched_events]

    assert "agent-teams-projects-changed" in dispatched_event_types
    assert "agent-teams-select-session" not in dispatched_event_types
    assert payload["projectViewTitle"] == "Automation"
    assert "Manual Run" in str(payload["contentHtml"])
    assert "Started automation run: session-new-1" in [str(item) for item in logs]


def test_project_view_renders_automation_details_without_helper_copy_and_prompt_card(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openAutomationHomeView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

initializeProjectView();
await openAutomationHomeView("aut_1");
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    contentHtml: els.projectViewContent.innerHTML,
}));
""".strip(),
        mock_api_source="""
export async function disableAutomationProject() {
    return { status: "disabled" };
}

export async function enableAutomationProject() {
    return { status: "enabled" };
}

export async function createAutomationProject() {
    return { automation_project_id: "aut_new" };
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function fetchAutomationProject() {
    return {
        automation_project_id: "aut_1",
        name: "daily-briefing",
        display_name: "Daily Briefing",
        status: "enabled",
        workspace_id: "alpha-project",
        prompt: "Line one.\\nLine two.",
        schedule_mode: "cron",
        cron_expression: "0 9 * * *",
        timezone: "Asia/Shanghai",
        next_run_at: "2026-03-14T09:00:00Z",
        last_run_started_at: "2026-03-14T08:00:00Z",
        delivery_events: [],
    };
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function fetchAutomationProjectSessions() {
    return [];
}

export async function fetchAutomationProjects() {
    return [{ automation_project_id: "aut_1", display_name: "Daily Briefing", name: "daily-briefing", status: "enabled", workspace_id: "alpha-project" }];
}

export async function fetchWorkspaces() {
    return [{ workspace_id: "alpha-project", root_path: "/work/alpha-project" }];
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
    throw new Error("not used");
}

export async function fetchConfigStatus() {
    return { skills: { skills: [] } };
}

export async function fetchOrchestrationConfig() {
    return { presets: [] };
}

export async function fetchRoleConfigOptions() {
    return { normal_mode_roles: [] };
}

export async function fetchTriggers() {
    return [];
}

export async function fetchWeChatGatewayAccounts() {
    return [];
}

export async function reloadSkillsConfig() {
    return { status: "ok" };
}

export async function createTrigger() {
    return { status: "ok" };
}

export async function updateTrigger() {
    return { status: "ok" };
}

export async function deleteTrigger() {
    return { status: "ok" };
}

export async function enableTrigger() {
    return { status: "ok" };
}

export async function disableTrigger() {
    return { status: "ok" };
}

export async function startWeChatGatewayLogin() {
    return { session_key: "wechat-login-1", qr_code_url: "https://example.test/qr.png" };
}

export async function waitWeChatGatewayLogin() {
    return { connected: true };
}

export async function updateWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function enableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function disableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function deleteWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function updateAutomationProject() {
    return { status: "ok" };
}
""".strip(),
    )

    content_html = str(payload["contentHtml"])
    assert "Review schedule and recent runs." not in content_html
    assert "Automation notifications are currently disabled." not in content_html
    assert (
        "Automation updates will be pushed to the selected Feishu chat."
        not in content_html
    )
    assert "automation-prompt-card" not in content_html
    assert "automation-prompt-inline" in content_html
    assert "feature-card automation-runs-card" not in content_html
    assert "automation-flat-section automation-runs-section" in content_html


def test_project_view_automation_home_sidebar_uses_flat_list_without_duplicate_title(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openAutomationHomeView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

initializeProjectView();
await openAutomationHomeView("aut_1");
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    contentHtml: els.projectViewContent.innerHTML,
}));
""".strip(),
        mock_api_source="""
export async function disableAutomationProject() {
    return { status: "disabled" };
}

export async function enableAutomationProject() {
    return { status: "enabled" };
}

export async function createAutomationProject() {
    return { automation_project_id: "aut_new" };
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function fetchAutomationProject() {
    return {
        automation_project_id: "aut_1",
        name: "daily-briefing",
        display_name: "Daily Briefing",
        status: "enabled",
        workspace_id: "alpha-project",
        prompt: "Line one.\\nLine two.",
        schedule_mode: "cron",
        cron_expression: "0 9 * * *",
        timezone: "Asia/Shanghai",
        next_run_at: "2026-03-14T09:00:00Z",
        last_run_started_at: "2026-03-14T08:00:00Z",
        delivery_events: [],
    };
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function fetchAutomationProjectSessions() {
    return [];
}

export async function fetchAutomationProjects() {
    return [
        { automation_project_id: "aut_1", display_name: "Daily Briefing", name: "daily-briefing", status: "enabled", workspace_id: "alpha-project", cron_expression: "0 9 * * *" },
        { automation_project_id: "aut_2", display_name: "Nightly Sync", name: "nightly-sync", status: "disabled", workspace_id: "alpha-project", cron_expression: "0 21 * * *" },
    ];
}

export async function fetchWorkspaces() {
    return [{ workspace_id: "alpha-project", root_path: "/work/alpha-project" }];
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
    throw new Error("not used");
}

export async function fetchConfigStatus() {
    return { skills: { skills: [] } };
}

export async function fetchOrchestrationConfig() {
    return { presets: [] };
}

export async function fetchRoleConfigOptions() {
    return { normal_mode_roles: [] };
}

export async function fetchTriggers() {
    return [];
}

export async function fetchWeChatGatewayAccounts() {
    return [];
}

export async function reloadSkillsConfig() {
    return { status: "ok" };
}

export async function createTrigger() {
    return { status: "ok" };
}

export async function updateTrigger() {
    return { status: "ok" };
}

export async function deleteTrigger() {
    return { status: "ok" };
}

export async function enableTrigger() {
    return { status: "ok" };
}

export async function disableTrigger() {
    return { status: "ok" };
}

export async function startWeChatGatewayLogin() {
    return { session_key: "wechat-login-1", qr_code_url: "https://example.test/qr.png" };
}

export async function waitWeChatGatewayLogin() {
    return { connected: true };
}

export async function updateWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function enableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function disableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function deleteWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function updateAutomationProject() {
    return { status: "ok" };
}
""".strip(),
    )

    content_html = str(payload["contentHtml"])
    assert "workspace-view-panel-header" not in content_html
    assert content_html.count(">Automation<") == 0

    components_css = load_components_css()

    assert ".automation-list-panel .feature-panel-body {" in components_css
    assert "padding: 0.7rem 0.95rem 0.85rem;" in components_css
    assert ".automation-record {" in components_css
    assert "border-radius: 0;" in components_css
    assert "border-bottom: 1px solid" in components_css


def test_project_view_automation_header_keeps_action_row_out_of_prompt_flow() -> None:
    components_css = load_components_css()

    assert ".automation-detail-head {" in components_css
    assert "grid-template-columns: minmax(0, 1fr) auto;" in components_css
    assert ".automation-detail-head .feature-action-row {" in components_css
    assert "justify-content: flex-end;" in components_css


def test_project_view_automation_editor_actions_keep_buttons_single_line() -> None:
    components_css = load_components_css()

    assert (
        ".automation-editor-modal .automation-editor-modal-content {" in components_css
    )
    assert "width: min(84vw, 1160px) !important;" in components_css
    assert ".automation-editor-actions {" in components_css
    assert "flex-wrap: nowrap;" in components_css
    assert ".automation-editor-actions .secondary-btn," in components_css
    assert "white-space: nowrap;" in components_css


def test_project_view_skills_feature_does_not_repeat_inner_title(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openSkillsFeatureView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

initializeProjectView();
await openSkillsFeatureView();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    projectViewTitle: els.projectViewTitle.textContent,
    contentHtml: els.projectViewContent.innerHTML,
}));
""".strip(),
        mock_api_source="""
export async function disableAutomationProject() {
    return { status: "disabled" };
}

export async function enableAutomationProject() {
    return { status: "enabled" };
}

export async function createAutomationProject() {
    return { automation_project_id: "aut_new" };
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function fetchAutomationProject() {
    throw new Error("not used");
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function fetchAutomationProjectSessions() {
    return [];
}

export async function fetchAutomationProjects() {
    return [];
}

export async function fetchWorkspaces() {
    return globalThis.__mockWorkspaces || [];
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
    throw new Error("not used");
}

export async function fetchConfigStatus() {
    return {
        skills: {
            skills: [
                {
                    name: "schedule-tasks",
                    description: "Run scheduled checks.",
                    ref: "schedule-tasks",
                    path: "/skills/schedule-tasks",
                    scope: "builtin",
                },
            ],
        },
    };
}

export async function fetchOrchestrationConfig() {
    return { presets: [] };
}

export async function fetchRoleConfigOptions() {
    return { normal_mode_roles: [] };
}

export async function fetchTriggers() {
    return [];
}

export async function fetchWeChatGatewayAccounts() {
    return [];
}

export async function reloadSkillsConfig() {
    return { status: "ok" };
}

export async function createTrigger() {
    return { status: "ok" };
}

export async function updateTrigger() {
    return { status: "ok" };
}

export async function deleteTrigger() {
    return { status: "ok" };
}

export async function enableTrigger() {
    return { status: "ok" };
}

export async function disableTrigger() {
    return { status: "ok" };
}

export async function startWeChatGatewayLogin() {
    return { session_key: "wechat-login-1", qr_code_url: "https://example.test/qr.png" };
}

export async function waitWeChatGatewayLogin() {
    return { connected: true };
}

export async function updateWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function enableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function disableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function deleteWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function updateAutomationProject() {
    return { status: "ok" };
}
""".strip(),
    )

    assert payload["projectViewTitle"] == "Skills"
    assert "<h3>Skills</h3>" not in str(payload["contentHtml"])


def test_project_view_opens_robot_dialog_in_gateway_feature(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openImFeatureView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

initializeProjectView();
await openImFeatureView();
await flushTasks();
await flushTasks();

const addButton = document.querySelector("[data-feature-gateway-add-feishu]");
addButton?.onclick?.();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    title: els.projectViewTitle.textContent,
    contentHtml: els.projectViewContent.innerHTML,
    modalHtml: globalThis.__bodyChildren.map(node => node.innerHTML).join("\\n"),
    showFormDialogCalls: globalThis.__showFormDialogCalls,
}));
""".strip(),
        mock_api_source="""
export async function disableAutomationProject() {
    return { status: "disabled" };
}

export async function enableAutomationProject() {
    return { status: "enabled" };
}

export async function createAutomationProject() {
    return { automation_project_id: "aut_new" };
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function fetchAutomationProject() {
    throw new Error("not used");
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function fetchAutomationProjectSessions() {
    return [];
}

export async function fetchAutomationProjects() {
    return [];
}

export async function fetchWorkspaces() {
    return [{ workspace_id: "default", root_path: "/work/default" }];
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

export async function fetchConfigStatus() {
    return { skills: { skills: [] } };
}

export async function fetchOrchestrationConfig() {
    return { presets: [] };
}

export async function fetchRoleConfigOptions() {
    return { normal_mode_roles: [] };
}

export async function fetchTriggers() {
    return [];
}

export async function fetchWeChatGatewayAccounts() {
    return [];
}

export async function reloadSkillsConfig() {
    return { status: "ok" };
}

export async function createTrigger() {
    return { status: "ok" };
}

export async function updateTrigger() {
    return { status: "ok" };
}

export async function deleteTrigger() {
    return { status: "ok" };
}

export async function enableTrigger() {
    return { status: "ok" };
}

export async function disableTrigger() {
    return { status: "ok" };
}

export async function startWeChatGatewayLogin() {
    return { session_key: "wechat-login-1", qr_code_url: "https://example.test/qr.png" };
}

export async function waitWeChatGatewayLogin() {
    return { connected: true };
}

export async function updateWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function enableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function disableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function deleteWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function updateAutomationProject() {
    return { status: "ok" };
}
""".strip(),
    )

    assert payload["title"] == "IM Gateway"
    assert payload["showFormDialogCalls"] == []
    assert "data-feature-gateway-modal" in str(payload["modalHtml"])
    assert 'id="feishu-trigger-name-input"' in str(payload["modalHtml"])
    assert 'id="feishu-app-id-input"' in str(payload["modalHtml"])
    assert 'id="feishu-app-secret-input"' in str(payload["modalHtml"])
    assert 'id="feishu-trigger-name-input"' not in str(payload["contentHtml"])


def test_project_view_opens_wechat_connect_modal_in_gateway_feature(
    tmp_path: Path,
) -> None:
    payload = _run_project_view_script(
        tmp_path=tmp_path,
        runner_source="""
import {
    initializeProjectView,
    openImFeatureView,
} from "./projectView.mjs";
import { els, flushTasks } from "./mockDom.mjs";

initializeProjectView();
await openImFeatureView();
await flushTasks();
await flushTasks();

const connectButton = document.querySelector("[data-feature-gateway-connect-wechat]");
connectButton?.onclick?.();
await flushTasks();
await flushTasks();
await flushTasks();

console.log(JSON.stringify({
    title: els.projectViewTitle.textContent,
    contentHtml: els.projectViewContent.innerHTML,
    modalHtml: globalThis.__bodyChildren.map(node => node.innerHTML).join("\\n"),
}));
""".strip(),
        mock_api_source="""
export async function disableAutomationProject() {
    return { status: "disabled" };
}

export async function enableAutomationProject() {
    return { status: "enabled" };
}

export async function createAutomationProject() {
    return { automation_project_id: "aut_new" };
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function fetchAutomationProject() {
    throw new Error("not used");
}

export async function fetchAutomationFeishuBindings() {
    return [];
}

export async function fetchAutomationProjectSessions() {
    return [];
}

export async function fetchAutomationProjects() {
    return [];
}

export async function fetchWorkspaces() {
    return [{ workspace_id: "default", root_path: "/work/default" }];
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

export async function fetchConfigStatus() {
    return { skills: { skills: [] } };
}

export async function fetchOrchestrationConfig() {
    return { presets: [] };
}

export async function fetchRoleConfigOptions() {
    return { normal_mode_roles: [] };
}

export async function fetchTriggers() {
    return [];
}

export async function fetchWeChatGatewayAccounts() {
    return [];
}

export async function reloadSkillsConfig() {
    return { status: "ok" };
}

export async function createTrigger() {
    return { status: "ok" };
}

export async function updateTrigger() {
    return { status: "ok" };
}

export async function deleteTrigger() {
    return { status: "ok" };
}

export async function enableTrigger() {
    return { status: "ok" };
}

export async function disableTrigger() {
    return { status: "ok" };
}

export async function startWeChatGatewayLogin() {
    return { session_key: "wechat-login-1", qr_code_url: "https://example.test/qr.png" };
}

export async function waitWeChatGatewayLogin() {
    return { connected: false, message: "Login failed." };
}

export async function updateWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function enableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function disableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function deleteWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function updateAutomationProject() {
    return { status: "ok" };
}
""".strip(),
    )

    assert payload["title"] == "IM Gateway"
    assert "data-feature-wechat-modal" in str(payload["modalHtml"])
    assert "https://example.test/qr.png" in str(payload["modalHtml"])
    assert "gateway-qr-card" not in str(payload["contentHtml"])


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
    mock_clawhub_settings_path = tmp_path / "settings" / "clawhubSettings.js"
    mock_github_settings_path = tmp_path / "settings" / "githubSettings.js"
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
        id: "",
        className: "",
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
        onchange: null,
        value: "",
        checked: false,
        style: {},
        classList: {
            add() {
                return undefined;
            },
            remove() {
                return undefined;
            },
        },
        addEventListener(name, handler) {
            if (name === "click") {
                this.onclick = handler;
            }
            if (name === "keydown") {
                this.onkeydown = handler;
            }
            if (name === "change") {
                this.onchange = handler;
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
        "[data-automation-run]": /data-automation-run/g,
        "[data-automation-editor-save]": /data-automation-editor-save/g,
        "[data-automation-editor-cancel]": /data-automation-editor-cancel/g,
        "[data-automation-editor-close]": /data-automation-editor-close/g,
        "[data-automation-editor-schedule-kind]": /id="automation-editor-schedule-kind-input"[\s\S]*?data-automation-editor-schedule-kind/g,
        "[data-automation-editor-binding]": /id="automation-editor-delivery-binding-input"[\s\S]*?data-automation-editor-binding/g,
        "[data-feature-gateway-add-feishu]": /data-feature-gateway-add-feishu/g,
        "[data-feature-gateway-connect-wechat]": /data-feature-gateway-connect-wechat/g,
    };
    const pattern = patterns[selector];
    const results = [];
    if (!pattern) {
        const dataSelectorMatch = /^\[(data-[a-z0-9_-]+)(?:="([^"]*)")?\]$/i.exec(selector);
        if (dataSelectorMatch) {
            const attrName = dataSelectorMatch[1];
            const attrValue = dataSelectorMatch[2];
            const escapedAttrName = attrName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
            const escapedAttrValue = String(attrValue || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
            const dataPattern = attrValue === undefined
                ? new RegExp(`${escapedAttrName}(?:="([^"]*)")?`, "g")
                : new RegExp(`${escapedAttrName}="${escapedAttrValue}"`, "g");
            let dataMatch = dataPattern.exec(source);
            while (dataMatch) {
                const attributes = {};
                attributes[attrName] = decodeHtmlAttribute(
                    attrValue === undefined ? (dataMatch[1] || "") : attrValue,
                );
                results.push(createTreeNode(attributes));
                dataMatch = dataPattern.exec(source);
            }
            return results;
        }
    }
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
        } else if (selector === "[data-automation-run]") {
            results.push(createTreeNode({}));
        } else if (selector === "[data-automation-editor-save]") {
            results.push(createTreeNode({}));
        } else if (selector === "[data-automation-editor-cancel]") {
            results.push(createTreeNode({}));
        } else if (selector === "[data-automation-editor-close]") {
            results.push(createTreeNode({}));
        } else if (selector === "[data-automation-editor-schedule-kind]") {
            const node = createTreeNode({});
            node.value = "daily";
            results.push(node);
        } else if (selector === "[data-automation-editor-binding]") {
            const node = createTreeNode({});
            node.value = "";
            results.push(node);
        } else if (selector === "[data-feature-gateway-add-feishu]") {
            results.push(createTreeNode({}));
        } else if (selector === "[data-feature-gateway-connect-wechat]") {
            results.push(createTreeNode({}));
        }
        match = pattern.exec(source);
    }
    return results;
}

function parseElementById(source, id) {
    const safeId = String(id || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const inputMatch = new RegExp(`<input[^>]*id="${safeId}"[^>]*>`, "i").exec(source);
    if (inputMatch) {
        const node = createTreeNode({ id });
        const markup = inputMatch[0];
        const valueMatch = /value="([^"]*)"/i.exec(markup);
        node.value = decodeHtmlAttribute(valueMatch ? valueMatch[1] : "");
        node.checked = /\schecked(?:\s|>)/i.test(markup);
        return node;
    }
    const textareaMatch = new RegExp(`<textarea[^>]*id="${safeId}"[^>]*>([\\s\\S]*?)<\\/textarea>`, "i").exec(source);
    if (textareaMatch) {
        const node = createTreeNode({ id });
        node.value = decodeHtmlAttribute(textareaMatch[1] || "");
        return node;
    }
    const selectMatch = new RegExp(`<select[^>]*id="${safeId}"[^>]*>([\\s\\S]*?)<\\/select>`, "i").exec(source);
    if (selectMatch) {
        const node = createTreeNode({ id });
        const selectedMatch = /<option[^>]*value="([^"]*)"[^>]*selected/i.exec(selectMatch[1]);
        const firstMatch = /<option[^>]*value="([^"]*)"/i.exec(selectMatch[1]);
        node.value = decodeHtmlAttribute((selectedMatch || firstMatch || [null, ""])[1] || "");
        return node;
    }
    return null;
}

function createHtmlElement() {
    let html = "";
    const cache = new Map();
    const idCache = new Map();
    return {
        id: "",
        className: "",
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
            idCache.clear();
        },
        querySelector(selector) {
            return this.querySelectorAll(selector)[0] || null;
        },
        querySelectorAll(selector) {
            if (selector.includes(",")) {
                return selector
                    .split(",")
                    .map(part => part.trim())
                    .flatMap(part => this.querySelectorAll(part));
            }
            if (!cache.has(selector)) {
                cache.set(selector, parseNodes(html, selector));
            }
            return cache.get(selector);
        },
        getElementById(id) {
            if (!idCache.has(id)) {
                idCache.set(id, parseElementById(html, id));
            }
            return idCache.get(id);
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
    const appendedChildren = [];
    globalThis.__bodyChildren = appendedChildren;

    return {
        body: {
            classList: {
                remove() {
                    return undefined;
                },
            },
            appendChild(node) {
                appendedChildren.push(node);
                if (node?.id) {
                    elements.set(node.id, node);
                }
                return node;
            },
        },
        addEventListener() {
            return undefined;
        },
        dispatchEvent(event) {
            globalThis.__dispatchedEvents.push({
                type: event?.type || null,
                detail: event?.detail || null,
            });
            return undefined;
        },
        querySelector(selector) {
            const toolbar = elements.get("project-view-toolbar-actions");
            const toolbarMatch = toolbar?.querySelector(selector);
            if (toolbarMatch) {
                return toolbarMatch;
            }
            const content = elements.get("project-view-content");
            const contentMatch = content?.querySelector(selector);
            if (contentMatch) {
                return contentMatch;
            }
            for (const child of appendedChildren) {
                const match = child?.querySelector?.(selector);
                if (match) {
                    return match;
                }
            }
            return null;
        },
        getElementById(id) {
            const element = elements.get(id);
            if (element) {
                return element;
            }
            for (const child of appendedChildren) {
                const match = child?.getElementById?.(id);
                if (match) {
                    return match;
                }
            }
            throw new Error(`Missing element: ${id}`);
        },
        createElement() {
            return createHtmlElement();
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

export async function createAutomationProject() {
    return { automation_project_id: "aut_new" };
}

export async function deleteAutomationProject() {
    return { status: "ok" };
}

export async function fetchAutomationProject() {
    return null;
}

export async function fetchAutomationProjects() {
    return [];
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

export async function fetchConfigStatus() {
    return { skills: { skills: [] } };
}

export async function fetchOrchestrationConfig() {
    return { presets: [] };
}

export async function fetchRoleConfigOptions() {
    return { normal_mode_roles: [] };
}

export async function fetchTriggers() {
    return [];
}

export async function fetchWeChatGatewayAccounts() {
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

export async function openWorkspaceRoot(workspaceId, mount = null) {
    globalThis.__openWorkspaceRootCalls.push({ workspaceId, mount });
    return { status: "ok" };
}

export async function fetchWorkspaceTree(workspaceId, path, mount = null) {
    globalThis.__treeRequests.push({ workspaceId, path, mount });
    return {
        workspace_id: workspaceId,
        mount_name: mount || "default",
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

export async function fetchWorkspaceDiffs(workspaceId, mount = null) {
    await new Promise(resolve => setTimeout(resolve, 0));
    globalThis.__diffRequests.push({ workspaceId, mount });
    return {
        workspace_id: workspaceId,
        mount_name: mount || "default",
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

export async function fetchWorkspaceDiffFile(workspaceId, path, mount = null) {
    await new Promise(resolve => setTimeout(resolve, 0));
    globalThis.__diffFileRequests.push({ workspaceId, path, mount });
    return {
        workspace_id: workspaceId,
        mount_name: mount || "default",
        path,
        change_type: "modified",
        diff: "changed file",
        is_binary: false,
    };
}

export async function runAutomationProject() {
    return { status: "ok" };
}

export async function reloadSkillsConfig() {
    return { status: "ok" };
}

export async function createTrigger() {
    return { status: "ok" };
}

export async function updateTrigger() {
    return { status: "ok" };
}

export async function deleteTrigger() {
    return { status: "ok" };
}

export async function enableTrigger() {
    return { status: "ok" };
}

export async function disableTrigger() {
    return { status: "ok" };
}

export async function startWeChatGatewayLogin() {
    return { session_key: "wechat-login-1", qr_code_url: "https://example.test/qr.png" };
}

export async function waitWeChatGatewayLogin() {
    return { connected: true };
}

export async function updateWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function enableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function disableWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function deleteWeChatGatewayAccount() {
    return { status: "ok" };
}

export async function updateAutomationProject() {
    return { status: "ok" };
}

export async function createGitHubTriggerAccount(payload) {
    globalThis.__createdGitHubAccountPayload = payload;
    return { account_id: "ghta_new", name: payload?.name || "github-main", display_name: payload?.display_name || "GitHub Main", status: payload?.enabled === false ? "disabled" : "enabled" };
}

export async function updateGitHubTriggerAccount(accountId, payload) {
    globalThis.__updatedGitHubAccountPayload = { accountId, payload };
    return { account_id: accountId, name: payload?.name || "github-main", display_name: payload?.display_name || "GitHub Main", status: payload?.enabled === false ? "disabled" : "enabled" };
}

export async function deleteGitHubTriggerAccount(accountId) {
    globalThis.__deletedGitHubAccountId = accountId;
    return { status: "ok" };
}

export async function enableGitHubTriggerAccount(accountId) {
    globalThis.__enabledGitHubAccountId = accountId;
    return { account_id: accountId, status: "enabled" };
}

export async function disableGitHubTriggerAccount(accountId) {
    globalThis.__disabledGitHubAccountId = accountId;
    return { account_id: accountId, status: "disabled" };
}

export async function createGitHubRepoSubscription(payload) {
    globalThis.__createdGitHubRepoPayload = payload;
    return { repo_subscription_id: "ghrs_new", account_id: payload?.account_id || "ghta_1", full_name: `${payload?.owner || "octocat"}/${payload?.repo_name || "Hello-World"}` };
}

export async function updateGitHubRepoSubscription(repoSubscriptionId, payload) {
    globalThis.__updatedGitHubRepoPayload = { repoSubscriptionId, payload };
    return { repo_subscription_id: repoSubscriptionId, account_id: "ghta_1", full_name: `${payload?.owner || "octocat"}/${payload?.repo_name || "Hello-World"}` };
}

export async function deleteGitHubRepoSubscription(repoSubscriptionId) {
    globalThis.__deletedGitHubRepoId = repoSubscriptionId;
    return { status: "ok" };
}

export async function enableGitHubRepoSubscription(repoSubscriptionId) {
    globalThis.__enabledGitHubRepoId = repoSubscriptionId;
    return { repo_subscription_id: repoSubscriptionId, enabled: true };
}

export async function disableGitHubRepoSubscription(repoSubscriptionId) {
    globalThis.__disabledGitHubRepoId = repoSubscriptionId;
    return { repo_subscription_id: repoSubscriptionId, enabled: false };
}

export async function createGitHubTriggerRule(payload) {
    globalThis.__createdGitHubRulePayload = payload;
    return {
        trigger_rule_id: "trg_new",
        provider: payload?.provider || "github",
        account_id: payload?.account_id || "ghta_1",
        repo_subscription_id: payload?.repo_subscription_id || "ghrs_1",
        name: payload?.name || "rule",
        enabled: payload?.enabled !== false,
        match_config: payload?.match_config || {},
        dispatch_config: payload?.dispatch_config || {},
    };
}

export async function updateGitHubTriggerRule(triggerRuleId, payload) {
    globalThis.__updatedGitHubRulePayload = { triggerRuleId, payload };
    return {
        trigger_rule_id: triggerRuleId,
        provider: "github",
        account_id: payload?.account_id || "ghta_1",
        repo_subscription_id: payload?.repo_subscription_id || "ghrs_1",
        name: payload?.name || "rule",
        enabled: payload?.enabled !== false,
        match_config: payload?.match_config || {},
        dispatch_config: payload?.dispatch_config || {},
    };
}

export async function deleteGitHubTriggerRule(triggerRuleId) {
    globalThis.__deletedGitHubRuleId = triggerRuleId;
    return { status: "ok" };
}

export async function enableGitHubTriggerRule(triggerRuleId) {
    globalThis.__enabledGitHubRuleId = triggerRuleId;
    return { trigger_rule_id: triggerRuleId, enabled: true };
}

export async function disableGitHubTriggerRule(triggerRuleId) {
    globalThis.__disabledGitHubRuleId = triggerRuleId;
    return { trigger_rule_id: triggerRuleId, enabled: false };
}
""".strip()
    resolved_mock_api_source = (
        _merge_mock_api_source(default_mock_api_source, mock_api_source)
        if mock_api_source
        else default_mock_api_source
    )
    required_api_fallbacks = {
        "openWorkspaceRoot": """
export async function openWorkspaceRoot(workspaceId, mount = null) {
    globalThis.__openWorkspaceRootCalls = globalThis.__openWorkspaceRootCalls || [];
    globalThis.__openWorkspaceRootCalls.push({ workspaceId, mount });
    return { status: "ok" };
}
""".strip(),
        "fetchGitHubTriggerAccounts": """
export async function fetchGitHubTriggerAccounts() {
    return globalThis.__mockGitHubAccounts || [];
}
""".strip(),
        "fetchGitHubRepoSubscriptions": """
export async function fetchGitHubRepoSubscriptions() {
    return globalThis.__mockGitHubRepos || [];
}
""".strip(),
        "fetchGitHubAccountRepositories": """
export async function fetchGitHubAccountRepositories() {
    return globalThis.__mockGitHubAvailableRepos || [];
}
""".strip(),
        "fetchGitHubTriggerRules": """
export async function fetchGitHubTriggerRules() {
    return globalThis.__mockGitHubRules || [];
}
""".strip(),
        "createGitHubTriggerAccount": """
export async function createGitHubTriggerAccount(payload) {
    globalThis.__createdGitHubAccountPayload = payload;
    return { account_id: "ghta_new", name: payload?.name || "github-main", display_name: payload?.display_name || "GitHub Main", status: payload?.enabled === false ? "disabled" : "enabled" };
}
""".strip(),
        "updateGitHubTriggerAccount": """
export async function updateGitHubTriggerAccount(accountId, payload) {
    globalThis.__updatedGitHubAccountPayload = { accountId, payload };
    return { account_id: accountId, name: payload?.name || "github-main", display_name: payload?.display_name || "GitHub Main", status: payload?.enabled === false ? "disabled" : "enabled" };
}
""".strip(),
        "deleteGitHubTriggerAccount": """
export async function deleteGitHubTriggerAccount(accountId) {
    globalThis.__deletedGitHubAccountId = accountId;
    return { status: "ok" };
}
""".strip(),
        "enableGitHubTriggerAccount": """
export async function enableGitHubTriggerAccount(accountId) {
    globalThis.__enabledGitHubAccountId = accountId;
    return { account_id: accountId, status: "enabled" };
}
""".strip(),
        "disableGitHubTriggerAccount": """
export async function disableGitHubTriggerAccount(accountId) {
    globalThis.__disabledGitHubAccountId = accountId;
    return { account_id: accountId, status: "disabled" };
}
""".strip(),
        "createGitHubRepoSubscription": """
export async function createGitHubRepoSubscription(payload) {
    globalThis.__createdGitHubRepoPayload = payload;
    return { repo_subscription_id: "ghrs_new", account_id: payload?.account_id || "ghta_1", full_name: `${payload?.owner || "octocat"}/${payload?.repo_name || "Hello-World"}` };
}
""".strip(),
        "updateGitHubRepoSubscription": """
export async function updateGitHubRepoSubscription(repoSubscriptionId, payload) {
    globalThis.__updatedGitHubRepoPayload = { repoSubscriptionId, payload };
    return { repo_subscription_id: repoSubscriptionId, account_id: "ghta_1", full_name: `${payload?.owner || "octocat"}/${payload?.repo_name || "Hello-World"}` };
}
""".strip(),
        "deleteGitHubRepoSubscription": """
export async function deleteGitHubRepoSubscription(repoSubscriptionId) {
    globalThis.__deletedGitHubRepoId = repoSubscriptionId;
    return { status: "ok" };
}
""".strip(),
        "enableGitHubRepoSubscription": """
export async function enableGitHubRepoSubscription(repoSubscriptionId) {
    globalThis.__enabledGitHubRepoId = repoSubscriptionId;
    return { repo_subscription_id: repoSubscriptionId, enabled: true };
}
""".strip(),
        "disableGitHubRepoSubscription": """
export async function disableGitHubRepoSubscription(repoSubscriptionId) {
    globalThis.__disabledGitHubRepoId = repoSubscriptionId;
    return { repo_subscription_id: repoSubscriptionId, enabled: false };
}
""".strip(),
        "createGitHubTriggerRule": """
export async function createGitHubTriggerRule(payload) {
    globalThis.__createdGitHubRulePayload = payload;
    return { trigger_rule_id: "trg_new", repo_subscription_id: payload?.repo_subscription_id || "ghrs_1", name: payload?.name || "rule" };
}
""".strip(),
        "updateGitHubTriggerRule": """
export async function updateGitHubTriggerRule(triggerRuleId, payload) {
    globalThis.__updatedGitHubRulePayload = { triggerRuleId, payload };
    return { trigger_rule_id: triggerRuleId, repo_subscription_id: "ghrs_1", name: payload?.name || "rule", enabled: payload?.enabled !== false };
}
""".strip(),
        "deleteGitHubTriggerRule": """
export async function deleteGitHubTriggerRule(triggerRuleId) {
    globalThis.__deletedGitHubRuleId = triggerRuleId;
    return { status: "ok" };
}
""".strip(),
        "enableGitHubTriggerRule": """
export async function enableGitHubTriggerRule(triggerRuleId) {
    globalThis.__enabledGitHubRuleId = triggerRuleId;
    return { trigger_rule_id: triggerRuleId, enabled: true };
}
""".strip(),
        "disableGitHubTriggerRule": """
export async function disableGitHubTriggerRule(triggerRuleId) {
    globalThis.__disabledGitHubRuleId = triggerRuleId;
    return { trigger_rule_id: triggerRuleId, enabled: false };
}
""".strip(),
    }
    for export_name, export_source in required_api_fallbacks.items():
        if f"export async function {export_name}" not in resolved_mock_api_source:
            resolved_mock_api_source = f"{resolved_mock_api_source}\n\n{export_source}"
    mock_api_path.write_text(
        resolved_mock_api_source,
        encoding="utf-8",
    )

    mock_state_path.write_text(
        """
export const state = {
    currentMainView: "session",
    currentProjectViewWorkspaceId: null,
    currentWorkspaceId: null,
    currentFeatureViewId: null,
};
""".strip(),
        encoding="utf-8",
    )

    mock_i18n_path.write_text(
        """
    const translations = {
        "workspace_view.title": "{workspace} Project",
        "workspace_view.bindings": "Bindings",
        "workspace_view.tree": "Files",
        "workspace_view.mounts": "Mounts",
        "workspace_view.mount_default": "Default",
        "workspace_view.mount_profile": "SSH profile",
        "workspace_view.mount_provider.local": "Local",
        "workspace_view.mount_provider.ssh": "SSH",
        "workspace_view.mount_provider.unknown": "Mount",
        "workspace_view.open_root": "Open project folder",
        "workspace_view.open_root_failed": "Failed to open project folder",
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
        "workspace_view.delivery_disabled": "Disabled",
        "workspace_view.delivery_events": "Delivery events",
        "workspace_view.feishu_trigger": "Feishu trigger",
        "workspace_view.feishu_chat": "Feishu chat",
        "workspace_view.chat_type": "Chat type",
        "workspace_view.delivery_help_feishu": "Automation updates will be pushed to the selected Feishu chat.",
        "settings.action.delete": "Delete",
        "settings.action.cancel": "Cancel",
        "settings.system.skills_reloaded": "Skills Reloaded",
        "settings.system.skills_reloaded_message": "Skills reloaded.",
        "settings.system.reload_failed": "Reload Failed",
        "settings.triggers.feishu_detail_copy": "Manage Feishu inbound accounts.",
        "settings.triggers.none": "No Feishu triggers",
        "settings.triggers.none_copy": "Add a Feishu trigger.",
        "settings.triggers.trigger_name": "Trigger Name",
        "settings.triggers.display_name": "Display Name",
        "settings.triggers.workspace": "Workspace ID",
        "settings.triggers.rule": "Trigger Rule",
        "settings.triggers.saved": "Saved",
        "settings.triggers.saved_message": "Feishu settings saved.",
        "settings.triggers.save_failed": "Save failed",
        "settings.triggers.bot_configuration": "Bot Configuration",
        "settings.triggers.session_configuration": "Session Configuration",
        "settings.triggers.feishu_app_name": "Feishu App Name",
        "settings.triggers.feishu_app_name_placeholder": "Agent Teams Bot",
        "settings.triggers.feishu_app_id": "Feishu App ID",
        "settings.triggers.feishu_app_id_placeholder": "cli_xxx",
        "settings.triggers.feishu_app_secret": "Feishu App Secret",
        "settings.triggers.feishu_app_secret_placeholder": "App secret",
        "settings.triggers.secret_keep_placeholder": "Configured. Leave blank to keep current value.",
        "settings.triggers.no_workspaces": "No workspaces",
        "settings.triggers.missing_name": "Trigger name is required.",
        "settings.triggers.missing_workspace": "Workspace ID is required.",
        "settings.triggers.missing_app_id": "App ID is required.",
        "settings.triggers.missing_app_name": "App name is required.",
        "settings.triggers.missing_app_secret": "App secret is required.",
        "settings.triggers.missing_orchestration_preset_id": "Preset is required in orchestration mode.",
        "settings.triggers.yolo": "YOLO",
        "settings.triggers.thinking_enabled": "Thinking Enabled",
        "settings.triggers.thinking_effort": "Thinking Effort",
        "settings.roles.edit": "Edit",
        "settings.triggers.delete_confirm_title": "Delete trigger",
        "settings.triggers.delete_confirm_message": "Delete trigger {name}?",
        "settings.triggers.deleted": "Deleted",
        "settings.triggers.deleted_message": "Trigger deleted.",
        "settings.gateway.connect_wechat": "Connect WeChat",
        "settings.gateway.wechat_none": "No WeChat accounts",
        "settings.gateway.wechat_none_copy": "Connect a WeChat account.",
        "settings.gateway.qr_title": "Scan To Connect",
        "settings.gateway.qr_copy": "Scan this QR code in WeChat.",
        "settings.gateway.login_waiting": "Waiting for QR scan confirmation...",
        "settings.gateway.login_failed": "WeChat login failed.",
        "settings.gateway.login_success": "WeChat connected.",
        "settings.gateway.status_running": "Running",
        "settings.gateway.enable_account": "Enable account",
        "settings.gateway.disable_account": "Disable account",
        "settings.gateway.delete_confirm_title": "Delete account",
        "settings.gateway.delete_confirm_message": "Delete account {name}?",
        "settings.gateway.saved": "Saved",
        "settings.gateway.saved_message": "WeChat account saved.",
        "settings.gateway.save_failed": "Save failed",
        "settings.gateway.deleted": "Deleted",
        "settings.gateway.deleted_message": "WeChat account deleted.",
        "feature.skills.title": "Skills",
        "feature.skills.directory_title": "Installed Skills",
        "feature.skills.summary": "{count} skills available",
        "feature.skills.empty": "No skills loaded",
        "feature.skills.empty_copy": "Reload after updating the configured skill directories.",
        "feature.skills.reload": "Reload Skills",
        "feature.skills.scope_builtin": "Built-in",
        "feature.skills.scope_app": "App",
        "feature.skills.scope_unknown": "Skill",
        "feature.automation.title": "Automation",
        "feature.automation.summary": "{count} schedules",
        "feature.automation.empty": "No automation projects",
        "feature.automation.empty_copy": "Create a scheduled project.",
        "feature.automation.create": "New Automation",
        "feature.automation.select": "Select an automation project from the list.",
        "feature.automation.create_first": "Create Automation",
        "feature.automation.section_schedules": "Schedules",
        "feature.automation.section_github": "GitHub",
        "feature.automation.github_summary": "{accounts} accounts · {repos} repos · {rules} rules",
        "feature.automation.github_access": "GitHub Access",
        "feature.automation.github_access_copy": "Shared token and connectivity checks for GitHub-triggered automation.",
        "feature.automation.github_access_status": "Shared",
        "feature.automation.github_access_detail_copy": "Shared token is reused when an account does not define its own override.",
        "feature.automation.github_summary_accounts": "Accounts",
        "feature.automation.github_summary_repos": "Repositories",
        "feature.automation.github_summary_rules": "Rules",
        "feature.automation.github_new_account": "New Account",
        "feature.automation.github_new_repo": "New Repo",
        "feature.automation.github_new_rule": "New Rule",
        "feature.automation.github_repo_copy": "Choose a repository visible to this account token. The webhook callback URL is generated automatically.",
        "feature.automation.github_rule_name": "Rule Name",
        "feature.automation.github_account": "Account",
        "feature.automation.github_repo_name": "Repository",
        "feature.automation.github_repo_select_copy": "Repository choices are fetched with the effective GitHub token for this account.",
        "feature.automation.github_repo_select_placeholder": "Select a repository",
        "feature.automation.github_repo_section": "Repositories",
        "feature.automation.github_rule_section": "Rules",
        "feature.automation.github_event_subscription": "Subscribed Event",
        "feature.automation.github_event_copy": "Select the GitHub webhook event for this rule. The repository subscribed events are derived automatically from enabled rules.",
        "feature.automation.github_event_pull_request": "Pull Request",
        "feature.automation.github_event_issues": "Issues",
        "feature.automation.github_actions": "Actions",
        "feature.automation.github_actions_placeholder": "Select actions",
        "feature.automation.github_actions_copy": "Select one or more GitHub actions. Pull Request options include opened, reopened, edited, synchronize, and review_requested. Issues typically use opened, reopened, and edited.",
        "feature.automation.github_draft_pr": "Draft Pull Request",
        "feature.automation.github_draft_pr_any": "Any",
        "feature.automation.github_draft_pr_false": "Ready for review only",
        "feature.automation.github_draft_pr_true": "Draft only",
        "feature.automation.github_base_branches": "Base Branches",
        "feature.automation.github_base_branches_all": "All branches",
        "feature.automation.github_webhook_registered": "Registered",
        "feature.automation.github_webhook_unregistered": "Unregistered",
        "feature.automation.github_webhook_error": "Error",
        "feature.automation.github_no_accounts": "No GitHub accounts",
        "feature.automation.github_no_accounts_copy": "Create an account to start binding repositories.",
        "feature.automation.github_no_repos": "No repositories",
        "feature.automation.github_no_repos_copy": "Create a repository subscription under this account.",
        "feature.automation.github_no_rules": "No rules",
        "feature.automation.github_no_rules_copy": "Create a rule for this repository.",
        "feature.automation.github_open_webhooks": "Open Webhooks",
        "feature.automation.github_callback_url": "Callback URL",
        "feature.automation.github_webhook_status": "Webhook Status",
        "feature.automation.github_default_branch": "Default Branch",
        "feature.automation.github_events": "Subscribed Events",
        "feature.automation.github_account_token": "Account Token",
        "feature.automation.github_account_secret": "Webhook Secret",
        "feature.automation.github_configured": "Configured",
        "feature.automation.github_not_configured": "Not configured",
        "feature.automation.github_show_webhook_secret": "Show Webhook Secret",
        "feature.automation.github_hide_webhook_secret": "Hide Webhook Secret",
        "settings.github.show_token": "Show GitHub token",
        "settings.github.hide_token": "Hide GitHub token",
        "feature.automation.github_account_required": "Account name is required.",
        "feature.automation.github_repo_required": "Repository name is required.",
        "feature.automation.github_repo_options_empty": "No repositories are available for this account token.",
        "feature.automation.github_saved_title": "Saved",
        "feature.automation.github_failed_title": "Save failed",
        "feature.automation.github_deleted_title": "Deleted",
        "feature.gateway.title": "IM Gateway",
        "feature.gateway.summary": "{feishu} Feishu · {wechat} WeChat",
        "feature.gateway.add_feishu": "Add Robot",
        "feature.gateway.feishu_section": "Feishu",
        "feature.gateway.wechat_section": "WeChat",
        "composer.no_roles": "No roles",
        "composer.no_presets": "No presets",
        "composer.mode_normal": "Normal Mode",
        "composer.mode_orchestration": "Orchestration",
        "automation.field.workspace": "Workspace",
        "automation.workspace.directory": "Workspace directory",
        "automation.workspace.missing": "Workspace missing",
        "automation.workspace.help": "Automation notifications are currently disabled.",
        "automation.status.enabled": "Enabled",
        "automation.status.disabled": "Disabled",
        "automation.action.edit": "Edit",
        "automation.action.run_now": "Run now",
        "automation.action.disable": "Disable",
        "automation.action.enable": "Enable",
        "automation.detail.configuration": "Configuration",
        "automation.detail.none": "None",
        "automation.detail.prompt": "Task Prompt",
        "automation.detail.overview_copy": "Review schedule and recent runs.",
        "automation.detail.schedule": "Schedule",
        "automation.detail.timezone": "Timezone",
        "automation.detail.next_run": "Next run",
        "automation.detail.last_run": "Last run",
        "automation.detail.updated_at": "Updated at",
        "automation.detail.recent_runs": "Recent runs",
        "automation.detail.no_runs": "No runs yet.",
        "automation.detail.not_scheduled": "Not scheduled",
        "automation.detail.never": "Never",
        "automation.run_status.completed": "Completed",
        "sidebar.log.started_bound_session": "Started automation run in bound IM session: {session_id}",
        "sidebar.log.started_automation_run": "Started automation run: {session_id}",
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
    globalThis.__logs.push(Array.from(arguments).map(value => String(value)).join(" "));
}
""".strip(),
        encoding="utf-8",
    )
    mock_feedback_path.write_text(
        """
export async function showFormDialog(options = {}) {
    globalThis.__showFormDialogCalls.push(options);
    const result = globalThis.__showFormDialogResult ?? null;
    if (result && typeof options.submitHandler === "function") {
        return await options.submitHandler(result);
    }
    return result;
}

export async function showConfirmDialog() {
    return true;
}

export function showToast(payload = {}) {
    globalThis.__toastCalls = globalThis.__toastCalls || [];
    globalThis.__toastCalls.push(payload);
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
    mock_clawhub_settings_path.parent.mkdir(parents=True, exist_ok=True)
    mock_clawhub_settings_path.write_text(
        """
export function bindClawHubSettingsHandlers() {
    globalThis.__clawhubSettingsBindCalls =
        (globalThis.__clawhubSettingsBindCalls || 0) + 1;
}

export async function loadClawHubSettingsPanel() {
    globalThis.__clawhubSettingsLoadCalls =
        (globalThis.__clawhubSettingsLoadCalls || 0) + 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_github_settings_path.write_text(
        """
export function bindGitHubSettingsHandlers() {
    globalThis.__githubSettingsBindCalls =
        (globalThis.__githubSettingsBindCalls || 0) + 1;
}

export async function loadGitHubSettingsPanel() {
    globalThis.__githubSettingsLoadCalls =
        (globalThis.__githubSettingsLoadCalls || 0) + 1;
}

export function renderGitHubAccessPanelMarkup() {
    return '<div id="feature-github-access-panel">GitHub access panel</div>';
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
        .replace("./settings/githubSettings.js", "./settings/githubSettings.js")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
import {{ createDomEnvironment, installGlobals }} from "./mockDom.mjs";

globalThis.__snapshotRequests = [];
globalThis.__diffRequests = [];
globalThis.__diffFileRequests = [];
globalThis.__treeRequests = [];
globalThis.__openWorkspaceRootCalls = [];
globalThis.__showFormDialogResult = null;
globalThis.__showFormDialogCalls = [];
globalThis.__dispatchedEvents = [];
globalThis.__logs = [];
globalThis.__toastCalls = [];
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
