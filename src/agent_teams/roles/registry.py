from __future__ import annotations

from pathlib import Path

import yaml

from agent_teams.core.models import RoleDefinition


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

    def list_roles(self) -> tuple[RoleDefinition, ...]:
        return tuple(self._roles)


class RoleLoader:
    REQUIRED_FIELDS = (
        "role_id",
        "name",
        "version",
        "capabilities",
        "constraints",
        "tools",
        "model_profile",
    )
    OPTIONAL_FIELDS = ("llm_profile",)

    def load_all(self, roles_dir: Path) -> RoleRegistry:
        registry = RoleRegistry()
        for md_file in sorted(roles_dir.glob("*.md")):
            registry.register(self.load_one(md_file))
        if not registry.list_roles():
            raise ValueError(f"No role files found in {roles_dir}")
        return registry

    def load_one(self, path: Path) -> RoleDefinition:
        raw = path.read_text(encoding="utf-8")
        front_matter, body = self._split_front_matter(raw)
        parsed = yaml.safe_load(front_matter)
        if not isinstance(parsed, dict):
            raise ValueError(f"Invalid front matter for role file: {path}")

        missing = [field for field in self.REQUIRED_FIELDS if field not in parsed]
        if missing:
            raise ValueError(f"Missing fields in {path}: {missing}")

        if not body.strip():
            raise ValueError(f"Empty system prompt in {path}")

        depends_on = parsed.get("depends_on", [])
        if depends_on is None:
            depends_on = []
        if not isinstance(depends_on, list):
            raise ValueError(f"depends_on must be a list in {path}")

        mcp_servers = parsed.get("mcp_servers", [])
        if mcp_servers is None:
            mcp_servers = []
        if not isinstance(mcp_servers, list):
            raise ValueError(f"mcp_servers must be a list in {path}")

        skills = parsed.get("skills", [])
        if skills is None:
            skills = []
        if not isinstance(skills, list):
            raise ValueError(f"skills must be a list in {path}")

        return RoleDefinition(
            role_id=str(parsed["role_id"]),
            name=str(parsed["name"]),
            version=str(parsed["version"]),
            capabilities=tuple(str(item) for item in parsed["capabilities"]),
            constraints=tuple(str(item) for item in parsed["constraints"]),
            tools=tuple(str(item) for item in parsed["tools"]),
            mcp_servers=tuple(str(item) for item in mcp_servers),
            skills=tuple(str(item) for item in skills),
            depends_on=tuple(str(item) for item in depends_on),
            model_profile=str(parsed["model_profile"]),
            llm_profile=str(parsed.get("llm_profile", "default")),
            system_prompt=body.strip(),
        )

    def _split_front_matter(self, content: str) -> tuple[str, str]:
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
