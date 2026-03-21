from __future__ import annotations

from collections.abc import Iterable, Mapping

DatasetRow = Mapping[str, object]


def load_dataset(
    path: str,
    *,
    split: str,
    streaming: bool = False,
) -> Iterable[DatasetRow]: ...
