from __future__ import annotations

from pathlib import Path

import subprocess


def test_core_api_facade_exports_update_session() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    api_module_path = repo_root / "frontend" / "dist" / "js" / "core" / "api.js"

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "globalThis.document = {"
                "querySelector() { return null; },"
                "querySelectorAll() { return []; },"
                "getElementById() { return null; },"
                "body: null"
                "}; "
                f"const mod = await import({api_module_path.as_uri()!r}); "
                "console.log(typeof mod.updateSession);"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert completed.stdout.strip() == "function"
