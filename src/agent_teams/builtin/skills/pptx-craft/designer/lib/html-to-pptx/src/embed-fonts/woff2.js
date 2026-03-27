/**
 * fonteditor-core/woff2 解码器 ESM 封装
 * fonteditor-core/woff2 是 CommonJS 模块，需要封装为 ESM 兼容形式
 */

import { getConfig } from './config.js';

let woff2Module = null;

/**
 * 初始化 woff2 解码器
 * @param {string} [wasmUrl] - WASM 文件 URL（可选，默认使用配置）
 * @returns {Promise<WOFF2Module>}
 */
export async function initWoff2(wasmUrl) {
  if (woff2Module) return woff2Module;
  // 使用动态 import 加载 fonteditor-core/woff2
  // 在浏览器环境中，这会在运行时从 CDN 加载
  // 使用 CDN URL 替代包名，避免 esbuild external 问题
  const cdnUrl = 'https://unpkg.com/fonteditor-core@2.6.3/woff2/index.js';
  const mod = await import(/* webpackIgnore: true */ cdnUrl);
  woff2Module = mod.default || mod;
  const url = wasmUrl || getConfig('woff2.wasmUrl');
  await woff2Module.init(url);
  return woff2Module;
}

/**
 * 解码 woff2 字体数据
 * @param {ArrayBuffer|Uint8Array} buffer - woff2 字体数据
 * @param {string} [wasmUrl] - WASM 文件 URL（可选，默认使用配置）
 * @returns {Promise<Uint8Array>} 解码后的 TTF 数据
 */
export async function decodeWoff2(buffer, wasmUrl) {
  const mod = await initWoff2(wasmUrl);
  return mod.decode(buffer);
}
