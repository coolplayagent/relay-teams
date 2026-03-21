from __future__ import annotations

from typing import Protocol


class DockerClient(Protocol):
    ...


def from_env() -> DockerClient: ...
