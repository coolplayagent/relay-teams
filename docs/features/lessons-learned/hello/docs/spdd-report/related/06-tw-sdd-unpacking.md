---
title: "Spec-driven development: Unpacking one of 2025's key new AI-assisted engineering practices"
authors: Liu Shangqi
source: https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices
published: 2025-12-04
topics: [SDD, spec-driven-development, AI-coding, engineering-practices]
---

# Spec-driven development

Unpacking one of 2025's key new AI-assisted engineering practices

Spec-driven development may not have the visibility of a term like vibe coding, but it's nevertheless one of the most important practices to emerge in 2025.

## Defining spec-driven development and competing interpretations of it

My understanding of spec-driven development (SDD) is that it's a development paradigm that uses well-crafted software requirement specifications as prompts, aided by AI coding agents, to generate executable code.

There are different opinions within the industry about what a spec is and its role in SDD. At the more radical end of the spectrum, there's an argument that we can now discard code and treat specs as the sole source of truth that needs maintenance. In contrast, more old-school technologists believe specs are merely elements that drive code generation, as it does in test-driven development. Executable code remains the source of truth you need to maintain.

## The context of spec-driven development's emergence

Manipulating computers with natural language that represents business has always been the holy grail of software development and programming language theory. In fact, attempts at spec-driven code generation pre-date the LLM era — they've just never reached the level of actual development.

Specs have been used in a number of different ways in software engineering. In distributed computing and RPC communication, specs act as communication contracts. In behavior-driven development (BDD), specs are used as a vehicle to facilitate collaboration with business users. However they're used, they're ultimately text-based instructions — and given LLMs ability to manipulate text, it's unsurprising that specs may play so nicely with the growth of AI in software engineering.

## What is a spec?

A specification is definitely more than just a product requirements document (PRD). Even simply applying a more structured prompt and more explicit technical constraints can produce better code than a plain PRD.

Technically, a specification should explicitly define the external behavior of the target software — things like input/output mappings, preconditions/postconditions, invariants, constraints, interface types, integration contracts and sequential logic/state machines.

### What makes a good spec?

- Use domain-oriented ubiquitous language to describe business intent rather than specific tech-bound implementations
- Have a clear structure, with a common style to define scenarios using Given/When/Then
- Strive for completeness yet conciseness, covering the critical path without enumerating all cases
- Aim for clarity and determinism to help reduce model hallucinations
- Don't underestimate the role of structured inputs and outputs

## Spec-driven development in practice

SDD workflows in practice can vary significantly depending on the tools you use. The core of SDD goes beyond vibe coding, separating the design and implementation phases. In the planning phase, requirements are first analyzed using an AI coding agent, which generates design and implementation plans. Typically, these requirements specifications are formalized into different Markdown (.md) files. Reviewing and validating these specifications is usually an iterative process that requires a human in the loop.

## Spec-driven development and context engineering

I often say that prompt engineering optimizes human-LLM interaction, while context engineering optimizes agent-LLM interaction. The spec-by-example we typically use in BDD is essentially the few-shot prompt technique. Separating requirements analysis and planning from the code implementation phase essentially compresses the context into specs.

## Is spec-driven development just a return to waterfall?

I've heard some people claim this is a return to waterfall — not unreasonably — but I believe this time is different. The problem with traditional waterfall development is its excessively long feedback cycles. The problems we currently encounter with AI coding are different — they stem from the fact that vibe coding is too fast, spontaneous and haphazard. It's important to bring serious requirements analysis, prudent software design, necessary architectural constraints, and human-in-the-loop governance into the picture. I'd argue that's what spec-driven development helps us do.

> "Spec drift and hallucination are inherently difficult to avoid. We still need highly deterministic CI/CD practices to ensure software quality and safeguard our architectures." — Liu Shangqi

## The challenges and risks of spec-driven development

There's a lack of consensus on the 'correct' spec-driven development workflow. Code generation from spec to LLMs isn't deterministic. Spec drift and hallucination are inherently difficult to avoid, so we still need highly deterministic CI/CD practices. The question of whether spec or code is the ultimate artifact of software development still needs to be explored.

**Spec-driven development remains an emerging practice as 2025 draws to a close; we're likely to see even more change in 2026.**
