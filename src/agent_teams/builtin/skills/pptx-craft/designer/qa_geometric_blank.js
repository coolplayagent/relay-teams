function computeUnionArea(rects) {
  if (rects.length === 0) return 0;

  const xCoords = new Set();
  for (const rect of rects) {
    xCoords.add(rect.left);
    xCoords.add(rect.right);
  }
  const sortedX = Array.from(xCoords).sort((a, b) => a - b);

  let totalArea = 0;

  for (let i = 0; i < sortedX.length - 1; i++) {
    const x1 = sortedX[i];
    const x2 = sortedX[i + 1];
    const stripWidth = x2 - x1;
    if (stripWidth <= 0) continue;

    const yIntervals = [];
    for (const rect of rects) {
      if (rect.left < x2 && rect.right > x1) {
        yIntervals.push([rect.top, rect.bottom]);
      }
    }

    yIntervals.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    let mergedHeight = 0;
    let curStart = -Infinity;
    let curEnd = -Infinity;

    for (const [ys, ye] of yIntervals) {
      if (ys > curEnd) {
        if (curEnd > curStart) mergedHeight += curEnd - curStart;
        curStart = ys;
        curEnd = ye;
      } else {
        curEnd = Math.max(curEnd, ye);
      }
    }
    if (curEnd > curStart) mergedHeight += curEnd - curStart;

    totalArea += stripWidth * mergedHeight;
  }

  return totalArea;
}

function calculateGeometricBlank(contentRects, slideArea) {
  const unionArea = computeUnionArea(contentRects);
  const coverageRatio = unionArea / slideArea;
  const blankRatio = Math.max(0, 1 - coverageRatio);

  return {
    coverageRatio,
    blankRatio,
    unionArea,
  };
}

module.exports = {
  computeUnionArea,
  calculateGeometricBlank,
};
