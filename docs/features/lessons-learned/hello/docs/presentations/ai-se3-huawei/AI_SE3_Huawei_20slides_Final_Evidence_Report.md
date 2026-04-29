# Evidence Report — AI_SE3_Huawei_20slides_Final.pptx

Generated at (UTC): 2026-03-31T01:22:39Z

Inputs inspected:
- `hello-root/AI_SE3_Huawei_20slides_Final.pptx`
- `hello-root/AI_SE3_Huawei_20slides_Final_Notes.md`
- `tmp/ai_se3_huawei_20slide_content_spec.md`

Scope note: this report inspects the PPTX package, notes, and spec only. The PPTX content was **not modified**.

## 1) Exact slide count and counting method

**Exact slide count: 20**

Exact shell command used:

```bash
python - <<'PY'
from zipfile import ZipFile
import re
with ZipFile('AI_SE3_Huawei_20slides_Final.pptx') as z:
    print(sum(1 for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)))
PY
```

Method: count slide parts directly inside the PPTX OOXML zip package matching `ppt/slides/slideN.xml`.

Corroborating evidence:
- `AI_SE3_Huawei_20slides_Final_Notes.md` line 8 states `页数：20 页（精确）`.
- `ppt/presentation.xml` contains 20 slide IDs (`rId2` through `rId21`).

## 2) PPTX file size

Exact shell command used:

```bash
stat -c '%n|%s bytes' AI_SE3_Huawei_20slides_Final.pptx
```

Result:
- `AI_SE3_Huawei_20slides_Final.pptx|574520 bytes`

## 3) Extracted slide titles

Titles were extractable from slide XML text. Best-effort title list:

1. AI大模型时代下的软件开发思考及调研
2. 执行摘要：本报告的五个判断
3. 为什么现在必须重看软件开发：AI 已从工具议题变成经营议题
4. 本报告的立场：真正要升级的不是开发工具，而是软件工程操作系统
5. 章节一｜范式迁移：从软件工程 2.0 走向 3.0
6. Software Engineering 3.0：从 code-centric 到 intent-centric
7. SE 3.0 对企业研发栈的含义：Teammate.next 到 Runtime.next
8. Thoughtworks 的提醒：不要把 GenAI 缩减为“写代码更快”
9. 章节二｜企业分水岭：从 Prompt Engineering 走向 Harness Engineering
10. 什么是 Harness Engineering：企业不是在“使用 AI”，而是在“驯化 AI”
11. Harness 的三大核心：Context Engineering、Architectural Constraints、Garbage Collection
12. Harness 不是文档堆砌，而是可运行的工程系统
13. 章节三｜证据审视：北美数据证明了什么，又没有证明什么
14. 效率证据：北美一线研究表明，AI 辅助对开发者个体产出有真实增益
15. 质量与信任证据：收益存在，但“几乎正确”仍是主流痛点
16. 关键反直觉：局部提效不自动转化为系统级交付提升
17. 章节四｜组织重构：Human-Agent Team 将成为新的研发基本单元
18. 研发组织的新模型：从 Org Chart 到 Work Chart / Human-Agent Team
19. 企业落地路线：从工具试点到平台化 AI-native engineering
20. 结论与建议：未来三年的胜负手，不是模型选择，而是工程系统能力

This matches the notes file section `## 二、最终 PPT 结构（20页）`.

## 4) Evidence of citations / source markers

Visible citation evidence is strong:
- **All 20 slides** contain a visible `资料来源：...` text block in extracted slide text.
- Bracketed source markers are visible in slide text, e.g.:
  - Slide 1: `[1][4][5]`
  - Slide 2: `[1][2][4][5][8][12][14]`
  - Slide 3: `[8][10][14][15]`
  - Slide 8: `[4][5][6][7]`
  - Slide 14: `[9][12][13]`
  - Slide 20: `[1][2][4][5][8][9][11][12][13][14][15]`
- Slide 20 also includes a visible `紧凑来源索引` section.
- The notes file provides a numbered bibliography `[1]`–`[15]` and a slide-by-slide mapping of claims to sources.

Conclusion: citation markers are not just in notes; they are observable in slide text extracted from the PPTX itself.

## 5) Evidence of coverage of required topics

### Software Engineering 3.0
Observable in slides **1, 2, 6, 7, 20**.
- Slide 6 title explicitly: `Software Engineering 3.0：从 code-centric 到 intent-centric`
- Slide 2 judgment: `软件工程进入 SE 3.0`
- Slide 20 conclusion: `SE 3.0 是软件工程的新叙事框架`

### Harness Engineering
Observable in slides **2, 9, 10, 11, 12, 16, 19, 20**.
- Slide 9 section title explicitly transitions to `Harness Engineering`
- Slide 10 title explicitly defines Harness Engineering
- Slide 11 covers the three Harness cores
- Slide 12 describes Harness as a runnable engineering system

### Thoughtworks
Observable in slides **1, 8, 10, 20** and extensively in notes.
- Slide 8 title explicitly: `Thoughtworks 的提醒：不要把 GenAI 缩减为“写代码更快”`
- Slide 10 body explicitly mentions `Martin Fowler / Thoughtworks`
- Notes bibliography includes multiple Thoughtworks sources: `[2]`, `[4]`, `[5]`, `[6]`, `[7]`

### North American company / publication analyses
Observable in slides **2, 3, 13, 14, 15, 18, 20**.
Representative evidence:
- Slide 3 references Microsoft Work Trend Index, DORA, GitHub, Stack Overflow
- Slide 14 references Microsoft Research, MIT Open Publishing, Accenture/GitHub evidence
- Slide 15 references GitHub quality experiment, DORA, Stack Overflow
- Slide 18 references Microsoft WTI and Stack Overflow
- Notes bibliography includes Microsoft, GitHub, DORA/Google Cloud, MIT, Stack Overflow

Conclusion: all four requested coverage areas are clearly present.

## 6) Observable style / layout clues from accessible text or metadata

From accessible text, notes, and OOXML metadata:
- **Overall style intent** in notes: `华为风格执行汇报版（浅底、克制红色强调、模块化、数据优先）`.
- **Language mix**: primarily Chinese body text with selective English technical terms (`Software Engineering 3.0`, `Harness Engineering`, `Human-Agent Team`, `Runtime.next`).
- **Section-divider structure** is visible on slides 5, 9, 13, 17, each with a large section statement and minimal supporting line.
- **Page numbering** is visible on slides (e.g. `02`, `03`, `04`, etc.); divider slides show large numerals like `05`, `09`, `13`, `17`.
- **Executive/data-first layout clues** from slide text:
  - Cover uses short slogan lines: `Intent > Code | System > Tool | Governance > Experiment`
  - Evidence slides use large metric callouts such as `82%`, `80%`, `75%+`, `26.08%`, `+53.2%`
  - Slide 20 uses `四条最终结论` plus a compact source index
- **Slide size** from `ppt/presentation.xml`: `cx=12191695`, `cy=6858000`, which is standard **16:9 widescreen**.
- **Theme / font metadata** from `ppt/theme/theme1.xml`:
  - Office theme detected
  - Major/minor Latin font: `Microsoft YaHei`
  - Hans script fonts: `等线 Light` / `等线`
- **Master text sizing** from `ppt/slideMaster1.xml` suggests presentation-style hierarchy:
  - Title default size `4400` (44 pt)
  - Body level 1 default size `3200` (32 pt)
- **Metadata sparsity**:
  - Core props `created` and `modified`: `2026-03-31T01:10:42Z`
  - Title/creator/company fields were not populated in accessible core/app properties

## Overall finding

The deliverable appears to be a **20-slide, citation-bearing, Huawei-style executive deck** with clearly extractable slide titles and explicit coverage of:
- Software Engineering 3.0
- Harness Engineering
- Thoughtworks viewpoints
- North American company/publication evidence

The PPTX shows visible source markers on every slide, and the notes file supplies a detailed source-to-slide claim map.
