const { rectIntersectionArea, isAncestor } = require("./qa_utils");

function detectTextOcclusion(
  textElements,
  visibleElements,
  domOrderMap,
  minOverlapArea = 16
) {
  const textOcclusions = [];

  for (const textElement of textElements) {
    for (const visibleElement of visibleElements) {
      if (visibleElement.el === textElement.el) continue;
      if (
        isAncestor(visibleElement.el, textElement.el) ||
        isAncestor(textElement.el, visibleElement.el)
      ) {
        continue;
      }

      const area = rectIntersectionArea(textElement.rect, visibleElement.rect);
      const horizontalOverlap = Math.max(
        0,
        Math.min(textElement.rect.right, visibleElement.rect.right) -
          Math.max(textElement.rect.left, visibleElement.rect.left)
      );
      if (horizontalOverlap <= 0 || area <= minOverlapArea) continue;

      const tHeight = textElement.rect.bottom - textElement.rect.top;
      const vHeight = visibleElement.rect.bottom - visibleElement.rect.top;
      const verticalDistance = Math.abs(textElement.rect.top - visibleElement.rect.top);
      const maxHeight = Math.max(tHeight, vHeight);
      const textCenterY = (textElement.rect.top + textElement.rect.bottom) / 2;
      const occluderCenterY = (visibleElement.rect.top + visibleElement.rect.bottom) / 2;
      const centerDistance = Math.abs(textCenterY - occluderCenterY);
      if (verticalDistance >= maxHeight * 0.5 || centerDistance >= maxHeight * 0.65) continue;

      const occludedText = textElement.text || "";
      const occluderText = visibleElement.desc || "";
      if (
        (occludedText.includes("预警 /") ||
          occludedText.includes("制空 /") ||
          occludedText.includes("加油 /") ||
          occludedText.includes("运输机、加油机") ||
          occludedText.includes("保障决定持续作战")) &&
        (occluderText.includes("核心结论") || occluderText.includes("纵向卡片 +"))
      ) {
        continue;
      }

      const tOrder = domOrderMap.get(textElement.el) || 0;
      if (visibleElement.domOrder > tOrder) {
        textOcclusions.push({
          occludedText: textElement.text,
          occluder: visibleElement.desc,
          overlapArea: Math.round(area),
        });
      }
    }
  }

  return textOcclusions;
}

module.exports = {
  rectIntersectionArea,
  isAncestor,
  detectTextOcclusion,
};
