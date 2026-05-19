# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.builtin import get_builtin_skills_dir
from relay_teams.skills.skill_registry import SkillRegistry


def _skill_path() -> Path:
    return get_builtin_skills_dir() / "relay-knowledge" / "SKILL.md"


def _skill_content() -> str:
    return _skill_path().read_text(encoding="utf-8")


def test_builtin_relay_knowledge_skill_is_discoverable(tmp_path: Path) -> None:
    registry = SkillRegistry.from_skill_dirs(
        app_skills_dir=tmp_path / "skills",
        builtin_skills_dir=get_builtin_skills_dir(),
    )

    skill = registry.get_skill_definition("relay-knowledge")

    assert skill is not None
    assert skill.metadata.name == "relay-knowledge"
    assert skill.metadata.resources == {}
    assert skill.metadata.scripts == {}


def test_builtin_relay_knowledge_skill_has_skill_version() -> None:
    content = _skill_content()

    assert "name: relay-knowledge" in content
    assert 'version: "1.0.0"' in content
    assert "description: Use Relay Knowledge through the relay-knowledge CLI" in content


def test_builtin_relay_knowledge_skill_uses_direct_cli_without_wrapper() -> None:
    content = _skill_content()

    assert "Invoke Relay Knowledge directly with the `relay-knowledge` CLI." in content
    assert "relay-knowledge --version" in content
    assert "relay-knowledge version" in content
    assert "relay-knowledge status --format json" in content
    assert "python " not in content
    assert "relay_knowledge_cli.py" not in content
    assert "scripts/relay_knowledge_cli.py" not in content
    assert not (
        get_builtin_skills_dir()
        / "relay-knowledge"
        / "scripts"
        / "relay_knowledge_cli.py"
    ).exists()


def test_builtin_relay_knowledge_skill_documents_cli_download_source() -> None:
    content = _skill_content()

    assert "https://github.com/coolplayagent/relay-knowledge/releases" in content
    assert "coolplayagent/relay-knowledge" in content
    assert "platform-specific archive" in content
    assert "checksums.txt" in content
    assert "executable on `PATH`" in content
    assert "managed tool installer or connector" in content
    assert "project-specific APIs or directories" in content


def test_builtin_relay_knowledge_skill_guides_latest_cli_download() -> None:
    content = _skill_content()

    assert "## Download Latest CLI" in content
    assert "do not rely on a hard-coded version" in content
    assert (
        "gh release view --repo coolplayagent/relay-knowledge --json "
        "tagName,url,assets,isLatest" in content
    )
    assert (
        "curl -fsSL "
        "https://api.github.com/repos/coolplayagent/relay-knowledge/releases/latest"
        in content
    )
    assert "Do not guess asset names; inspect the latest release asset list." in content
    assert "sha256sum -c checksums.txt --ignore-missing" in content
    assert "cargo binstall relay-knowledge --no-confirm" in content
    assert "cargo install relay-knowledge --locked" in content
    assert "cargo info relay-knowledge" in content
    assert "cargo search relay-knowledge --limit 5" in content
    assert "fall back to the GitHub release path" in content


def test_builtin_relay_knowledge_skill_avoids_project_specific_coupling() -> None:
    content = _skill_content()

    assert "Agent Teams" not in content
    assert "relay-teams" not in content
    assert "/api/connectors" not in content
    assert "app bin directory" not in content


def test_builtin_relay_knowledge_skill_documents_version_check() -> None:
    content = _skill_content()

    assert "## Version Checks" in content
    assert "relay-knowledge version" in content
    assert "relay-knowledge version check --format json" in content
    assert "does not load runtime configuration" in content
    assert "does not access the network" in content
    assert "configured `net::http` path" in content
    assert "query GitHub Releases and crates.io" in content
    assert "caches the result in the runtime cache directory" in content
    assert "read-only network diagnostic" in content
    assert "does not install, replace, or restart the binary" in content
    assert "main command stdout is emitted first" in content
    assert "must not be interpreted as command failure or automatic upgrade" in content


def test_builtin_relay_knowledge_skill_constrains_cli_commands() -> None:
    content = _skill_content()

    assert "## Command Allowlist" in content
    assert "Allowed `repo` subcommands" in content
    assert "Do not use `repo list`" in content
    assert "Do not run bare `repo`" in content
    assert "- `repo`\n" not in content
    assert "- `health`" in content
    assert "- `version`" in content
    assert "- `version check`" in content
    assert "- `help`" in content
    assert "- `ingest`" in content
    assert "- `provider probe`" in content
    assert "- `proposal list`" in content
    assert "- `service definition write`" in content
    assert "- `service operator pause`" in content
    assert "- `repo register`" in content
    assert "- `repo status`" in content


def test_builtin_relay_knowledge_skill_declares_global_flags() -> None:
    content = _skill_content()

    assert "Allowed global flags:" in content
    assert "- `--version`" in content
    assert "- `--help`" in content
    assert "- `--format text|json|markdown|streaming-json`" in content
    assert "Do not use `--format streaming-json` with `version`" in content
    assert "Prefer `text` or `json` for `help`." in content


def test_builtin_relay_knowledge_skill_lists_setup_profiles() -> None:
    content = _skill_content()

    assert "relay-knowledge setup profile local --format json" in content
    assert "relay-knowledge setup profile agent-readonly --format json" in content
    assert "relay-knowledge setup profile service --format json" in content
    assert "relay-knowledge setup profile external-embedding --format json" in content
    assert "Use `setup profile` only with one of" in content
    assert "Profiles are recommendations only" in content


def test_builtin_relay_knowledge_skill_guides_external_vector_environment() -> None:
    content = _skill_content()

    assert "For external semantic/vector backends" in content
    assert "RELAY_KNOWLEDGE_SEMANTIC_BACKEND=external" in content
    assert "RELAY_KNOWLEDGE_VECTOR_BACKEND=external" in content
    assert "RELAY_KNOWLEDGE_LLM_PROVIDER=openai_compatible" in content
    assert "RELAY_KNOWLEDGE_EMBEDDING_BASE_URL" in content
    assert "RELAY_KNOWLEDGE_EMBEDDING_API_KEY" in content
    assert "RELAY_KNOWLEDGE_TEXT_EMBEDDING_MODEL" in content
    assert "RELAY_KNOWLEDGE_IMAGE_EMBEDDING_MODEL" in content
    assert "RELAY_KNOWLEDGE_EMBEDDING_DIMENSION" in content
    assert "relay-knowledge provider probe --format json" in content
    assert (
        "relay-knowledge index refresh --kind semantic --kind vector --format json"
        in content
    )


def test_builtin_relay_knowledge_skill_does_not_register_for_diagnostics() -> None:
    content = _skill_content()

    assert (
        "For diagnostic or status-only requests, do not register a repository just "
        "to discover an alias" in content
    )
    assert "ask for the alias or report that the alias is required" in content
    assert "repo register" in content


def test_builtin_relay_knowledge_skill_guides_registration_for_code_graphs() -> None:
    content = _skill_content()

    assert (
        "Treat requests to build, open, inspect, or query a code knowledge graph, "
        "code graph, repository graph, code knowledge map, or code map as "
        "repository indexing/setup intent." in content
    )
    assert (
        "For indexing, code-query, code graph, or code map requests where the user "
        "asks to use the current repository and no alias is provided" in content
    )


def test_builtin_relay_knowledge_skill_constrains_repo_index_options() -> None:
    content = _skill_content()

    assert "Do not pass `--path` or `--language` to `repo index`" in content
    assert "only accepts the alias plus `--ref` and `--dry-run`" in content
    assert (
        "relay-knowledge repo scope preview <alias> --ref HEAD --format json" in content
    )
    assert "relay-knowledge repo index <alias> --dry-run --format json" in content


def test_builtin_relay_knowledge_skill_documents_cold_index_progress() -> None:
    content = _skill_content()

    assert "Cold `repo index` can take a long time." in content
    assert "relay-knowledge repo index <alias> --ref HEAD --format json" in content
    assert "relay-knowledge repo status <alias> --format json" in content
    assert "relay-knowledge worker status --format json" in content
    assert "relay-knowledge worker status --kind embedding --format json" in content
    assert "tell the user they can continue checking progress" in content
    assert "use `task.state` as the authoritative state" in content
    assert "treat the index as ready only when `task.state` is `succeeded`" in content
    assert "Use `status.state=fresh` only when the response has no `task`" in content
    assert "Do not start `repo query`, `repo impact`, or `repo report`" in content


def test_builtin_relay_knowledge_skill_lists_parameter_enums() -> None:
    content = _skill_content()

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
        "Allowed `proposal list --state` values: `proposed`, `accepted`, "
        "`rejected`, `superseded`." in content
    )


def test_builtin_relay_knowledge_skill_constrains_proposal_decisions() -> None:
    content = _skill_content()

    assert "proposal reject <proposal_id> --by <actor>" in content
    assert (
        "`proposal accept`, `proposal reject`, and `proposal supersede` require "
        "a proposal id and `--by <actor>`." in content
    )
    assert (
        "Do not run a proposal decision command if either value is unknown." in content
    )


def test_builtin_relay_knowledge_skill_requires_background_service_run() -> None:
    content = _skill_content()
    service_block = content.split("Use service diagnostics first:", 1)[1].split(
        "Do not run `service run`",
        1,
    )[0]

    assert "Do not run `service run` as a normal synchronous shell command." in content
    assert "host's background process mechanism" in content
    assert "do not run it as a blocking foreground command" in content
    assert "Run `service run` only through a background process mechanism." in content
    assert "service run --web --mcp streamable-http" not in service_block


def test_builtin_relay_knowledge_skill_requires_explicit_mutation_request() -> None:
    content = _skill_content()

    assert "Only run mutation commands when the user explicitly asks" in content
    assert "`repo index`" in content
    assert "`proposal accept`" in content
    assert "`service operator resume`" in content
    assert "`service run`" in content
    assert "Do not mutate Relay Knowledge state for diagnostic requests." in content
    assert "version check" in content


def test_builtin_relay_knowledge_skill_constrains_subagent_concurrency() -> None:
    content = _skill_content()

    assert "## Concurrency And Subagents" in content
    assert "Use subagents only when the user explicitly asks" in content
    assert "Good parallel subagent tasks are read-only" in content
    assert "Do not let subagents concurrently run mutation commands." in content
    assert "Mutation commands are `ingest`, `repo register`, `repo index`" in content
    assert "Subagents should return observations only." in content
    assert "The main agent decides whether an index is usable" in content


def test_builtin_relay_knowledge_skill_documents_db_reuse_on_upgrade() -> None:
    content = _skill_content()

    assert "## Upgrade And Database Reuse" in content
    assert (
        "reuse its existing runtime database and config across CLI upgrades" in content
    )
    assert "do not delete data/config directories" in content
    assert "reuse the same `RELAY_KNOWLEDGE_HOME`" in content
    assert "relay-knowledge setup doctor --format json" in content
    assert "relay-knowledge status --format json" in content
    assert "relay-knowledge health --format json" in content
    assert (
        "Do not assume an upgraded CLI requires rebuilding a cold repository index."
        in content
    )
