---
title: "OpenSPDD — SPDD AI Coding Assistant Command Template Manager"
authors: Wei Zhang (gszhangwei)
source: https://github.com/gszhangwei/open-spdd
license: MIT
stars: 88
language: Go
topics: [SPDD, OpenSPDD, CLI, REASONS-canvas, tooling]
---

# OpenSPDD

> **Structured Prompt-Driven Development** — Transform AI coding prompts into executable design contracts

OpenSPDD is a methodology and cross-platform CLI tool for the AI coding era. It upgrades AI coding prompts from "disposable inputs" to "executable design contracts" with bidirectional synchronization between design and implementation.

## Why OpenSPDD?

| Problem | Typical Plan Documents | REASONS Canvas |
| --- | --- | --- |
| **Nature** | Task list | Design contract |
| **Constraints** | None — AI improvises freely | Explicit — Norms define "how", Safeguards define "what not to do" |
| **Detail Level** | High-level: _"Create BillingService"_ | Precise: _method signatures, parameters, error handling, DI patterns_ |
| **Traceability** | None — docs don't update with code | Yes — `/spdd-sync` enables reverse sync |
| **Validation** | Vague — _"done when complete"_ | Explicit — exact error messages, HTTP status codes in Safeguards |
| **Dependencies** | Implicit — AI infers | Explicit — Operations define strict execution order |

## The REASONS Canvas Framework

```
┌─────────────────────────────────────────────────────────────────────┐
│                        REASONS Canvas                                │
├─────────────────────────────────────────────────────────────────────┤
│  R - Requirements    The "why" — business goals and scope            │
│  E - Entities        Domain model (Mermaid class diagrams)           │
│  A - Approach        Solution strategy and trade-offs                │
│  S - Structure       Architecture, inheritance, dependencies         │
│  O - Operations      Precise implementation tasks in order           │
│  N - Norms           Coding standards and patterns                   │
│  S - Safeguards      Constraints and guardrails                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Core Workflow

```
Business Requirement → /spdd-analysis → /spdd-reasons-canvas → /spdd-generate → Code Review → /spdd-sync → Next iteration
```

**Key principle**: _"When reality diverges, fix the prompt first — then update the code."_

## Features

- **Cross-platform**: Supports Cursor, Claude Code, GitHub Copilot, and Antigravity
- **Auto-detection**: Automatically detects your AI coding environment
- **Single Binary**: All templates embedded via Go's embed directive
- **Bidirectional Sync**: Keep design documents and code in sync
- **Interactive UI**: Modern terminal UI for command selection

## Installation

```shell
# Homebrew (macOS/Linux)
brew install gszhangwei/tools/openspdd

# Go Install
go install github.com/gszhangwei/open-spdd@latest

# Download Binary from GitHub Releases
```

## Quick Start

```shell
cd your-project
openspdd init
openspdd generate --all

# Step 1: Strategic analysis
/spdd-analysis @requirements/user-registration.md

# Step 2: Generate REASONS Canvas from analysis
/spdd-reasons-canvas @spdd/analysis/xxx.md

# Step 3: Generate code from REASONS Canvas
/spdd-generate @spdd/prompt/xxx.md

# Step 4: After code review/refactoring, sync changes back
/spdd-sync @spdd/prompt/xxx.md
```

## Available Commands

### Core Commands

| Command | Description |
| --- | --- |
| `spdd-analysis` | Strategic analysis of requirements |
| `spdd-reasons-canvas` | Generate REASONS-Canvas structured prompts |
| `spdd-generate` | Generate code from structured SPDD prompt files |
| `spdd-prompt-update` | Update existing SPDD prompt with new requirements |
| `spdd-sync` | Sync code changes back to SPDD prompt files |

### Optional Commands (Beta)

| Command | Description |
| --- | --- |
| `spdd-story` | Decompose feature requirements into INVEST-compliant stories |
| `spdd-code-review` | Review code against REASONS-Canvas, detecting intent drift |
| `spdd-api-test` | Generate self-contained shell scripts with cURL commands |

## Supported Environments

| Tool | Detection | Config Directory |
| --- | --- | --- |
| Cursor | `.cursor/`, `.cursorrules` | `.cursor/commands/` |
| Claude Code | `.claude/`, `CLAUDE.md` | `.claude/commands/` |
| Antigravity | `.antigravity/` | `.antigravity/commands/` |
| GitHub Copilot | `.github/copilot-instructions.md` | `.github/copilot-prompts/` |

## Example Project

- **token-billing**: SPDD 示例项目 — LLM Token 计费引擎
  - [GitHub](https://github.com/gszhangwei/token-billing)
  - 包含完整迭代：iteration-1-end → enhancement → after-enhancement

## When to Use OpenSPDD

| Scenario | Recommendation |
| --- | --- |
| Enterprise feature development | Highly recommended |
| Team collaboration | Highly recommended |
| Complex refactoring | Recommended |
| Cross-tool workflows | Recommended |
| Quick prototypes | Consider (may be overhead) |
| One-off scripts | Not recommended |
