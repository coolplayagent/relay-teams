# Feature Opportunity List: Agent Capability Directions Inferred From Today's AI Signals

## Goal

This document captures a practical feature backlog for `agent-teams` based on the capability directions repeatedly surfaced in recent work and discussions:

- mobile/phone control
- self-evolution / failure-driven improvement
- group intelligence / market-style or Darwinian multi-agent selection
- computer use
- automatic community operations / moderation

The intention is not to copy hype terms into the product, but to translate them into implementable, testable platform capabilities.

---

## Executive summary

The biggest opportunity for `agent-teams` is to evolve from a role-based orchestrator into a **real-world execution platform**.

That means moving beyond:
- pure text workflows
- tool-call-only automation
- single-agent success metrics

And adding stronger support for:
- embodied digital execution (computer + mobile control)
- iterative self-improvement loops
- competitive and cooperative multi-agent selection
- production-safe community automation
- observability-driven adaptation

The most important features are not the flashiest ones. The best near-term roadmap is:

1. Computer Use
2. Mobile Control
3. Self-Evolution via failure replay
4. Automatic Community Management
5. Group Intelligence / Darwinian agent selection

---

## 1. Computer Use

### Why it matters
Computer-use agents are becoming the universal fallback for software that lacks clean APIs. For an orchestration system, this is the bridge from "planning" to "actually doing".

### What `agent-teams` can borrow
- sandboxed desktop execution environments
- screenshot -> action loops
- replayable task traces
- benchmarkable execution tasks
- human-in-the-loop approval for risky actions

### Recommended features
#### 1.1 Built-in Computer Use Runtime
- add a first-class runtime type for GUI-based execution
- support screenshot observations, click/type/scroll actions, and state polling
- allow tasks to declare `execution_surface = api | browser | desktop | hybrid`

#### 1.2 Screen Observation Tooling
- native tool contracts for:
  - capture_screen
  - list_windows
  - focus_window
  - click_at
  - type_text
  - scroll_view
  - hotkey
- make these runtime-governed, permission-scoped tools rather than ad hoc shell hacks

#### 1.3 Trace + Replay
- store every action trajectory as a structured artifact
- allow later replay and diffing of GUI workflows
- support failure review in web UI

#### 1.4 Safety Layer
- approval policies for:
  - destructive actions
  - external websites
  - credential fields
  - file deletion / uploads
- require redaction and masking in screenshots when needed

### Priority
**Very high**

---

## 2. Mobile / Phone Control

### Why it matters
A large share of modern workflows live on phones: messaging, approvals, creator tools, social/community management, field ops, commerce and enterprise mobile apps.

### What `agent-teams` can borrow
- phone agent concepts like AutoGLM-Phone style task execution
- mobile UI grounding + action layers
- app-aware workflows with login state preservation

### Recommended features
#### 2.1 Mobile Session Runtime
- define a mobile runtime type parallel to desktop/browser runtimes
- support Android first via emulator/device bridge
- later evaluate iOS via hosted automation or external connectors

#### 2.2 Mobile Action Toolset
- capture_screen_mobile
- tap
- swipe
- type_text_mobile
- back
- open_app
- long_press
- wait_for_element

#### 2.3 Session-Aware App Flows
- persist app login/session state per workspace or per role
- support reusable mobile workflows: publish post, reply DM, approve request, update spreadsheet

#### 2.4 Mobile + Human Approval
- mobile actions should default to stronger confirmation policies because they often affect production accounts directly

### Priority
**High**

---

## 3. Self-Evolution / Failure-Driven Improvement

### Why it matters
Today, many agent systems fail repeatedly in the same way. Real value comes when the system improves from these failures instead of only logging them.

### What `agent-teams` can borrow
- failed-experience learning
- run trace analysis
- prompt / skill / route optimization from historical outcomes

### Recommended features
#### 3.1 Failure Memory Store
- persist structured failure cases with:
  - task context
  - chosen plan
  - tools used
  - observed failure mode
  - human correction if available

#### 3.2 Replay + Reflection Jobs
- scheduled background runs that cluster failures
- generate candidate improvements for:
  - prompts
  - skill routing rules
  - tool policies
  - retry strategy

#### 3.3 Controlled Self-Improvement Pipeline
- no uncontrolled model self-modification
- instead, propose changes as artifacts:
  - prompt patch
  - config patch
  - routing recommendation
  - docs change
- optionally create PRs automatically for approved categories

#### 3.4 Benchmark Regression Harness
- tie frequent failures to regression tests
- every repeated failure class should be promotable into a test case

### Priority
**High**

---

## 4. Automatic Community Management

### Why it matters
This is highly aligned with current gateway capabilities. `agent-teams` already touches Feishu and WeChat. Community ops is a realistic near-term execution domain with clear ROI.

### Recommended features
#### 4.1 Community Operations Role Pack
Built-in roles for:
- moderator
- community editor
- digest bot
- escalation triager
- support concierge

#### 4.2 Moderation Policy Engine
- detect spam, abuse, phishing, repetitive posts, off-topic threads
- apply actions by policy level:
  - suggest
  - draft response
  - warn
  - queue for review
  - auto-hide (only where approved)

#### 4.3 Community Workflow Automations
- welcome flow
- FAQ auto-answer with citation
- summarize daily discussion threads
- identify unanswered questions
- escalate sensitive topics to humans

#### 4.4 Community Health Dashboard
- response latency
- unresolved questions
- moderation actions
- repeat offenders
- trending issues / sentiment clusters

### Priority
**High**

---

## 5. Group Intelligence / Social Darwinism Style Multi-Agent Selection

### Why it matters
The useful product interpretation is not ideology. It is **competitive and evolutionary agent selection**: multiple agents propose solutions, then the system selects, combines, or mutates the best strategies.

### What to avoid
- do not frame this as ideology or unsafe autonomous competition
- frame it as controlled search, selection, and ensemble optimization

### Recommended features
#### 5.1 Candidate Population Execution Mode
- allow one task to spawn multiple strategy agents in parallel
- examples:
  - planner A vs planner B
  - fast agent vs cautious agent
  - tool-first vs reasoning-first

#### 5.2 Selection Layer
- compare outputs by:
  - evaluator model
  - rule-based score
  - human score
  - benchmark result

#### 5.3 Mutation / Crossover for Strategies
- derive new candidate prompts or plans from successful runs
- keep explicit provenance for every derived strategy

#### 5.4 Tournament Evaluation Mode
- useful for coding, planning, research summarization, moderation decisions, and workflow routing

### Priority
**Medium-high**

---

## 6. Suggested roadmap for `agent-teams`

### Phase 1: practical execution
- Computer Use runtime
- Mobile runtime (Android-first)
- stronger action approval + safety model
- trace capture and replay

### Phase 2: operator quality
- failure memory store
- regression extraction from failed runs
- self-improvement proposal pipeline
- community management role pack

### Phase 3: adaptive orchestration
- population execution mode
- evaluator-driven agent selection
- strategy mutation and tournament comparison
- benchmark dashboards for route and role performance

---

## 7. Concrete feature backlog

### P0
- Built-in desktop computer-use runtime
- Action trace capture + replay UI
- Permission-scoped click/type/scroll tool contracts
- Community moderation workflow pack

### P1
- Android mobile control runtime
- Failure memory repository
- Reflection-to-PR proposal pipeline
- Community health analytics dashboard

### P2
- Population execution mode
- Agent tournament evaluator
- Prompt/route mutation pipeline
- Cross-surface workflows: browser + desktop + mobile

---

## 8. Rational product conclusion

The strongest opportunities for `agent-teams` are the ones that convert orchestration into **observable execution** and convert failures into **system learning**.

If we had to choose only one sequence, it should be:

1. Computer Use
2. Mobile Control
3. Failure-driven self-evolution
4. Automatic Community Management
5. Group-intelligence selection layer

This keeps the roadmap grounded in real operator value while still building toward a more adaptive multi-agent platform.
