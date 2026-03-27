// src/parser/dom.js

/**
 * Check if an element has white-space that prevents wrapping.
 * @param {CSSStyleDeclaration} style
 * @returns {boolean}
 */
export function isNoWrap(style) {
  const ws = style.whiteSpace;
  // 'nowrap' and 'pre' prevent automatic wrapping
  // 'pre-wrap' preserves manual newlines but still wraps automatically, so we don't skip it
  return ws === 'nowrap' || ws === 'pre';
}
