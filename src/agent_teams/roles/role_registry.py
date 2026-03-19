# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import yaml

from agent_teams.roles.memory_models import MemoryProfile, default_memory_profile
from agent_teams.roles.role_models import RoleConfigSource
from agent_teams.roles.role_models import RoleDefinition

COORDINATOR_REQUIRED_TOOLS = frozenset(
    (
        "create_tasks",
        "update_task",
        "dispatch_task",
    )
)
LEGACY_COORDINATOR_IDENTIFIERS = frozenset(
    ("coordinator", "coordinator agent", "coordinator_agent")
)


def is_coordinator_role_definition(role: RoleDefinition) -> bool:
    return COORDINATOR_REQUIRED_TOOLS.issubset(
        set(role.tools)
    ) or _looks_like_legacy_coordinator(role)


def _looks_like_legacy_coordinator(role: RoleDefinition) -> bool:
    role_id = role.role_id.strip().casefold()
    name = role.name.strip().casefold()
    return (
        role_id in LEGACY_COORDINATOR_IDENTIFIERS
        or name in LEGACY_COORDINATOR_IDENTIFIERS
    )


class RoleRegistry:
    def __init__(self) -> None:
        self._roles: list[RoleDefinition] = []

    def register(self, role: RoleDefinition) -> None:
        for idx, existing in enumerate(self._roles):
            if existing.role_id == role.role_id:
                self._roles[idx] = role
                return
        self._roles.append(role)

    def get(self, role_id: str) -> RoleDefinition:
        for role in self._roles:
            if role.role_id == role_id:
                return role
        raise KeyError(f"Unknown role_id: {role_id}")

    def get_coordinator(self) -> RoleDefinition:
        candidates = [
            role for role in self._roles if is_coordinator_role_definition(role)
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            role_ids = ", ".join(sorted(role.role_id for role in candidates))
            raise ValueError(f"Multiple coordinator role candidates found: {role_ids}")

        legacy_candidates = [
            role for role in self._roles if _looks_like_legacy_coordinator(role)
        ]
        if len(legacy_candidates) == 1:
            return legacy_candidates[0]
        if len(legacy_candidates) > 1:
            role_ids = ", ".join(sorted(role.role_id for role in legacy_candidates))
            raise ValueError(
                f"Multiple legacy coordinator role candidates found: {role_ids}"
            )

        raise KeyError("Coordinator role could not be resolved from loaded roles")

    def get_coordinator_role_id(self) -> str:
        return self.get_coordinator().role_id

    def is_coordinator_role(self, role_id: str) -> bool:
        try:
            return self.get_coordinator_role_id() == role_id
        except (KeyError, ValueError):
            return False

    def list_roles(self) -> tuple[RoleDefinition, ...]:
        return tuple(self._roles)


class RoleLoader:
    REQUIRED_FIELDS = (
        "role_id",
        "name",
        "description",
        "version",
        "tools",
    )
    OPTIONAL_FIELDS = ("model_profile",)

    def load_all(self, roles_dir: Path) -> RoleRegistry:
        registry = RoleRegistry()
        for md_file in sorted(roles_dir.glob("*.md")):
            registry.register(self.load_one(md_file))
        if not registry.list_roles():
            raise ValueError(f"No role files found in {roles_dir}")
        return registry

    def load_builtin_and_app(
        self,
        *,
        builtin_roles_dir: Path,
        app_roles_dir: Path,
    ) -> RoleRegistry:
        registry = RoleRegistry()
        for md_file in sorted(builtin_roles_dir.glob("*.md")):
            registry.register(self.load_one(md_file))
        for md_file in sorted(app_roles_dir.glob("*.md")):
            registry.register(self.load_one(md_file))
        if not registry.list_roles():
            raise ValueError(
                f"No role files found in {builtin_roles_dir} or {app_roles_dir}"
            )
        return registry

    def build_effective_role_map(
        self,
        *,
        builtin_roles_dir: Path,
        app_roles_dir: Path,
    ) -> dict[str, tuple[Path, RoleConfigSource]]:
        resolved: dict[str, tuple[Path, RoleConfigSource]] = {}
        for md_file in sorted(builtin_roles_dir.glob("*.md")):
            definition = self.load_one(md_file)
            resolved[definition.role_id] = (md_file, RoleConfigSource.BUILTIN)
        for md_file in sorted(app_roles_dir.glob("*.md")):
            definition = self.load_one(md_file)
            resolved[definition.role_id] = (md_file, RoleConfigSource.APP)
        return resolved

    def load_one(self, path: Path) -> RoleDefinition:
        raw = path.read_text(encoding="utf-8")
        return self.load_from_text(raw, source_name=str(path))

    def load_from_text(self, content: str, *, source_name: str) -> RoleDefinition:
        front_matter, body = self._split_front_matter(content)
        parsed = yaml.safe_load(front_matter)
        if not isinstance(parsed, dict):
            raise ValueError(f"Invalid front matter for role file: {source_name}")

        missing = [field for field in self.REQUIRED_FIELDS if field not in parsed]
        if missing:
            raise ValueError(f"Missing fields in {source_name}: {missing}")

        if not body.strip():
            raise ValueError(f"Empty system prompt in {source_name}")

        if "depends_on" in parsed:
            raise ValueError(
                f"depends_on is not allowed in role file {source_name}; task ordering belongs to runtime task orchestration, not role metadata"
            )

        mcp_servers = parsed.get("mcp_servers", [])
        if mcp_servers is None:
            mcp_servers = []
        if not isinstance(mcp_servers, list):
            raise ValueError(f"mcp_servers must be a list in {source_name}")

        skills = parsed.get("skills", [])
        if skills is None:
            skills = []
        if not isinstance(skills, list):
            raise ValueError(f"skills must be a list in {source_name}")

        memory_profile_raw = parsed.get("memory_profile")
        memory_profile = default_memory_profile()
        if memory_profile_raw is not None:
            if not isinstance(memory_profile_raw, dict):
                raise ValueError(f"memory_profile must be an object in {source_name}")
            memory_profile = MemoryProfile.model_validate(memory_profile_raw)

        return RoleDefinition(
            role_id=str(parsed["role_id"]),
            name=str(parsed["name"]),
            description=str(parsed["description"]),
            version=str(parsed["version"]),
            tools=tuple(str(item) for item in parsed["tools"]),
            mcp_servers=tuple(str(item) for item in mcp_servers),
            skills=tuple(str(item) for item in skills),
            model_profile=str(parsed.get("model_profile", "default")),
            memory_profile=memory_profile,
            system_prompt=body.strip(),
        )

    def _split_front_matter(self, content: str) -> tuple[str, str]:
        content = content.lstrip("\ufeff")
        if not content.startswith("---"):
            raise ValueError("Role markdown must start with YAML front matter")

        lines = content.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise ValueError("Role markdown must start with YAML front matter")

        end_index: int | None = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_index = idx
                break

        if end_index is None:
            raise ValueError("Invalid YAML front matter delimiters")

        front_matter = "".join(lines[1:end_index])
        body = "".join(lines[end_index + 1 :])
        return front_matter, body
