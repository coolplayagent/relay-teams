# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
from io import BytesIO
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
from types import ModuleType
from types import SimpleNamespace
from typing import cast

import pytest

from relay_teams.builtin import get_builtin_skills_dir
from relay_teams.skills.skill_registry import SkillRegistry


def _load_wrapper_module() -> ModuleType:
    script_path = (
        get_builtin_skills_dir()
        / "relay-knowledge"
        / "scripts"
        / "relay_knowledge_cli.py"
    )
    spec = importlib.util.spec_from_file_location(
        "relay_knowledge_cli_test_module",
        script_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _child_process_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        normalized_key = key.upper()
        if normalized_key.startswith("COV_CORE_") or normalized_key.startswith(
            "COVERAGE_",
        ):
            env.pop(key)
    return env


def test_builtin_relay_knowledge_skill_is_discoverable(tmp_path: Path) -> None:
    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / "skills",
        builtin_skills_dir=get_builtin_skills_dir(),
    )

    skill = registry.get_skill_definition("relay-knowledge")

    assert skill is not None
    assert skill.metadata.name == "relay-knowledge"
    assert "scripts/relay_knowledge_cli.py" in skill.metadata.resources


def test_builtin_relay_knowledge_skill_constrains_cli_commands() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert "## Command Allowlist" in content
    assert "Allowed `repo` subcommands" in content
    assert "Do not use `repo list`" in content
    assert "Do not run bare `repo`" in content
    assert "- `repo`\n" not in content
    assert "- `health`" in content
    assert "- `version`" in content
    assert "- `help`" in content
    assert "- `ingest`" in content
    assert "- `provider probe`" in content
    assert "- `proposal list`" in content
    assert "- `service definition write`" in content
    assert "- `service operator pause`" in content
    assert "- `repo register`" in content
    assert "- `repo status`" in content


def test_builtin_relay_knowledge_skill_declares_global_flags() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert "## Global Flags" in content
    assert "- `--version`" in content
    assert "- `--help`" in content
    assert "- `--format text|json|markdown|streaming-json`" in content
    assert 'relay_knowledge_cli.py" -- --version' in content
    assert "Do not use `--format streaming-json` with `version`" in content
    assert "Prefer `text` or `json` for `help`." in content


def test_builtin_relay_knowledge_skill_requires_synchronous_long_timeout_index() -> (
    None
):
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert "Do not use wrapper `--detach`." in content
    assert 'relay_knowledge_cli.py" --detach' not in content
    assert "--timeout 1140 -- repo index" in content
    assert "--timeout 1200 -- repo index" not in content
    assert "outer command timeout" in content
    assert "`1200000` ms" in content
    assert "`1800000` ms" not in content
    assert "`3600000` ms" not in content
    assert "treat the index as ready only when `task.state` is `succeeded`" in content
    assert "use `task.state` as the authoritative state" in content
    assert "Use `status.state=fresh` only when the response has no `task`" in content
    assert "do not start `repo query`, `repo impact`, or `repo report`" in content
    assert "shell(command='python" in content
    assert (
        "repo index <alias> --ref HEAD --format json', timeout_ms=1200000)" in content
    )
    register_block = content.split("Register and index a repository:", 1)[1].split(
        "When running `repo index`",
        1,
    )[0]
    assert "repo status <alias>" not in register_block
    assert (
        "Only run `repo status <alias> --format json` after `repo index` "
        "if the index response does not already prove" in content
    )


def test_builtin_relay_knowledge_skill_constrains_repo_index_options() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert "Do not pass `--path` or `--language` to `repo index`" in content
    assert "only accepts the alias plus `--ref` and `--dry-run`" in content
    assert (
        "Use `--path <filter>` and repeated `--language <id>` on `repo register`, "
        "`repo index`, `repo query`" not in content
    )


def test_builtin_relay_knowledge_skill_does_not_register_for_diagnostics() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert (
        "For diagnostic or status-only requests, do not register a repository just "
        "to discover an alias" in content
    )
    assert "ask for the alias or report that the alias is required" in content
    assert "repository registration" in content


def test_builtin_relay_knowledge_skill_requires_background_service_run() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    service_block = content.split("Service and MCP access:", 1)[1].split(
        "Do not run `service run`",
        1,
    )[0]

    assert "Do not run `service run` as a normal synchronous shell command." in content
    assert "outer shell tool `background=true`" in content
    assert "shell(command=" in content
    assert "background=true)" in content
    assert "Run `service run` only as an outer background shell task." in content
    assert "service run --web --mcp streamable-http" not in service_block


def test_builtin_relay_knowledge_skill_lists_setup_profiles() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert "setup profile local" in content
    assert "setup profile agent-readonly" in content
    assert "setup profile service" in content
    assert "setup profile external-embedding" in content
    assert "Use `setup profile` only with one of" in content


def test_builtin_relay_knowledge_skill_lists_parameter_enums() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert (
        "Allowed `repo query --kind` values: `hybrid`, `symbol`, `definition`, "
        "`references`, `callers`, `callees`, `imports`." in content
    )
    assert (
        "Allowed `index refresh --kind` values: `bm25`, `semantic`, `vector`."
        in content
    )
    assert (
        "Allowed `worker --kind` values: `embedding`, `ocr`, `vision`, "
        "`extractor`." in content
    )
    assert (
        "Allowed `--freshness` values for knowledge and repo queries: "
        "`allow-stale`, `wait-until-fresh`, `graph-only`." in content
    )
    assert "Use `allow-stale` by default." in content
    assert "Use `wait-until-fresh` only when the user explicitly asks" in content
    assert (
        "For `--freshness wait-until-fresh`, use wrapper `--timeout 1140` "
        "and outer `timeout_ms=1200000`." in content
    )
    assert (
        "Allowed `proposal list --state` values: `proposed`, `accepted`, "
        "`rejected`, `superseded`." in content
    )


def test_builtin_relay_knowledge_skill_requires_explicit_mutation_request() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert "Only run mutation commands when the user explicitly asks" in content
    assert "`repo index-worker`" in content
    assert "`proposal accept`" in content
    assert "`service operator resume`" in content
    assert "`service run`" in content


def test_builtin_relay_knowledge_skill_constrains_proposal_decisions() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert "proposal reject <proposal_id> --by <actor>" in content
    assert (
        "`proposal accept`, `proposal reject`, and `proposal supersede` require "
        "a proposal id and `--by <actor>`." in content
    )
    assert (
        "Do not run a proposal decision command if either value is unknown." in content
    )


def test_builtin_relay_knowledge_skill_constrains_index_worker_usage() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert "repo index-worker --task-id <task_id>" in content
    assert (
        "only for executing or recovering an already queued code index task" in content
    )
    assert "Do not invent a task id" in content
    assert "use `repo index <alias> --ref <ref>`" in content
    assert "--timeout 1140 -- repo index-worker" in content
    assert (
        "repo index-worker --task-id <task_id> --format json', timeout_ms=1200000)"
        in content
    )
    assert "If `repo index-worker` returns empty output" in content
    assert (
        'Treat empty output from `repo index-worker` as "no queued task was claimed"'
        in content
    )


def test_builtin_relay_knowledge_skill_uses_long_timeout_for_other_mutations() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert "--timeout 1140 -- index refresh" in content
    assert "--timeout 1140 --env RELAY_KNOWLEDGE_FILE_INDEX_ROOTS" in content
    assert "--timeout 1140 -- worker run-once" in content
    assert "--timeout 1140 -- repo update" in content
    assert (
        "repo update <alias> --base main --head HEAD --format json', timeout_ms=1200000)"
        in content
    )
    assert "After `repo update` returns, inspect the response" in content
    assert "missing or stale base scope" in content
    assert "do not continue analysis from a failed update" in content
    assert "index refresh --kind bm25 --format json', timeout_ms=1200000)" in content
    assert "files index --root" in content
    assert (
        "worker run-once --kind embedding --format json', timeout_ms=1200000)"
        in content
    )
    assert (
        "Use wrapper `--timeout 1140` and outer `timeout_ms=1200000` for "
        "potentially long mutation or worker commands" in content
    )
    assert (
        "`repo update`, `files index`, `index refresh`, and `worker run-once`"
        in content
    )


def test_builtin_relay_knowledge_skill_converts_setup_commands_to_wrapper() -> None:
    skill_path = get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert "Do not execute those commands verbatim" in content
    assert "Convert each suggestion to the wrapper form in this skill" in content
    assert (
        "Convert raw `relay-knowledge ...` commands returned by setup diagnostics "
        'into `python "<skill_dir>/scripts/relay_knowledge_cli.py" -- ...` '
        "wrapper commands before execution." in content
    )


def test_relay_knowledge_cli_wrapper_passes_through_command(
    monkeypatch,
    capsys,
) -> None:
    module = _load_wrapper_module()
    monkeypatch.setattr(
        module, "_resolve_cli_path", lambda install_if_missing: Path(sys.executable)
    )
    monkeypatch.setattr(module, "_build_env", lambda overrides: _child_process_env())

    exit_code = module.main(["--", "-c", "print('relay-knowledge-ok')"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.replace("\r\n", "\n") == "relay-knowledge-ok\n"
    assert captured.err == ""


def test_relay_knowledge_cli_wrapper_reports_missing_cli(monkeypatch, capsys) -> None:
    module = _load_wrapper_module()

    def resolve_cli_path(*, install_if_missing: bool) -> Path:
        raise RuntimeError("relay knowledge missing")

    monkeypatch.setattr(module, "_resolve_cli_path", resolve_cli_path)

    exit_code = module.main(["--", "status"])

    captured = capsys.readouterr()
    assert exit_code == 127
    assert "relay knowledge missing" in captured.err


def test_relay_knowledge_cli_wrapper_reports_timeout(monkeypatch, capsys) -> None:
    module = _load_wrapper_module()
    monkeypatch.setattr(
        module, "_resolve_cli_path", lambda install_if_missing: Path(sys.executable)
    )

    def run_cli_process(
        command: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        timeout: float | None,
    ) -> int:
        raise subprocess.TimeoutExpired(command, timeout or 0)

    monkeypatch.setattr(module, "_run_cli_process", run_cli_process)

    exit_code = module.main(["--timeout", "1", "--", "status"])

    captured = capsys.readouterr()
    assert exit_code == 124
    assert "timed out after 1 seconds" in captured.err


def test_relay_knowledge_cli_wrapper_reports_execution_error(
    monkeypatch, capsys
) -> None:
    module = _load_wrapper_module()
    monkeypatch.setattr(
        module, "_resolve_cli_path", lambda install_if_missing: Path(sys.executable)
    )

    def run_cli_process(
        command: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        timeout: float | None,
    ) -> int:
        raise OSError("boom")

    monkeypatch.setattr(module, "_run_cli_process", run_cli_process)

    exit_code = module.main(["--", "status"])

    captured = capsys.readouterr()
    assert exit_code == 127
    assert "failed to execute relay-knowledge: boom" in captured.err


def test_relay_knowledge_cli_wrapper_resolves_cwd_and_env(monkeypatch) -> None:
    module = _load_wrapper_module()

    assert module._resolve_cwd(None) == Path.cwd().resolve()
    assert module._resolve_cwd(".") == Path.cwd().resolve()

    env = module._build_env(["RELAY_TEST=value"])
    assert env["RELAY_TEST"] == "value"

    with pytest.raises(SystemExit):
        module._build_env(["missing-separator"])


def test_relay_knowledge_cli_wrapper_resolves_cli_path(monkeypatch) -> None:
    module = _load_wrapper_module()

    class ReadyService:
        def inspect_tool(self, tool_id: object) -> object:
            return SimpleNamespace(
                status=module.BinaryToolStatus.READY,
                path=str(Path(sys.executable)),
                target_version="1.2.3",
            )

    monkeypatch.setattr(module, "BinaryToolService", ReadyService)

    assert module._resolve_cli_path(install_if_missing=False) == Path(sys.executable)


def test_relay_knowledge_cli_wrapper_installs_cli_when_requested(monkeypatch) -> None:
    module = _load_wrapper_module()

    class InstallService:
        async def ensure_tool_path(self, tool_id: object) -> Path:
            return Path(sys.executable)

    monkeypatch.setattr(module, "BinaryToolService", InstallService)

    assert module._resolve_cli_path(install_if_missing=True) == Path(sys.executable)


def test_relay_knowledge_cli_wrapper_errors_when_cli_missing(monkeypatch) -> None:
    module = _load_wrapper_module()

    class MissingService:
        def inspect_tool(self, tool_id: object) -> object:
            return SimpleNamespace(status=object(), path=None, target_version="1.2.3")

    monkeypatch.setattr(module, "BinaryToolService", MissingService)

    with pytest.raises(RuntimeError, match="target version 1.2.3"):
        module._resolve_cli_path(install_if_missing=False)


def test_relay_knowledge_cli_wrapper_times_out_immediately(
    monkeypatch,
) -> None:
    module = _load_wrapper_module()
    terminated_pids: list[int] = []

    def terminate_process(proc: subprocess.Popen[bytes]) -> None:
        terminated_pids.append(proc.pid)
        proc.kill()
        proc.wait(timeout=2)

    monkeypatch.setattr(module, "_terminate_process_tree", terminate_process)

    with pytest.raises(subprocess.TimeoutExpired):
        module._run_cli_process(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=str(Path.cwd()),
            env=_child_process_env(),
            timeout=0,
        )

    assert terminated_pids


def test_relay_knowledge_cli_wrapper_times_out_while_streams_are_open(
    monkeypatch,
) -> None:
    module = _load_wrapper_module()
    terminated_pids: list[int] = []

    def terminate_process(proc: subprocess.Popen[bytes]) -> None:
        terminated_pids.append(proc.pid)
        proc.kill()
        proc.wait(timeout=2)

    monkeypatch.setattr(module, "_terminate_process_tree", terminate_process)

    with pytest.raises(subprocess.TimeoutExpired):
        module._run_cli_process(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=str(Path.cwd()),
            env=_child_process_env(),
            timeout=0.1,
        )

    assert terminated_pids


def test_relay_knowledge_cli_wrapper_times_out_after_streams_close(
    monkeypatch,
) -> None:
    module = _load_wrapper_module()
    terminated_pids: list[int] = []

    def terminate_process(proc: subprocess.Popen[bytes]) -> None:
        terminated_pids.append(proc.pid)
        proc.kill()
        proc.wait(timeout=2)

    monkeypatch.setattr(module, "_terminate_process_tree", terminate_process)

    with pytest.raises(subprocess.TimeoutExpired):
        module._run_cli_process(
            [
                sys.executable,
                "-c",
                "import sys, time; sys.stdout.close(); sys.stderr.close(); "
                + "time.sleep(10)",
            ],
            cwd=str(Path.cwd()),
            env=_child_process_env(),
            timeout=0.1,
        )

    assert terminated_pids


def test_relay_knowledge_cli_wrapper_pumps_stream_remainder() -> None:
    module = _load_wrapper_module()
    output_queue: queue.Queue[tuple[str, str] | None] = queue.Queue()

    module._pump_stream("stdout", BytesIO(b"\xe2\x82"), output_queue)

    assert output_queue.get_nowait() == ("stdout", "")
    assert output_queue.get_nowait() == ("stdout", "\ufffd")
    assert output_queue.get_nowait() is None


def test_relay_knowledge_cli_wrapper_terminates_child_when_group_signal_fails(
    monkeypatch,
) -> None:
    module = _load_wrapper_module()

    class FakeProc:
        pid = 123

        def __init__(self) -> None:
            self.wait_calls = 0
            self.terminated = False
            self.killed = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired(["relay-knowledge"], timeout or 0)
            return 0

    proc = FakeProc()
    monkeypatch.setattr(module.os, "name", "posix")
    monkeypatch.setattr(module.signal, "SIGKILL", signal.SIGTERM, raising=False)
    monkeypatch.setattr(module, "_signal_process_group", lambda pid, sig: False)

    module._terminate_process_tree(cast(subprocess.Popen[bytes], proc))

    assert proc.terminated is True
    assert proc.killed is True


def test_relay_knowledge_cli_wrapper_terminates_windows_process_tree(
    monkeypatch,
    capsys,
) -> None:
    module = _load_wrapper_module()

    class FakeProc:
        pid = 123

        def __init__(self) -> None:
            self.wait_calls = 0
            self.killed = False

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired(["relay-knowledge"], timeout or 0)
            return 0

        def kill(self) -> None:
            self.killed = True

    proc = FakeProc()
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module, "_taskkill_process_tree", lambda pid: False)

    module._terminate_process_tree(cast(subprocess.Popen[bytes], proc))

    captured = capsys.readouterr()
    assert proc.killed is True
    assert "taskkill failed" in captured.err


def test_relay_knowledge_cli_wrapper_warns_when_forced_kill_does_not_exit(
    monkeypatch,
    capsys,
) -> None:
    module = _load_wrapper_module()

    class FakeProc:
        pid = 123

        def poll(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired(["relay-knowledge"], timeout or 0)

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    proc = FakeProc()
    monkeypatch.setattr(module.os, "name", "posix")
    monkeypatch.setattr(module.signal, "SIGKILL", signal.SIGTERM, raising=False)
    monkeypatch.setattr(module, "_signal_process_group", lambda pid, sig: False)

    module._terminate_process_tree(cast(subprocess.Popen[bytes], proc))

    captured = capsys.readouterr()
    assert "did not exit after forced kill" in captured.err


def test_relay_knowledge_cli_wrapper_taskkill_success_and_failure(
    monkeypatch,
) -> None:
    module = _load_wrapper_module()

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    assert module._taskkill_process_tree(123) is True

    def fail_run(*args: object, **kwargs: object) -> object:
        raise OSError

    monkeypatch.setattr(module.subprocess, "run", fail_run)
    assert module._taskkill_process_tree(123) is False


def test_relay_knowledge_cli_wrapper_creation_flags(monkeypatch) -> None:
    module = _load_wrapper_module()

    monkeypatch.setattr(module.os, "name", "posix")
    assert module._creation_flags() == 0

    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(
        module.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False
    )
    assert module._creation_flags() == 512


def test_relay_knowledge_cli_wrapper_signals_process_group(monkeypatch) -> None:
    module = _load_wrapper_module()
    calls: list[tuple[int, signal.Signals]] = []

    def killpg(pid: int, sig: signal.Signals) -> None:
        calls.append((pid, sig))

    monkeypatch.setattr(module.os, "killpg", killpg, raising=False)

    assert module._signal_process_group(123, signal.SIGTERM) is True
    assert calls == [(123, signal.SIGTERM)]


def test_relay_knowledge_cli_wrapper_falls_back_when_group_signal_unavailable(
    monkeypatch,
    capsys,
) -> None:
    module = _load_wrapper_module()
    monkeypatch.delattr(module.os, "killpg", raising=False)

    assert module._signal_process_group(123, signal.SIGTERM) is False

    captured = capsys.readouterr()
    assert "process-group signaling is unavailable" in captured.err


def test_relay_knowledge_cli_wrapper_falls_back_when_group_signal_fails(
    monkeypatch,
    capsys,
) -> None:
    module = _load_wrapper_module()

    def killpg(pid: int, sig: signal.Signals) -> None:
        raise PermissionError

    monkeypatch.setattr(module.os, "killpg", killpg, raising=False)

    assert module._signal_process_group(123, signal.SIGTERM) is False

    captured = capsys.readouterr()
    assert "process group could not be signaled" in captured.err


def test_relay_knowledge_cli_wrapper_uses_streaming_process_execution() -> None:
    script_path = (
        get_builtin_skills_dir()
        / "relay-knowledge"
        / "scripts"
        / "relay_knowledge_cli.py"
    )
    content = script_path.read_text(encoding="utf-8")

    assert "subprocess.Popen(" in content
    assert "capture_output=True" not in content
    assert "stream.read(4096)" in content
    assert "getincrementaldecoder" in content
    assert "for line in stream" not in content


def test_relay_knowledge_cli_wrapper_uses_strict_timeout_and_tree_kill() -> None:
    script_path = (
        get_builtin_skills_dir()
        / "relay-knowledge"
        / "scripts"
        / "relay_knowledge_cli.py"
    )
    content = script_path.read_text(encoding="utf-8")

    assert "remaining <= 0" in content
    assert "proc.wait(timeout=remaining)" in content
    assert "_terminate_process_tree(proc)" in content
    assert "taskkill" in content
    assert "killpg(pid" in content
    assert "proc.terminate()" in content
    assert "proc.kill()" in content
    assert "signal.SIGKILL" in content
    assert "return False" in content
    assert "CREATE_NEW_PROCESS_GROUP" in content
    assert 'start_new_session=os.name != "nt"' in content
    assert (
        "warning: taskkill failed while terminating relay-knowledge process tree"
        in content
    )
    assert "return completed.returncode == 0" in content
    assert "if proc.poll() is not None:\n        return" not in content


def test_relay_knowledge_cli_wrapper_bootstraps_src_path() -> None:
    script_path = (
        get_builtin_skills_dir()
        / "relay-knowledge"
        / "scripts"
        / "relay_knowledge_cli.py"
    )
    content = script_path.read_text(encoding="utf-8")

    assert "_SCRIPT_PATH = Path(__file__).resolve()" in content
    assert '(_parent / "relay_teams").is_dir()' in content
    assert "sys.path.insert(0, str(_parent))" in content


def test_relay_knowledge_cli_wrapper_requires_command(capsys) -> None:
    module = _load_wrapper_module()

    exit_code = module.main([])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Usage: relay_knowledge_cli.py" in captured.err


def test_relay_knowledge_cli_wrapper_defaults_to_no_timeout() -> None:
    module = _load_wrapper_module()

    args = module._parse_args(["--", "--version"])

    assert args.timeout is None


def test_relay_knowledge_cli_wrapper_accepts_long_timeout() -> None:
    module = _load_wrapper_module()

    args = module._parse_args(["--timeout", "1140", "--", "repo", "index", "core"])

    assert args.timeout == 1140
    assert module._normalize_command(args.command) == ["repo", "index", "core"]
