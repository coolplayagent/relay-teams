#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const outputDirArg = process.argv[2];
const skillRoot = path.resolve(__dirname, "..");

function isSubpath(parentPath, childPath) {
  const parent = path.resolve(parentPath);
  const child = path.resolve(childPath);
  return child === parent || child.startsWith(parent + path.sep);
}

if (!outputDirArg) {
  console.error("Error: Missing output directory argument");
  process.exit(1);
}

const resolvedPath = path.resolve(process.cwd(), outputDirArg);
if (path.basename(resolvedPath) === "pages") {
  console.error("Error: Do not pass a path ending in 'pages' to this script");
  process.exit(1);
}

if (isSubpath(skillRoot, resolvedPath)) {
  console.error(`Error: Refusing to create output inside the skill directory: ${resolvedPath}`);
  process.exit(1);
}

const pagesDir = path.join(resolvedPath, "pages");
fs.mkdirSync(pagesDir, { recursive: true });

if (!fs.existsSync(pagesDir) || !fs.statSync(pagesDir).isDirectory()) {
  console.error(`Error: Failed to create directory ${pagesDir}`);
  process.exit(1);
}

console.log(path.resolve(pagesDir));
