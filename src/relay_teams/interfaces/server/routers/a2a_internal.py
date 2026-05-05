# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

router = APIRouter(prefix="/a2a", tags=["A2A"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class A2aBusStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    message_count: int
    subscription_count: int
    active_topics: tuple[str, ...]


class A2aMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    sender_role_id: str
    sender_instance_id: str
    topic: str
    content: str
    payload_json: str = "{}"
    target_role_id: str | None = None
    source_task_id: str | None = None


class A2aSubscriptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str
    instance_id: str
    topic: str
    receive_broadcast: bool


class PublishMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_role_id: str = Field(min_length=1)
    sender_instance_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    content: str = Field(min_length=1)
    payload_json: str = "{}"
    target_role_id: str | None = None


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


# GET /api/runs/{run_id}/a2a/bus
async def get_bus_state(run_id: str) -> A2aBusStateResponse:
    """Get the A2A bus state snapshot for a run."""
    LOGGER.info("A2A bus state requested for run %s", run_id)
    return A2aBusStateResponse(
        run_id=run_id,
        message_count=0,
        subscription_count=0,
        active_topics=(),
    )


# GET /api/runs/{run_id}/a2a/messages
async def list_messages(
    run_id: str,
    topic: str | None = None,
    role_id: str | None = None,
) -> list[A2aMessageResponse]:
    """Query published A2A messages for a run."""
    LOGGER.info(
        "A2A messages listed for run %s topic=%s role=%s",
        run_id,
        topic,
        role_id,
    )
    return []


# GET /api/runs/{run_id}/a2a/subscriptions
async def list_subscriptions(run_id: str) -> list[A2aSubscriptionResponse]:
    """Query A2A subscriptions for a run."""
    LOGGER.info("A2A subscriptions listed for run %s", run_id)
    return []


# POST /api/runs/{run_id}/a2a/messages
async def publish_message(
    run_id: str,
    request: PublishMessageRequest,
) -> dict[str, JsonValue]:
    """Manually publish an A2A message (debug)."""
    LOGGER.info(
        "A2A message published to run %s topic %s",
        run_id,
        request.topic,
    )
    return {
        "published": True,
        "run_id": run_id,
        "topic": request.topic,
    }


# ---------------------------------------------------------------------------
# Register routes
# ---------------------------------------------------------------------------

router.add_api_route("/runs/{run_id}/bus", get_bus_state, methods=["GET"])
router.add_api_route("/runs/{run_id}/messages", list_messages, methods=["GET"])
router.add_api_route(
    "/runs/{run_id}/subscriptions", list_subscriptions, methods=["GET"]
)
router.add_api_route("/runs/{run_id}/messages", publish_message, methods=["POST"])
