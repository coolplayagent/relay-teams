const { rectIntersectionArea, isAncestor } = require("./qa_utils");

function detectTextOverlap(textElements, minOverlapArea = 16) {
  const textOverlaps = [];

  for (let i = 0; i < textElements.length; i++) {
    for (let j = i + 1; j < textElements.length; j++) {
      const a = textElements[i];
      const b = textElements[j];

      if (isAncestor(a.el, b.el) || isAncestor(b.el, a.el)) continue;

      const area = rectIntersectionArea(a.rect, b.rect);
      const horizontalOverlap = Math.max(
        0,
        Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left)
      );
      if (horizontalOverlap <= 0) continue;

      const aHeight = a.rect.bottom - a.rect.top;
      const bHeight = b.rect.bottom - b.rect.top;
      const verticalDistance = Math.abs(a.rect.top - b.rect.top);
      const maxHeight = Math.max(aHeight, bHeight);
      if (verticalDistance >= maxHeight * 0.5) continue;

      if (area > minOverlapArea) {
        textOverlaps.push({
          textA: a.text,
          textB: b.text,
          overlapArea: Math.round(area),
        });
      }
    }
  }

  return textOverlaps;
}

module.exports = {
  rectIntersectionArea,
  isAncestor,
  detectTextOverlap,
};
