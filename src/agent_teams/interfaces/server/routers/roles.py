from fastapi import APIRouter, Depends
from agent_teams.interfaces.sdk.client import AgentTeamsApp
from agent_teams.core.config import load_runtime_config
from agent_teams.roles.registry import RoleLoader
from agent_teams.tools.defaults import build_default_registry
import inspect

router = APIRouter(prefix="/roles", tags=["Roles"])

# We import the dependency function late or expect it on the Request to avoid circular dependencies
# A simpler way in APIRouter is to look up the state
from fastapi import Request

def get_sdk(request: Request) -> AgentTeamsApp:
    return request.app.state.sdk

from .system import DEFAULT_CONFIG_DIR

@router.get("/")
def list_roles(sdk: AgentTeamsApp = Depends(get_sdk)):
    roles = sdk.list_roles()
    return [role.model_dump() for role in roles]

@router.post("/validate")
def validate_roles():
    config = load_runtime_config(config_dir=DEFAULT_CONFIG_DIR)
    registry = RoleLoader().load_all(config.paths.roles_dir)
    tool_registry = build_default_registry()
    
    for role in registry.list_roles():
        tool_registry.validate_known(role.tools)
        
    loaded_count = len(registry.list_roles())
    return {"valid": True, "loaded_count": loaded_count}
