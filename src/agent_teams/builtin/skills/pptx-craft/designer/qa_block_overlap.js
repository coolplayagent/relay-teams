const {
  rectIntersectionArea,
  isAncestor,
  isBackgroundOrDecor,
} = require("./qa_utils");

function detectBlockOverlap(
  directChildren,
  subtreeBlocks,
  slideRect,
  overlapTol = 2,
  minOverlapArea = 16
) {
  const blockOverlaps = [];
  const { width: slideWidth, height: slideHeight } = slideRect;

  const filteredDirectChildren = directChildren.filter(
    (directChild) => !isBackgroundOrDecor(directChild, slideWidth, slideHeight)
  );

  for (let i = 0; i < filteredDirectChildren.length; i++) {
    for (let j = i + 1; j < filteredDirectChildren.length; j++) {
      const area = rectIntersectionArea(
        filteredDirectChildren[i].rect,
        filteredDirectChildren[j].rect
      );
      if (area > minOverlapArea) {
        blockOverlaps.push({
          blockA: filteredDirectChildren[i].desc,
          blockB: filteredDirectChildren[j].desc,
          overlapArea: Math.round(area),
          level: "direct_children",
        });
      }
    }
  }

  for (let i = 0; i < subtreeBlocks.length; i++) {
    for (let j = i + 1; j < subtreeBlocks.length; j++) {
      if (subtreeBlocks[i].treeIdx === subtreeBlocks[j].treeIdx) continue;
      if (
        isAncestor(subtreeBlocks[i].el, subtreeBlocks[j].el) ||
        isAncestor(subtreeBlocks[j].el, subtreeBlocks[i].el)
      ) {
        continue;
      }

      const area = rectIntersectionArea(subtreeBlocks[i].rect, subtreeBlocks[j].rect);
      if (area > minOverlapArea) {
        blockOverlaps.push({
          blockA: subtreeBlocks[i].desc,
          blockB: subtreeBlocks[j].desc,
          overlapArea: Math.round(area),
          level: "cross_subtree",
        });
      }
    }
  }

  return blockOverlaps;
}

module.exports = {
  rectIntersectionArea,
  isAncestor,
  isBackgroundOrDecor,
  detectBlockOverlap,
};
