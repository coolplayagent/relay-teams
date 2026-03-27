#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const packageRoot = __dirname;
const bundlePath = path.join(packageRoot, "dist", "dom-to-pptx.bundle.js");
const nodeBundlePath = path.join(
  packageRoot,
  "node",
  "dist",
  "dom-to-pptx.bundle.js"
);

const issues = [];
const nodeMajor = Number(process.versions.node.split(".")[0] || 0);

if (nodeMajor < 18) {
  issues.push(`Node.js >= 18 is required. Current: ${process.versions.node}`);
}

if (!fs.existsSync(path.join(packageRoot, "node_modules"))) {
  issues.push("node_modules is missing. Run `npm install` in this directory.");
}

if (!fs.existsSync(bundlePath) || !fs.existsSync(nodeBundlePath)) {
  issues.push("Bundle is missing. Run `npm run build` in this directory.");
}

if (issues.length > 0) {
  console.error("html-to-pptx dependency check failed:");
  for (const issue of issues) {
    console.error(`- ${issue}`);
  }
  process.exit(1);
}

console.log("html-to-pptx is ready.");
