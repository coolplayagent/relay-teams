// 形状处理相关函数

import {
  generateCustomShapeSVG as utilsGenerateCustomShapeSVG,
  generateGradientSVG as utilsGenerateGradientSVG,
  generateBlurredSVG as utilsGenerateBlurredSVG,
  getBorderInfo as utilsGetBorderInfo,
  generateCompositeBorderSVG as utilsGenerateCompositeBorderSVG,
} from '../utils.js';

export function generateCustomShapeSVG(options) {
  return utilsGenerateCustomShapeSVG(options.w, options.h, options.color, options.opacity, options.radii);
}

export function generateGradientSVG(color, direction) {
  return utilsGenerateGradientSVG(color, direction);
}

export function generateBlurredSVG(color, blur) {
  return utilsGenerateBlurredSVG(color, blur);
}

export function getBorderInfo(style, scale) {
  return utilsGetBorderInfo(style, scale);
}

export function generateCompositeBorderSVG(borderInfo) {
  return utilsGenerateCompositeBorderSVG(borderInfo);
}
