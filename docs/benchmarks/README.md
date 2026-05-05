# Benchmark Suite

This directory contains the three-layer benchmark system for relay-teams.

## Layer 1: Micro-Benchmarks

**Location:** `benchmarks/micro/`

Individual capability performance tests using `pytest-benchmark`. These tests measure the raw throughput of core operations.

### Core capabilities covered

| Benchmark file | Capability measured |
|---|---|
| `test_micro_role_creation.py` | RoleDefinition JSON parsing and validation speed |
| `test_micro_task_creation.py` | TaskEnvelope creation and dependency graph resolution |
| `test_micro_graph_topology.py` | DAG topological sort performance |
| `test_micro_verification.py` | Verification check construction and evaluation |
| `test_micro_wakeup_queue.py` | Wakeup queue entry creation and coalescing |
| `test_micro_memory_search.py` | BM25 search and memory entry serialization |

### Running locally

```bash
# Run with benchmark timing enabled
uv run --extra dev pytest benchmarks/micro/ --benchmark-only

# Run without benchmark timing (for CI validation)
uv run --extra dev pytest benchmarks/micro/ --benchmark-disable

# Save results as JSON
uv run --extra dev pytest benchmarks/micro/ --benchmark-only --benchmark-json=tmp/bench-results.json
```

### CI integration

- **Trigger:** Push to `main`, PR to `main`, manual dispatch
- **Workflow:** `.github/workflows/benchmarks-micro.yml`
- **Regression detection:** Performance deviation greater than 10% from baseline triggers a warning

## Layer 2: SWE-bench Continuous Tracking

**Location:** `benchmarks/swebench/`

End-to-end task resolution tracking against the SWE-bench Verified dataset. Measures the percentage of SWE-bench instances that relay-teams can successfully resolve.

### Components

| File | Purpose |
|---|---|
| `config.py` | `SWEBenchConfig`, `SWEBenchInstanceResult`, `SWEBenchRunResult` models |
| `runner.py` | `SWEBenchRunner` -- orchestrates instance execution |
| `reporter.py` | Report generation (JSON) and summary output |

### Running locally

```bash
# Ensure SWE-bench dataset is available (JSONL format)
uv run --extra dev python -c "
import asyncio
from pathlib import Path
from benchmarks.swebench.config import SWEBenchConfig
from benchmarks.swebench.runner import SWEBenchRunner
from benchmarks.swebench.reporter import generate_report, print_summary

config = SWEBenchConfig(
    dataset_path=Path('tmp/swebench-dataset.jsonl'),
    max_instances=10,
    output_dir=Path('benchmarks/swebench/results'),
)
runner = SWEBenchRunner()
result = asyncio.run(runner.run(config))
path = generate_report(result, config.output_dir)
print_summary(result)
"
```

### CI integration

- **Trigger:** Push to `main`, daily schedule (06:00 UTC), manual dispatch
- **Workflow:** `.github/workflows/benchmarks-swebench.yml`
- **Output:** JSON report persisted as CI artifact

## Layer 3: Spec-Compliance Check

**Location:** `benchmarks/spec_compliance/`

Static analysis of `src/relay_teams/` against coding standards defined in `AGENTS.md`. Produces an overall compliance score and per-file violation details.

### Check categories (8 total)

| Category | What it checks |
|---|---|
| `model_types` | No `typing.Any`, no `@dataclass` |
| `annotations` | `from __future__ import annotations` present |
| `imports` | No `TYPE_CHECKING` import guards |
| `path_usage` | No `os.path` -- must use `pathlib.Path` |
| `emoji_free` | No emoji characters in source |
| `type_ignore_free` | No `# type: ignore` comments |
| `hasattr_free` | No `hasattr()` calls |
| `module_init` | All packages have `__init__.py` |

### Running locally

```bash
# Run compliance check
uv run --extra dev python -m benchmarks.spec_compliance.runner

# Or via Python API
uv run --extra dev python -c "
from benchmarks.spec_compliance.runner import SpecComplianceRunner
runner = SpecComplianceRunner()
result = runner.run()
print(f'Overall score: {result.overall_score:.1%}')
print(f'Modules checked: {result.modules_checked}')
"
```

### CI integration

- **Trigger:** PR to `main`, manual dispatch
- **Workflow:** `.github/workflows/benchmarks-spec-compliance.yml`
- **Failure threshold:** `overall_score < 0.7` (70%)

## Design Principles

1. **Micro-benchmarks** validate individual component performance; they should complete in under 60 seconds total
2. **SWE-bench tracking** measures end-to-end quality at the task resolution level; it is expensive and runs on schedule
3. **Spec-compliance** acts as a CI gate to prevent coding standard drift; it is fast and runs on every PR
