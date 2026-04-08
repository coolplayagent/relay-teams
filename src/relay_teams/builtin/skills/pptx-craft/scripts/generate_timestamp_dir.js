#!/usr/bin/env node
// 生成带序号的时间戳目录

import fs from 'fs';
import path from 'path';

const baseDir = process.argv[2] || 'output';
const now = new Date();
const timestampPrefix = [
  now.getFullYear(),
  String(now.getMonth() + 1).padStart(2, '0'),
  String(now.getDate()).padStart(2, '0'),
  '_',
  String(now.getHours()).padStart(2, '0'),
  String(now.getMinutes()).padStart(2, '0'),
  String(now.getSeconds()).padStart(2, '0')
].join('');

// 确保基础目录存在
if (!fs.existsSync(baseDir)) {
  fs.mkdirSync(baseDir, { recursive: true });
}

// 查找同前缀的目录序号
let seq = 0;
while (fs.existsSync(path.join(baseDir, `${timestampPrefix}_${String(seq).padStart(3, '0')}`))) {
  seq++;
}

const timestampDir = path.join(baseDir, `${timestampPrefix}_${String(seq).padStart(3, '0')}`);
fs.mkdirSync(timestampDir, { recursive: true });

console.log(timestampDir);
