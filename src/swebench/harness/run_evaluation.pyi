from __future__ import annotations

from collections.abc import Mapping

from docker import DockerClient
from swebench.harness.test_spec.test_spec import TestSpec


def run_instance(
    *,
    test_spec: TestSpec,
    pred: Mapping[str, str | None],
    rm_image: bool,
    force_rebuild: bool,
    client: DockerClient,
    run_id: str,
    timeout: int | None = None,
) -> dict[str, object]: ...
