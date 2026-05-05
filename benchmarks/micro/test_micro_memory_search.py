# -*- coding: utf-8 -*-
from __future__ import annotations

import math

from relay_teams.memory.models import MemoryEntry


def _bm25_search(
    query_tokens: tuple[str, ...],
    entries: list[MemoryEntry],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[tuple[MemoryEntry, float]]:
    """Simplified BM25 scoring over memory entry titles and bodies."""
    corpus: list[tuple[str, ...]] = []
    for e in entries:
        tokens = (e.content.title + " " + e.content.body).lower().split()
        corpus.append(tuple(tokens))

    avgdl = sum(len(doc) for doc in corpus) / max(len(corpus), 1)
    n = len(corpus)

    df: dict[str, int] = {}
    for doc in corpus:
        seen: set[str] = set()
        for t in doc:
            if t not in seen:
                df[t] = df.get(t, 0) + 1
                seen.add(t)

    results: list[tuple[MemoryEntry, float]] = []
    for idx, doc in enumerate(corpus):
        score = 0.0
        dl = len(doc)
        tf_map: dict[str, int] = {}
        for t in doc:
            tf_map[t] = tf_map.get(t, 0) + 1
        for qt in query_tokens:
            tf = tf_map.get(qt, 0)
            if tf == 0:
                continue
            idf = math.log((n - df.get(qt, 0) + 0.5) / (df.get(qt, 0) + 0.5) + 1)
            denom = tf + k1 * (1 - b + b * dl / max(avgdl, 1))
            score += idf * (tf * (k1 + 1)) / max(denom, 1e-9)
        if score > 0:
            results.append((entries[idx], score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def test_micro_memory_bm25_100(benchmark, memory_entries_100):
    query = ("memory", "entry", "body", "content")
    result = benchmark(_bm25_search, query, memory_entries_100)
    assert len(result) > 0


def test_micro_memory_bm25_1000(benchmark, memory_entries_1000):
    query = ("memory", "entry", "body", "content")
    result = benchmark(_bm25_search, query, memory_entries_1000)
    assert len(result) > 0


def test_micro_memory_serialization_100(benchmark, memory_entries_100):
    def _roundtrip() -> list[MemoryEntry]:
        import json

        raw = json.dumps([e.model_dump(mode="json") for e in memory_entries_100])
        return [MemoryEntry.model_validate(d) for d in json.loads(raw)]

    result = benchmark(_roundtrip)
    assert len(result) == 100


def test_micro_memory_serialization_1000(benchmark, memory_entries_1000):
    def _roundtrip() -> list[MemoryEntry]:
        import json

        raw = json.dumps([e.model_dump(mode="json") for e in memory_entries_1000])
        return [MemoryEntry.model_validate(d) for d in json.loads(raw)]

    result = benchmark(_roundtrip)
    assert len(result) == 1000
