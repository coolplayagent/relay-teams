#!/usr/bin/env node

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const skillRoot = path.resolve(__dirname, "..");
const convertScript = path.join(skillRoot, "scripts", "convert_pages.js");

function makeTempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "pptx-craft-smoke-"));
}

function writeFixtureHtml(targetPath) {
  const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>pptx-craft smoke</title>
    <style>
      html, body {
        margin: 0;
        padding: 0;
        background: #e7ecf3;
        font-family: Arial, sans-serif;
      }

      .ppt-slide {
        width: 1280px;
        height: 720px;
        box-sizing: border-box;
        padding: 72px 88px;
        background: linear-gradient(135deg, #1d3557 0%, #457b9d 100%);
        color: #f8fafc;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
      }

      .eyebrow {
        font-size: 28px;
        letter-spacing: 2px;
        text-transform: uppercase;
      }

      h1 {
        margin: 0;
        font-size: 64px;
        line-height: 1.1;
      }

      .card {
        width: 420px;
        padding: 28px 32px;
        border-radius: 24px;
        background: rgba(255, 255, 255, 0.14);
      }

      .card strong {
        display: block;
        font-size: 22px;
        margin-bottom: 12px;
      }

      .card span {
        font-size: 18px;
        line-height: 1.5;
      }
    </style>
  </head>
  <body>
    <div class="ppt-slide" type="content">
      <div class="eyebrow">Smoke Test</div>
      <h1>Minimal HTML converts into a PPTX.</h1>
      <div class="card">
        <strong>Expected result</strong>
        <span>The wrapper script generates a non-empty pages.pptx file.</span>
      </div>
    </div>
  </body>
</html>`;

  fs.writeFileSync(targetPath, html, "utf8");
}

function main() {
  const tempRoot = makeTempDir();
  const pagesDir = path.join(tempRoot, "pages");
  const outputPath = path.join(tempRoot, "pages.pptx");
  const exportQaJsonPath = path.join(tempRoot, "export_qa_result.json");

  fs.mkdirSync(pagesDir, { recursive: true });
  writeFixtureHtml(path.join(pagesDir, "page-1.pptx.html"));

  const result = spawnSync(process.execPath, [convertScript, pagesDir, outputPath], {
    cwd: skillRoot,
    encoding: "utf8",
  });

  if (result.status !== 0) {
    process.stdout.write(result.stdout || "");
    process.stderr.write(result.stderr || "");
  }

  assert.strictEqual(result.status, 0, "smoke conversion should exit successfully");
  assert.ok(fs.existsSync(outputPath), "pages.pptx should be created");
  assert.ok(fs.statSync(outputPath).size > 0, "pages.pptx should be non-empty");
  assert.ok(fs.existsSync(exportQaJsonPath), "export_qa_result.json should be created");

  const exportQaResult = JSON.parse(fs.readFileSync(exportQaJsonPath, "utf8"));
  assert.strictEqual(exportQaResult.passed, true, "export QA should pass for the smoke deck");
  assert.match(
    exportQaResult.mode,
    /^(visual\+structural|structural_only)$/u,
    "export QA should report its execution mode"
  );

  fs.rmSync(tempRoot, { recursive: true, force: true });
}

main();
