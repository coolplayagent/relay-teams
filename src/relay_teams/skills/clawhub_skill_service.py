# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path, PurePosixPath
import re
import shutil
from tempfile import mkdtemp
from uuid import uuid4

import yaml

from relay_teams.skills.clawhub_models import (
    ClawHubSkillDetail,
    ClawHubSkillFile,
    ClawHubSkillSummary,
    ClawHubSkillWriteRequest,
)
from relay_teams.skills.skill_models import SkillSource

_SKILL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ClawHubSkillService:
    def __init__(
        self,
        *,
        config_dir: Path,
        on_skill_mutated: Callable[[], None] | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._skills_dir = (config_dir / "skills").resolve()
        self._on_skill_mutated = on_skill_mutated

    def list_skills(self) -> tuple[ClawHubSkillSummary, ...]:
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        entries = [
            self._build_summary(skill_dir)
            for skill_dir in sorted(self._iter_skill_directories())
        ]
        return tuple(entries)

    def get_skill(self, skill_id: str) -> ClawHubSkillDetail:
        normalized_skill_id = _normalize_skill_id(skill_id)
        skill_dir = self._skill_dir_for_id(normalized_skill_id)
        if not skill_dir.is_dir():
            raise KeyError(f"Unknown ClawHub skill: {normalized_skill_id}")
        return self._build_detail(skill_dir)

    def save_skill(
        self,
        skill_id: str,
        request: ClawHubSkillWriteRequest,
    ) -> ClawHubSkillDetail:
        normalized_skill_id = _normalize_skill_id(skill_id)
        normalized_name = _normalize_runtime_name(request.runtime_name)
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._raise_if_duplicate_runtime_name(
            skill_id=normalized_skill_id,
            runtime_name=normalized_name,
        )

        target_dir = self._skill_dir_for_id(normalized_skill_id)
        staging_dir = Path(
            mkdtemp(prefix=f".{normalized_skill_id}-", dir=self._skills_dir)
        )
        backup_dir: Path | None = None

        try:
            self._write_skill_snapshot(
                skill_dir=staging_dir,
                runtime_name=normalized_name,
                description=request.description,
                instructions=request.instructions,
                files=request.files,
            )
            if target_dir.exists():
                backup_dir = target_dir.parent / f".{target_dir.name}.bak-{uuid4().hex}"
                target_dir.rename(backup_dir)
            staging_dir.rename(target_dir)
            self._notify_skill_mutated()
        except Exception:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            if backup_dir is not None and backup_dir.exists():
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                backup_dir.rename(target_dir)
            raise
        if backup_dir is not None and backup_dir.exists():
            shutil.rmtree(backup_dir)

        return self.get_skill(normalized_skill_id)

    def delete_skill(self, skill_id: str) -> None:
        normalized_skill_id = _normalize_skill_id(skill_id)
        target_dir = self._skill_dir_for_id(normalized_skill_id)
        if not target_dir.is_dir():
            raise KeyError(f"Unknown ClawHub skill: {normalized_skill_id}")
        shutil.rmtree(target_dir)
        self._notify_skill_mutated()

    def _notify_skill_mutated(self) -> None:
        if self._on_skill_mutated is not None:
            self._on_skill_mutated()

    def _iter_skill_directories(self) -> tuple[Path, ...]:
        return tuple(
            path
            for path in self._skills_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )

    def _skill_dir_for_id(self, skill_id: str) -> Path:
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        resolved_path = (self._skills_dir / skill_id).resolve()
        try:
            resolved_path.relative_to(self._skills_dir)
        except ValueError as exc:
            raise ValueError(
                f"ClawHub skill path escapes the managed skills root: {skill_id}"
            ) from exc
        return resolved_path

    def _build_summary(self, skill_dir: Path) -> ClawHubSkillSummary:
        skill_id = skill_dir.name
        manifest_path = skill_dir / "SKILL.md"
        try:
            parsed = _parse_skill_manifest(manifest_path)
            return ClawHubSkillSummary(
                skill_id=skill_id,
                runtime_name=parsed.runtime_name,
                description=parsed.description,
                ref=parsed.runtime_name,
                source=SkillSource.USER_RELAY_TEAMS,
                directory=skill_dir.as_posix(),
                manifest_path=manifest_path.as_posix(),
                valid=True,
            )
        except Exception as exc:
            return ClawHubSkillSummary(
                skill_id=skill_id,
                runtime_name=None,
                description="",
                ref=None,
                directory=skill_dir.as_posix(),
                manifest_path=manifest_path.as_posix(),
                valid=False,
                error=str(exc),
            )

    def _build_detail(self, skill_dir: Path) -> ClawHubSkillDetail:
        summary = self._build_summary(skill_dir)
        files = tuple(
            _read_skill_file(skill_dir=skill_dir, path=path)
            for path in _iter_skill_files(skill_dir)
            if path.name != "SKILL.md"
        )
        if summary.valid:
            manifest_path = skill_dir / "SKILL.md"
            parsed = _parse_skill_manifest(manifest_path)
            return ClawHubSkillDetail(
                **summary.model_dump(),
                instructions=parsed.instructions,
                manifest_content=parsed.manifest_content,
                files=files,
            )
        manifest_content = None
        manifest_path = skill_dir / "SKILL.md"
        if manifest_path.is_file():
            try:
                manifest_content = manifest_path.read_text(encoding="utf-8")
            except Exception:
                manifest_content = None
        return ClawHubSkillDetail(
            **summary.model_dump(),
            instructions="",
            manifest_content=manifest_content,
            files=files,
        )

    def _write_skill_snapshot(
        self,
        *,
        skill_dir: Path,
        runtime_name: str,
        description: str,
        instructions: str,
        files: tuple[ClawHubSkillFile, ...],
    ) -> None:
        skill_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = skill_dir / "SKILL.md"
        manifest_path.write_text(
            _render_skill_manifest(
                runtime_name=runtime_name,
                description=description,
                instructions=instructions,
            ),
            encoding="utf-8",
        )

        seen_paths: set[str] = set()
        for file in files:
            normalized_path = _normalize_relative_file_path(file.path)
            if normalized_path == "SKILL.md":
                raise ValueError("SKILL.md is managed by the ClawHub skill editor")
            if normalized_path in seen_paths:
                raise ValueError(f"Duplicate skill file path: {normalized_path}")
            seen_paths.add(normalized_path)
            target_path = skill_dir / Path(*PurePosixPath(normalized_path).parts)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if file.encoding == "utf-8":
                target_path.write_text(file.content, encoding="utf-8")
                continue
            if file.encoding == "base64":
                target_path.write_bytes(base64.b64decode(file.content.encode("ascii")))
                continue
            raise ValueError(f"Unsupported file encoding: {file.encoding}")

    def _raise_if_duplicate_runtime_name(
        self,
        *,
        skill_id: str,
        runtime_name: str,
    ) -> None:
        for existing_dir in self._iter_skill_directories():
            if existing_dir.name == skill_id:
                continue
            manifest_path = existing_dir / "SKILL.md"
            try:
                parsed = _parse_skill_manifest(manifest_path)
            except Exception:
                continue
            if parsed.runtime_name == runtime_name:
                raise ValueError(
                    "Duplicate app skill runtime name: "
                    f"{runtime_name} already exists in {existing_dir.name}"
                )


class _ParsedSkillManifest:
    def __init__(
        self,
        *,
        runtime_name: str,
        description: str,
        instructions: str,
        manifest_content: str,
    ) -> None:
        self.runtime_name = runtime_name
        self.description = description
        self.instructions = instructions
        self.manifest_content = manifest_content


def _parse_skill_manifest(manifest_path: Path) -> _ParsedSkillManifest:
    if not manifest_path.is_file():
        raise ValueError("SKILL.md not found")
    raw = manifest_path.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with YAML front matter")
    end_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        raise ValueError("Invalid YAML front matter delimiters")
    front_matter = "".join(lines[1:end_index])
    body = "".join(lines[end_index + 1 :]).strip()
    payload = yaml.safe_load(front_matter)
    if not isinstance(payload, dict):
        raise ValueError("SKILL.md front matter must be a mapping")
    raw_name = payload.get("name")
    runtime_name = _normalize_runtime_name(
        raw_name if isinstance(raw_name, str) else ""
    )
    raw_description = payload.get("description")
    description = raw_description.strip() if isinstance(raw_description, str) else ""
    return _ParsedSkillManifest(
        runtime_name=runtime_name,
        description=description,
        instructions=body,
        manifest_content=raw,
    )


def _render_skill_manifest(
    *,
    runtime_name: str,
    description: str,
    instructions: str,
) -> str:
    front_matter = {
        "name": runtime_name,
        "description": description.strip(),
    }
    serialized_front_matter = yaml.safe_dump(
        front_matter,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    body = instructions.rstrip()
    return f"---\n{serialized_front_matter}\n---\n{body}\n"


def _iter_skill_files(skill_dir: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                path
                for path in skill_dir.rglob("*")
                if path.is_file()
                and not any(
                    part.startswith(".") for part in path.relative_to(skill_dir).parts
                )
            ),
            key=lambda path: path.relative_to(skill_dir).as_posix(),
        )
    )


def _read_skill_file(*, skill_dir: Path, path: Path) -> ClawHubSkillFile:
    relative_path = path.relative_to(skill_dir).as_posix()
    try:
        return ClawHubSkillFile(
            path=relative_path,
            content=path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    except UnicodeDecodeError:
        return ClawHubSkillFile(
            path=relative_path,
            content=base64.b64encode(path.read_bytes()).decode("ascii"),
            encoding="base64",
        )


def _normalize_skill_id(value: str) -> str:
    normalized = value.strip()
    if not _SKILL_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "skill_id must start with an alphanumeric character and only use "
            "letters, digits, dot, underscore, or hyphen"
        )
    return normalized


def _normalize_runtime_name(value: str) -> str:
    normalized = value.strip()
    if not _SKILL_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "runtime_name must start with an alphanumeric character and only use "
            "letters, digits, dot, underscore, or hyphen"
        )
    return normalized


def _normalize_relative_file_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        raise ValueError("Skill file path is required")
    relative_path = PurePosixPath(normalized)
    if relative_path.is_absolute():
        raise ValueError(f"Skill file path must be relative: {value}")
    if any(part in {"", ".", ".."} for part in relative_path.parts):
        raise ValueError(f"Invalid skill file path: {value}")
    return relative_path.as_posix()
