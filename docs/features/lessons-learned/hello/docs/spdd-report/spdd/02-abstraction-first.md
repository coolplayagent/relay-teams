---
title: Abstraction First — Design Before You Generate
authors: Wei Zhang, Jessie Jie Xia
source: https://martinfowler.com/articles/structured-prompt-driven/abstraction-first.html
published: 2026-04-28
parent: Structured-Prompt-Driven Development (SPDD)
topics: [SPDD, abstraction, design, code-generation]
---

# Abstraction First

> design before you generate

Before generating any code, you need to be clear about what objects exist, how they collaborate, and where the boundaries are. Without that, AI often sprints on implementation details while the structure falls apart. Unclear responsibilities, duplicated logic, inconsistent interfaces, and the cost shows up later in review and rework.

## What to focus on during review

### Check that the prompt matches business intent

- **Requirement fidelity**: Does the Requirements section accurately capture the core intent of the user story from the PO?
- **Acceptance coverage**: Does the prompt fully cover the acceptance criteria defined by the business, without omissions or misinterpretations?
- **Term alignment**: Are domain terms used in the prompt consistent with the business language and the team's established understanding?

### Validate the abstraction model

- **Entities accuracy**: Do the defined entities, value objects, and their relationships reflect the real domain? (A visual review helps. Mermaid class diagrams work well.)
- **Approach soundness**: Is the high-level design strategy reasonable? Does it address the core business problem, with a coherent flow?
- **Structural fit**: Are components, dependencies, and inheritance/implementation relationships clear, and correctly grounded in the existing technical context?

### Review engineering boundaries and constraints

- **Standards injection**: Do Norms correctly encode the team's cross-cutting engineering standards (naming conventions, logging, error-handling strategy, etc.)?
- **Hard constraints**: Are non-negotiable boundaries explicitly stated (performance limits, security requirements, and similar guardrails)?

### Ensure tasks are executable

- **Atomic decomposition**: Are tasks broken down into independent, testable, acceptance-ready technical units?
- **End-to-end completeness**: Do the tasks form a complete chain that delivers the requirement, without logical gaps?
- **Clarity**: Are task descriptions specific enough to reduce uncertainty and prevent the LLM from inventing details?

## Capabilities you need

- **Structured modelling**: distill complex requirements into a clear domain model and solution (entities, interactions, boundaries) and bring the team to agreement.
- **Engineering trade-off decisions**: make architectural choices between the existing codebase and new requirements, while preserving coherence and consistency.
- **Atomic task design**: break an abstract solution into a set of independent, testable, acceptance-ready technical tasks.
- **Visual communication**: use lightweight diagrams (ER diagrams, sequence diagrams, flow charts) to turn narrative requirements into an explicit logic model and remove ambiguity.

## Operating principles

- **Design before generation**: if the design and boundaries aren't clear, don't generate code.
- **Contract first**: define interface responsibilities before filling in implementation details.
- **Control granularity**: split work into sensible units, build one piece, finish one piece. So you avoid "generate a big blob, then throw it all away."
- **Diagram early**: use simple diagrams to align quickly and reduce endless debates over wording.

---

*This page is part of [Structured-Prompt-Driven Development (SPDD)](https://martinfowler.com/articles/structured-prompt-driven/) by Wei Zhang and Jessie Jie Xia*
