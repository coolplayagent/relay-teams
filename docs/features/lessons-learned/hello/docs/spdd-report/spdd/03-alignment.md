---
title: Alignment — Lock Intent Before You Write Code
authors: Wei Zhang, Jessie Jie Xia
source: https://martinfowler.com/articles/structured-prompt-driven/alignment.html
published: 2026-04-28
parent: Structured-Prompt-Driven Development (SPDD)
topics: [SPDD, alignment, requirements, business-intent]
---

# Alignment

> lock intent before you write code

Before implementation, you need to make "what we will do / what we won't do" explicit, and agree on the standards and hard constraints up front. Otherwise you end up with fast output and slow rework.

## What to focus on during review

### Anchor on business value and outcomes

- **Confirm the real problem**: What user pain points are we actually solving, rather than "what feature are we building"?
- **Value hypothesis**: After this goes live, what business benefit do we expect (and how would we know)?
- **Non-goals / out of scope**: Make explicit what we're not doing, to prevent scope creep.

### Align on domain language

- **Term precision**: What do key terms (e.g., "customer," "order," "asset") mean in this context?
- **Remove ambiguity**: Ensure developers and domain experts mean the same thing. Avoid "same word, different meaning" and "same meaning, different words."

### Make rules and acceptance criteria testable

- **Happy path**: Is the normal business flow clear and complete?
- **Edge cases**: Are important exceptions and limits defined (e.g., "What if inventory is zero?" "What's the maximum amount?")?
- **Definition of Done**: What exactly counts as "accepted" from the business side?

### Confirm dependencies and hidden constraints

- **Upstream dependencies**: Does this change rely on other unfinished modules or decisions?
- **Legacy constraints**: Are there existing business rules or special data-handling logic we must preserve?

## Capabilities you need

- **Business analysis**: translate user pain points into testable functional requirements.
- **Domain modelling**: define a shared vocabulary across business and technical roles.
- **Scope control**: draw clear boundaries between "in scope" and "out of scope" to avoid scope creep.

## Operating principles

- **Stage-gated validation**: follow the sequence analysis doc → structured prompt → code. If the earlier artifact isn't aligned, don't advance.

---

*This page is part of [Structured-Prompt-Driven Development (SPDD)](https://martinfowler.com/articles/structured-prompt-driven/) by Wei Zhang and Jessie Jie Xia*
