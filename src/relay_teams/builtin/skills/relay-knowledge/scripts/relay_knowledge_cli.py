# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import asyncio
import codecs
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import BinaryIO

_SCRIPT_PATH = Path(__file__).resolve()
for _parent in _SCRIPT_PATH.parents:
    if (_parent / "relay_teams").is_dir():
        sys.path.insert(0, str(_parent))
        break

from relay_teams.binary_tools import BinaryToolId, BinaryToolService, BinaryToolStatus  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    command = _normalize_command(args.command)
    if not command:
        print(
            "Usage: relay_knowledge_cli.py [--cwd DIR] [--timeout SECONDS] "
            "[--install-if-missing] [--env KEY=VALUE] -- <relay-knowledge args>",
            file=sys.stderr,
        )
        return 2

    try:
        cli_path = _resolve_cli_path(install_if_missing=args.install_if_missing)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 127

    cwd_path = _resolve_cwd(args.cwd)
    cwd = str(cwd_path)
    env = _build_env(args.env)

    try:
        return _run_cli_process(
            [str(cli_path), *command],
            cwd=cwd,
            env=env,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"relay-knowledge command timed out after {args.timeout:g} seconds.",
            file=sys.stderr,
        )
        return 124
    except OSError as exc:
        print(f"failed to execute relay-knowledge: {exc}", file=sys.stderr)
        return 127


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Relay Knowledge through the Agent Teams CLI connector.",
    )
    parser.add_argument("--cwd", help="Working directory for the CLI process.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Command timeout in seconds. Defaults to no timeout.",
    )
    parser.add_argument(
        "--install-if-missing",
        action="store_true",
        help="Download the connector-managed Relay Knowledge CLI when missing.",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Environment override for the CLI process. May be repeated.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def _normalize_command(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        return command[1:]
    return command


def _resolve_cwd(cwd_arg: str | None) -> Path:
    if cwd_arg is None:
        return Path.cwd().resolve()
    return Path(cwd_arg).expanduser().resolve()


def _resolve_cli_path(*, install_if_missing: bool) -> Path:
    service = BinaryToolService()
    if install_if_missing:
        return asyncio.run(service.ensure_tool_path(BinaryToolId.RELAY_KNOWLEDGE))

    item = service.inspect_tool(BinaryToolId.RELAY_KNOWLEDGE)
    if item.status == BinaryToolStatus.READY and item.path:
        return Path(item.path)

    target_version = (
        f" target version {item.target_version}" if item.target_version else ""
    )
    raise RuntimeError(
        "Relay Knowledge CLI is not installed. Install it from the Relay Knowledge "
        f"connector{target_version}, or rerun this script with --install-if-missing."
    )


def _build_env(overrides: list[str]) -> dict[str, str]:
    env = os.environ.copy()
    for override in overrides:
        key, separator, value = override.partition("=")
        key = key.strip()
        if not separator or not key:
            raise SystemExit(f"invalid --env value: {override!r}; expected KEY=VALUE")
        env[key] = value
    return env


def _run_cli_process(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: float | None,
) -> int:
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name != "nt",
        creationflags=_creation_flags(),
    )
    output_queue: queue.Queue[tuple[str, str] | None] = queue.Queue()
    streams = tuple(
        (name, stream)
        for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr))
        if stream is not None
    )
    threads = [
        threading.Thread(
            target=_pump_stream,
            args=(name, stream, output_queue),
            daemon=True,
        )
        for name, stream in streams
    ]
    for thread in threads:
        thread.start()

    deadline = None if timeout is None else time.monotonic() + timeout
    closed_streams = 0
    while closed_streams < len(threads):
        queue_timeout = None
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                assert timeout is not None
                _terminate_process_tree(proc)
                raise subprocess.TimeoutExpired(command, timeout)
            queue_timeout = max(0.001, remaining)
        try:
            item = output_queue.get(timeout=queue_timeout)
        except queue.Empty as exc:
            assert timeout is not None
            _terminate_process_tree(proc)
            raise subprocess.TimeoutExpired(command, timeout) from exc

        if item is None:
            closed_streams += 1
            continue

        stream_name, chunk = item
        target = sys.stdout if stream_name == "stdout" else sys.stderr
        target.write(chunk)
        target.flush()

    if deadline is None:
        return proc.wait()

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        assert timeout is not None
        _terminate_process_tree(proc)
        raise subprocess.TimeoutExpired(command, timeout)

    try:
        return proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        assert timeout is not None
        _terminate_process_tree(proc)
        raise subprocess.TimeoutExpired(command, timeout) from exc


def _pump_stream(
    stream_name: str,
    stream: BinaryIO,
    output_queue: queue.Queue[tuple[str, str] | None],
) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while chunk := stream.read(4096):
            output_queue.put((stream_name, decoder.decode(chunk)))
        remainder = decoder.decode(b"", final=True)
        if remainder:
            output_queue.put((stream_name, remainder))
    finally:
        output_queue.put(None)


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def _terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        taskkill_succeeded = _taskkill_process_tree(proc.pid)
        if not taskkill_succeeded:
            print(
                "warning: taskkill failed while terminating relay-knowledge process tree; "
                "falling back to direct process kill.",
                file=sys.stderr,
            )
    else:
        signal_succeeded = _signal_process_group(proc.pid, signal.SIGTERM)
        if not signal_succeeded and proc.poll() is None:
            proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            taskkill_succeeded = _taskkill_process_tree(proc.pid)
            if not taskkill_succeeded:
                proc.kill()
        else:
            signal_succeeded = _signal_process_group(proc.pid, signal.SIGKILL)
            if not signal_succeeded and proc.poll() is None:
                proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            print(
                "warning: relay-knowledge process did not exit after forced kill; "
                "continuing best-effort shutdown.",
                file=sys.stderr,
            )


def _taskkill_process_tree(pid: int) -> bool:
    try:
        completed = subprocess.run(
            ["taskkill", "/f", "/t", "/pid", str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _signal_process_group(pid: int, sig: signal.Signals) -> bool:
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        print(
            "warning: process-group signaling is unavailable; falling back to direct "
            "relay-knowledge process termination.",
            file=sys.stderr,
        )
        return False
    try:
        killpg(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        print(
            "warning: relay-knowledge process group could not be signaled; "
            "continuing best-effort shutdown.",
            file=sys.stderr,
        )
        return False


if __name__ == "__main__":
    raise SystemExit(main())
