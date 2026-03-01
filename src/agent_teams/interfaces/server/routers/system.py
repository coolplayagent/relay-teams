from fastapi import APIRouter, HTTPException
from agent_teams.core.config import load_runtime_config
from pathlib import Path

# Since this config logic existed in app.py we replicate it here
def _get_project_root() -> Path:
    return Path(__file__).parent.parent.parent.parent.parent.parent

DEFAULT_CONFIG_DIR = _get_project_root() / ".agent_teams"

router = APIRouter(prefix="/global", tags=["System"])

@router.get("/health")
def health_check():
    # app.version is technically available from the app request but hardcoded for simplicity right now
    return {"status": "ok", "version": "0.1.0"}

@router.get("/config")
def get_global_config():
    try:
        config = load_runtime_config(config_dir=DEFAULT_CONFIG_DIR)
        return config.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
