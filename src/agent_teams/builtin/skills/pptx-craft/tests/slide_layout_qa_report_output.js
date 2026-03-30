#!/usr/bin/env node

const assert = require("assert");

const { generateReport } = require("../designer/slide_layout_qa");

function captureLogs(fn) {
  const originalLog = console.log;
  const lines = [];

  console.log = (...args) => {
    lines.push(args.join(" "));
  };

  try {
    return { result: fn(), lines };
  } finally {
    console.log = originalLog;
  }
}

function main() {
  const slide = {
    index: 0,
    filePath: "page-2.pptx.html",
    type: "content",
    blankRatio: 61.4,
    pixelAnalysis: { geometryCoverage: 35 },
    overflows: [{ text: "<div>" }],
    textOverlaps: [{ textA: "Title", textB: "Subtitle" }],
    textOcclusions: [{ occludedText: "Revenue", occluder: "<div.overlay>" }],
    childOverflows: [
      { child: "<p>", parent: "<div.card>", overflowType: "parent_overflow" },
      { child: "<span>", parent: "<section.wrapper>", overflowType: "ancestor_clip" },
    ],
    blockOverlaps: [{ blockA: "<div.left>", blockB: "<div.right>" }],
    recursiveBlanks: [{ selector: ".hero-panel", blankRatio: 48 }],
  };

  const { result, lines } = captureLogs(() => generateReport([slide]));
  const output = lines.join("\n");

  assert.strictEqual(result.hasIssues, true, "report should mark the slide as failing");
  assert.match(output, /Page 1: FAIL \(8 issues\)/, "summary line should include issue count");
  assert.match(output, /\[error\] blank_rate: blank ratio 61\.4%/);
  assert.match(output, /\[error\] overflow: <div> overflows slide bounds/);
  assert.match(output, /\[warning\] text_overlap: Title overlaps Subtitle/);
  assert.match(output, /\[error\] text_occlusion: Revenue is occluded by <div\.overlay>/);
  assert.match(output, /\[warning\] child_overflow: <p> overflows <div\.card>/);
  assert.match(output, /\[warning\] ancestor_clip: <span> overflows <section\.wrapper>/);
  assert.match(output, /\[warning\] block_overlap: <div\.left> overlaps <div\.right>/);
  assert.match(output, /\[warning\] recursive_blank: \.hero-panel blank ratio 48%/);
}

main();
