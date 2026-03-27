// 入口，barrel export

import * as PptxGenJSImport from 'pptxgenjs';
import html2canvas from 'html2canvas';

// FontAwesome SVG Core
import { icon, library } from '@fortawesome/fontawesome-svg-core';
import { fas } from '@fortawesome/free-solid-svg-icons';
import { far } from '@fortawesome/free-regular-svg-icons';
import { fab } from '@fortawesome/free-brands-svg-icons';

// Add all icons to library
library.add(fas, far, fab);

// Normalize import
const PptxGenJS = PptxGenJSImport?.default ?? PptxGenJSImport;

// 字体嵌入工具
import {
  getUsedFontFamilies,
  getAutoDetectedFonts,
  withPPTXEmbedFonts,
} from './utils.js';

// Re-export from utils
export {
  parseColor,
  getTextStyle,
  isTextContainer,
  detectLineBreaks,
  detectLineBreaksForTextNode,
  detectLineBreaksForInlineElement,
  detectLineBreakBetweenNodes,
  getVisibleShadow,
  generateGradientSVG,
  svgStringToPng,
  getRotation,
  svgToPng,
  svgToSvg,
  getPadding,
  getSoftEdges,
  generateBlurredSVG,
  getBorderInfo,
  generateCompositeBorderSVG,
  isClippedByParent,
  generateCustomShapeSVG,
  extractTableData,
} from './utils.js';

// Re-export from converter
export { getProcessedImage } from './converter/image.js';
export { processSlide } from './converter/slide.js';

// Re-export from parser
export { isNoWrap } from './parser/dom.js';

const PPI = 96;
const PX_TO_INCH = 1 / PPI;

/**
 * Main export function.
 * @param {HTMLElement | string | Array<HTMLElement | string>} target
 * @param {Object} options
 * @param {string} [options.fileName]
 * @param {boolean} [options.skipDownload=false] - If true, prevents automatic download
 * @param {Object} [options.listConfig] - Config for bullets
 * @param {boolean} [options.svgAsVector=false] - If true, keeps SVG as vector (for Convert to Shape in PowerPoint)
 * @param {boolean} [options.autoEmbedFonts=true] - If true, auto-detect and embed fonts used in the DOM
 * @param {Array<{name: string, url: string}>} [options.fonts] - Explicit fonts to embed
 * @param {string} [options.author] - Document author
 * @param {string} [options.company] - Company name
 * @param {string} [options.title] - Document title
 * @param {string} [options.subject] - Document subject
 * @returns {Promise<Blob>} - Returns the generated PPTX Blob
 */
export async function exportToPptx(target, options = {}) {
  // 默认启用字体嵌入
  console.log('[font] exportToPptx called, initial options:', options);
  options = { autoEmbedFonts: true, ...options };
  console.log('[font] After merging options:', options);
  // 动态导入 processSlide 以避免循环依赖
  const { processSlide } = await import('./converter/slide.js');

  const resolvePptxConstructor = (pkg) => {
    if (!pkg) return null;
    if (typeof pkg === 'function') return pkg;
    if (pkg && typeof pkg.default === 'function') return pkg.default;
    if (pkg && typeof pkg.PptxGenJS === 'function') return pkg.PptxGenJS;
    if (pkg && pkg.PptxGenJS && typeof pkg.PptxGenJS.default === 'function')
      return pkg.PptxGenJS.default;
    return null;
  };

  const PptxConstructor = resolvePptxConstructor(PptxGenJS);
  if (!PptxConstructor) throw new Error('PptxGenJS constructor not found.');
  console.log('[font] PptxConstructor resolved:', !!PptxConstructor);

  // 使用字体嵌入增强的 pptxgenjs 类
  console.log('[font] Loading withPPTXEmbedFonts...');
  const EnhancedPptx = await withPPTXEmbedFonts(PptxConstructor);
  console.log('[font] EnhancedPptx loaded:', !!EnhancedPptx, EnhancedPptx.name);
  const pptx = new EnhancedPptx();
  pptx.layout = 'LAYOUT_16x9';
  
  // 设置文档属性
  if (options.author) pptx.author = options.author;
  if (options.company) pptx.company = options.company;
  if (options.title) pptx.title = options.title;
  if (options.subject) pptx.subject = options.subject;
  
  console.log('[font] pptx instance created, has addFont:', typeof pptx.addFont);

  const elements = Array.isArray(target) ? target : [target];

  for (let i = 0; i < elements.length; i++) {
    const root = typeof elements[i] === 'string' ? document.querySelector(elements[i]) : elements[i];
    if (!root) {
      console.warn('Element not found, skipping slide:', elements[i]);
      continue;
    }
    const slide = pptx.addSlide();
    // 页码从 1 开始
    const pageNum = i + 1;
    await processSlide(root, slide, pptx, options, pageNum);
  }

  // 字体嵌入逻辑 - 预注册字体到增强类
  console.log('[font] Checking autoEmbedFonts: options.autoEmbedFonts =', options.autoEmbedFonts, typeof options.autoEmbedFonts);
  if (options.autoEmbedFonts) {
    console.log('[font] autoEmbedFonts enabled');

    // A. Scan DOM for used font families
    const usedFamilies = getUsedFontFamilies(elements);
    console.log('[font] Used families found:', Array.from(usedFamilies));

    // B. 获取可嵌入的字体（含二进制数据）
    console.log('[font] Fetching fonts from CDN...');
    const detectedFonts = await getAutoDetectedFonts(usedFamilies);
    console.log('[font] Detected fonts:', detectedFonts.length, detectedFonts.map(f => f.name));

    // C. 合并显式指定的字体
    const explicitFonts = options.fonts || [];
    for (const fontCfg of explicitFonts) {
      if (!fontCfg.buffer && fontCfg.url) {
        try {
          const response = await fetch(fontCfg.url);
          if (response.ok) {
            const buffer = await response.arrayBuffer();
            const ext = fontCfg.url.split('.').pop().split(/[?#]/)[0].toLowerCase();
            const type = ['woff', 'otf'].includes(ext) ? ext : 'ttf';
            detectedFonts.push({ name: fontCfg.name, buffer, type });
          }
        } catch (e) {
          console.warn(`Failed to fetch explicit font: ${fontCfg.name}`, e);
        }
      } else if (fontCfg.buffer) {
        detectedFonts.push(fontCfg);
      }
    }

    // D. 预注册字体到增强类
    for (const fontCfg of detectedFonts) {
      try {
        await pptx.addFont({
          fontFace: fontCfg.name,
          fontFile: fontCfg.buffer,
          fontType: fontCfg.type || 'ttf',
        });
        console.log(`[font] Registered: ${fontCfg.name}, buffer size: ${fontCfg.buffer.byteLength}, type: ${fontCfg.type}`);
      } catch (e) {
        console.warn(`Failed to register font: ${fontCfg.name}`, e);
      }
    }

    if (detectedFonts.length > 0) {
      console.log('[font] Total fonts to embed:', detectedFonts.map((f) => f.name));
      console.log('[font] _pptxEmbedFonts before write:', pptx._pptxEmbedFonts);
    } else {
      console.log('[font] No fonts to embed');
    }
  }

  // write() 会被 withPPTXEmbedFonts 覆盖，自动完成字体嵌入
  console.log('[font] Calling pptx.write()...');
  console.log('[font] pptx._pptxEmbedFonts fonts count:', pptx._pptxEmbedFonts?.fonts?.length);
  const finalBlob = await pptx.write({ outputType: 'blob' });
  console.log('[font] write() completed, blob size:', finalBlob?.size);

  // Output Handling
  // If skipDownload is NOT true, proceed with browser download
  if (!options.skipDownload) {
    const fileName = options.fileName || 'export.pptx';
    const url = URL.createObjectURL(finalBlob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // Always return the blob so the caller can use it (e.g. upload to server)
  return finalBlob;
}
