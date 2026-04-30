# Runtime Injection Semantics

This document defines the runtime contract for injecting user-visible messages
into an active run. It is the source of truth for backend timing, SSE events,
and frontend timeline placement.

## Terms

- `inject`: queue a message that becomes a model-visible user message in the
  target run conversation.
- `force inject`: promote queued public user inject messages into one interrupt
  injection.
- `injection_enqueued`: the backend accepted an injection into the runtime
  queue. This event is not proof that the model has seen the message.
- `injection_applied`: the backend appended the injection to the target
  conversation and rebuilt the model iteration context. The next model request
  for that target must include the injected message.
- safe boundary: a conversation point where the persisted model history does
  not contain an unmatched assistant `tool_call`.
- tool batch: one assistant response containing one or more tool calls and the
  matching tool-result request that closes those calls.

## Queued Inject Timing

Queued inject is an insertion into the next model request at the earliest safe
boundary. It is not delayed until the whole run becomes idle.

The runtime must apply queued inject messages at these boundaries:

- before starting a model request, if the target queue is non-empty;
- immediately after a complete tool batch has been persisted, before any
  subsequent model request;
- before accepting a final model answer, if queued inject messages arrived while
  the answer was being produced.

The runtime must not insert an injected user message between an assistant
`tool_call` and its matching `tool_result`. If the model has already produced a
tool-call batch, queued inject messages wait until all results for that batch
are committed. The inject message is then appended after the tool result and
before the next model request.

Multiple queued public user inject messages drained at the same boundary are
merged into one user message in queue order, separated by blank lines. Internal
system injections can share the same queue primitive, but public user injection
rendering and merging rules apply only to public user messages.

## Force Inject Timing

Force inject is the interrupt path for already queued public user messages. It
does not create a parallel conversation path.

When force inject is requested, the backend promotes the current queued public
user inject messages for the coordinator into one interrupt injection. The model
step is interrupted at the next runtime interrupt check. The runtime then
rebuilds from the last persisted safe boundary and appends the promoted message
before the next model request.

Force inject may discard streamed UI fragments that have not reached a durable
safe boundary, but it must not persist an orphan tool result or split a
tool-call/tool-result pair.

## Required Timeline Example

For the user request `使用shell打印pwd`, assume the model calls the shell tool
with `pwd`. If the user types `上一级的` while that first tool call is in
progress, the target history order must become:

1. Original user message: `使用shell打印pwd`
2. Assistant tool call: `pwd`
3. Tool result for `pwd`, for example `/c/Users/yex/Documents/workspace/agent-teams`
4. Injected user message: `上一级的`
5. Next assistant/model response generated with the injected message in context

The injected message must not wait for a second tool call or a later idle state.
The next model request after the first `pwd` result must include `上一级的`.

## SSE and Frontend Contract

The frontend renders injection state from backend events. It must not decide
whether a message entered model history.

- `injection_enqueued` may be shown in the composer queue as pending work.
- `injection_applied` must be rendered in the timeline at the position where the
  backend applied it.
- `client_message_id` is only a client correlation key for reconciling local
  optimistic queue UI with backend events. It is not a model-visible identifier
  and must not be used as a database write path.
- Historical session projections must derive injection placement from persisted
  backend events and conversation order, not from frontend reconstruction.

If an applied injection supersedes pending streamed tool-call UI, the event
sets `supersedes_pending_tool_calls`. Clients may remove those uncommitted
visual fragments, but persisted tool calls and tool results remain governed by
the backend safe-boundary rules.
