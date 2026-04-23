# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import math
import re
from collections.abc import Iterable, Mapping

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.agents.instances.models import (
    RuntimeToolSnapshotEntry,
    RuntimeToolsSnapshot,
)
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import ToolContext, ToolDeps, execute_tool_call

DESCRIPTION = load_tool_description(__file__)
_DEFAULT_MAX_RESULTS = 5
_MAX_RESULTS_LIMIT = 20
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_SELECT_PREFIX = "select:"
_MIN_TOKEN_LENGTH = 2
_NAME_WEIGHT = 4.0
_SERVER_WEIGHT = 2.5
_KEYWORD_WEIGHT = 2.0
_DESCRIPTION_WEIGHT = 1.0
_SOURCE_WEIGHT = 0.5
_BM25_K1 = 1.2
_BM25_B = 0.75
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "authorized",
        "by",
        "current",
        "for",
        "from",
        "function",
        "functions",
        "in",
        "local",
        "mcp",
        "of",
        "on",
        "or",
        "runtime",
        "server",
        "skill",
        "the",
        "to",
        "tool",
        "tools",
        "with",
    }
)


@dataclass(frozen=True)
class _SearchableTool:
    entry: RuntimeToolSnapshotEntry
    weighted_terms: Counter[str]
    matched_terms: frozenset[str]
    weighted_length: float
    name_text: str
    server_text: str
    description_text: str


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def tool_search(
        ctx: ToolContext,
        query: str,
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> dict[str, JsonValue]:
        """Discover runtime-authorized tools and inspect their contracts."""

        def _action(
            query: str,
            max_results: int = _DEFAULT_MAX_RESULTS,
        ) -> dict[str, JsonValue]:
            return _search_runtime_tools(
                ctx=ctx,
                query=query.strip(),
                max_results=_clamp_max_results(max_results),
            )

        return await execute_tool_call(
            ctx,
            tool_name="tool_search",
            args_summary={
                "query": query,
                "max_results": max_results,
            },
            action=_action,
            raw_args=locals(),
        )


def _search_runtime_tools(
    *,
    ctx: ToolContext,
    query: str,
    max_results: int,
) -> dict[str, JsonValue]:
    (
        authorized_tools,
        total_authorized_tools,
        active_local_tools,
    ) = _load_authorized_tools(ctx)
    if authorized_tools is None:
        return _build_search_response(
            query=query,
            mode="keyword",
            matches=(),
            total_authorized_tools=0,
            active_local_tools=(),
            warning="Runtime tool snapshot is unavailable for the current instance.",
        )

    if not query:
        return _build_search_response(
            query=query,
            mode="keyword",
            matches=(),
            total_authorized_tools=total_authorized_tools,
            active_local_tools=active_local_tools,
            warning="Query must not be empty.",
        )

    if query.casefold().startswith(_SELECT_PREFIX):
        requested_names = _parse_select_query(query)
        requested_matches = tuple(
            entry
            for name in requested_names
            if (entry := _find_tool_by_name(authorized_tools, name)) is not None
        )
        matches = requested_matches[:max_results]
        missing_names = tuple(
            name
            for name in requested_names
            if _find_tool_by_name(authorized_tools, name) is None
        )
        warning_parts: list[str] = []
        if missing_names:
            warning_parts.append(
                "Some requested tools were not authorized for this runtime: "
                + ", ".join(missing_names)
            )
        if len(requested_matches) > max_results:
            warning_parts.append(
                f"Only the first {max_results} matched tools were returned due to max_results."
            )
        warning = " ".join(warning_parts) if warning_parts else None
        return _build_search_response(
            query=query,
            mode="select",
            matches=matches,
            total_authorized_tools=total_authorized_tools,
            active_local_tools=active_local_tools,
            include_schema=True,
            warning=warning,
        )

    exact_match = _find_tool_by_name(authorized_tools, query)
    if exact_match is not None:
        keyword_matches = _search_by_keywords(
            authorized_tools=authorized_tools,
            query=query,
            max_results=max_results,
        )
        matches = _prepend_exact_match(
            exact_match=exact_match,
            keyword_matches=keyword_matches,
            max_results=max_results,
        )
        return _build_search_response(
            query=query,
            mode="exact",
            matches=matches,
            total_authorized_tools=total_authorized_tools,
            active_local_tools=active_local_tools,
            include_schema=True,
        )

    matches = _search_by_keywords(
        authorized_tools=authorized_tools,
        query=query,
        max_results=max_results,
    )
    return _build_search_response(
        query=query,
        mode="keyword",
        matches=matches,
        total_authorized_tools=total_authorized_tools,
        active_local_tools=active_local_tools,
    )


def _load_authorized_tools(
    ctx: ToolContext,
) -> (
    tuple[tuple[RuntimeToolSnapshotEntry, ...], int, tuple[str, ...]]
    | tuple[None, int, tuple[str, ...]]
):
    try:
        runtime_record = ctx.deps.agent_repo.get_instance(ctx.deps.instance_id)
    except KeyError:
        return None, 0, ()
    snapshot = _parse_runtime_tools_snapshot(runtime_record.runtime_tools_json)
    authorized_tools = _flatten_runtime_tools(snapshot)
    return (
        authorized_tools,
        len(authorized_tools),
        _parse_runtime_active_tools_json(runtime_record.runtime_active_tools_json),
    )


def _parse_runtime_tools_snapshot(raw_snapshot: str) -> RuntimeToolsSnapshot:
    normalized_snapshot = raw_snapshot.strip()
    if not normalized_snapshot:
        return RuntimeToolsSnapshot()
    try:
        return RuntimeToolsSnapshot.model_validate_json(normalized_snapshot)
    except (ValueError, TypeError):
        return RuntimeToolsSnapshot()


def _parse_runtime_active_tools_json(raw_active_tools: str) -> tuple[str, ...]:
    normalized_active_tools = raw_active_tools.strip()
    if not normalized_active_tools:
        return ()
    try:
        parsed = json.loads(normalized_active_tools)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(item for item in parsed if isinstance(item, str))


def _flatten_runtime_tools(
    snapshot: RuntimeToolsSnapshot,
) -> tuple[RuntimeToolSnapshotEntry, ...]:
    return (
        tuple(snapshot.local_tools)
        + tuple(snapshot.skill_tools)
        + tuple(snapshot.mcp_tools)
    )


def _parse_select_query(query: str) -> tuple[str, ...]:
    raw_selection = query[len(_SELECT_PREFIX) :]
    names: list[str] = []
    seen: set[str] = set()
    for item in raw_selection.split(","):
        normalized_name = item.strip()
        if not normalized_name:
            continue
        folded_name = normalized_name.casefold()
        if folded_name in seen:
            continue
        seen.add(folded_name)
        names.append(normalized_name)
    return tuple(names)


def _find_tool_by_name(
    entries: Iterable[RuntimeToolSnapshotEntry],
    tool_name: str,
) -> RuntimeToolSnapshotEntry | None:
    normalized_name = tool_name.strip().casefold()
    if not normalized_name:
        return None
    for entry in entries:
        if entry.name.casefold() == normalized_name:
            return entry
    return None


def _prepend_exact_match(
    *,
    exact_match: RuntimeToolSnapshotEntry,
    keyword_matches: tuple[RuntimeToolSnapshotEntry, ...],
    max_results: int,
) -> tuple[RuntimeToolSnapshotEntry, ...]:
    matches: list[RuntimeToolSnapshotEntry] = [exact_match]
    seen = {exact_match.name.casefold()}
    for entry in keyword_matches:
        folded_name = entry.name.casefold()
        if folded_name in seen:
            continue
        seen.add(folded_name)
        matches.append(entry)
        if len(matches) >= max_results:
            break
    return tuple(matches)


def _search_by_keywords(
    *,
    authorized_tools: tuple[RuntimeToolSnapshotEntry, ...],
    query: str,
    max_results: int,
) -> tuple[RuntimeToolSnapshotEntry, ...]:
    normalized_query = query.strip().casefold()
    query_terms = _query_terms(normalized_query)
    if not query_terms:
        return ()
    searchable_tools = tuple(
        _build_searchable_tool(entry) for entry in authorized_tools
    )
    avg_weighted_length = (
        sum(item.weighted_length for item in searchable_tools) / len(searchable_tools)
        if searchable_tools
        else 1.0
    )
    document_frequency = _document_frequency(searchable_tools, query_terms)

    scored_matches: list[tuple[float, RuntimeToolSnapshotEntry]] = []
    for searchable in searchable_tools:
        score = _score_tool_match(
            searchable=searchable,
            normalized_query=normalized_query,
            query_terms=query_terms,
            document_frequency=document_frequency,
            total_documents=len(searchable_tools),
            avg_weighted_length=avg_weighted_length,
        )
        if score <= 0:
            continue
        scored_matches.append((score, searchable.entry))

    scored_matches.sort(key=lambda item: (-item[0], item[1].name.casefold()))
    return tuple(entry for _score, entry in scored_matches[:max_results])


def _score_tool_match(
    *,
    searchable: _SearchableTool,
    normalized_query: str,
    query_terms: tuple[str, ...],
    document_frequency: Mapping[str, int],
    total_documents: int,
    avg_weighted_length: float,
) -> float:
    entry = searchable.entry
    score = 0.0
    matched_terms: set[str] = set()

    if normalized_query and normalized_query == entry.name.casefold():
        return 1_000.0
    if normalized_query and normalized_query in searchable.name_text:
        score += 4.0
    elif normalized_query and normalized_query in searchable.server_text:
        score += 2.5
    elif normalized_query and normalized_query in searchable.description_text:
        score += 1.5

    for term in query_terms:
        idf = _inverse_document_frequency(
            term=term,
            document_frequency=document_frequency,
            total_documents=total_documents,
        )
        weighted_tf = float(searchable.weighted_terms.get(term, 0.0))
        if weighted_tf > 0:
            matched_terms.add(term)
            score += _bm25_score(
                weighted_tf=weighted_tf,
                weighted_length=searchable.weighted_length,
                avg_weighted_length=avg_weighted_length,
                idf=idf,
            )
            continue
        if term in searchable.name_text:
            matched_terms.add(term)
            score += idf * 1.2
        elif term in searchable.server_text:
            matched_terms.add(term)
            score += idf * 0.8

    if len(matched_terms) < _minimum_required_term_matches(query_terms):
        return 0.0
    if len(matched_terms) == len(query_terms):
        score += 1.0
    return score


def _schema_tokens(schema: Mapping[str, JsonValue]) -> tuple[str, ...]:
    collected_tokens: list[str] = []
    _collect_schema_tokens(dict(schema), collected_tokens, depth=0)
    return tuple(collected_tokens)


def _collect_schema_tokens(
    value: JsonValue,
    collected_tokens: list[str],
    *,
    depth: int,
) -> None:
    if depth > 3:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            collected_tokens.extend(_tokenize(key))
            if key in {"title", "description"} and isinstance(item, str):
                collected_tokens.extend(_tokenize(item))
            _collect_schema_tokens(item, collected_tokens, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _collect_schema_tokens(item, collected_tokens, depth=depth + 1)


def _tokenize(value: str) -> tuple[str, ...]:
    normalized_value = value.casefold()
    return tuple(
        token
        for token in _TOKEN_PATTERN.findall(normalized_value)
        if len(token) >= _MIN_TOKEN_LENGTH
    )


def _minimum_required_term_matches(query_terms: tuple[str, ...]) -> int:
    if len(query_terms) <= 2:
        return len(query_terms)
    return math.ceil(len(query_terms) * 0.6)


def _query_terms(query: str) -> tuple[str, ...]:
    raw_terms = _tokenize(query)
    filtered_terms = tuple(term for term in raw_terms if term not in _STOPWORDS)
    if filtered_terms:
        return filtered_terms
    return raw_terms


def _build_searchable_tool(entry: RuntimeToolSnapshotEntry) -> _SearchableTool:
    name_terms = _tokenize(entry.name)
    server_terms = _tokenize(entry.server_name)
    keyword_terms = _schema_tokens(entry.parameters_json_schema)
    description_terms = _tokenize(entry.description)
    source_terms = _tokenize(entry.source)

    weighted_terms: Counter[str] = Counter()
    weighted_terms.update({term: _NAME_WEIGHT for term in name_terms})
    weighted_terms.update({term: _SERVER_WEIGHT for term in server_terms})
    weighted_terms.update({term: _KEYWORD_WEIGHT for term in keyword_terms})
    weighted_terms.update({term: _DESCRIPTION_WEIGHT for term in description_terms})
    weighted_terms.update({term: _SOURCE_WEIGHT for term in source_terms})

    return _SearchableTool(
        entry=entry,
        weighted_terms=weighted_terms,
        matched_terms=frozenset(weighted_terms.keys()),
        weighted_length=sum(weighted_terms.values()) or 1.0,
        name_text=_normalize_search_text(entry.name),
        server_text=_normalize_search_text(entry.server_name),
        description_text=_normalize_search_text(entry.description),
    )


def _document_frequency(
    searchable_tools: tuple[_SearchableTool, ...],
    query_terms: tuple[str, ...],
) -> dict[str, int]:
    return {
        term: sum(1 for item in searchable_tools if term in item.matched_terms)
        for term in query_terms
    }


def _inverse_document_frequency(
    *,
    term: str,
    document_frequency: Mapping[str, int],
    total_documents: int,
) -> float:
    df = document_frequency.get(term, 0)
    return math.log(1.0 + ((total_documents - df + 0.5) / (df + 0.5)))


def _bm25_score(
    *,
    weighted_tf: float,
    weighted_length: float,
    avg_weighted_length: float,
    idf: float,
) -> float:
    normalized_length = avg_weighted_length or 1.0
    denominator = weighted_tf + _BM25_K1 * (
        1.0 - _BM25_B + _BM25_B * (weighted_length / normalized_length)
    )
    if denominator <= 0:
        return 0.0
    return idf * ((weighted_tf * (_BM25_K1 + 1.0)) / denominator)


def _normalize_search_text(value: str) -> str:
    normalized = value.casefold().replace("_", " ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(part for part in normalized.split() if part)


def _build_search_response(
    *,
    query: str,
    mode: str,
    matches: tuple[RuntimeToolSnapshotEntry, ...],
    total_authorized_tools: int,
    active_local_tools: tuple[str, ...],
    include_schema: bool = False,
    warning: str | None = None,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "query": query,
        "mode": mode,
        "total_authorized_tools": total_authorized_tools,
        "matches": [
            _serialize_match(
                entry,
                include_schema=include_schema,
                active_local_tools=active_local_tools,
            )
            for entry in matches
        ],
    }
    if warning:
        payload["warning"] = warning
    return payload


def _serialize_match(
    entry: RuntimeToolSnapshotEntry,
    *,
    include_schema: bool,
    active_local_tools: tuple[str, ...],
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "name": entry.name,
        "source": entry.source,
        "description": entry.description,
        "kind": entry.kind,
        "sequential": entry.sequential,
        "activation_state": (
            "deferred"
            if entry.source == "local" and entry.name not in set(active_local_tools)
            else "active"
        ),
    }
    if entry.server_name:
        payload["server_name"] = entry.server_name
    if entry.strict is not None:
        payload["strict"] = entry.strict
    if include_schema:
        payload["parameters_json_schema"] = dict(entry.parameters_json_schema)
    return payload


def _clamp_max_results(value: int) -> int:
    return max(1, min(_MAX_RESULTS_LIMIT, int(value)))
