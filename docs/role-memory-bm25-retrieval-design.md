# Role Memory BM25 Retrieval Design

## Summary

This document defines how Agent Teams can add non-vector retrieval for durable role memory by reusing the existing local retrieval stack based on SQLite FTS5 and BM25.

The goal is not to replace reflection summaries or compaction. The goal is to add a retrieval path for memory records so runtime memory injection can rank relevant historical facts, plans, and prior decisions without requiring embeddings.

This design deliberately follows the same retrieval primitives already used by skill routing:

- typed retrieval scopes
- durable `retrieval_documents`
- SQLite FTS5 indexes
- query-time BM25 field weighting
- stable diagnostics and observability without storing raw query text in metrics

## Goals

- Reuse the existing retrieval module for role memory instead of introducing a separate search subsystem.
- Support memory retrieval without vectors or external services.
- Preserve exact-match strength for names, file paths, issue IDs, commands, error text, and project-specific vocabulary.
- Keep memory ranking explainable through fields, weights, lexical matching, and recency rules.
- Keep reflection memory and raw episodic memory as separate layers with different retrieval roles.
- Make retrieval configurable enough to support both English and mixed English/Chinese memory corpora.
- Keep the design compatible with future hybrid retrieval, but do not require vectors now.

## Non-Goals

- Do not replace `role_memories.content_markdown` as the durable reflection summary.
- Do not require semantic embeddings, ANN indexes, or external vector databases.
- Do not promise transcript-scale full replay into prompts.
- Do not store raw search queries in metrics or traces.
- Do not make memory retrieval a hard dependency for session startup or prompt assembly.

## Why BM25 Fits Agent Memory

Agent memory often contains exact lexical anchors that dense retrieval can blur:

- repository names
- branch names
- file paths
- stack traces
- API route names
- bug IDs
- task IDs
- user-specific entities
- shell commands
- dates and version strings

For these cases, BM25 is a strong first-stage ranker because it rewards sparse high-signal term overlap and is easy to reason about. Agent Teams already uses SQLite FTS5 and BM25 for skill routing, so extending the same stack to memory reduces implementation risk and operational cost.

## Historical Retrieval Lineage Relevant Here

The memory retrieval design should borrow from the classical lexical IR stack rather than from vector-only RAG assumptions.

Relevant ideas:

- Boolean and inverted-index retrieval for strict term filtering
- TF-IDF and SMART weighting for sparse salience
- BM25 and the probabilistic relevance framework for general-purpose ranking
- query likelihood language models for future alternative ranking backends
- Rocchio or pseudo-relevance feedback for query expansion
- passage retrieval and proximity-aware ranking for local evidence windows
- MMR for diversity and de-duplication across repeated memory shards
- MDL and rate-distortion ideas for deciding which memory details should survive compaction

This document only standardizes BM25-first retrieval. The other algorithms remain optional follow-up layers.

## Mathematical Retrieval Algorithms Relevant to This Design

### BM25

Primary first-stage ranker for memory retrieval.

For query term `q_i`, document `D`, and average document length `avgdl`:

```text
BM25(D, Q) = Σ IDF(q_i) * (f(q_i, D) * (k1 + 1)) /
             (f(q_i, D) + k1 * (1 - b + b * |D| / avgdl))
```

Practical role here:

- rank memory episodes by lexical relevance
- reward exact anchor overlap
- remain interpretable through term frequency, document length, and field weights

Paper:

- Stephen Robertson, Hugo Zaragoza, "The Probabilistic Relevance Framework: BM25 and Beyond" — https://www.staff.city.ac.uk/~sbrp622/papers/foundations_bm25_review.pdf

### TF-IDF / SMART weighting

Useful as the conceptual predecessor to BM25 and still valuable for debugging lexical salience.

```text
tf-idf(t, D) = tf(t, D) * log(N / df(t))
```

Practical role here:

- explain why rare identifiers and error codes should dominate common words
- support future fallback rankers or diagnostics

Paper:

- Gerard Salton, Christopher Buckley, "Term-weighting approaches in automatic text retrieval" — https://www.sciencedirect.com/science/article/abs/pii/0306457388900210/

### Rocchio relevance feedback

Useful for future query expansion from accepted memory hits.

```text
q_m = α q_0 + β / |D_r| * Σ d in D_r d - γ / |D_nr| * Σ d in D_nr d
```

Practical role here:

- expand sparse user queries with terms from confirmed relevant memories
- keep future memory retrieval adaptive without vectors

Paper:

- J. J. Rocchio, "Relevance Feedback in Information Retrieval" — https://sigir.org/files/museum/pub-08/XXIII-1.pdf

### Query likelihood language modeling

A future alternative lexical ranker.

```text
score(D, Q) = P(Q | D) = Π P(q_i | D)
```

Practical role here:

- support an alternative to BM25 when memory corpora become highly uneven in style and length

Paper:

- Jay M. Ponte, W. Bruce Croft, "A Language Modeling Approach to Information Retrieval" — https://ciir.cs.umass.edu/pubfiles/ir-120.pdf

### MMR

Useful after BM25 retrieval for novelty and de-duplication.

```text
MMR = argmax_{D_i in R \ S} [ λ * Sim_1(D_i, Q) - (1 - λ) * max_{D_j in S} Sim_2(D_i, D_j) ]
```

Practical role here:

- reduce repeated memory shards in prompt injection
- keep one summary and one detail instead of many near-duplicates

Paper:

- Jaime Carbonell, Jade Goldstein, "The Use of MMR, Diversity-Based Reranking for Reordering Documents and Producing Summaries" — https://www.cs.cmu.edu/~jgc/publication/The_Use_MMR_Diversity_Based_LTMIR_1998.pdf

### Rate-distortion theory

The right mathematical lens for compaction and memory-budget trade-offs.

```text
R(D) = min I(X; X_hat)
```

subject to expected distortion `E[d(X, X_hat)] <= D`.

Practical role here:

- define memory compaction as keeping the minimum bits needed under a task-loss budget
- justify why reflection summaries and episodic memories should be separate layers

Papers:

- Claude E. Shannon, "A Mathematical Theory of Communication" — https://people.math.harvard.edu/~ctm/home/text/others/shannon/entropy/entropy.pdf
- Claude E. Shannon, "Coding Theorems for a Discrete Source With a Fidelity Criterion" — https://gwern.net/doc/cs/algorithm/information/1959-shannon.pdf

### Information bottleneck

A useful abstraction for deciding what information should survive memory compression.

```text
min I(X; T) - β I(T; Y)
```

Practical role here:

- compress history into a smaller memory state `T`
- retain information that predicts future task utility `Y`
- drop irrelevant transcript detail even when it is lexically rich

Papers:

- Naftali Tishby, Fernando C. Pereira, William Bialek, "The Information Bottleneck Method" — https://www.princeton.edu/~wbialek/our_papers/tishby+al_99.pdf
- Naftali Tishby, Noga Zaslavsky, "Deep Learning and the Information Bottleneck Principle" — https://arxiv.org/abs/1503.02406

## Existing Foundation in This Repository

The repository already contains a general retrieval layer:

- scope kinds include `memory` in `src/agent_teams/retrieval/retrieval_models.py`
- local storage is backed by SQLite FTS5 in `src/agent_teams/retrieval/sqlite_store.py`
- query-time field weighting is already supported through `title_weight`, `body_weight`, and `keyword_weight`
- the schema is already documented in `docs/database-schema.md`
- skill routing already proves the architectural pattern in `docs/skill-routing-design.md`

This means memory retrieval can be added as a new scope consumer rather than as a new platform capability.

## Memory Layers

The runtime should treat durable memory as three separate retrieval layers.

### 1. Reflection memory

This is the current bounded summary stored in `role_memories.content_markdown`.

Use:

- default low-cost cross-session strategy memory
- high precision policy and preference recall
- always small enough for direct prompt injection

Not for:

- fine-grained fact lookup across long histories
- exact retrieval of older one-off details

### 2. Episodic memory

This is a new conceptual layer of discrete memory units derived from conversation history, compaction checkpoints, manual notes, or future task outcomes.

Use:

- retrieval corpus for BM25 ranking
- exact recall of prior facts, actions, and outcomes
- support for richer prompt rehydration

### 3. Working memory

This is the per-run or per-session active context already held in the prompt and recent message window.

Use:

- immediate continuity
- recent task state

Not for:

- durable memory persistence

## Scope Model

Memory retrieval should use retrieval scope kind `memory` and a role-workspace scoped `scope_id`.

Recommended scope key:

- `scope_kind = memory`
- `scope_id = collision-safe composite derived from workspace_id and role_id`

Recommended encoding rules:

- do not join raw identifiers with `:` or another unescaped delimiter
- use a structured serialization such as JSON, length-prefixed segments, or escaped components
- keep the same encoding rule everywhere the memory scope is produced or queried

Example safe forms:

- JSON tuple style: `[{workspace_id}, {role_id}]` after deterministic serialization
- length-prefixed style: `{len(workspace_id)}:{workspace_id}{len(role_id)}:{role_id}`

This follows the existing durable memory boundary described in `docs/role-workspace-memory-design.md`: role memory is shared by the same `role_id + workspace_id` pair across sessions.

This scope shape keeps retrieval aligned with current memory ownership and avoids accidental leakage across roles or workspaces, even when identifiers themselves contain `:` or other separator characters.

## Retrieval Document Model

Each episodic memory unit should be projected into one `RetrievalDocument`.

Recommended projection:

- `document_id`: stable memory record ID
- `title`: compact label for the episode
- `body`: main natural-language memory text
- `keywords`: normalized lexical anchors

Recommended source fields for memory projection:

- summary sentence
- original key facts
- user/entity names
- file paths
- commands
- error names
- task IDs
- ticket IDs
- tags such as `decision`, `preference`, `bug`, `constraint`, `plan`, `result`

Example conceptual projection:

- `title`: `Fix for prompt preview schema mismatch`
- `body`: natural-language memory with what happened, why it mattered, and what the final outcome was
- `keywords`: `prompts preview schema mismatch api router fix pydantic response`

## Document Granularity

The retrieval unit should be smaller than a whole conversation and larger than a single utterance.

Preferred unit:

- one completed episode
- one decision record
- one compaction segment
- one manually curated memory note

Avoid:

- indexing every message line as a separate document
- indexing only one global memory blob

Rationale:

- message-level indexing is noisy and redundant
- one-blob indexing destroys passage selectivity and ranking quality

## Tokenizer Strategy

Default tokenizer choice should depend on corpus shape.

### `unicode61`

Use by default for:

- English-heavy code and docs work
- command-line text
- structured identifiers separated by punctuation

### `trigram`

Use for:

- mixed Chinese and English memory corpora
- identifiers or phrases that do not tokenize well with standard word boundaries
- fuzzy substring-heavy recall needs

Because tokenizer choice is already part of `RetrievalScopeConfig`, memory retrieval can reuse the same backend behavior as existing retrieval scopes.

## Ranking Model

The first-stage ranking model should be BM25 over three weighted fields:

- `title`
- `body`
- `keywords`

Initial recommended weights for memory scope:

- `title_weight = 6.0`
- `body_weight = 1.0`
- `keyword_weight = 4.0`

Rationale:

- memory titles usually contain the most compact semantic label
- keyword anchors often carry entities, commands, and identifiers
- body should remain broad recall text, but not dominate exact anchors

These values should remain configurable at scope creation time.

## Query Construction

Memory retrieval queries should be constructed from stable runtime context rather than from the full prompt.

Recommended query inputs:

- current user objective
- current task title or delegated task objective
- recent user message text
- role name and role description
- visible shared-state snapshot
- optional manually supplied retrieval hints

Do not use:

- full system prompt text
- hidden chain-of-thought
- raw workspace file contents for the initial query
- entire conversation transcript concatenated blindly

The query should remain short and high-signal.

## Retrieval Pipeline

Recommended pipeline:

1. Build query text from current objective and recent context.
2. Search the `memory` scope with BM25.
3. Filter invalid, stale, or access-ineligible memory records.
4. Apply recency and importance adjustments outside SQLite ranking.
5. Deduplicate near-identical memories.
6. Apply MMR-style novelty selection when multiple hits say the same thing.
7. Inject the top bounded set into the prompt.

This keeps BM25 as the primary lexical ranker while letting runtime policy shape the final selected memory set.

## Recency and Salience Reranking

Pure BM25 is not enough for memory because old exact matches can dominate new but still relevant records.

Add a second-stage rerank score outside the FTS query:

`final_score = lexical_score * importance_boost * recency_boost * source_quality_boost`

Suggested signals:

- `importance_boost`: manual pinning, decision memory, user preference memory
- `recency_boost`: newer memory gets moderate lift, not absolute override
- `source_quality_boost`: manually curated note or compaction summary may outrank noisy auto-captured text

This should remain deterministic and explainable.

## Diversity Control

Memory corpora often contain repeated summaries of the same event.

After BM25 retrieval, apply diversity control such as MMR or a simpler duplicate suppression rule:

- penalize same-tag and same-time-window duplicates
- penalize near-identical titles
- keep one high-level summary plus at most one detailed shard for the same episode

This prevents prompt waste from repeated memory copies.

## Prompt Injection Shape

Memory retrieval output should be injected as a bounded appendix, not merged into the system prompt.

Recommended structure:

```md
## Retrieved Role Memory

### Memory 1
Source: decision
When: 2026-03-30
Summary: ...

### Memory 2
Source: preference
When: 2026-03-25
Summary: ...
```

Rules:

- keep the system prompt stable
- inject retrieved memory into per-turn runtime/user prompt assembly only
- cap by token budget and memory count
- prefer short memory summaries plus stable IDs over raw transcript dumps

## Write Path

Memory retrieval quality depends on write-time normalization.

When creating episodic memory documents, the write path should:

- normalize keywords
- extract entities and stable identifiers
- store concise titles
- avoid giant body blobs
- deduplicate semantically identical records when possible
- preserve timestamps and source type for reranking

Potential writers:

- compaction step
- manual reflection refresh flow
- future task completion hooks
- future user-memory pinning flows

## Recommended Minimal Implementation Order

### Phase 1

- add a memory-scope document writer fed from compaction or manual memory updates
- use the existing retrieval module and schema unchanged
- add retrieval APIs used only by prompt assembly
- inject top `k` memory snippets into the user/runtime prompt path

### Phase 2

- add recency and importance reranking
- add duplicate suppression or MMR-style selection
- add tokenizer choice for Chinese-heavy corpora

### Phase 3

- add query expansion through tags or pseudo-relevance feedback
- add optional hybrid retrieval if vectors are introduced later

## API and Service Boundaries

Memory retrieval should remain inside backend service boundaries.

Suggested ownership:

- retrieval backend stays in `agent_teams.retrieval`
- memory-specific indexing and ranking policy lives with role memory services, not in interface routers
- interfaces expose diagnostics and previews, but should not implement ranking logic directly

This mirrors the repository rule that interface layers do not own backend domain behavior.

## Observability

Reuse the existing retrieval metrics and trace model.

Track:

- memory retrieval search volume
- latency
- failure rate
- indexed document count per memory scope
- prompt injection count and token usage

Do not store:

- raw query text in metrics
- sensitive memory bodies in logs

## Failure Behavior

Memory retrieval must fail soft.

If retrieval fails:

- continue with reflection summary only
- do not fail prompt assembly
- log one warning with role/workspace context
- surface diagnostics where available

If the memory scope is empty:

- inject nothing beyond existing reflection summary
- do not treat this as an error

## Security and Privacy

Because role memory can contain sensitive user history, the retrieval layer must respect the same scope boundaries as durable memory.

Requirements:

- never search outside the active `role_id + workspace_id` scope
- never log full memory bodies in warning paths
- avoid indexing secrets when the write path can recognize them
- support future deletion and reindexing when memory records are removed

## Open Questions

- What exact episodic memory table or persistence model should back `document_id` ownership?
- Should compaction produce one summary document, multiple episode documents, or both?
- Should `role_memories.content_markdown` also be indexed as one high-priority synthetic document?
- Should memory retrieval diagnostics be exposed in prompt preview and session debug endpoints?
- Should tokenizer default for `memory` be `trigram` when the active UI language is Chinese?

## Recommendation

Agent Teams should add memory retrieval by extending the current retrieval platform, not by introducing vectors first.

The most practical default is:

- lexical retrieval only
- `scope_kind = memory`
- `scope_id = collision-safe composite of workspace_id and role_id`
- SQLite FTS5 BM25 ranking
- weighted `title/body/keywords`
- second-stage recency and importance reranking
- diversity filtering before prompt injection

This matches the repository's current architecture, keeps the system explainable, and solves the exact-match memory recall problem that agent workflows hit most often.

## Related Files

- `docs/role-workspace-memory-design.md`
- `docs/skill-routing-design.md`
- `docs/database-schema.md`
- `src/agent_teams/retrieval/retrieval_models.py`
- `src/agent_teams/retrieval/sqlite_store.py`
- `src/agent_teams/skills/skill_routing_service.py`
