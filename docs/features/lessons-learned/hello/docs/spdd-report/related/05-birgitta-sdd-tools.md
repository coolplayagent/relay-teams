---
title: Understanding Spec-Driven Development — Kiro, spec-kit, and Tessl
authors: Birgitta Böckeler
source: https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html
published: 2025-10-15
series: Exploring GenAI
topics: [SDD, spec-driven-development, Kiro, spec-kit, Tessl]
---

# Understanding Spec-Driven-Development: Kiro, spec-kit, and Tessl

I've been trying to understand one of the latest AI coding buzzword: Spec-driven development (SDD). I looked at three of the tools that label themselves as SDD tools and tried to untangle what it means, as of now.

## Definition

Like with many emerging terms in this fast-paced space, the definition of "spec-driven development" (SDD) is still in flux. Here's what I can gather from how I have seen it used so far: Spec-driven development means writing a "spec" before writing code with AI ("documentation first"). The spec becomes the source of truth for the human and the AI.

After looking over the usages of the term, and some of the tools that claim to be implementing SDD, it seems to me that in reality, there are multiple implementation levels to it:

1. **Spec-first**: A well thought-out spec is written first, and then used in the AI-assisted development workflow for the task at hand.
2. **Spec-anchored**: The spec is kept even after the task is complete, to continue using it for evolution and maintenance of the respective feature.
3. **Spec-as-source**: The spec is the main source file over time, and only the spec is edited by the human, the human never touches the code.

All SDD approaches and definitions I've found are spec-first, but not all strive to be spec-anchored or spec-as-source. And often it's left vague or totally open what the spec maintenance strategy over time is meant to be.

## What is a spec?

A spec is a structured, behavior-oriented artifact - or a set of related artifacts - written in natural language that expresses software functionality and serves as guidance to AI coding agents. Each variant of spec-driven development defines their approach to a spec's structure, level of detail, and how these artifacts are organized within a project.

There is a useful difference to be made I think between specs and the more general context documents for a codebase. That general context are things like rules files, or high level descriptions of the product and the codebase. Some tools call this context a **memory bank**, so that's what I will use here. These files are relevant across all AI coding sessions in the codebase, whereas specs only relevant to the tasks that actually create or change that particular functionality.

## The challenge with evaluating SDD tools

It turns out to be quite time-consuming to evaluate SDD tools and approaches in a way that gets close to real usage. For two of the three tools I tried it also seems to be even more work to introduce them into an existing codebase, therefore making it even harder to evaluate their usefulness for brownfield codebases.

## Kiro

Kiro is the simplest (or most lightweight) one of the three I tried. It seems to be mostly spec-first, all the examples I have found use it for a task, or a user story, with no mention of how to use the requirements document in a spec-anchored way over time, across multiple tasks.

**Workflow:** Requirements → Design → Tasks

Each workflow step is represented by one markdown document, and Kiro guides you through those 3 workflow steps inside of its VS Code based distribution.

Kiro also has the concept of a memory bank, they call it "steering". Its contents are flexible, and their workflow doesn't seem to rely on any specific files being there. The default topology created by Kiro when you ask it to generate steering documents is product.md, structure.md, tech.md.

## Spec-kit

Spec-kit is GitHub's version of SDD. It is distributed as a CLI that can create workspace setups for a wide range of common coding assistants. Once that structure is set up, you interact with spec-kit via slash commands in your coding assistant. Because all of its artifacts are put right into your workspace, this is the most customizable one of the three tools discussed here.

**Workflow:** Constitution → 𝄆 Specify → Plan → Tasks 𝄇

Spec-kit's memory bank concept is a prerequisite for the spec-driven approach. They call it a **constitution**. The constitution is supposed to contain the high level principles that are "immutable" and should always be applied, to every change. It's basically a very powerful rules file that is heavily used by the workflow.

At first glance, GitHub seems to be aspiring to a spec-anchored approach. However, spec-kit creates a branch for every spec that gets created, which seems to indicate that they see a spec as a living artifact for the lifetime of a change request, not the lifetime of a feature. This makes me think that spec-kit is still what I would call spec-first only, not spec-anchored over time.

## Tessl Framework

*(Still in private beta)*

Like spec-kit, the Tessl Framework is distributed as a CLI that can create all the workspace and config structure for a variety of coding assistants. The CLI command also doubles as an MCP server.

Tessl is the only one of these three tools that explicitly aspires to a spec-anchored approach, and is even exploring the spec-as-source level of SDD. A Tessl spec can serve as the main artifact that is being maintained and edited, with the code even marked with a comment at the top saying `// GENERATED FROM SPEC - DO NOT EDIT`. This is currently a 1:1 mapping between spec and code files, i.e. one spec translates into one file in the codebase.

Even at this low abstraction level I have seen the non-determinism in action though, when I generated code multiple times from the same spec. It was an interesting exercise to iterate on the spec and make it more and more specific to increase the repeatability of the code generation.

## Observations and questions

### One workflow to fit all sizes?

Kiro and spec-kit provide one opinionated workflow each, but I'm quite sure that neither of them is suitable for the majority of real life coding problems. When I asked Kiro to fix a small bug, it quickly became clear that the workflow was like using a sledgehammer to crack a nut. The requirements document turned this small bug into 4 "user stories" with a total of 16 acceptance criteria.

### Reviewing markdown over reviewing code?

Spec-kit created a LOT of markdown files for me to review. They were repetitive, both with each other, and with the code that already existed. To be honest, I'd rather review code than all these markdown files. An effective SDD tool would have to provide a very good spec review experience.

### False sense of control?

Even with all of these files and templates and prompts and workflows and checklists, I frequently saw the agent ultimately not follow all the instructions. Yes, the context windows are now larger, but just because the windows are larger, doesn't mean that AI will properly pick up on everything that's in there.

### Spec-anchored and spec-as-source: Are we learning from the past?

While many people draw analogies between SDD and TDD or BDD, I think another important parallel to look at for spec-as-source in particular is MDD (model-driven development). The models in MDD were basically the specs. Ultimately, MDD never took off for business applications, it sits at an awkward abstraction level and just creates too much overhead and constraints. But LLMs take some of the overhead and constraints of MDD away, so there is a new hope that we can now finally focus on writing specs and just generate code from them. With LLMs, we are not constrained by a predefined and parseable spec language anymore, and we don't have to build elaborate code generators. The price for that is LLMs' non-determinism of course.

## Conclusions

The general principle of spec-first is definitely valuable in many situations, and the different approaches of how to structure that spec are very sought after. But the term "spec-driven development" isn't very well defined yet, and it's already semantically diffused. I've even recently heard people use "spec" basically as a synonym for "detailed prompt".

Regarding the tools I've tried, I have listed many of my questions about their real world usefulness here. I wonder if some of them are trying to feed AI agents with our existing workflows too literally, ultimately amplifying existing challenges like review overload and hallucinations. Especially with the more elaborate approaches that create lots of files, I can't help but think of the German compound word "Verschlimmbesserung": Are we making something worse in the attempt of making it better?

---

*Part of ["Exploring Gen AI"](https://martinfowler.com/articles/exploring-gen-ai.html) series by Birgitta Böckeler*
