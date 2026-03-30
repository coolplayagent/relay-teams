#!/usr/bin/env node

const assert = require("assert");

const { applyVisualQa } = require("../scripts/export_qa");

async function main() {
  const results = [
    {
      file: "page-1.pptx.html",
      type: "content",
      issues: [],
    },
  ];
  const capabilities = {
    enabled: true,
    soffice: "/usr/bin/soffice",
    pdftoppm: "/usr/bin/pdftoppm",
    reason: null,
  };

  const outcome = await applyVisualQa(
    results,
    ["page-1.pptx.html"],
    "/tmp/pages.pptx",
    capabilities,
    async () => {
      throw new Error("playwright timeout");
    }
  );

  assert.strictEqual(outcome.mode, "structural_only");
  assert.strictEqual(outcome.capabilityReport.visual, false);
  assert.match(outcome.capabilityReport.reason, /visual QA failed at runtime: playwright timeout/);
  assert.deepStrictEqual(outcome.deckIssues, [
    {
      type: "visual_qa_runtime_failure",
      severity: "error",
      detail: "visual QA failed at runtime: playwright timeout",
    },
  ]);
  assert.match(
    outcome.qaResults[0].visualWarning,
    /visual QA failed at runtime: playwright timeout; structural checks only/
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
