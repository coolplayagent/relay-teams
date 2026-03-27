#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const skillRoot = path.resolve(__dirname, "..");
const exporterRoot = path.join(skillRoot, "designer", "lib", "html-to-pptx");
const exporterBundle = path.join(exporterRoot, "dist", "dom-to-pptx.bundle.js");
const exporterNodeBundle = path.join(
  exporterRoot,
  "node",
  "dist",
  "dom-to-pptx.bundle.js"
);

function npmCommand() {
  return "npm";
}

function quoteArg(arg) {
  if (/[\s"]/u.test(arg)) {
    return `"${String(arg).replace(/"/g, '\\"')}"`;
  }
  return String(arg);
}

function exists(targetPath) {
  return fs.existsSync(targetPath);
}

function isSubpath(parentPath, childPath) {
  const parent = path.resolve(parentPath);
  const child = path.resolve(childPath);
  return child === parent || child.startsWith(parent + path.sep);
}

function assertOutsideSkillRoot(targetPath, label = "output path") {
  if (isSubpath(skillRoot, targetPath)) {
    throw new Error(
      `Refusing to use ${label} inside the skill directory: ${path.resolve(
        targetPath
      )}`
    );
  }
}

function run(command, args, cwd) {
  const result =
    process.platform === "win32"
      ? spawnSync(
          process.env.ComSpec || "cmd.exe",
          ["/d", "/s", "/c", [command, ...args].map(quoteArg).join(" ")],
          {
            cwd,
            stdio: "inherit",
            shell: false,
          }
        )
      : spawnSync(command, args, {
          cwd,
          stdio: "inherit",
          shell: false,
        });

  if (result.status !== 0) {
    throw new Error(
      `Command failed: ${[command, ...args].join(" ")} (cwd: ${cwd})`
    );
  }
}

function ensureDesignerReady(options = {}) {
  const ifNeeded = Boolean(options.ifNeeded);

  if (!exists(path.join(exporterRoot, "package.json"))) {
    throw new Error(`Nested exporter package is missing: ${exporterRoot}`);
  }

  const exporterNodeModules = path.join(exporterRoot, "node_modules");
  const needsInstall = !exists(exporterNodeModules);
  const needsBuild = !exists(exporterBundle) || !exists(exporterNodeBundle);

  if (!ifNeeded || needsInstall) {
    console.log("[setup-designer] Installing nested html-to-pptx dependencies...");
    run(npmCommand(), ["install"], exporterRoot);
  }

  if (!ifNeeded || needsBuild) {
    console.log("[setup-designer] Building dom-to-pptx bundle...");
    run(npmCommand(), ["run", "build"], exporterRoot);
  }

  if (!exists(exporterBundle) || !exists(exporterNodeBundle)) {
    throw new Error(
      "html-to-pptx build did not produce the required bundle files."
    );
  }

  console.log("[setup-designer] Exporter is ready.");
}

if (require.main === module) {
  try {
    ensureDesignerReady({ ifNeeded: process.argv.includes("--if-needed") });
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}

module.exports = {
  ensureDesignerReady,
  assertOutsideSkillRoot,
  exporterRoot,
  exporterBundle,
  exporterNodeBundle,
  skillRoot,
};
