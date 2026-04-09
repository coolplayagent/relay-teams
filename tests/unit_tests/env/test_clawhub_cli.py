# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess

from relay_teams.env import clawhub_cli


def test_resolve_existing_clawhub_path_uses_npm_global_bin(
    monkeypatch,
    tmp_path: Path,
) -> None:
    clawhub_cli.clear_clawhub_path_cache()
    npm_global_bin = tmp_path / "npm-bin"
    npm_global_bin.mkdir()
    clawhub_path = npm_global_bin / "clawhub"
    clawhub_path.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(clawhub_cli, "resolve_system_clawhub_path", lambda: None)
    monkeypatch.setattr(
        clawhub_cli,
        "resolve_npm_global_clawhub_path",
        lambda npm_path=None, base_env=None: clawhub_path,
    )

    resolved = clawhub_cli.resolve_existing_clawhub_path()

    assert resolved == clawhub_path


def test_resolve_existing_clawhub_path_rechecks_after_cached_miss(
    monkeypatch,
    tmp_path: Path,
) -> None:
    clawhub_cli.clear_clawhub_path_cache()
    clawhub_path = tmp_path / "clawhub"
    clawhub_path.write_text("#!/bin/sh\n", encoding="utf-8")
    call_count = 0

    def resolve_system_path() -> Path | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None
        return clawhub_path

    monkeypatch.setattr(clawhub_cli, "resolve_system_clawhub_path", resolve_system_path)
    monkeypatch.setattr(
        clawhub_cli,
        "resolve_npm_global_clawhub_path",
        lambda npm_path=None, base_env=None: None,
    )

    assert clawhub_cli.resolve_existing_clawhub_path() is None
    assert clawhub_cli.resolve_existing_clawhub_path() == clawhub_path


def test_install_clawhub_via_npm_prefers_huaweicloud_registry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    clawhub_cli.clear_clawhub_path_cache()
    npm_path = tmp_path / "npm"
    npm_path.write_text("", encoding="utf-8")
    npm_global_bin = tmp_path / "npm-bin"
    npm_global_bin.mkdir()
    clawhub_path = npm_global_bin / "clawhub"
    calls: list[list[str]] = []

    monkeypatch.setattr(clawhub_cli, "resolve_npm_path", lambda: npm_path)
    monkeypatch.setattr(clawhub_cli, "resolve_system_clawhub_path", lambda: None)
    monkeypatch.setattr(
        clawhub_cli,
        "resolve_npm_global_bin_dir",
        lambda npm_path=None, base_env=None: npm_global_bin,
    )

    def fake_run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        clawhub_path.write_text("#!/bin/sh\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="installed\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = clawhub_cli.install_clawhub_via_npm(timeout_seconds=30.0)

    assert result.ok is True
    assert result.attempted is True
    assert result.clawhub_path == str(clawhub_path)
    assert result.registry == clawhub_cli.CLAWHUB_PREFERRED_NPM_REGISTRY
    assert calls == [
        [
            str(npm_path),
            "install",
            "--global",
            "--loglevel",
            "error",
            clawhub_cli.CLAWHUB_NPM_PACKAGE_NAME,
            "--registry",
            clawhub_cli.CLAWHUB_PREFERRED_NPM_REGISTRY,
        ]
    ]


def test_install_clawhub_via_npm_falls_back_to_default_registry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    clawhub_cli.clear_clawhub_path_cache()
    npm_path = tmp_path / "npm"
    npm_path.write_text("", encoding="utf-8")
    npm_global_bin = tmp_path / "npm-bin"
    npm_global_bin.mkdir()
    clawhub_path = npm_global_bin / "clawhub"
    calls: list[list[str]] = []

    monkeypatch.setattr(clawhub_cli, "resolve_npm_path", lambda: npm_path)
    monkeypatch.setattr(clawhub_cli, "resolve_system_clawhub_path", lambda: None)
    monkeypatch.setattr(
        clawhub_cli,
        "resolve_npm_global_bin_dir",
        lambda npm_path=None, base_env=None: npm_global_bin,
    )

    def fake_run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout="",
                stderr="mirror unavailable\n",
            )
        clawhub_path.write_text("#!/bin/sh\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="installed\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = clawhub_cli.install_clawhub_via_npm(timeout_seconds=30.0)

    assert result.ok is True
    assert result.attempted is True
    assert result.clawhub_path == str(clawhub_path)
    assert result.registry is None
    assert calls == [
        [
            str(npm_path),
            "install",
            "--global",
            "--loglevel",
            "error",
            clawhub_cli.CLAWHUB_NPM_PACKAGE_NAME,
            "--registry",
            clawhub_cli.CLAWHUB_PREFERRED_NPM_REGISTRY,
        ],
        [
            str(npm_path),
            "install",
            "--global",
            "--loglevel",
            "error",
            clawhub_cli.CLAWHUB_NPM_PACKAGE_NAME,
        ],
    ]
