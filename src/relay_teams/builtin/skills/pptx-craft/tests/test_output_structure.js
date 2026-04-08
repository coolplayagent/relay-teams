#!/usr/bin/env node
/**
 * 验证 PPT Pipeline 输出目录结构
 */

const fs = require("fs");
const path = require("path");

const TIMESTAMP_PATTERN = /^\d{8}_\d{6}_\d{3}$/;

function validateOutputStructure(outputBase = "output", latestOnly = false) {
  const outputPath = path.resolve(outputBase);

  // 检查输出目录是否存在
  if (!fs.existsSync(outputPath)) {
    console.log("[OK] 输出目录不存在（首次运行）");
    return true;
  }

  // 检查是否有直接放在 output/pages 的情况（错误）
  const wrongPages = path.join(outputPath, "pages");
  if (fs.existsSync(wrongPages) && fs.statSync(wrongPages).isDirectory()) {
    const htmlFiles = fs.readdirSync(wrongPages).filter((f) => f.endsWith(".pptx.html"));
    if (htmlFiles.length > 0) {
      console.log(`[ERROR] 发现 ${wrongPages} 包含 ${htmlFiles.length} 个页面文件`);
      return false;
    }
  }

  // 检查时间戳目录结构
  const entries = fs.readdirSync(outputPath, { withFileTypes: true });
  const timestampDirs = entries
    .filter((e) => e.isDirectory() && TIMESTAMP_PATTERN.test(e.name))
    .map((e) => e.name);

  if (timestampDirs.length === 0) {
    console.log("[WARN] 没有找到时间戳目录");
    return true; // 可能还没有生成
  }

  // 按名称排序（时间戳字典序即时间顺序）
  timestampDirs.sort();

  const dirsToCheck = latestOnly ? [timestampDirs[timestampDirs.length - 1]] : timestampDirs;

  if (latestOnly) {
    console.log(`检查最新时间戳目录: ${dirsToCheck[0]}`);
  }

  let allValid = true;
  for (const tsDir of dirsToCheck) {
    const pagesDir = path.join(outputPath, tsDir, "pages");
    if (fs.existsSync(pagesDir) && fs.statSync(pagesDir).isDirectory()) {
      const pages = fs.readdirSync(pagesDir).filter((f) => f.startsWith("page-") && f.endsWith(".pptx.html"));
      console.log(`[OK] ${tsDir}/pages/ 包含 ${pages.length} 个页面文件`);
    } else {
      console.log(`[WARN] ${tsDir}/ 下没有 pages 目录`);
      allValid = false;
    }
  }

  return allValid;
}

function main() {
  const args = process.argv.slice(2);
  let outputBase = "output";
  let latestOnly = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--help") {
      console.log("验证 PPT Pipeline 输出目录结构");
      console.log("");
      console.log("用法: node test_output_structure.js [选项]");
      console.log("");
      console.log("选项:");
      console.log("  --output-base <路径>  输出目录基础路径 (默认: output)");
      console.log("  --latest            只检查最新的时间戳目录");
      console.log("  --help              显示帮助信息");
      process.exit(0);
    } else if (args[i] === "--output-base" && i + 1 < args.length) {
      outputBase = args[++i];
    } else if (args[i] === "--latest") {
      latestOnly = true;
    }
  }

  const success = validateOutputStructure(outputBase, latestOnly);
  process.exit(success ? 0 : 1);
}

main();
