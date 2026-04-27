# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from glob import glob
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from relay_teams.net.clients import create_async_http_client
from relay_teams.paths import get_app_config_dir, get_project_root_or_none

INSTRUCTION_FILE_CANDIDATES: tuple[str, str, str] = (
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
)
PROMPTS_CONFIG_NAME = "prompts.json"
REMOTE_TIMEOUT_SECONDS = 5.0


class PromptInstructionsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instructions: tuple[str, ...] = ()


class LoadedPromptInstructionSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    source: str
    source_type: str
    local_path: Optional[Path] = None
    load_reason: str = "initial"
    memory_type: str = ""


class LoadedPromptInstructions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    sections: tuple[str, ...] = ()
    local_paths: tuple[Path, ...] = ()
    sources: tuple[LoadedPromptInstructionSource, ...] = ()


class PromptInstructionResolver:
    def __init__(
        self,
        *,
        app_config_dir: Path | None = None,
        instructions: tuple[str, ...] | None = None,
    ) -> None:
        self._app_config_dir = (
            get_app_config_dir()
            if app_config_dir is None
            else app_config_dir.expanduser().resolve()
        )
        self._instructions = instructions

    @property
    def prompts_file(self) -> Path:
        return self._app_config_dir / PROMPTS_CONFIG_NAME

    async def load_initial_instructions(
        self,
        *,
        working_directory: Path | None,
        worktree_root: Path | None,
    ) -> LoadedPromptInstructions:
        resolved_workdir = (
            Path.cwd().resolve()
            if working_directory is None
            else working_directory.expanduser().resolve()
        )
        resolved_root = (
            get_project_root_or_none(start_dir=resolved_workdir) or resolved_workdir
            if worktree_root is None
            else worktree_root.expanduser().resolve()
        )
        project_paths = self._resolve_project_instruction_paths(
            start_dir=resolved_workdir,
            stop_dir=resolved_root,
        )
        global_paths = self._resolve_global_instruction_paths()
        configured_sources = self._resolve_configured_instruction_sources(
            working_directory=resolved_workdir,
            worktree_root=resolved_root,
        )
        return await self._load_sources(
            tuple(project_paths) + tuple(global_paths) + configured_sources
        )

    def resolve_dynamic_paths(
        self,
        *,
        file_path: Path,
        workspace_root: Path,
        already_loaded_paths: tuple[Path, ...] = (),
    ) -> tuple[Path, ...]:
        claimed_paths = {path.resolve() for path in already_loaded_paths}
        matches: list[Path] = []
        current = file_path.resolve().parent
        resolved_root = workspace_root.expanduser().resolve()

        while current != resolved_root and resolved_root in current.parents:
            candidate = self._first_existing_instruction_path(current)
            if candidate is not None and candidate.resolve() not in claimed_paths:
                resolved_candidate = candidate.resolve()
                matches.append(resolved_candidate)
                claimed_paths.add(resolved_candidate)
            current = current.parent
        return tuple(matches)

    async def load_paths(self, paths: tuple[Path, ...]) -> LoadedPromptInstructions:
        return await self._load_sources(paths)

    async def _load_sources(
        self,
        sources: tuple[Path | str, ...],
    ) -> LoadedPromptInstructions:
        sections: list[str] = []
        local_paths: list[Path] = []
        loaded_sources: list[LoadedPromptInstructionSource] = []
        seen_local_paths: set[Path] = set()

        for source in sources:
            if isinstance(source, Path):
                resolved_path = source.expanduser().resolve()
                if resolved_path in seen_local_paths:
                    continue
                seen_local_paths.add(resolved_path)
                sections.append(
                    "Instructions from: "
                    + str(resolved_path)
                    + "\n"
                    + resolved_path.read_text(encoding="utf-8").strip()
                )
                local_paths.append(resolved_path)
                loaded_sources.append(
                    LoadedPromptInstructionSource(
                        source=str(resolved_path),
                        source_type="local",
                        local_path=resolved_path,
                        memory_type=_instruction_memory_type_for_path(resolved_path),
                    )
                )
                continue
            remote_text = await self._fetch_url(source)
            sections.append("Instructions from: " + source + "\n" + remote_text)
            loaded_sources.append(
                LoadedPromptInstructionSource(
                    source=source,
                    source_type="remote",
                )
            )

        return LoadedPromptInstructions(
            sections=tuple(section for section in sections if section.strip()),
            local_paths=tuple(local_paths),
            sources=tuple(loaded_sources),
        )

    async def _fetch_url(self, url: str) -> str:
        async with create_async_http_client(
            timeout_seconds=REMOTE_TIMEOUT_SECONDS,
            connect_timeout_seconds=REMOTE_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text.strip()

    def _resolve_project_instruction_paths(
        self,
        *,
        start_dir: Path,
        stop_dir: Path,
    ) -> tuple[Path, ...]:
        for file_name in INSTRUCTION_FILE_CANDIDATES:
            matches = self._find_up(file_name, start_dir=start_dir, stop_dir=stop_dir)
            if matches:
                return matches
        return ()

    def _resolve_global_instruction_paths(self) -> tuple[Path, ...]:
        candidate = self._app_config_dir / "AGENTS.md"
        if candidate.exists():
            return (candidate.resolve(),)
        return ()

    def _resolve_configured_instruction_sources(
        self,
        *,
        working_directory: Path,
        worktree_root: Path,
    ) -> tuple[Path | str, ...]:
        resolved_sources: list[Path | str] = []
        for entry in self._load_config().instructions:
            if _is_remote_source(entry):
                resolved_sources.append(entry)
                continue

            expanded_path = Path(entry).expanduser()
            if expanded_path.is_absolute():
                resolved_sources.extend(
                    Path(match).expanduser().resolve()
                    for match in sorted(glob(str(expanded_path), recursive=True))
                )
                continue

            for base_dir in _iter_dirs_up(
                start_dir=working_directory, stop_dir=worktree_root
            ):
                resolved_sources.extend(
                    Path(match).expanduser().resolve()
                    for match in sorted(
                        glob(str((base_dir / entry).resolve()), recursive=True)
                    )
                )
        return tuple(resolved_sources)

    def _load_config(self) -> PromptInstructionsConfig:
        if self._instructions is not None:
            return PromptInstructionsConfig(instructions=self._instructions)
        return load_prompt_instructions_config(self._app_config_dir)

    def _find_up(
        self,
        file_name: str,
        *,
        start_dir: Path,
        stop_dir: Path,
    ) -> tuple[Path, ...]:
        result: list[Path] = []
        for current_dir in _iter_dirs_up(start_dir=start_dir, stop_dir=stop_dir):
            candidate = current_dir / file_name
            if candidate.exists():
                result.append(candidate.resolve())
        return tuple(result)

    def _first_existing_instruction_path(self, directory: Path) -> Path | None:
        for file_name in INSTRUCTION_FILE_CANDIDATES:
            candidate = directory / file_name
            if candidate.exists():
                return candidate.resolve()
        return None


def load_prompt_instructions_config(config_dir: Path) -> PromptInstructionsConfig:
    prompts_file = config_dir.expanduser().resolve() / PROMPTS_CONFIG_NAME
    if not prompts_file.exists():
        return PromptInstructionsConfig()

    try:
        payload = json.loads(prompts_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to parse {PROMPTS_CONFIG_NAME}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{PROMPTS_CONFIG_NAME} must be a JSON object.")
    try:
        return PromptInstructionsConfig.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"Invalid {PROMPTS_CONFIG_NAME}: {exc}") from exc


def _iter_dirs_up(*, start_dir: Path, stop_dir: Path) -> tuple[Path, ...]:
    resolved_start = start_dir.expanduser().resolve()
    resolved_stop = stop_dir.expanduser().resolve()
    current = resolved_start
    result: list[Path] = []

    while True:
        result.append(current)
        if current == resolved_stop:
            break
        if current == current.parent:
            break
        current = current.parent

    return tuple(result)


def _is_remote_source(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"}


def _instruction_memory_type_for_path(path: Path) -> str:
    file_name = path.name.casefold()
    if file_name == "agents.md":
        return "project"
    if file_name == "claude.md":
        return "claude"
    if file_name == "gemini.md":
        return "gemini"
    return "configured"
