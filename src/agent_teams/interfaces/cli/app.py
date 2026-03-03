from __future__ import annotations

import json
from pathlib import Path

import typer

from agent_teams.core.config import load_runtime_config
from agent_teams.core.enums import RunEventType
from agent_teams.core.models import IntentInput
from agent_teams.interfaces.sdk.client import AgentTeamsApp
from agent_teams.roles.registry import RoleLoader
from agent_teams.tools.defaults import build_default_registry

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)


def _get_project_root() -> Path:
    return Path(__file__).parent.parent.parent.parent.parent


DEFAULT_CONFIG_DIR = _get_project_root() / ".agent_teams"


@app.command("run-intent")
def run_intent(
    intent: str = typer.Option(..., "--intent"),
    session_id: str | None = typer.Option(None, "--session-id"),
    config_dir: Path = DEFAULT_CONFIG_DIR,
    debug: bool = typer.Option(False, "--debug", help="Enable verbose debug logs"),
) -> None:
    sdk = AgentTeamsApp(config_dir=config_dir, debug=debug)
    result = sdk.run_intent(IntentInput(session_id=session_id, intent=intent))
    if debug:
        typer.echo(result.model_dump_json(indent=2))


@app.command("run-intent-stream")
def run_intent_stream(
    intent: str = typer.Option(..., "--intent"),
    session_id: str | None = typer.Option(None, "--session-id"),
    config_dir: Path = DEFAULT_CONFIG_DIR,
    debug: bool = typer.Option(False, "--debug", help="Enable verbose debug logs"),
) -> None:
    sdk = AgentTeamsApp(config_dir=config_dir, debug=debug)
    for event in sdk.run_intent_stream(
        IntentInput(session_id=session_id, intent=intent)
    ):
        if debug:
            typer.echo(event.model_dump_json())
        else:
            if event.event_type == RunEventType.TEXT_DELTA:
                payload = json.loads(event.payload_json)
                typer.echo(payload.get("content", ""), nl=False)

@app.command("chat")
def chat(
    session_id: str | None = typer.Option(None, "--session-id"),
    config_dir: Path = DEFAULT_CONFIG_DIR,
    debug: bool = typer.Option(False, "--debug", help="Enable verbose debug logs"),
) -> None:
    sdk = AgentTeamsApp(config_dir=config_dir, debug=debug)
    if not session_id:
        record = sdk.create_session()
        session_id = record.session_id
    else:
        sdk.get_session(session_id)
        
    typer.echo(f"Starting interactive chat (Session: {session_id}). Type 'exit' or 'quit' to stop.")
    
    while True:
        try:
            intent = typer.prompt("Prompt")
        except typer.Abort:
            typer.echo()
            break
            
        if intent.strip().lower() in ("exit", "quit"):
            break
            
        if not intent.strip():
            continue
            
        for event in sdk.run_intent_stream(
            IntentInput(session_id=session_id, intent=intent)
        ):
            if debug:
                typer.echo(event.model_dump_json())
            else:
                if event.event_type == RunEventType.TEXT_DELTA:
                    payload = json.loads(event.payload_json)
                    typer.echo(payload.get("content", ""), nl=False)
        typer.echo()


@app.command("tasks-list")
def tasks_list(config_dir: Path = DEFAULT_CONFIG_DIR) -> None:
    sdk = AgentTeamsApp(config_dir=config_dir)
    for task in sdk.list_tasks():
        typer.echo(task.model_dump_json())


@app.command("tasks-query")
def tasks_query(task_id: str, config_dir: Path = DEFAULT_CONFIG_DIR) -> None:
    sdk = AgentTeamsApp(config_dir=config_dir)
    task = sdk.query_task(task_id)
    typer.echo(task.model_dump_json(indent=2))


@app.command("session-create")
def session_create(
    session_id: str | None = typer.Option(None, "--session-id"),
    config_dir: Path = DEFAULT_CONFIG_DIR,
) -> None:
    sdk = AgentTeamsApp(config_dir=config_dir)
    record = sdk.create_session(session_id=session_id)
    typer.echo(record.model_dump_json(indent=2))


@app.command("session-list")
def session_list(config_dir: Path = DEFAULT_CONFIG_DIR) -> None:
    sdk = AgentTeamsApp(config_dir=config_dir)
    for record in sdk.list_sessions():
        typer.echo(record.model_dump_json())


@app.command("session-get")
def session_get(
    session_id: str,
    config_dir: Path = DEFAULT_CONFIG_DIR,
) -> None:
    sdk = AgentTeamsApp(config_dir=config_dir)
    record = sdk.get_session(session_id)
    typer.echo(record.model_dump_json(indent=2))


@app.command("roles-validate")
def roles_validate(config_dir: Path = DEFAULT_CONFIG_DIR) -> None:
    runtime = load_runtime_config(config_dir=config_dir)
    registry = RoleLoader().load_all(runtime.paths.roles_dir)
    tool_registry = build_default_registry()
    for role in registry.list_roles():
        tool_registry.validate_known(role.tools)
    typer.echo(f"Loaded {len(registry.list_roles())} roles")


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host ID to bind the server to"),
    port: int = typer.Option(8000, "--port", help="Port to bind the server to")
) -> None:
    import uvicorn
    from agent_teams.interfaces.server.app import app as fastapi_app
    
    # We run the Uvicorn ASGI server with the FastAPI app instance from server.app
    typer.echo(f"Starting Agent Teams server on http://{host}:{port}")
    uvicorn.run(fastapi_app, host=host, port=port)


def main() -> None:
    app()
