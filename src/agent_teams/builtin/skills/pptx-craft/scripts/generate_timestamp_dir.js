#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const skillRoot = path.resolve(__dirname, "..");
const baseDir = path.resolve(process.cwd(), process.argv[2] || "output");

function isSubpath(parentPath, childPath) {
  const parent = path.resolve(parentPath);
  const child = path.resolve(childPath);
  return child === parent || child.startsWith(parent + path.sep);
}

if (isSubpath(skillRoot, baseDir)) {
  console.error(`Error: Refusing to create output inside the skill directory: ${baseDir}`);
  process.exit(1);
}

const now = new Date();
const timestampPrefix = [
  now.getFullYear(),
  String(now.getMonth() + 1).padStart(2, "0"),
  String(now.getDate()).padStart(2, "0"),
  "_",
  String(now.getHours()).padStart(2, "0"),
  String(now.getMinutes()).padStart(2, "0"),
  String(now.getSeconds()).padStart(2, "0"),
].join("");

if (!fs.existsSync(baseDir)) {
  fs.mkdirSync(baseDir, { recursive: true });
}

let seq = 0;
while (fs.existsSync(path.join(baseDir, `${timestampPrefix}_${String(seq).padStart(3, "0")}`))) {
  seq++;
}

const timestampDir = path.join(
  baseDir,
  `${timestampPrefix}_${String(seq).padStart(3, "0")}`
);
fs.mkdirSync(timestampDir, { recursive: true });

console.log(path.resolve(timestampDir));
