// 文本处理相关函数

import {
  getTextStyle as utilsGetTextStyle,
  isTextContainer as utilsIsTextContainer,
  detectLineBreaks as utilsDetectLineBreaks,
  detectLineBreaksForTextNode as utilsDetectLineBreaksForTextNode,
  detectLineBreaksForInlineElement as utilsDetectLineBreaksForInlineElement,
  detectLineBreakBetweenNodes as utilsDetectLineBreakBetweenNodes,
} from '../utils.js';

export function getTextStyle(node, layoutConfig) {
  return utilsGetTextStyle(node, layoutConfig);
}

export function isTextContainer(node) {
  return utilsIsTextContainer(node);
}

export function detectLineBreaks(element) {
  return utilsDetectLineBreaks(element);
}

export function detectLineBreaksForTextNode(textNode) {
  return utilsDetectLineBreaksForTextNode(textNode);
}

export function detectLineBreaksForInlineElement(element) {
  return utilsDetectLineBreaksForInlineElement(element);
}

export function detectLineBreakBetweenNodes(node1, node2) {
  return utilsDetectLineBreakBetweenNodes(node1, node2);
}
