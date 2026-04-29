# Realtime Eval Guide

- Source: OpenAI Cookbook
- Original URL: https://developers.openai.com/cookbook/examples/realtime_eval_guide
- Access date: 2026-04-19
- Published: 2026-01-25
- Type: Web guide

## Summary / key value

A concrete blueprint for building realtime voice-agent evaluation harnesses. It defines the maturity ladder of **crawl / walk / run** harnesses and explains how to separate content quality from audio quality, log event-level traces, and create reproducible replayable test loops.

## Why it is relevant to AI Harness Engineering

- Direct eval harness guidance.
- Includes runnable reference harnesses for single-turn replay, saved-audio replay, and model-simulated multi-turn evals.
- Relevant for agent eval frameworks and AI workflow infrastructure.

## Important excerpts

> The three things that make results robust: a dataset, graders, and an eval harness.

> A realtime eval is only as trustworthy as the harness that runs it.

## Archived content

The guide frames realtime evals as harder than text because both content quality and audio quality matter. It recommends staged complexity:

- **Crawl**: single-turn replay
- **Walk**: saved audio replay
- **Run**: model-simulated multi-turn evaluation

It also points to reusable harness code in `openai-cookbook/examples/evals/realtime_evals`.
