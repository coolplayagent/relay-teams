#!/usr/bin/env node
/**
 * 确保输出目录存在并返回绝对路径
 */

import fs from "fs";
import path from "path";

const OUTPUT_DIR = process.argv[2];

// 防护检查：拒绝已包含 pages 的路径
const resolvedPath = path.resolve(OUTPUT_DIR);
if (path.basename(resolvedPath) === "pages") {
  console.error("Error: Do not pass a path ending in 'pages' to this script");
  process.exit(1);
}

// 始终在传入目录下创建 "pages" 子目录
const pagesDir = path.join(OUTPUT_DIR, "pages");
fs.mkdirSync(pagesDir, { recursive: true });

// 验证目录存在
if (!fs.existsSync(pagesDir) || !fs.statSync(pagesDir).isDirectory()) {
  console.error(`Error: Failed to create directory ${pagesDir}`);
  process.exit(1);
}

// 返回创建的 pages 目录（而非传入的 OUTPUT_DIR）
console.log(path.resolve(pagesDir));
