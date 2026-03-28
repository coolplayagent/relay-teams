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
const exporterPackage = path.join(exporterRoot, "package.json");
const topLevelPackages = ["playwright", "pngjs"];

if (!versionOk()) {
  issues.push(`Node.js >= 18 is required. Current: ${process.versions.node}`);
}

for (const packageName of topLevelPackages) {
  if (!exists(path.join(skillRoot, "node_modules", packageName))) {
    issues.push(`Top-level dependency missing: ${packageName}. Run \`npm install\`.`);
  }
}

if (!exists(exporterPackage)) {
  issues.push(
    `Nested exporter package definition is missing: ${path.relative(
      skillRoot,
      exporterPackage
    )}`
  );
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

if (issues.length === 0) {
  try {
    const { chromium } = require("playwright");
    const executablePath = chromium.executablePath();

    if (!executablePath || !exists(executablePath)) {
      issues.push(
        "Chromium browser is not installed for Playwright. Run `npm run install-browser`."
      );
    }
  } catch (error) {
    issues.push(
      `Playwright browser probe failed: ${error.message}. Run \`npm install\` and \`npm run install-browser\`.`
    );
  }
}

if (issues.length > 0) {
  console.error("Dependency check failed:");
  for (const issue of issues) {
    console.error(`- ${issue}`);
  }
  process.exit(1);
}

console.log("Dependencies look ready.");
