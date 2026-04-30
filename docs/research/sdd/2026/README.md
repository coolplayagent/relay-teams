# AI SDD (Spec-Driven Development) Resource Archive — 2026

> Curated collection of 80 resources on Spec-Driven Development, AI coding agents, context engineering, and related topics. Total size: ~108 MB.

---

## Directory Structure

```
sdd/
├── 01-academic-papers/     # 37 PDF — arXiv / academic papers
├── 02-industry-reports/    #  6 PDF — consulting firms & industry bodies
├── 03-practitioner-blogs/  # 12 MD  — personal practice & experience blogs
├── 04-tech-company-guides/ # 14 MD  — vendor/tool guides & tutorials
├── 05-analysis-notes/      # 10 MD  — deep analysis, Martin Fowler series, notes
└── README.md               # this index
```

---

## 01 — Academic Papers (37 PDF)

### Core SDD Papers
| Paper ID | File | Key Focus |
|----------|------|-----------|
| 2602.00180 | `arxiv-2602.00180_Spec-Driven-Development-From-Code-to-Contract.pdf` | Foundational SDD paper — 3 levels of spec rigor (Piskala, ACM AIware 2026) |
| 2601.03878 | `arxiv-2601.03878_Understanding-Spec-Driven-Code-Gen-with-LLMs.pdf` | Empirical study design for specification-driven code generation (Rosa et al.) |
| 2602.02584 | `arxiv-2602.02584_Constitutional-SDD-Security-by-Construction.pdf` | Security-by-construction in AI-assisted code generation |
| 2603.25697 | `arxiv-2603.25697_The-Kitchen-Loop-User-Spec-Driven-Dev.pdf` | User-spec-driven development for self-evolving codebase (Roy) |
| 2604.03758 | `arxiv-2604.03758_AutoReSpec-Framework-Generating-Spec-with-LLMs.pdf` | Automated spec generation framework (Shahariar et al.) |
| 2604.12268 | `arxiv-2604.12268_CodeSpecBench-Benchmarking-LLMs-for-Spec-Gen.pdf` | Benchmark for executable behavioral specification generation |
| — | `arXiv_Spec_Kit_Agents_Context_Grounded_2026.pdf` | Multi-agent SDD pipeline with PM/dev roles (Taghavi & Bhavani) |
| 2603.17399 | `arxiv-2603.17399_Bootstrapping-Coding-Agents-Spec-Is-the-Program.pdf` | "The specification is the program" (Monperrus, KTH) |
| 2603.20151 | `arxiv-2603.20151_Design-OS-Spec-Driven-Framework-for-Engineering.pdf` | Spec-driven framework for engineering system design |

### Agent & Benchmark Papers
| Paper ID | File | Key Focus |
|----------|------|-----------|
| 2602.09447 | `arxiv-2602.09447_SWE-AGI-Benchmarking-Spec-Driven-Software-Construction.pdf` | First spec-driven from-scratch benchmark (MoonBit) |
| 2602.02262 | `arxiv-2602.02262_OmniCode-Benchmark-SE-Agents.pdf` | Omnilingual benchmark for SE agents |
| 2603.04601 | `arxiv-2603.04601_Vibe-Code-Bench-Evaluating-AI-Web-App-Dev.pdf` | Evaluating AI for web app development |
| 2603.05344 | `arxiv-2603.05344_Building-Effective-AI-Coding-Agents-Context-Engineering.pdf` | Scaffolding, harness, context engineering best practices |
| 2603.24755 | `arxiv-2603.24755_SlopCodeBench-Coding-Agents-Degrade-Long-Horizon.pdf` | How coding agents degrade over long horizons |
| 2604.01527 | `arxiv-2604.01527_ProdCodeBench-Production-Derived-Benchmark-AI-Coding-Agents.pdf` | Production-derived benchmark for AI coding agents |
| 2604.11518 | `arxiv-2604.11518_Benchmark-Driven-Evolution-Production-AI-Agent-JPMC.pdf` | JPMorgan production AI agent evolution |
| 2602.08915 | `arxiv-2602.08915_Comparing-AI-Coding-Agents-PR-Acceptance.pdf` | Comparing agents on PR acceptance rates |
| 2603.03823 | `arxiv-2603.03823_SWE-CI-Agent-Capabilities-Maintaining-Codebases.pdf` | Agent capabilities for maintaining codebases |

### Specification & Formal Methods
| Paper ID | File | Key Focus |
|----------|------|-----------|
| 2604.00280 | `arxiv-2604.00280_VeriAct-Agentic-Synthesis-Formal-Specs.pdf` | Correct and complete formal spec synthesis |
| 2603.25773 | `arxiv-2603.25773_Specification-as-Quality-Gate-AI-Code-Review.pdf` | Spec as quality gate in AI code review |
| 2509.09917 | `arxiv-2509.09917_SLD-Spec-LLM-Specification-Generation-Loop-Functions.pdf` | Loop function spec generation |
| 2602.13723 | `arxiv-2602.13723_ARC-Compiling-Requirement-Scenarios-Web-System.pdf` | Compiling requirement scenarios for web systems |
| 2602.13611 | `arxiv-2602.13611_From-What-to-How-Bridging-Requirements-with-LLMs.pdf` | Bridging requirements to implementation |
| 2509.01313 | `arxiv-2509.01313_Aligning-Requirement-LLM-Code-Generation.pdf` | Aligning requirements in LLM code generation |
| 2603.16348 | `arxiv-2603.16348_Prompts-Blend-Requirements-and-Solutions.pdf` | How prompts blend requirements with solutions |

### Survey / Broader AI Coding
| Paper ID | File | Key Focus |
|----------|------|-----------|
| 2508.11126 | `arxiv-2508.11126_AI-Agentic-Programming-Survey.pdf` | Survey of AI agentic programming |
| 2510.09721 | `arxiv-2510.09721_Comprehensive-Survey-Benchmarks-LLM-Agentic-SE.pdf` | Comprehensive LLM SE agent benchmarks survey |
| 2506.13932 | `arxiv-2506.13932_Code-Reasoning-for-SE-Tasks-Survey.pdf` | Code reasoning for SE tasks |
| 2601.13118 | `arxiv-2601.13118_Guidelines-Prompt-LLMs-Code-Generation.pdf` | Prompting guidelines for LLM code generation |
| 2601.18341 | `arxiv-2601.18341_Agentic-Much-Adoption-Coding-Agents-GitHub.pdf` | How much adoption of coding agents on GitHub |
| 2602.15763 | `arxiv-2602.15763_GLM-5-From-Vibe-Coding-to-Agentic-Engineering.pdf` | GLM-5 transition from vibe coding to agentic engineering |
| 2603.15691 | `arxiv-2603.15691_VibeContract-QA-Piece-in-Vibe-Coding.pdf` | QA in vibe coding era |
| 2602.20478 | `arxiv-2602.20478_Codified-Context-Infrastructure-for-AI-Agents.pdf` | Context infrastructure for AI agents |
| 2502.05310 | `arxiv-2502.05310_Oracular-Programming-LLM-Enabled-Software.pdf` | Oracular programming paradigm |
| 2503.02400 | `arxiv-2503.02400_Promptware-Engineering-SE-for-LLM-Prompt-Dev.pdf` | Promptware engineering |
| 2603.25928 | `arxiv-2603.25928_Self-Organizing-Multi-Agent-Systems-for-Continuous-SD.pdf` | Self-organizing multi-agent continuous development |
| 2604.04990 | `arxiv-2604.04990_Architecture-Without-Architects-AI-Coding-Agents.pdf` | Architecture without architects |

---

## 02 — Industry Reports (6 PDF)

| File | Source | Size | Focus |
|------|--------|------|-------|
| `McKinsey-State-of-Organizations-2026.pdf` | McKinsey | 17 MB | Organizational AI transformation trends |
| `PwC-Agentic-SDLC-in-Practice-2026.pdf` | PwC | 4.9 MB | Agentic SDLC — autonomous software delivery |
| `PwC-Six-Business-Predictions-AI-2026.pdf` | PwC | 2.8 MB | Six business predictions for AI |
| `BCG-AI-Radar-2026-As-AI-Investments-Surge-CEOs-Take-the-Lead.pdf` | BCG | 522 KB | AI investment surge, CEO leadership |
| `Deloitte-Tech-Trends-2026.pdf` | Deloitte | 2.4 MB | Technology trends including AI coding |
| `Anthropic-2026-State-of-AI-Agents-Report.pdf` | Anthropic | 2.3 MB | State of AI agents report |

---

## 03 — Practitioner Blogs (12 MD)

| Author | File | Company/Context | Highlights |
|--------|------|-----------------|------------|
| **Ghulam Ahmed** | `blog-gahmed-Spec-Driven-Dev-with-Claude-Code-Full-Setup.md` | — | 4-phase SDD workflow, CLAUDE.md structure, subagent delegation, context management |
| **Gordon Burgett** | `blog-gordonburgett-Pushing-Claude-Code-Further-with-SDD.md` | Albers Aerospace | Cucumber + AI agent; Rails microservice extraction; camera firmware integration tests in Rust |
| **Navin Varma** | `blog-nvarma-SDD-for-Responsible-AI-2026.md` | Workday | Structuring thinking before code; SDD for responsible AI; tool convergence table |
| **Eddie Legg** | `blog-leadingedje-Teaching-AI-to-Build-Software-Five-Iterations-SDD.md` | Leading EDJE | Same app ×5 iterations; $153→$634 cost analysis; enforcement hierarchy (tooling > templates > docs) |
| **Josip Budalic** | `blog-hotfix-10x-Dev-Speed-with-AI-SDD.md` | HOTFIX d.o.o. | spec-kit vs OpenSpec comparison; fintech dashboard; when to use which tool |
| **Matt Walker** | `blog-mrmatt-Building-MrMatt-io-Spec-Driven-Dev.md` | — | plan-as-spec; 44 features shipped; `/feature` + `/ship` workflow; git worktrees |
| **Marvin Zhang** | `blog-marvinzhang-Introducing-LeanSpec-SDD-Framework.md` | Codervisor | LeanSpec: 5 first principles; <300 line specs; built LeanSpec with LeanSpec (dogfooding) |
| **Ivan Magda** | `blog-ivanmagda-Five-Things-AI-Coding-Agents.md` | — | 5 scaffolding lessons; context rot research; enforcement hierarchy; compact CLAUDE.md |
| **Qais Hweidi** | `blog-qaishweidi-13-Lessons-1-Year-100pct-AI-Code.md` | — | 1M+ views post (2026 update); parallel agents; 1-shot prompt test; first-1000-lines pattern |
| **Vishwasa Navada K** | `blog-vishwasnavada-Honest-Math-Coding-AI-Agents-Production.md` | AntStack | 86K LOC/188 sessions real data; 30% boost on boilerplate only; PRs ballooned 91% |
| **Markus Lachinger** | `blog-mmlac-500k-LOC-AI-Lessons-Learned.md` | — | 500K lines .NET desktop app; model selection heuristics; TDD debugging; prompt pack |
| **Iones Walter** | `Medium_IonesWalter_AI_SDD_in_2026.md` | — | AI SDD landscape overview |

---

## 04 — Tech Company & Tool Guides (14 MD)

| File | Source | Focus |
|------|--------|-------|
| `GitHub_Spec_Kit_SDD_Blog.md` | GitHub (Delimarsky) | Spec Kit introduction, 4 phases |
| `Microsoft_Spec_Kit_Deep_Dive.md` | Microsoft (Delimarsky) | Specify CLI deep dive |
| `Anthropic_Code_Execution_with_MCP.md` | Anthropic | MCP code execution, 98.7% token savings |
| `OpenAI_Harness_Engineering.md` | OpenAI (Lopopolo) | 0 manually-written code, harness patterns |
| `Google_Jules_Async_Coding_Agent.md` | Google | Jules async coding agent |
| `JetBrains_Junie_Spec_Driven_Approach.md` | JetBrains | Junie SDD workflow |
| `RedHat_SDD_Improves_AI_Coding_Quality.md` | Red Hat | Spec vs vibe coding |
| `AugmentCode_SDD_Practitioner_Guide.md` | Augment Code | 6-element spec, adversarial agent |
| `Pockit_SDD_Complete_Guide.md` | Pockit | 4-phase SDD loop |
| `QubitTool_Spec_Coding_SDD_Guide.md` | QubitTool | OpenSpec framework |
| `AWS_Kiro_SDD_Case_Study_Drug_Discovery.md` | AWS | Kiro SDD drug discovery case study |
| `Rackspace_Kiro_SDD_Success.md` | Rackspace | Kiro success story |
| `IBM_SDD_AI_Assisted_Coding_Explained.md` | IBM | AI-assisted coding explained |
| `sdd_sh_What_Is_SDD.md` | sdd.sh | SDD overview & tooling ecosystem |

---

## 05 — Analysis Notes (10 MD)

| File | Author/Source | Focus |
|------|---------------|-------|
| `SDD_Deep_Analysis_Companies_Tools_Practices.md` | Internal analysis | Cross-company comparison of SDD practices |
| `Rushi_SDD_Technical_Deep_Dive_Frameworks_Comparison.md` | Rushi | Technical deep dive, frameworks comparison |
| `arXiv_SDD_Code_to_Contract_2026.md` | arXiv analysis | SDD paper analysis notes |
| `arXiv_Spec_Kit_Agents_Context_Grounded_2026.md` | arXiv analysis | Spec Kit Agents paper analysis |
| `MartinFowler_Exploring_GenAI_Index.md` | Martin Fowler | Full article series index (25+ articles) |
| `MartinFowler_SDD_Kiro_SpecKit_Tessl.md` | Martin Fowler | 3 levels of SDD, tool comparison |
| `MartinFowler_Context_Engineering_Coding_Agents.md` | Martin Fowler | Context engineering primer |
| `MartinFowler_Harness_Engineering_First_Thoughts.md` | Martin Fowler | Harness engineering components |
| `Lapsley_Spec_Driven_LLM_Development.md` | David Lapsley | SDLD methodology, EARS syntax |
| `Thoughtworks_Tech_Radar_SDD_Assess.md` | ThoughtWorks | Tech Radar SDD assessment |

---

## Key Tools/Frameworks Referenced

| Tool | Developer | Type | Key Link |
|------|-----------|------|----------|
| **Spec Kit** | GitHub | OSS SDD toolkit | github.com/github/spec-kit |
| **OpenSpec** | Fission AI | Lightweight SDD | github.com/Fission-AI/OpenSpec |
| **LeanSpec** | Marvin Zhang (Codervisor) | Lightweight SDD framework | github.com/codervisor/lean-spec |
| **Kiro** | AWS | Agentic SDD IDE | kiro.dev |
| **Tessl** | Tessl | AI-assisted SDD | docs.tessl.io |
| **Conflux** | Community | Spec-driven orchestrator | dev.to/tumf |
| **MoonBit** | — | Spec-first language | moonbitlang.com |

---

## Source URLs

- SDD foundational paper: https://arxiv.org/abs/2602.00180
- GitHub Spec Kit: https://github.com/github/spec-kit
- OpenSpec: https://github.com/Fission-AI/OpenSpec
- LeanSpec: https://www.lean-spec.dev/
- Kiro: https://kiro.dev
- Tessl: https://docs.tessl.io
- Martin Fowler series: https://martinfowler.com/articles/exploring-gen-ai.html
- OpenAI Harness: https://openai.com/index/harness-engineering/
- Anthropic MCP: https://www.anthropic.com/engineering/code-execution-with-mcp
- sdd.sh: https://sdd.sh

---

*Archive created: 2026-04-19 | Last updated: 2026-04-19*
