from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import sys

import httpx
import pytest

from integration_tests.support.config_builder import (
    assert_integration_model_config_uses_fake_llm,
    write_test_runtime_config,
)
from integration_tests.support.environment import IntegrationEnvironment
from integration_tests.support.process_control import (
    ManagedProcess,
    find_free_port,
    start_process,
    stop_process,
    wait_for_http_ready,
)


_HOME_ENV_KEYS: tuple[str, ...] = ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")


def _capture_home_env() -> dict[str, str | None]:
    return {key: os.environ.get(key) for key in _HOME_ENV_KEYS}


def _apply_home_env(runtime_root: Path) -> None:
    resolved_runtime_root = runtime_root.resolve()
    os.environ["HOME"] = str(resolved_runtime_root)
    os.environ["USERPROFILE"] = str(resolved_runtime_root)
    drive, tail = os.path.splitdrive(str(resolved_runtime_root))
    if drive:
        os.environ["HOMEDRIVE"] = drive
        os.environ["HOMEPATH"] = tail or "\\"
    else:
        os.environ.pop("HOMEDRIVE", None)
        os.environ.pop("HOMEPATH", None)


def _restore_home_env(original_env: dict[str, str | None]) -> None:
    for key, value in original_env.items():
        if value is None:
            os.environ.pop(key, None)
            continue
        os.environ[key] = value


@pytest.fixture(scope="session")
def integration_env(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[IntegrationEnvironment]:
    repo_root = Path(__file__).resolve().parent.parent.parent
    runtime_root = tmp_path_factory.mktemp("agent-teams-integration")
    config_dir = runtime_root / ".agent-teams"

    fake_llm_port = find_free_port()
    backend_port = find_free_port()

    fake_llm_admin_url = f"http://127.0.0.1:{fake_llm_port}"
    fake_llm_v1_base_url = f"{fake_llm_admin_url}/v1"
    api_base_url = f"http://127.0.0.1:{backend_port}"

    write_test_runtime_config(
        config_dir=config_dir,
        fake_llm_v1_base_url=fake_llm_v1_base_url,
    )
    assert_integration_model_config_uses_fake_llm(config_dir=config_dir)

    original_home_env = _capture_home_env()
    _apply_home_env(runtime_root)

    shared_env = os.environ.copy()
    python_paths = [str(repo_root), str(repo_root / "src"), str(repo_root / "tests")]
    existing_pythonpath = shared_env.get("PYTHONPATH", "")
    if existing_pythonpath:
        python_paths.append(existing_pythonpath)
    shared_env["PYTHONPATH"] = os.pathsep.join(python_paths)

    fake_llm_log_file = runtime_root / "fake-llm.log"
    backend_log_file = runtime_root / "backend.log"

    fake_llm_process = start_process(
        name="fake-llm",
        command=(
            sys.executable,
            "-m",
            "uvicorn",
            "integration_tests.support.fake_llm_server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(fake_llm_port),
            "--log-level",
            "warning",
        ),
        cwd=repo_root,
        env=shared_env,
        log_file=fake_llm_log_file,
    )
    backend_process: ManagedProcess | None = None
    try:
        wait_for_http_ready(
            url=f"{fake_llm_admin_url}/health",
            timeout_seconds=20.0,
            process=fake_llm_process,
        )

        backend_process = start_process(
            name="agent-teams-backend",
            command=(
                sys.executable,
                "-m",
                "uvicorn",
                "agent_teams.interfaces.server.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(backend_port),
                "--log-level",
                "warning",
            ),
            cwd=runtime_root,
            env=shared_env,
            log_file=backend_log_file,
        )
        wait_for_http_ready(
            url=f"{api_base_url}/api/system/health",
            timeout_seconds=30.0,
            process=backend_process,
        )

        yield IntegrationEnvironment(
            api_base_url=api_base_url,
            fake_llm_admin_url=fake_llm_admin_url,
            fake_llm_v1_base_url=fake_llm_v1_base_url,
            config_dir=config_dir,
            backend_log_file=backend_log_file,
            fake_llm_log_file=fake_llm_log_file,
        )
    finally:
        if backend_process is not None:
            stop_process(backend_process)
        stop_process(fake_llm_process)
        _restore_home_env(original_home_env)


@pytest.fixture()
def api_client(integration_env: IntegrationEnvironment) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=integration_env.api_base_url, timeout=40.0) as client:
        yield client
