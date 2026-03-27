const pixelBlank = require("./qa_pixel_blank");

const DEFAULT_CONFIG = {
  recursiveBlankThreshold: 0.4,
  minElementAreaRatio: 0.05,
};

function cropImageFromScreenshot(fullScreenshot, rect, slideWidth, slideHeight) {
  const { PNG } = require("pngjs");

  try {
    const fullPng = PNG.sync.read(fullScreenshot);
    const cropLeft = Math.max(0, Math.floor(rect.left));
    const cropTop = Math.max(0, Math.floor(rect.top));
    const cropRight = Math.min(slideWidth, Math.ceil(rect.right));
    const cropBottom = Math.min(slideHeight, Math.ceil(rect.bottom));
    const cropWidth = cropRight - cropLeft;
    const cropHeight = cropBottom - cropTop;

    if (cropWidth <= 0 || cropHeight <= 0) {
      return null;
    }

    const croppedPng = new PNG({ width: cropWidth, height: cropHeight });
    for (let y = 0; y < cropHeight; y++) {
      for (let x = 0; x < cropWidth; x++) {
        const srcIdx = ((cropTop + y) * fullPng.width + (cropLeft + x)) * 4;
        const dstIdx = (y * cropWidth + x) * 4;

        if (srcIdx < fullPng.data.length) {
          croppedPng.data[dstIdx] = fullPng.data[srcIdx];
          croppedPng.data[dstIdx + 1] = fullPng.data[srcIdx + 1];
          croppedPng.data[dstIdx + 2] = fullPng.data[srcIdx + 2];
          croppedPng.data[dstIdx + 3] = fullPng.data[srcIdx + 3];
        }
      }
    }

    return PNG.sync.write(croppedPng);
  } catch (error) {
    console.error(`[recursive_blank] Failed to crop image: ${error.message}`);
    return null;
  }
}

function getElementSelector(elementInfo) {
  if (elementInfo.id) {
    return `#${elementInfo.id}`;
  }

  let selector = elementInfo.tagName.toLowerCase();
  if (elementInfo.className && typeof elementInfo.className === "string") {
    const classes = elementInfo.className.trim().split(/\s+/).slice(0, 2).join(".");
    if (classes) selector += `.${classes}`;
  }

  return selector;
}

function detectElementRecursively(
  element,
  fullScreenshot,
  config,
  slideWidth,
  slideHeight,
  slideArea,
  parentPath = ""
) {
  const results = [];
  const elementArea = element.width * element.height;
  const minArea = slideArea * config.minElementAreaRatio;

  if (elementArea < minArea) return results;

  const elementScreenshot = cropImageFromScreenshot(
    fullScreenshot,
    {
      left: element.left,
      top: element.top,
      right: element.right,
      bottom: element.bottom,
    },
    slideWidth,
    slideHeight
  );

  if (!elementScreenshot) return results;

  const blankInfo = pixelBlank.analyze(elementScreenshot);
  const selector = parentPath
    ? `${parentPath} > ${getElementSelector(element)}`
    : getElementSelector(element);

  if (blankInfo.blankRatio > config.recursiveBlankThreshold) {
    results.push({
      selector,
      rect: {
        left: Math.round(element.left),
        top: Math.round(element.top),
        width: Math.round(element.width),
        height: Math.round(element.height),
      },
      blankRatio: Math.round(blankInfo.blankRatio * 1000) / 10,
      backgroundColor: blankInfo.backgroundColor,
      stopped: true,
      reason: "blank ratio exceeds threshold",
    });
    return results;
  }

  if (element.children && element.children.length > 0) {
    for (const child of element.children) {
      results.push(
        ...detectElementRecursively(
          child,
          fullScreenshot,
          config,
          slideWidth,
          slideHeight,
          slideArea,
          selector
        )
      );
    }
  }

  return results;
}

async function detectRecursiveBlank(page, slide, options = {}) {
  const config = { ...DEFAULT_CONFIG, ...(options.config || {}) };
  const slideWidth = 1280;
  const slideHeight = 720;
  const slideArea = slideWidth * slideHeight;

  let screenshot = options.fullScreenshot;
  if (!screenshot) {
    await slide.scrollIntoViewIfNeeded();
    screenshot = await slide.screenshot();
  }

  const domTree = await page.evaluate(() => {
    function buildDomTree(el, maxDepth = 10, currentDepth = 0) {
      if (currentDepth >= maxDepth) return null;

      const style = window.getComputedStyle(el);
      if (
        style.display === "none" ||
        style.visibility === "hidden" ||
        style.opacity === "0"
      ) {
        return null;
      }

      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return null;

      const elementInfo = {
        tagName: el.tagName,
        className: el.className,
        id: el.id,
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      };

      const children = [];
      for (const child of el.children) {
        const childInfo = buildDomTree(child, maxDepth, currentDepth + 1);
        if (childInfo) children.push(childInfo);
      }

      if (children.length > 0) {
        elementInfo.children = children;
      }

      return elementInfo;
    }

    const slideEl = document.querySelector(".ppt-slide");
    if (!slideEl) return null;

    const children = [];
    for (const child of slideEl.children) {
      const childInfo = buildDomTree(child);
      if (childInfo) children.push(childInfo);
    }

    return children;
  });

  if (!domTree || domTree.length === 0) return [];

  const results = [];
  for (const rootElement of domTree) {
    results.push(
      ...detectElementRecursively(
        rootElement,
        screenshot,
        config,
        slideWidth,
        slideHeight,
        slideArea
      )
    );
  }

  return results;
}

function analyzeElementBlank(screenshot) {
  return pixelBlank.analyze(screenshot);
}

module.exports = {
  detectRecursiveBlank,
  analyzeElementBlank,
  cropImageFromScreenshot,
  DEFAULT_CONFIG,
};
