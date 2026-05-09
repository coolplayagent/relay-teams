# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agent_runtimes.bus import A2ABus
from relay_teams.agent_runtimes.bus_models import (
    A2aBusMessage,
    A2aBusState,
    A2aSubscription,
    A2aTopic,
)
from relay_teams.agent_runtimes.tools import send_a2a_message, subscribe_a2a_topic
from relay_teams.agents import orchestration


def test_a2a_package_exports_runtime_implementations() -> None:
    assert orchestration.A2ABus is A2ABus
    assert orchestration.A2aBusMessage is A2aBusMessage
    assert orchestration.A2aBusState is A2aBusState
    assert orchestration.A2aSubscription is A2aSubscription
    assert orchestration.A2aTopic is A2aTopic
    assert orchestration.send_a2a_message is send_a2a_message
    assert orchestration.subscribe_a2a_topic is subscribe_a2a_topic
