/**
 * 字体嵌入模块配置
 * 可通过 window.EMBED_FONTS_CONFIG 或全局变量覆盖
 */

const DEFAULT_CONFIG = {
  // woff2 WASM 文件的 CDN URL
  woff2: {
    wasmUrl: 'https://unpkg.com/fonteditor-core@2.6.3/woff2/woff2.wasm'
  }
};

/**
 * 获取配置项
 * @param {string} key - 配置键，支持点号分隔的路径（如 'woff2.wasmUrl'）
 * @param {any} [defaultValue] - 默认值
 * @returns {any}
 */
export function getConfig(key, defaultValue) {
  // 优先使用运行时配置
  const config = window.EMBED_FONTS_CONFIG || DEFAULT_CONFIG;
  const keys = key.split('.');
  let value = config;
  for (const k of keys) {
    if (value && typeof value === 'object' && k in value) {
      value = value[k];
    } else {
      return defaultValue !== undefined ? defaultValue : undefined;
    }
  }
  return value;
}

export { DEFAULT_CONFIG };
