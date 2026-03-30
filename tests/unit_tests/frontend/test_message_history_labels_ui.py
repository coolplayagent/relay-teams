# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_message_history_uses_task_prompt_override_for_user_messages(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "history.js"
    )
    module_under_test_path = tmp_path / "history.mjs"
    runner_path = tmp_path / "runner.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../core/state.js", "./mockState.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockHelpers.mjs").write_text(
        """
export function applyToolReturn() {
    return undefined;
}

export function appendMessageText() {
    return undefined;
}

export function appendThinkingText() {
    return undefined;
}

export function buildToolBlock() {
    return {};
}

export function decoratePendingApprovalBlock() {
    return undefined;
}

export function findToolBlockInContainer() {
    return null;
}

export function forceScrollBottom() {
    return undefined;
}

export function indexPendingToolBlock() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockHelpers.mjs").write_text(
        """
export function applyToolReturn() {
    return undefined;
}

export function appendMessageText() {
    return undefined;
}

export function appendThinkingText() {
    return undefined;
}

export function buildToolBlock() {
    return {};
}

export function decoratePendingApprovalBlock() {
    return undefined;
}

export function findToolBlockInContainer() {
    return null;
}

export function forceScrollBottom() {
    return undefined;
}

export function indexPendingToolBlock() {
    return undefined;
}

export function labelFromRole() {
    return 'System';
}

export function parseApprovalArgsPreview() {
    return {};
}

export function renderMessageBlock(container, role, label) {
    globalThis.__renderCalls.push({ role, label });
    return {
        contentEl: {
            appendChild() {
                return undefined;
            },
            querySelector() {
                return null;
            },
        },
    };
}

export function renderParts() {
    return undefined;
}

export function resolvePendingToolBlock() {
    return null;
}

export function setToolStatus() {
    return undefined;
}

export function setToolValidationFailureState() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export function isRunPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__renderCalls = [];

const { renderHistoricalMessageList } = await import('./history.mjs');

renderHistoricalMessageList(
    {
        querySelector() {
            return null;
        },
        appendChild() {
            return undefined;
        },
    },
    [
        {
            role: 'user',
            role_id: 'writer',
            instance_id: 'inst-1',
            message: {
                parts: [
                    {
                        part_kind: 'user-prompt',
                        content: 'Draft the response.',
                    },
                ],
            },
        },
    ],
    {
        userRoleLabel: 'Task Prompt',
    },
);

console.log(JSON.stringify(globalThis.__renderCalls));
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

    payload = json.loads(completed.stdout)
    assert payload == [{"role": "user", "label": "Task Prompt"}]
