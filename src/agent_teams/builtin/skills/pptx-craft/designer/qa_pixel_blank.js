const PIXEL_SAMPLE_CONFIG = {
  scaleRatio: 1 / 16,
};

function scalePNG(pngBuffer, ratio) {
  const { PNG } = require("pngjs");
  const png = PNG.sync.read(pngBuffer);
  const { width, height, data } = png;

  if (ratio >= 1) {
    return { width, height, data };
  }

  const newWidth = Math.max(1, Math.floor(width * ratio));
  const newHeight = Math.max(1, Math.floor(height * ratio));
  const newData = Buffer.alloc(newWidth * newHeight * 4);

  const blockWidth = width / newWidth;
  const blockHeight = height / newHeight;

  for (let y = 0; y < newHeight; y++) {
    for (let x = 0; x < newWidth; x++) {
      const srcXStart = Math.floor(x * blockWidth);
      const srcXEnd = Math.min(width, Math.floor((x + 1) * blockWidth));
      const srcYStart = Math.floor(y * blockHeight);
      const srcYEnd = Math.min(height, Math.floor((y + 1) * blockHeight));

      let rSum = 0;
      let gSum = 0;
      let bSum = 0;
      let aSum = 0;
      let count = 0;

      for (let sy = srcYStart; sy < srcYEnd; sy++) {
        for (let sx = srcXStart; sx < srcXEnd; sx++) {
          const srcIdx = (sy * width + sx) * 4;
          rSum += data[srcIdx];
          gSum += data[srcIdx + 1];
          bSum += data[srcIdx + 2];
          aSum += data[srcIdx + 3];
          count++;
        }
      }

      const dstIdx = (y * newWidth + x) * 4;
      if (count > 0) {
        newData[dstIdx] = Math.round(rSum / count);
        newData[dstIdx + 1] = Math.round(gSum / count);
        newData[dstIdx + 2] = Math.round(bSum / count);
        newData[dstIdx + 3] = Math.round(aSum / count);
      }
    }
  }

  return { width: newWidth, height: newHeight, data: newData };
}

function detectBackgroundColor(pngBuffer) {
  const { width, height, data } = scalePNG(
    pngBuffer,
    PIXEL_SAMPLE_CONFIG.scaleRatio
  );

  const colorMap = new Map();
  let validPixels = 0;

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = (y * width + x) * 4;
      const r = data[idx];
      const g = data[idx + 1];
      const b = data[idx + 2];
      const a = data[idx + 3];

      if (a < 200) continue;

      validPixels++;
      const key = `${r},${g},${b}`;
      colorMap.set(key, (colorMap.get(key) || 0) + 1);
    }
  }

  if (validPixels === 0 || colorMap.size === 0) {
    return {
      r: 255,
      g: 255,
      b: 255,
      coverage: 0,
      colorCount: 0,
      topColors: [],
    };
  }

  const sortedColors = Array.from(colorMap.entries())
    .map(([key, count]) => {
      const [r, g, b] = key.split(",").map(Number);
      return { r, g, b, count, pct: count / validPixels };
    })
    .sort((a, b) => b.count - a.count);

  const bg = sortedColors[0];
  return {
    r: bg.r,
    g: bg.g,
    b: bg.b,
    coverage: bg.pct,
    colorCount: colorMap.size,
    topColors: sortedColors.slice(0, 5),
  };
}

function calculateVisualCoverage(_pngBuffer, bgColor) {
  const coverageRatio = 1 - bgColor.coverage;
  return {
    coverageRatio,
    blankRatio: bgColor.coverage,
    note: "content density = 1 - background coverage",
  };
}

function analyze(pngBuffer) {
  const backgroundColor = detectBackgroundColor(pngBuffer);
  const coverage = calculateVisualCoverage(pngBuffer, backgroundColor);

  return {
    backgroundColor,
    coverageRatio: coverage.coverageRatio,
    blankRatio: coverage.blankRatio,
    pixelAnalysis: coverage,
  };
}

module.exports = {
  scalePNG,
  detectBackgroundColor,
  calculateVisualCoverage,
  analyze,
  PIXEL_SAMPLE_CONFIG,
};
