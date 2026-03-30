#!/usr/bin/env node

const assert = require("assert");

const { generateReport } = require("../scripts/export_qa");

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
  const results = [
    {
      file: "page-1.pptx.html",
      type: "cover",
      visualDiffRatio: 0.3521,
      issues: [
        {
          type: "badge_alignment",
          severity: "error",
          detail: "近7年低点 is not aligned with Badge Background",
        },
      ],
    },
    {
      file: "page-2.pptx.html",
      type: "content",
      issues: [],
    },
  ];

  const { result, lines } = captureLogs(() =>
    generateReport(results, {
      pageCountMismatch: "expected 2 slides but found 1 in pages.pptx",
      deckIssues: [
        {
          type: "visual_qa_runtime_failure",
          severity: "error",
          detail: "visual QA failed at runtime: playwright timeout",
        },
      ],
      mode: "visual+structural",
      capabilities: { visual: true },
    })
  );

  const output = lines.join("\n");
  assert.strictEqual(result.hasIssues, true, "report should fail on deck/page issues");
  assert.strictEqual(result.jsonResult.failed_pages[0].issues.length, 2);
  assert.match(output, /Export QA Report/);
  assert.match(output, /Mode: visual\+structural/);
  assert.match(output, /\[error\] slide_count_mismatch: expected 2 slides but found 1 in pages\.pptx/);
  assert.match(output, /\[error\] visual_qa_runtime_failure: visual QA failed at runtime: playwright timeout/);
  assert.match(output, /Page 1: FAIL \(1 issues\)/);
  assert.match(output, /\[error\] badge_alignment: 近7年低点 is not aligned with Badge Background/);
  assert.match(output, /Page 2: PASS/);
}

main();
