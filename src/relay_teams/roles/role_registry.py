# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import yaml

from relay_teams.computer import ExecutionSurface
from relay_teams.hooks import (
    parse_tolerant_hooks_payload,
)
from relay_teams.hooks.hook_models import HooksConfig
from relay_teams.logger import get_logger
from relay_teams.roles.default_role_tools import (
    COORDINATOR_IDENTIFIERS,
    COORDINATOR_REQUIRED_TOOLS,
    apply_default_role_tools,
)
from relay_teams.roles.memory_models import MemoryProfile, default_memory_profile
from relay_teams.roles.role_models import RoleConfigSource, RoleDefinition, RoleMode

MAIN_AGENT_ROLE_ID = "MainAgent"
MAIN_AGENT_IDENTIFIERS = frozenset(("mainagent", "main agent", "main_agent"))
LOGGER = get_logger(__name__)


def is_coordinator_role_definition(role: RoleDefinition) -> bool:
    role_id = role.role_id.strip().casefold()
    name = role.name.strip().casefold()
    return (
        COORDINATOR_REQUIRED_TOOLS.issubset(set(role.tools))
        or role_id in COORDINATOR_IDENTIFIERS
        or name in COORDINATOR_IDENTIFIERS
    )


def is_main_agent_role_definition(role: RoleDefinition) -> bool:
    role_id = role.role_id.strip().casefold()
    name = role.name.strip().casefold()
    return role_id in MAIN_AGENT_IDENTIFIERS or name in MAIN_AGENT_IDENTIFIERS


def is_reserved_system_role_definition(role: RoleDefinition) -> bool:
    return is_coordinator_role_definition(role) or is_main_agent_role_definition(role)


class SystemRolesUnavailableError(RuntimeError):
    pass


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

        raise KeyError("Coordinator role could not be resolved from loaded roles")

    def get_coordinator_role_id(self) -> str:
        return self.get_coordinator().role_id

    def get_main_agent(self) -> RoleDefinition:
        candidates = [
            role for role in self._roles if is_main_agent_role_definition(role)
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            role_ids = ", ".join(sorted(role.role_id for role in candidates))
            raise ValueError(f"Multiple main agent role candidates found: {role_ids}")
        raise KeyError("Main agent role could not be resolved from loaded roles")

    def get_main_agent_role_id(self) -> str:
        return self.get_main_agent().role_id

    def list_normal_mode_roles(self) -> tuple[RoleDefinition, ...]:
        main_agent = self.get_main_agent()
        roles = [main_agent]
        non_system_roles = sorted(
            (
                role
                for role in self._roles
                if not self.is_coordinator_role(role.role_id)
                and not self.is_main_agent_role(role.role_id)
                and _role_available_in_normal_mode(role)
            ),
            key=lambda role: (role.name, role.role_id),
        )
        roles.extend(non_system_roles)
        return tuple(roles)

    def list_subagent_roles(self) -> tuple[RoleDefinition, ...]:
        return tuple(
            sorted(
                (
                    role
                    for role in self._roles
                    if not self.is_coordinator_role(role.role_id)
                    and not self.is_main_agent_role(role.role_id)
                    and _role_available_as_subagent(role)
                ),
                key=lambda role: (role.name, role.role_id),
            )
        )

    def resolve_normal_mode_role_id(self, role_id: str | None) -> str:
        main_agent_role_id = self.get_main_agent_role_id()
        normalized = str(role_id or "").strip()
        if not normalized:
            return main_agent_role_id
        if self.is_coordinator_role(normalized):
            raise ValueError(
                f"Coordinator role cannot be used in normal mode: {normalized}"
            )
        try:
            role = self.get(normalized)
        except KeyError as exc:
            raise ValueError(f"Unknown normal mode role: {normalized}") from exc
        if self.is_main_agent_role(role.role_id):
            return role.role_id
        if is_reserved_system_role_definition(role):
            raise ValueError(
                f"Reserved system role cannot be used in normal mode: {role.role_id}"
            )
        if not _role_available_in_normal_mode(role):
            raise ValueError(
                f"Role cannot be used in normal mode: {role.role_id} (mode={role.mode.value})"
            )
        return role.role_id

    def resolve_subagent_role_id(self, role_id: str) -> str:
        normalized = str(role_id or "").strip()
        if not normalized:
            raise ValueError("role_id must not be empty")
        if self.is_coordinator_role(normalized):
            raise ValueError(
                f"Coordinator role cannot be used as a subagent: {normalized}"
            )
        if self.is_main_agent_role(normalized):
            raise ValueError(
                f"Main agent role cannot be used as a subagent: {normalized}"
            )
        try:
            role = self.get(normalized)
        except KeyError as exc:
            raise ValueError(f"Unknown subagent role: {normalized}") from exc
        if is_reserved_system_role_definition(role):
            raise ValueError(
                f"Reserved system role cannot be used as a subagent: {role.role_id}"
            )
        if not _role_available_as_subagent(role):
            raise ValueError(
                f"Role cannot be used as a subagent: {role.role_id} (mode={role.mode.value})"
            )
        return role.role_id

    def is_coordinator_role(self, role_id: str) -> bool:
        try:
            return self.get_coordinator_role_id() == role_id
        except (KeyError, ValueError):
            return False

    def is_main_agent_role(self, role_id: str) -> bool:
        try:
            return self.get_main_agent_role_id() == role_id
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

    def load_all(self, roles_dir: Path, *, allow_empty: bool = False) -> RoleRegistry:
        registry = RoleRegistry()
        for md_file in sorted(roles_dir.glob("*.md")):
            registry.register(self.load_one(md_file))
        if not allow_empty and not registry.list_roles():
            raise ValueError(f"No role files found in {roles_dir}")
        return registry

    def load_builtin_and_app(
        self,
        *,
        builtin_roles_dir: Path,
        app_roles_dir: Path,
        allow_empty: bool = False,
    ) -> RoleRegistry:
        registry = RoleRegistry()
        for md_file in sorted(builtin_roles_dir.glob("*.md")):
            registry.register(self.load_one(md_file))
        for md_file in sorted(app_roles_dir.glob("*.md")):
            registry.register(self.load_one(md_file))
        if not allow_empty and not registry.list_roles():
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
            tools=apply_default_role_tools(
                role_id=str(parsed["role_id"]),
                role_name=str(parsed["name"]),
                mode=str(parsed.get("mode", RoleMode.PRIMARY.value)),
                tools=tuple(str(item) for item in parsed["tools"]),
            ),
            mcp_servers=tuple(str(item) for item in mcp_servers),
            skills=tuple(str(item) for item in skills),
            model_profile=str(parsed.get("model_profile", "default")),
            bound_agent_id=(
                str(parsed["bound_agent_id"]).strip()
                if "bound_agent_id" in parsed and parsed["bound_agent_id"] is not None
                else None
            ),
            execution_surface=ExecutionSurface(
                str(parsed.get("execution_surface", ExecutionSurface.API.value))
            ),
            mode=RoleMode(str(parsed.get("mode", RoleMode.PRIMARY.value))),
            memory_profile=memory_profile,
            hooks=_parse_frontmatter_hooks(
                parsed.get("hooks"),
                source_name=source_name,
            ),
            source_path=Path(source_name) if source_name else None,
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


def ensure_required_system_roles(registry: RoleRegistry) -> None:
    errors: list[str] = []
    try:
        registry.get_coordinator_role_id()
    except (KeyError, ValueError) as exc:
        errors.append(f"coordinator: {exc}")
    try:
        registry.get_main_agent_role_id()
    except (KeyError, ValueError) as exc:
        errors.append(f"main_agent: {exc}")
    if errors:
        raise SystemRolesUnavailableError(
            "Required system roles are unavailable: " + "; ".join(errors)
        )


def _role_available_in_normal_mode(role: RoleDefinition) -> bool:
    return role.mode in {RoleMode.PRIMARY, RoleMode.ALL}


def _role_available_as_subagent(role: RoleDefinition) -> bool:
    return role.mode in {RoleMode.SUBAGENT, RoleMode.ALL}


def _parse_frontmatter_hooks(
    value: object,
    *,
    source_name: str,
) -> HooksConfig:
    try:
        if isinstance(value, dict) and "hooks" in value:
            return parse_tolerant_hooks_payload(value)
        if isinstance(value, dict):
            return parse_tolerant_hooks_payload({"hooks": value})
    except Exception as exc:
        LOGGER.warning(
            "Ignoring invalid role frontmatter hooks",
            extra={"source_name": source_name, "error": str(exc)},
        )
    return HooksConfig()
