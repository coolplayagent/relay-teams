#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const {
  assertOutsideSkillRoot,
  ensureDesignerReady,
  exporterRoot,
} = require("./setup_designer");

function usage() {
  console.log(
    "Usage: node scripts/convert_pages.js <pages_dir> [output_pptx_path]"
  );
}

const pagesDirArg = process.argv[2];
const outputArg = process.argv[3];

if (!pagesDirArg) {
  usage();
  process.exit(1);
}

const pagesDir = path.resolve(process.cwd(), pagesDirArg);
const outputPath = path.resolve(
  process.cwd(),
  outputArg || path.join(pagesDir, "pages.pptx")
);

if (!fs.existsSync(pagesDir) || !fs.statSync(pagesDir).isDirectory()) {
  console.error(`Pages directory not found: ${pagesDir}`);
  process.exit(1);
}

try {
  assertOutsideSkillRoot(pagesDir, "pages directory");
  assertOutsideSkillRoot(outputPath, "PPTX output path");
  ensureDesignerReady({ ifNeeded: true });
} catch (error) {
  console.error(error.message);
  process.exit(1);
}

const convertScript = path.join(exporterRoot, "node", "convert.js");
const result = spawnSync(process.execPath, [convertScript, pagesDir, outputPath], {
  stdio: "inherit",
  shell: false,
});

if (result.status !== 0) {
  process.exit(result.status || 1);
}
