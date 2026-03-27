#!/usr/bin/env node

/**
 * HTML 到 PPTX 转换脚本
 * 使用 Playwright 无头浏览器运行 dom-to-pptx
 * 支持单文件转换和目录批量转换
 */

import { chromium } from 'playwright';
import { readFile, writeFile, readdir, stat, mkdir } from 'fs/promises';
import { existsSync } from 'fs';
import { resolve, dirname, join, basename } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 全局浏览器实例（复用）
let sharedBrowser = null;

/**
 * 主函数：HTML 文件或目录转 PPTX
 * 支持单文件转换和目录批量转换
 */
async function convertHtmlToPptx(inputPath, outputPath, options = {}) {
  const inputStat = await stat(inputPath);

  if (inputStat.isDirectory()) {
    return await convertDirectory(inputPath, outputPath, options);
  } else {
    return await convertSingleFile(inputPath, outputPath, options);
  }
}

/**
 * 单文件转换
 */
async function convertSingleFile(htmlPath, outputPath, options = {}) {
  const {
    selector = '.ppt-slide',
    slideWidth = 10,
    slideHeight = 5.625,
    timeout = 60000,
    reuseBrowser = true
  } = options;

  console.log(`📄 读取 HTML 文件: ${htmlPath}`);
  const html = await readFile(htmlPath, 'utf-8');

  console.log('🚀 启动浏览器...');
  const { browser, shouldCloseBrowser } = await getBrowser(reuseBrowser);

  const page = await browser.newPage();
  page.setDefaultTimeout(timeout);

  try {
    console.log('📝 注入 HTML 内容...');
    // 使用 'load' 替代 'networkidle'，避免因 CDN 资源加载慢而卡住
    await page.setContent(html, { waitUntil: 'load' });

    console.log('📦 加载依赖库...');
    await injectDependencies(page);

    console.log('🔄 执行转换...');
    const pptxArray = await page.evaluate(async ({ sel, opts }) => {
      const { exportToPptx } = window.domToPptx;
      // 使用 querySelectorAll 获取所有匹配的元素
      const elements = Array.from(document.querySelectorAll(sel));
      if (elements.length === 0) {
        throw new Error(`未找到匹配选择器 "${sel}" 的元素`);
      }
      console.log(`找到 ${elements.length} 个幻灯片元素`);
      // 传递元素数组给 exportToPptx
      const blob = await exportToPptx(elements, opts);
      const arrayBuffer = await blob.arrayBuffer();
      return Array.from(new Uint8Array(arrayBuffer));
    }, {
      sel: selector,
      opts: { slideWidth, slideHeight }
    });

    const pptxBuffer = Buffer.from(pptxArray);

    await mkdir(dirname(outputPath), { recursive: true });
    console.log(`💾 保存 PPTX: ${outputPath}`);
    await writeFile(outputPath, pptxBuffer);

    console.log(`✅ 转换完成！文件大小: ${(pptxBuffer.length / 1024).toFixed(2)} KB`);

  } catch (error) {
    console.error('❌ 转换失败:', error.message);
    throw error;
  } finally {
    await page.close();
    if (shouldCloseBrowser) {
      await closeBrowser();
    }
  }
}

/**
 * 获取或创建浏览器实例
 */
async function getBrowser(reuseBrowser = true) {
  let browser = sharedBrowser;
  let shouldCloseBrowser = false;

  if (!browser) {
    browser = await chromium.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    if (reuseBrowser) {
      sharedBrowser = browser;
    } else {
      shouldCloseBrowser = true;
    }
  }

  return { browser, shouldCloseBrowser };
}

/**
 * 目录批量转换
 * 扫描目录中的 page-N.pptx.html 文件，逐页在完整 HTML 环境中独立渲染
 * （Tailwind 编译、脚本执行、图表渲染），提取编译后 CSS + 处理后 HTML，
 * 通过 scoped wrapper 隔离各页样式，最终合并转换为单个 PPTX
 */
async function convertDirectory(dirPath, outputPath, options = {}) {
  const {
    selector = '.ppt-slide',
    slideWidth = 10,
    slideHeight = 5.625,
    timeout = 60000,
    reuseBrowser = true
  } = options;

  // 查找所有页面文件
  const files = await findPageFiles(dirPath);
  if (files.length === 0) {
    throw new Error(`目录 ${dirPath} 中未找到 page-N.pptx.html 文件`);
  }
  console.log(`📂 找到 ${files.length} 个页面文件`);

  const { browser, shouldCloseBrowser } = await getBrowser(reuseBrowser);
  // 收集每页的 { scopedCss, slideHtmls }
  const pageResults = [];

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const pageIndex = i + 1;
    console.log(`📄 渲染页面 ${pageIndex}/${files.length}: ${basename(file)}`);

    const page = await browser.newPage();
    page.setDefaultTimeout(timeout);

    try {
      const html = await readFile(file, 'utf-8');

      // 1. 在完整 HTML 环境中加载（Tailwind CDN 编译、脚本执行）
      await page.setContent(html, { waitUntil: 'load' });

      // 2. 等待 networkidle，确保 CDN 资源和脚本加载完成
      try {
        await page.waitForLoadState('networkidle', { timeout: 15000 });
      } catch {
        console.log(`  ⏳ 页面 ${pageIndex} networkidle 超时，继续处理...`);
      }

      // 3. 额外等待，确保 ECharts 等图表完成渲染
      await page.waitForTimeout(1000);

      // 4. 在浏览器端提取编译后 CSS + 处理后 slide HTML
      const result = await page.evaluate(({ sel, pageIdx }) => {
        // --- 提取所有编译后的 CSS ---
        const cssTexts = [];
        // 收集跨域样式表的 href（如 FontAwesome CDN），这些无法读取 cssRules
        const externalLinks = [];
        for (const sheet of document.styleSheets) {
          try {
            const rules = sheet.cssRules || sheet.rules;
            if (!rules) continue;
            for (const rule of rules) {
              cssTexts.push(rule.cssText);
            }
          } catch {
            // 跨域样式表无法访问，记录 href 以便在合并文档中引入
            if (sheet.href) {
              externalLinks.push(sheet.href);
            }
          }
        }

        // --- 给 CSS 规则添加 scope 前缀 ---
        const scopeAttr = `data-page-${pageIdx}`;
        const scopedRules = cssTexts.map(rule => {
          // 跳过 @keyframes / @font-face 等 at-rule（不加前缀）
          if (rule.startsWith('@keyframes') || rule.startsWith('@font-face')) {
            return rule;
          }
          // @media 等需要处理内部规则
          if (rule.startsWith('@media') || rule.startsWith('@supports') || rule.startsWith('@layer')) {
            return rule.replace(/([^{}]+)\{/g, (match, selectorPart, offset) => {
              // 第一个 { 是 at-rule 本身，不处理
              if (offset === rule.indexOf('{')) return match;
              // 内部选择器加 scope
              return scopeSelector(selectorPart, scopeAttr) + '{';
            });
          }
          // 普通规则：提取选择器部分加 scope
          const braceIdx = rule.indexOf('{');
          if (braceIdx === -1) return rule;
          const selectorPart = rule.substring(0, braceIdx);
          const rest = rule.substring(braceIdx);
          return scopeSelector(selectorPart, scopeAttr) + rest;
        });

        function scopeSelector(selectorText, attr) {
          // 多个选择器用逗号分隔，每个都加 scope
          return selectorText.split(',').map(s => {
            s = s.trim();
            if (!s) return s;
            // 对 *, html, body, :root 等全局选择器，替换为 scope wrapper
            if (s === '*' || s === '::before' || s === '::after'
                || s === '*, ::before, ::after' || s === ':root'
                || s === 'html' || s === 'body') {
              return `[${attr}] ${s === 'html' || s === 'body' || s === ':root' ? '' : s}`.trim() || `[${attr}]`;
            }
            // 其他选择器：在最前面加 scope 属性选择器
            return `[${attr}] ${s}`;
          }).join(', ');
        }

        // --- 将 ECharts canvas 转为 base64 图片 ---
        function convertCanvasToImage(container) {
          const canvases = container.querySelectorAll('canvas');
          canvases.forEach(canvas => {
            try {
              const dataUrl = canvas.toDataURL('image/png');
              const img = document.createElement('img');
              img.src = dataUrl;
              // 继承 canvas 容器的尺寸
              const rect = canvas.getBoundingClientRect();
              img.style.width = rect.width + 'px';
              img.style.height = rect.height + 'px';
              img.style.display = 'block';
              canvas.parentNode.replaceChild(img, canvas);
            } catch (e) {
              console.warn('Canvas 转图片失败:', e.message);
            }
          });
        }

        // --- 提取 slide HTML ---
        const slides = document.querySelectorAll(sel);
        const slideHtmls = Array.from(slides).map(slide => {
          convertCanvasToImage(slide);
          return slide.outerHTML;
        });

        return {
          scopedCss: scopedRules.join('\n'),
          slideHtmls,
          externalLinks
        };
      }, { sel: selector, pageIdx: pageIndex });

      if (result.slideHtmls.length === 0) {
        console.warn(`⚠️ 文件 ${basename(file)} 中未找到 ${selector} 元素`);
      } else {
        console.log(`  ✓ 提取 ${result.slideHtmls.length} 个幻灯片，CSS ${(result.scopedCss.length / 1024).toFixed(1)}KB`);
        pageResults.push({
          pageIndex,
          scopedCss: result.scopedCss,
          slideHtmls: result.slideHtmls,
          externalLinks: result.externalLinks
        });
      }
    } finally {
      await page.close();
    }
  }

  const totalSlides = pageResults.reduce((sum, p) => sum + p.slideHtmls.length, 0);
  console.log(`🎨 共收集 ${totalSlides} 个幻灯片，开始合并转换...`);

  // 构建合并 HTML：每页 slide 包裹在 scoped wrapper 中，CSS 通过 scope 属性隔离
  const mergedHtml = buildMergedHtml(pageResults);

  const page = await browser.newPage();
  page.setDefaultTimeout(timeout);

  try {
    await page.setContent(mergedHtml, { waitUntil: 'load' });
    await injectDependencies(page);

    const pptxArray = await page.evaluate(async ({ sel, opts }) => {
      const { exportToPptx } = window.domToPptx;
      const elements = Array.from(document.querySelectorAll(sel));
      const blob = await exportToPptx(elements, opts);
      const arrayBuffer = await blob.arrayBuffer();
      return Array.from(new Uint8Array(arrayBuffer));
    }, {
      sel: selector,
      opts: { slideWidth, slideHeight }
    });

    const pptxBuffer = Buffer.from(pptxArray);

    await mkdir(dirname(outputPath), { recursive: true });
    console.log(`💾 保存 PPTX: ${outputPath}`);
    await writeFile(outputPath, pptxBuffer);

    console.log(`✅ 转换完成！文件大小: ${(pptxBuffer.length / 1024).toFixed(2)} KB`);

  } catch (error) {
    console.error('❌ 目录转换失败:', error.message);
    throw error;
  } finally {
    await page.close();
    if (shouldCloseBrowser) {
      await closeBrowser();
    }
  }
}

/**
 * 扫描目录中的页面文件并按序号排序
 */
async function findPageFiles(dirPath) {
  const entries = await readdir(dirPath, { withFileTypes: true });
  const files = entries
    .filter(e => e.isFile() && /^page-(\d+)\.pptx\.html$/.test(e.name))
    .map(e => ({
      path: join(dirPath, e.name),
      num: parseInt(e.name.match(/^page-(\d+)/)[1])
    }))
    .sort((a, b) => a.num - b.num)
    .map(f => f.path);
  return files;
}

/**
 * 构建合并的 HTML 文档
 * 每页的 CSS 通过 [data-page-N] 属性选择器隔离，避免不同页面的
 * Tailwind 配置、自定义样式互相冲突
 */
function buildMergedHtml(pageResults) {
  // 收集所有 scoped CSS
  const allCss = pageResults.map(p => `/* === Page ${p.pageIndex} === */\n${p.scopedCss}`).join('\n\n');

  // 收集所有外部样式表链接（去重），如 FontAwesome CDN
  const allExternalLinks = [...new Set(pageResults.flatMap(p => p.externalLinks))];
  const linkTags = allExternalLinks.map(href => `  <link href="${href}" rel="stylesheet" />`).join('\n');

  // 每页的 slide 包裹在带 scope 属性的 wrapper 中
  const allSlides = pageResults.map(p => {
    const attr = `data-page-${p.pageIndex}`;
    return p.slideHtmls.map(html => `<div ${attr}>\n${html}\n</div>`).join('\n');
  }).join('\n');

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
${linkTags}
  <style>
    body { background: #1a1a2e; margin: 0; padding: 40px; }
${allCss}
  </style>
</head>
<body>
${allSlides}
</body>
</html>`;
}

/**
 * 注入依赖库
 */
function resolveBundlePath() {
  const candidates = [
    resolve(__dirname, 'dist/dom-to-pptx.bundle.js'),
    resolve(__dirname, '../dist/dom-to-pptx.bundle.js')
  ];

  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return candidate;
    }
  }

  throw new Error(
    `Missing dom-to-pptx bundle. Expected one of: ${candidates.join(', ')}`
  );
}

/**
 * 注入依赖库
 */
async function injectDependencies(page) {
  // 设置字体嵌入配置（WASM 文件 URL）
  await page.addInitScript(() => {
    window.EMBED_FONTS_CONFIG = {
      woff2: { wasmUrl: 'https://unpkg.com/fonteditor-core@2.4.1/woff2/woff2.wasm' }
    };
  });

  // 使用已打包的 dom-to-pptx bundle
  const bundlePath = resolveBundlePath();
  const bundleCode = await readFile(bundlePath, 'utf-8');

  // 注入打包后的代码
  // 使用 evaluate 直接执行脚本，而不是 addScriptTag
  await page.evaluate(bundleCode);

  // 等待模块加载完成
  try {
    await page.waitForFunction(() => typeof window.domToPptx?.exportToPptx === 'function', { timeout: 5000 });
  } catch (e) {
    // 调试：检查 window.domToPptx 的值
    const domToPptxExists = await page.evaluate(() => typeof window.domToPptx !== 'undefined');
    const exportToPptxExists = await page.evaluate(() => typeof window.domToPptx?.exportToPptx !== 'undefined');
    console.log('Debug: window.domToPptx exists:', domToPptxExists);
    console.log('Debug: window.domToPptx.exportToPptx exists:', exportToPptxExists);
    throw e;
  }
}

/**
 * 关闭共享浏览器
 */
async function closeBrowser() {
  if (sharedBrowser) {
    await sharedBrowser.close();
    sharedBrowser = null;
  }
}

/**
 * CLI 入口
 */
async function main() {
  const args = process.argv.slice(2);

  if (args.length === 0) {
    console.log(`
用法: node convert.js <输入路径> [输出文件] [选项]

参数:
  输入路径      输入的 HTML 文件路径或包含 page-N.pptx.html 的目录
  输出文件      输出的 PPTX 文件路径（可选，默认为同名 .pptx 或目录名.pptx）

选项:
  --selector    CSS 选择器（默认: .ppt-slide）
  --width       幻灯片宽度（英寸，默认: 10）
  --height      幻灯片高度（英寸，默认: 5.625）
  --timeout     超时时间（毫秒，默认: 60000）

示例:
  # 单文件转换
  node convert.js input.html
  node convert.js input.html output.pptx
  node convert.js input.html --selector=".slide" --width=10

  # 目录批量转换（合并为单个 PPTX）
  node convert.js ./pages/
  node convert.js ./pages/ output.pptx
    `);
    process.exit(0);
  }

  const inputPath = resolve(args[0]);
  let outputPath = args[1];

  // 解析选项
  const options = {};
  for (const arg of args.slice(2)) {
    if (arg.startsWith('--')) {
      const [key, value] = arg.slice(2).split('=');
      options[key] = value || true;
    }
  }

  // 默认输出路径
  if (!outputPath || outputPath.startsWith('--')) {
    const inputStat = await stat(inputPath);
    if (inputStat.isDirectory()) {
      outputPath = join(inputPath, 'pages.pptx');
    } else {
      // 文件输入：输出到同名 .pptx
      outputPath = inputPath.replace(/\.pptx\.html$/, '.pptx').replace(/\.html$/, '.pptx');
    }
  }
  outputPath = resolve(outputPath);

  try {
    await convertHtmlToPptx(inputPath, outputPath, options);
  } catch (error) {
    console.error('转换失败:', error);
    process.exit(1);
  } finally {
    await closeBrowser();
  }
}

// 强制执行 main
main().catch(err => {
  console.error('执行失败:', err);
  process.exit(1);
});

export { convertHtmlToPptx, closeBrowser };
