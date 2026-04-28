# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_main_agent_role_alias_is_primary_before_options_load(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "frontend" / "dist" / "js" / "core" / "state.js").read_text(
        encoding="utf-8"
    )
    (tmp_path / "state.mjs").write_text(source, encoding="utf-8")
    (tmp_path / "runner.mjs").write_text(
        """
globalThis.document = {
  getElementById() { return null; },
  querySelector() { return null; },
};

const {
  getMainAgentRoleId,
  isMainAgentRoleId,
  isRunPrimaryRoleId,
  state,
} = await import('./state.mjs');

state.currentSessionMode = 'normal';
state.mainAgentRoleId = null;
state.currentNormalRootRoleId = null;
state.runPrimaryRoleMap = {};

console.log(JSON.stringify({
  defaultMainAgentRoleId: getMainAgentRoleId(),
  mainAgentPrimary: isRunPrimaryRoleId('MainAgent', 'run-1'),
  spacedMainAgentPrimary: isRunPrimaryRoleId('Main Agent', 'run-1'),
  mainAgentAlias: isMainAgentRoleId('main_agent'),
  workerPrimary: isRunPrimaryRoleId('Worker', 'run-1'),
}));
""".strip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "runner.mjs"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=3,
    )

    payload = json.loads(result.stdout)
    assert payload == {
        "defaultMainAgentRoleId": "MainAgent",
        "mainAgentPrimary": True,
        "spacedMainAgentPrimary": True,
        "mainAgentAlias": True,
        "workerPrimary": False,
    }
