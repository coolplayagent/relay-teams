#!/usr/bin/env node

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const { writeJsonOutput } = require("../designer/slide_layout_qa");

function captureLogs(fn) {
  const originalLog = console.log;
  const lines = [];

  console.log = (...args) => {
    lines.push(args.join(" "));
  };

  try {
    fn();
  } finally {
    console.log = originalLog;
  }

  return lines;
}

function main() {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "pptx-craft-json-output-"));
  const outputPath = path.join(tempRoot, "layout_qa_result.json");
  const payload = { passed: false, failed_pages: [{ file: "page-1.pptx.html" }] };

  try {
    const lines = captureLogs(() => writeJsonOutput(payload, outputPath));
    const output = lines.join("\n");

    assert.ok(fs.existsSync(outputPath), "json result file should be created");
    assert.match(output, new RegExp(`JSON saved to ${outputPath.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
    assert.match(
      output,
      new RegExp(`JSON directory: ${tempRoot.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`)
    );
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
}

main();
