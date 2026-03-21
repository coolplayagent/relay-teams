from __future__ import annotations

from collections.abc import Mapping, Sequence

from docker import DockerClient


def build_instance_images(
    *,
    client: DockerClient,
    dataset: Sequence[Mapping[str, object]],
    force_rebuild: bool,
    max_workers: int,
    tag: str,
    env_image_tag: str,
) -> None: ...
