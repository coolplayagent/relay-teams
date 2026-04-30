---
title: Iterative Review — Turn Output into a Controlled Loop
authors: Wei Zhang, Jessie Jie Xia
source: https://martinfowler.com/articles/structured-prompt-driven/iterative-review.html
published: 2026-04-28
parent: Structured-Prompt-Driven Development (SPDD)
topics: [SPDD, review, iteration, quality]
---

# Iterative Review

> turn output into a controlled loop

You want AI assistance to behave like an engineering process, not a one-shot draft. Without a disciplined review-and-iterate loop, teams either keep forcing the model to patch things until the solution drifts, or they restart repeatedly and lose control of cost and time.

## What to focus on during review

### Prompt and code consistency

- **Spec stays ahead of implementation**: If there's any logic change (for example, moving business logic during a refactor), has the structured prompt been updated first?

### Architecture and responsibility boundaries

- **Layering discipline**: Does the code follow the intended layered architecture?
- **Clear contracts**: Are responsibilities cleanly split between interfaces and implementations?

### Cross-cutting engineering standards

- **Exception handling**: Are there unnecessary `try/catch` blocks? Does the code follow the global exception-handling approach (e.g., a global exception handler)?
- **Encapsulation**: Are object construction and field initialization encapsulated inside domain objects (e.g., the `Agent` domain object), rather than scattered across the service layer?
- **Obvious code smells**: watch for magic numbers, long methods, and similar maintainability issues.
- **Team-specific standards**: apply any additional conventions your team relies on.

### Hallucination and correctness checks

- **Alignment with the prompt**: Does the generated code actually implement what the prompt describes?
- **Imports and dependencies**: Are imports correct and minimal? Watch for missing, extra, or incorrect references.
- **Syntax and compilation**: Do a quick scan for obvious syntax errors or compilation failures caused by invented APIs or incorrect assumptions.

## Capabilities you need

- **Prompt debugging**: when the code fails or behavior drifts, you can correct it by updating the prompt and regenerating, rather than patching the code with manual hacks.
- **Functional validation**: quickly set up and run the system locally, and verify behavior through hands-on execution and observation against business expectations.
- **Deep code review**: once functionality is correct, review structure and implementation details to catch maintainability and risk issues.
- **Asset integrity**: ensure the code you commit maps cleanly to the exact prompt version, so future changes remain traceable and maintainable.

## Operating principles

- **Prompt as code**: treat the structured prompt as a first-class source artifact. Any requirement change or bug fix must update the prompt and the code together, so they stay in sync.
- **Run first, review second**: make "correct behavior" the first quality gate. If the system doesn't behave as expected, iterate on the prompt first. Only after functional validation passes should you invest in deeper code-level review.

---

*This page is part of [Structured-Prompt-Driven Development (SPDD)](https://martinfowler.com/articles/structured-prompt-driven/) by Wei Zhang and Jessie Jie Xia*
