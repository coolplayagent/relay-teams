# Testing Agent Skills Systematically with Evals

- Source: OpenAI Developers
- Original URL: https://developers.openai.com/blog/eval-skills
- Access date: 2026-04-19
- Published: 2026-01-22
- Type: Web article

## Summary / key value

Practical guide for building a lightweight **evaluation harness** around agent skills. It translates “skill quality” into measurable outcome/process/style/efficiency checks and shows how to use captured traces plus deterministic graders.

## Why it is relevant to AI Harness Engineering

- Directly about agent-skill eval harnesses.
- Shows concrete acceptance criteria, trace capture, and grading patterns.
- Useful for LLM evaluation harness and agentic coding harness workflows.

## Important excerpts

> Concretely, an eval is: a prompt → a captured run (trace + artifacts) → a small set of checks → a score you can compare over time.

> In practice, evals for agent skills look a lot like lightweight end-to-end tests.

## Archived content

OpenAI recommends defining success before writing the skill, splitting checks into outcome, process, style, and efficiency goals. The article then walks through building a sample `setup-demo-app` skill, manually triggering it to expose hidden assumptions, and using deterministic graders over captured agent runs.

The write-up is a strong implementation reference for turning agent behavior into regression-testable artifacts.
