---
title: "Spec-Driven Development — Thoughtworks Technology Radar"
authors: Thoughtworks
source: https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development
published: 2025-11
edition: Technology Radar Vol.33
rating: Assess
topics: [SDD, technology-radar, spec-driven-development]
---

# Spec-Driven Development

**Published:** Nov 2025 | **Rating:** Assess

Spec-driven development is an emerging approach to AI-assisted coding workflows. While the term's definition is still evolving, it generally refers to workflows that begin with a structured functional specification, then proceed through multiple steps to break it down into smaller pieces, solutions and tasks. The specification can take many forms: a single document, a set of documents or structured artifacts that capture different functional aspects."

We've seen many developers adopt this style (and have one of our own that we're sharing internally at Thoughtworks). Three tools in particular have recently explored distinct interpretations of spec-driven development:

- **Amazon's Kiro** guides users through three workflow stages — requirements, design and tasks creation.
- **GitHub's spec-kit** follows a similar three-step process but adds richer orchestration, configurable prompts and a "constitution" defining immutable principles that must always be followed.
- **Tessl Framework** (still in private beta as of September 2025) takes a more radical approach in which the specification itself becomes the maintained artifact, rather than the code.

We find this space fascinating, though the workflows remain elaborate and opinionated. These tools behave very differently depending on task size and type; some generate lengthy spec files that are hard to review, and when they produce PRDs or user stories, it's sometimes unclear who their intended user is. We may be relearning a bitter lesson — that handcrafting detailed rules for AI ultimately doesn't scale.

---

*Source: [Thoughtworks Technology Radar](https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development)*
