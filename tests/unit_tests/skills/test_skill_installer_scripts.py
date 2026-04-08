# -*- coding: utf-8 -*-
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import json
import os
import subprocess
import sys
import threading
from urllib.error import URLError
import zipfile

import pytest

from relay_teams.builtin import get_builtin_skills_dir
from relay_teams.skills import installer_support


def test_resolve_source_from_marketplace_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        installer_support,
        "_request_text",
        lambda url: (
            '<a href="https://github.com/openai/skills/tree/main/skills/.experimental/demo-skill">'
            "demo"
            "</a>"
        ),
    )

    source = installer_support.resolve_source_from_url(
        "https://skillsmp.example/zh/demo"
    )

    assert source.repo == "openai/skills"
    assert source.ref == "main"
    assert source.path == "skills/.experimental/demo-skill"


def test_install_from_repo_paths_falls_back_to_git_on_download_auth_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected_result = (
        installer_support.SkillInstallResult(
            skill_name="demo-skill",
            destination=tmp_path / "skills" / "demo-skill",
            source=installer_support.SkillSource(
                repo="openai/skills",
                ref="main",
                path="skills/.curated/demo-skill",
            ),
        ),
    )

    monkeypatch.setattr(
        installer_support,
        "_install_via_download",
        lambda **kwargs: (_ for _ in ()).throw(
            installer_support._DownloadAuthError("auth")
        ),
    )
    monkeypatch.setattr(
        installer_support,
        "_install_via_git",
        lambda **kwargs: expected_result,
    )

    result = installer_support.install_from_repo_paths(
        repo="openai/skills",
        ref="main",
        paths=("skills/.curated/demo-skill",),
        dest_root=str(tmp_path / "skills"),
        name=None,
        method=installer_support.InstallMethod.AUTO,
    )

    assert result == expected_result


def test_install_from_repo_paths_reports_download_and_git_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        installer_support,
        "_install_via_download",
        lambda **kwargs: (_ for _ in ()).throw(
            installer_support._DownloadAuthError("download auth failure")
        ),
    )
    monkeypatch.setattr(
        installer_support,
        "_install_via_git",
        lambda **kwargs: (_ for _ in ()).throw(
            installer_support.SkillInstallerError("git fallback failure")
        ),
    )

    with pytest.raises(installer_support.SkillInstallerError) as exc_info:
        installer_support.install_from_repo_paths(
            repo="openai/skills",
            ref="main",
            paths=("skills/.curated/demo-skill",),
            dest_root=str(tmp_path / "skills"),
            name=None,
            method=installer_support.InstallMethod.AUTO,
        )

    message = str(exc_info.value)
    assert "Direct download failed and git fallback also failed." in message
    assert "download auth failure" in message
    assert "git fallback failure" in message


def test_list_skills_script_reports_installed_annotations(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".relay-teams" / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: installed alpha\n---\nUse alpha.\n",
        encoding="utf-8",
    )

    routes = {
        "/repos/openai/skills/contents/skills/.curated?ref=main": _json_response(
            [
                {"name": "alpha", "type": "dir"},
                {"name": "beta", "type": "dir"},
            ]
        ),
    }
    with _serve_http(routes) as base_url:
        result = _run_script(
            script_name="list-skills.py",
            args=(
                "--repo",
                "openai/skills",
                "--path",
                "skills/.curated",
                "--format",
                "json",
            ),
            repo_root=Path(__file__).resolve().parents[3],
            home_dir=tmp_path,
            extra_env={
                "AGENT_TEAMS_SKILL_GITHUB_API_BASE": base_url,
            },
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["repo"] == "openai/skills"
    assert payload["path"] == "skills/.curated"
    assert payload["entries"] == [
        {"name": "alpha", "installed": True},
        {"name": "beta", "installed": False},
    ]


def test_install_skill_script_downloads_and_installs_skill(tmp_path: Path) -> None:
    archive_bytes = _build_repo_archive(
        {
            "skills/.curated/demo-skill/SKILL.md": (
                "---\n"
                "name: demo-skill\n"
                "description: demo installer\n"
                "---\n"
                "Install demo skill.\n"
            ),
            "skills/.curated/demo-skill/scripts/demo.py": "print('demo')\n",
        }
    )
    routes = {
        "/repos/openai/skills/zipball/main": _bytes_response(
            archive_bytes, "application/zip"
        ),
    }
    with _serve_http(routes) as base_url:
        result = _run_script(
            script_name="install-skill-from-github.py",
            args=(
                "--repo",
                "openai/skills",
                "--path",
                "skills/.curated/demo-skill",
            ),
            repo_root=Path(__file__).resolve().parents[3],
            home_dir=tmp_path,
            extra_env={
                "AGENT_TEAMS_SKILL_GITHUB_API_BASE": base_url,
            },
        )

    assert result.returncode == 0, result.stderr
    installed_skill_dir = tmp_path / ".relay-teams" / "skills" / "demo-skill"
    assert (installed_skill_dir / "SKILL.md").exists()
    assert (installed_skill_dir / "scripts" / "demo.py").exists()
    assert not (tmp_path / ".relay-teams" / "roles" / "MainAgent.md").exists()
    assert "Restart Agent Teams to pick up new skills." in result.stdout
    assert result.stderr == ""


def test_bind_skill_script_updates_main_agent_role(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".relay-teams" / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo installer\n---\nUse demo.\n",
        encoding="utf-8",
    )

    result = _run_script(
        script_name="bind-skill-to-role.py",
        args=(
            "--skill",
            "demo-skill",
            "--role",
            "MainAgent",
        ),
        repo_root=Path(__file__).resolve().parents[3],
        home_dir=tmp_path,
        extra_env={},
    )

    assert result.returncode == 0, result.stderr
    role_path = tmp_path / ".relay-teams" / "roles" / "MainAgent.md"
    assert role_path.exists()
    role_text = role_path.read_text(encoding="utf-8")
    assert "- app:demo-skill" in role_text
    assert "Updated roles: MainAgent" in result.stdout
    assert result.stderr == ""


def test_bind_skill_script_defaults_to_current_role_env(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".relay-teams" / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo installer\n---\nUse demo.\n",
        encoding="utf-8",
    )

    result = _run_script(
        script_name="bind-skill-to-role.py",
        args=(
            "--skill",
            "demo-skill",
        ),
        repo_root=Path(__file__).resolve().parents[3],
        home_dir=tmp_path,
        extra_env={
            "AGENT_TEAMS_CURRENT_ROLE_ID": "Crafter",
        },
    )

    assert result.returncode == 0, result.stderr
    role_path = tmp_path / ".relay-teams" / "roles" / "Crafter.md"
    assert role_path.exists()
    role_text = role_path.read_text(encoding="utf-8")
    assert "- app:demo-skill" in role_text
    assert "Updated roles: Crafter" in result.stdout


def test_mount_skills_to_roles_creates_main_agent_override(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".relay-teams" / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo installer\n---\nUse demo.\n",
        encoding="utf-8",
    )
    old_home = os.environ.get("HOME")
    old_userprofile = os.environ.get("USERPROFILE")
    home_value = tmp_path.resolve().as_posix()
    os.environ["HOME"] = home_value
    os.environ["USERPROFILE"] = home_value
    try:
        mounted_roles = installer_support.mount_skills_to_roles(
            role_ids=("MainAgent",),
            skill_names=("demo-skill",),
        )
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        if old_userprofile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = old_userprofile

    assert mounted_roles == ("MainAgent",)
    role_path = tmp_path / ".relay-teams" / "roles" / "MainAgent.md"
    assert role_path.exists()
    role_text = role_path.read_text(encoding="utf-8")
    assert "role_id: MainAgent" in role_text
    assert "- builtin:skill-installer" in role_text
    assert "- app:demo-skill" in role_text


def test_resolve_role_mount_targets_defaults_to_current_role_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_TEAMS_CURRENT_ROLE_ID", "Crafter")

    targets = installer_support._resolve_role_mount_targets(())

    assert targets == ("Crafter",)


def test_request_bytes_reports_timeout_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_timeout(*args: object, **kwargs: object) -> object:
        raise URLError(TimeoutError("timed out"))

    monkeypatch.setattr(installer_support, "urlopen", _raise_timeout)

    with pytest.raises(installer_support.SkillInstallerError) as exc_info:
        installer_support._request_bytes("https://example.com/skills")

    message = str(exc_info.value)
    assert "Request timed out after" in message
    assert "https://example.com/skills" in message
    assert "TimeoutError: timed out" in message


def test_run_git_reports_command_context_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _failed_run(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git", "fetch"],
            returncode=1,
            stdout="",
            stderr="fatal: could not read from remote repository",
        )

    monkeypatch.setattr(subprocess, "run", _failed_run)

    with pytest.raises(installer_support.SkillInstallerError) as exc_info:
        installer_support._run_git(tmp_path, "git", "fetch")

    message = str(exc_info.value)
    assert "Git command failed with exit code 1" in message
    assert "git fetch" in message
    assert tmp_path.resolve().as_posix() in message
    assert "fatal: could not read from remote repository" in message


def test_run_git_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _timeout_run(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            cmd=["git", "fetch"],
            timeout=installer_support._GIT_TIMEOUT_SECONDS,
        )

    monkeypatch.setattr(subprocess, "run", _timeout_run)

    with pytest.raises(installer_support.SkillInstallerError) as exc_info:
        installer_support._run_git(tmp_path, "git", "fetch")

    message = str(exc_info.value)
    assert "Git command timed out after" in message
    assert "git fetch" in message
    assert tmp_path.resolve().as_posix() in message


def test_install_skill_script_reports_errors_on_stderr(tmp_path: Path) -> None:
    existing_skill_dir = tmp_path / ".relay-teams" / "skills" / "demo-skill"
    existing_skill_dir.mkdir(parents=True)
    (existing_skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: existing\n---\nUse demo.\n",
        encoding="utf-8",
    )

    result = _run_script(
        script_name="install-skill-from-github.py",
        args=(
            "--repo",
            "openai/skills",
            "--path",
            "skills/.curated/demo-skill",
        ),
        repo_root=Path(__file__).resolve().parents[3],
        home_dir=tmp_path,
        extra_env={},
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Destination skill directory already exists" in result.stderr


def test_bind_skill_script_requires_skill_argument(tmp_path: Path) -> None:
    result = _run_script(
        script_name="bind-skill-to-role.py",
        args=(),
        repo_root=Path(__file__).resolve().parents[3],
        home_dir=tmp_path,
        extra_env={},
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Provide at least one --skill value" in result.stderr


def _run_script(
    *,
    script_name: str,
    args: tuple[str, ...],
    repo_root: Path,
    home_dir: Path,
    extra_env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    script_path = get_builtin_skills_dir() / "skill-installer" / "scripts" / script_name
    env = os.environ.copy()
    existing_python_path = env.get("PYTHONPATH", "").strip()
    source_path = (repo_root / "src").resolve().as_posix()
    env["PYTHONPATH"] = (
        source_path
        if not existing_python_path
        else source_path + os.pathsep + existing_python_path
    )
    home_value = home_dir.resolve().as_posix()
    env["HOME"] = home_value
    env["USERPROFILE"] = home_value
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(script_path), *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=30,
    )


def _build_repo_archive(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for relative_path, content in files.items():
            archive.writestr(f"openai-skills-main/{relative_path}", content)
    return buffer.getvalue()


def _json_response(payload: object) -> tuple[int, bytes, str]:
    return (200, json.dumps(payload).encode("utf-8"), "application/json")


def _bytes_response(body: bytes, content_type: str) -> tuple[int, bytes, str]:
    return (200, body, content_type)


@contextmanager
def _serve_http(routes: dict[str, tuple[int, bytes, str]]):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route_key = self.path
            if route_key not in routes:
                self.send_response(404)
                self.end_headers()
                return
            status, body, content_type = routes[route_key]
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            _ = (format, args)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
