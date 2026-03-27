#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const {
  exporterBundle,
  exporterNodeBundle,
  exporterRoot,
  skillRoot,
} = require("./setup_designer");

function versionOk() {
  const major = Number(process.versions.node.split(".")[0] || 0);
  return major >= 18;
}

function exists(targetPath) {
  return fs.existsSync(targetPath);
}

const issues = [];

if (!versionOk()) {
  issues.push(`Node.js >= 18 is required. Current: ${process.versions.node}`);
}

if (!exists(path.join(skillRoot, "node_modules", "playwright"))) {
  issues.push("Top-level dependency missing: playwright. Run `npm install`.");
}

if (!exists(path.join(exporterRoot, "node_modules"))) {
  issues.push(
    "Nested exporter dependencies are missing. Run `npm run setup-designer`."
  );
}

if (!exists(exporterBundle) || !exists(exporterNodeBundle)) {
  issues.push(
    "Exporter bundle is missing. Run `npm run setup-designer` to build it."
  );
}

if (issues.length > 0) {
  console.error("Dependency check failed:");
  for (const issue of issues) {
    console.error(`- ${issue}`);
  }
  process.exit(1);
}

console.log("Dependencies look ready.");
