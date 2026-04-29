# Harness engineering: leveraging Codex in an agent-first world

- Source: OpenAI
- Original URL: https://openai.com/index/harness-engineering/
- Access date: 2026-04-19
- Published: 2026-02-11
- Type: Web article

## Summary / key value

This is one of the clearest 2026 primary-source descriptions of **AI harness engineering** as an operating model. It explains how OpenAI used Codex to build a product with no manually written code, and which harness components mattered: repository legibility, tool/runtime integration, UI + observability feedback loops, custom lints, approval/review flows, and recurring cleanup jobs.

## Why it is relevant to AI Harness Engineering

- Directly names and defines “harness engineering”.
- Describes agent harness design patterns for coding agents at production scale.
- Covers app-driving, observability, repository knowledge systems, evaluation harnesses, and guardrail enforcement.
- Strong reference for agentic coding infrastructure and engineering workflow design.

## Important excerpts

> Humans steer. Agents execute.

> Our most difficult challenges now center on designing environments, feedback loops, and control systems that help agents accomplish our goal: build and maintain complex, reliable software at scale.

> Agents produce: ... Evaluation harnesses ... Internal developer tools ... Review comments and responses ...

## Archived content

# Harness engineering: leveraging Codex in an agent-first world | OpenAI

February 11, 2026

By Ryan Lopopolo, Member of the Technical Staff

Over the past five months, our team has been running an experiment: building and shipping an internal beta of a software product with **0 lines of manually-written code**.

The product has internal daily users and external alpha testers. It ships, deploys, breaks, and gets fixed. What’s different is that every line of code—application logic, tests, CI configuration, documentation, observability, and internal tooling—has been written by Codex.

OpenAI describes the engineering work as building the environment around the agent: application legibility, repository-local knowledge, tool access, observability, architectural constraints, and evaluation loops.

The article details several harness techniques:

- per-worktree app booting for isolated validation
- Chrome DevTools integration so the agent can drive the UI
- local observability stack exposed to the agent through queryable logs/metrics/traces
- repository docs as system-of-record for agent context
- custom linters and structural tests to enforce invariants
- recurring background cleanup / garbage-collection tasks to counter agent drift

For the full archived article text, see the original URL above.
