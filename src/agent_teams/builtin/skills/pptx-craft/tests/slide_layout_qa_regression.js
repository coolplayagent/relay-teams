#!/usr/bin/env node

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const { detectLayoutIssues } = require("../designer/qa_core");

function makeTempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "pptx-craft-layout-qa-"));
}

function writeFixture(targetPath) {
  const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>layout qa regression</title>
    <style>
      body {
        margin: 0;
        font-family: Arial, sans-serif;
      }

      .ppt-slide {
        width: 1280px;
        height: 720px;
        box-sizing: border-box;
        padding: 56px;
        background: #f9fafb;
        color: #111827;
      }

      h1 {
        margin: 0 0 24px;
        font-size: 48px;
      }

      .panel {
        width: 360px;
        padding: 24px;
        border-radius: 18px;
        border: 1px solid #d1d5db;
        background: #ffffff;
      }

      p {
        margin: 0;
        font-size: 24px;
        line-height: 1.4;
      }
    </style>
  </head>
  <body>
    <div class="ppt-slide" type="content">
      <h1>Layout QA</h1>
      <div class="panel">
        <p>Browser-side helper injection should work.</p>
      </div>
    </div>
  </body>
</html>`;

  fs.writeFileSync(targetPath, html, "utf8");
}

async function main() {
  const tempRoot = makeTempDir();
  const htmlPath = path.join(tempRoot, "page-1.pptx.html");

  try {
    writeFixture(htmlPath);

    const results = await detectLayoutIssues({ htmlPath });

    assert.ok(Array.isArray(results), "detectLayoutIssues should return an array");
    assert.strictEqual(results.length, 1, "fixture should produce one slide result");

    const [result] = results;
    assert.ok(Object.prototype.hasOwnProperty.call(result, "coverageRatio"));
    assert.ok(Object.prototype.hasOwnProperty.call(result, "blankRatio"));
    assert.ok(Array.isArray(result.overflows), "overflows should be an array");
    assert.ok(Array.isArray(result.textOverlaps), "textOverlaps should be an array");
    assert.ok(Array.isArray(result.textOcclusions), "textOcclusions should be an array");
    assert.ok(Array.isArray(result.childOverflows), "childOverflows should be an array");
    assert.ok(Array.isArray(result.blockOverlaps), "blockOverlaps should be an array");
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
