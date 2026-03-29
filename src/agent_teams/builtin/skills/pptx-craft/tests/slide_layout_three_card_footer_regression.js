#!/usr/bin/env node

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { detectLayoutIssues } = require("../designer/qa_core");

function makeTempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "pptx-craft-three-card-"));
}

function writeFixture(targetPath) {
  const html = `<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8" />
<style>
*{box-sizing:border-box;} body{margin:0;font-family:Arial,sans-serif;}
.ppt-slide{width:1280px;height:720px;overflow:hidden;background:#fff;position:relative;}
.header{height:92px;border-bottom:1px solid #d9d9d9;padding:0 42px;display:flex;align-items:center;}
.lead{position:absolute;left:56px;top:124px;width:1168px;min-height:82px;background:#fff1ef;border:1px solid #ffd0cb;padding:14px 20px;}
.lead h2{margin:0 0 6px 0;font-size:27px;line-height:1.35;}
.lead p{margin:0;font-size:16px;line-height:1.55;}
.grid{position:absolute;left:56px;top:236px;width:1168px;height:360px;display:grid;grid-template-columns:repeat(3,1fr);gap:20px;}
.card{border:1px solid #d9d9d9;padding:18px 18px 16px 18px;background:#fff;position:relative;}
.card h3{margin:10px 0 10px 0;font-size:22px;line-height:1.3;}
.card ul{margin:0;padding-left:18px;font-size:16px;line-height:1.58;}
.bottom{position:absolute;left:56px;bottom:26px;width:1168px;border-top:1px solid #e6e6e6;padding-top:10px;font-size:15px;color:#666;display:flex;justify-content:space-between;}
</style></head>
<body>
<div class="ppt-slide" type="content">
<div class="header">Title</div>
<div class="lead"><h2>今天的 AI 信息并不分散，而是收敛到政策边界、真实工程价值与 Agent 落地难点三个方向</h2><p>把零散新闻串起来看，会发现同一条主线：治理在前移，工程在算账，Agent 开始面对真正的系统问题。</p></div>
<div class="grid">
  <div class="card"><h3>Anthropic 与政策边界</h3><ul><li>法官裁定五角大楼不得封杀 Anthropic</li><li>AI 公司与政府边界公开化</li><li>安全、采购、治理开始交织</li></ul></div>
  <div class="card"><h3>AI 重写旧系统进入算账阶段</h3><ul><li>Reco.ai 宣称 7 小时重写 JSONata</li><li>社区开始拆 ROI 是否成立</li><li>vibe coding 从震惊走向审计</li></ul></div>
  <div class="card"><h3>Agent 焦点转向工程难题</h3><ul><li>不再只比模型能力上限</li><li>更关注数据、质量与稳定性</li><li>主矛盾从更强转向更可用</li></ul></div>
</div>
<div class="bottom"><div>一句话：今天的 AI 主线是治理前移 + 工程落地 + Agent 系统化</div><div>来源：AI Digest / 媒体跟进</div></div>
</div></body></html>`;
  fs.writeFileSync(targetPath, html, "utf8");
}

async function main() {
  const tempRoot = makeTempDir();
  const htmlPath = path.join(tempRoot, "page-1.pptx.html");
  try {
    writeFixture(htmlPath);
    const results = await detectLayoutIssues({ htmlPath });
    assert.ok(Array.isArray(results) && results.length === 1);
    const [result] = results;
    assert.ok(Array.isArray(result.overflows));
    assert.strictEqual(result.overflows.length, 0, "three-card layout should not overflow");
    assert.ok(Array.isArray(result.textOverlaps));
    assert.strictEqual(result.textOverlaps.length, 0, "three-card layout should not overlap text");
    assert.ok(Array.isArray(result.blockOverlaps));
    assert.strictEqual(result.blockOverlaps.length, 0, "three-card layout should not overlap blocks");
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
