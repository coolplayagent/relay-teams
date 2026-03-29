const fs = require("fs");
const path = require("path");

const geometricBlank = require("./qa_geometric_blank");
const pixelBlank = require("./qa_pixel_blank");
const slideOverflow = require("./qa_slide_overflow");
const textOcclusion = require("./qa_text_occlusion");
const elementOverlap = require("./qa_element_overlap");
const childOverflow = require("./qa_child_overflow");
const blockOverlap = require("./qa_block_overlap");
const recursiveBlank = require("./qa_recursive_blank");
const utils = require("./qa_utils");

const BUILTIN_DEFAULTS = {
  blankThreshold: 0.25,
  overflowTolerance: 2,
  textOverlapMinArea: 16,
  pixelBlankThreshold: 0.4,
  minTextSafePadding: 8,
  siblingAlignTolerance: 6,
  minSlideSafeMargin: 24,
};

function parseJsonc(content) {
  let cleaned = content.replace(/\/\/.*$/gm, "");
  cleaned = cleaned.replace(/\/\*[\s\S]*?\*\//g, "");
  return JSON.parse(cleaned);
}

function findConfigPath() {
  const searchPaths = [
    path.join(process.cwd(), "slide_layout_qa.config.jsonc"),
    path.join(__dirname, "slide_layout_qa.config.jsonc"),
  ];

  for (const configPath of searchPaths) {
    if (fs.existsSync(configPath)) {
      return configPath;
    }
  }
  return null;
}

function loadConfig(configPath) {
  const defaults = {
    ...BUILTIN_DEFAULTS,
    recursiveBlankThreshold: 0.4,
    minElementAreaRatio: 0.05,
    disableChildOverflow: false,
    disableTextOverlap: false,
    disableBlockOverlap: false,
  };

  const resolvedPath = configPath || findConfigPath();
  if (!resolvedPath) {
    return { ...defaults };
  }

  try {
    const fullPath = path.resolve(resolvedPath);
    const configData = parseJsonc(fs.readFileSync(fullPath, "utf8"));
    return { ...defaults, ...configData };
  } catch (error) {
    console.warn(`[qa_core] Failed to read config: ${error.message}`);
    return { ...defaults };
  }
}

function createBrowserContext() {
  return {
    rectIntersectionAreaStr: utils.rectIntersectionArea.toString(),
    isAncestorStr: utils.isAncestor.toString(),
    isBackgroundOrDecorStr: utils.isBackgroundOrDecor.toString(),
    detectSlideOverflowStr: slideOverflow.detectSlideOverflow.toString(),
    buildOverflowElementsStr: childOverflow.buildOverflowElements.toString(),
    detectChildOverflowStr: childOverflow.detectChildOverflow.toString(),
    detectTextOverlapStr: elementOverlap.detectTextOverlap.toString(),
    detectTextOcclusionStr: textOcclusion.detectTextOcclusion.toString(),
    detectBlockOverlapStr: blockOverlap.detectBlockOverlap.toString(),
    detectInBrowserStr: detectInBrowser.toString(),
  };
}

async function detectLayoutIssues(options) {
  const { htmlPath, config: providedConfig } = options;
  const config = providedConfig || loadConfig();
  const overflowTolerance = config.overflowTolerance || 2;
  const textOverlapMinArea = config.textOverlapMinArea || 16;

  let playwright;
  try {
    playwright = require("playwright");
  } catch (_error) {
    throw new Error("Playwright is not installed");
  }

  let browser;
  try {
    browser = await playwright.chromium.launch({ headless: true });
    const page = await browser.newPage();
    await page.setViewportSize({ width: 1400, height: 900 });

    const absolutePath = path.resolve(htmlPath);
    await page.goto(`file://${absolutePath}`, {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });
    await new Promise((resolve) => setTimeout(resolve, 3000));

    const slide = await page.locator(".ppt-slide").first();
    let pixelResults = [];
    let screenshot = null;

    try {
      const slideCount = await page.locator(".ppt-slide").count();
      if (slideCount === 0) {
        console.error("[qa_core] No .ppt-slide element found");
      }

      await slide.scrollIntoViewIfNeeded();
      await new Promise((resolve) => setTimeout(resolve, 100));

      const box = await slide.boundingBox();
      if (!box) {
        throw new Error("Unable to read slide bounds");
      }

      screenshot = await slide.screenshot();
      const backgroundColor = pixelBlank.detectBackgroundColor(screenshot);
      const coverage = pixelBlank.calculateVisualCoverage(screenshot, backgroundColor);
      const type = (await slide.getAttribute("type")) || "content";

      pixelResults.push({
        index: 0,
        type,
        backgroundColor,
        coverageRatio: coverage.coverageRatio,
        blankRatio: coverage.blankRatio,
        pixelAnalysis: coverage,
        dimensions: {
          width: Math.round(box.width),
          height: Math.round(box.height),
        },
      });
    } catch (error) {
      console.error(`[qa_core] Pixel analysis failed: ${error.message}`);
      pixelResults.push({
        index: 0,
        type: "content",
        error: true,
      });
    }

    const browserContext = createBrowserContext();

    const results = await page.evaluate(
      ({
        overflowTolerance,
        textOverlapMinArea,
        pixelResults,
        browserContext,
        disableChildOverflow,
        disableTextOverlap,
        disableBlockOverlap,
      }) => {
        eval(browserContext.rectIntersectionAreaStr);
        eval(browserContext.isAncestorStr);
        eval(browserContext.isBackgroundOrDecorStr);
        eval(browserContext.detectSlideOverflowStr);
        eval(browserContext.buildOverflowElementsStr);
        eval(browserContext.detectChildOverflowStr);
        eval(browserContext.detectTextOverlapStr);
        eval(browserContext.detectTextOcclusionStr);
        eval(browserContext.detectBlockOverlapStr);
        const slideOverflow = { detectSlideOverflow };
        const childOverflow = { buildOverflowElements, detectChildOverflow };
        eval(browserContext.detectInBrowserStr);
        return detectInBrowser(overflowTolerance, textOverlapMinArea, pixelResults, {
          disableChildOverflow,
          disableTextOverlap,
          disableBlockOverlap,
        });
      },
      {
        overflowTolerance,
        textOverlapMinArea,
        pixelResults,
        browserContext,
        disableChildOverflow: config.disableChildOverflow,
        disableTextOverlap: config.disableTextOverlap,
        disableBlockOverlap: config.disableBlockOverlap,
      }
    );

    const pixelData = pixelResults[0];
    if (pixelData && !pixelData.error && results[0]) {
      const type = pixelData.type || "content";
      const hasOverflow = results[0].overflows && results[0].overflows.length > 0;
      const geometryBlankRatio =
        results[0].pixelAnalysis &&
        results[0].pixelAnalysis.geometryCoverage !== undefined
          ? 100 - results[0].pixelAnalysis.geometryCoverage
          : 0;
      const hasGeometricBlank = geometryBlankRatio > config.blankThreshold * 100;
      const hasPixelBlank = pixelData.blankRatio > config.pixelBlankThreshold;

      if (type === "content" && !hasOverflow && !hasGeometricBlank && !hasPixelBlank) {
        const recursiveResults = await recursiveBlank.detectRecursiveBlank(page, slide, {
          fullScreenshot: screenshot,
          config: {
            recursiveBlankThreshold: config.recursiveBlankThreshold || 0.4,
            minElementAreaRatio: config.minElementAreaRatio || 0.05,
          },
        });

        if (recursiveResults.length > 0) {
          results[0].recursiveBlanks = recursiveResults;
        }
      }
    }

    return results;
  } finally {
    if (browser) {
      await browser.close();
    }
  }
}

function detectInBrowser(overflowTolerance, textOverlapMinArea, pixelResults, config = {}) {
  const disableChildOverflow = config.disableChildOverflow || false;
  const disableTextOverlap = config.disableTextOverlap || false;
  const disableBlockOverlap = config.disableBlockOverlap || false;
  const minTextSafePadding = config.minTextSafePadding || 8;
  const siblingAlignTolerance = config.siblingAlignTolerance || 6;
  const minSlideSafeMargin = config.minSlideSafeMargin || 24;

  function isBackgroundOrDecor(el, style, rect, slideRect) {
    return rect.width >= slideRect.width && rect.height >= slideRect.height;
  }

  function isVisualLeaf(el, style, visited = new Set()) {
    if (visited.has(el)) return false;
    visited.add(el);

    if (
      el.tagName === "IMG" ||
      el.tagName === "SVG" ||
      el.closest("svg") ||
      el.tagName === "CANVAS"
    ) {
      return true;
    }

    if (style.backgroundImage !== "none") return true;

    const hasText = el.innerText && el.innerText.trim().length > 0;
    if (!hasText) return false;

    for (const child of el.children) {
      if (child.innerText && child.innerText.trim().length > 0) {
        const bgColor = style.backgroundColor;
        const hasVisualDecoration =
          bgColor !== "rgba(0, 0, 0, 0)" &&
          bgColor !== "transparent" &&
          (parseFloat(style.borderTopWidth) > 0 ||
            parseFloat(style.borderBottomWidth) > 0 ||
            parseFloat(style.borderLeftWidth) > 0 ||
            parseFloat(style.borderRightWidth) > 0);

        return hasVisualDecoration;
      }
    }

    return true;
  }

  function isLeafTextNode(el) {
    const text = el.innerText && el.innerText.trim();
    if (!text) return false;
    for (const child of el.children) {
      if (child.innerText && child.innerText.trim().length > 0) {
        return false;
      }
    }
    return true;
  }

  function hasVisualContent(el, style) {
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.opacity === "0"
    ) {
      return false;
    }

    if (
      el.tagName === "IMG" ||
      el.tagName === "SVG" ||
      el.closest("svg") ||
      el.tagName === "CANVAS"
    ) {
      return true;
    }

    if (style.backgroundImage !== "none") return true;

    return Boolean(el.innerText && el.innerText.trim().length > 0);
  }

  function isAncestor(a, b) {
    let node = b.parentElement;
    while (node) {
      if (node === a) return true;
      node = node.parentElement;
    }
    return false;
  }

  function rectIntersectionArea(r1, r2) {
    const xOverlap = Math.max(
      0,
      Math.min(r1.right, r2.right) - Math.max(r1.left, r2.left)
    );
    const yOverlap = Math.max(
      0,
      Math.min(r1.bottom, r2.bottom) - Math.max(r1.top, r2.top)
    );
    return xOverlap * yOverlap;
  }

  function clipToSlide(rect, slideLeft, slideTop, slideWidth, slideHeight) {
    return {
      left: Math.max(0, rect.left - slideLeft),
      top: Math.max(0, rect.top - slideTop),
      right: Math.min(slideWidth, rect.right - slideLeft),
      bottom: Math.min(slideHeight, rect.bottom - slideTop),
    };
  }

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

  const slide = document.querySelector(".ppt-slide");
  if (!slide) return [];

  const slideRect = slide.getBoundingClientRect();
  const slideWidth = slideRect.width;
  const slideHeight = slideRect.height;
  const slideArea = slideWidth * slideHeight;
  const allElements = slide.querySelectorAll("*");

  const domOrderMap = new Map();
  let domIdx = 0;
  allElements.forEach((el) => domOrderMap.set(el, domIdx++));

  const pixelData = pixelResults.find((result) => result.index === 0);
  let coverageRatio = 0.5;
  let blankRatio = 0.5;
  let bgColorDebug = null;

  if (pixelData && !pixelData.error) {
    coverageRatio = pixelData.coverageRatio;
    blankRatio = pixelData.blankRatio;
    bgColorDebug = pixelData.backgroundColor;
  }

  const contentRects = [];
  const leafContentElements = [];

  allElements.forEach((el) => {
    const style = window.getComputedStyle(el);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.opacity === "0"
    ) {
      return;
    }

    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    if (isBackgroundOrDecor(el, style, rect, slideRect)) return;
    if (!isVisualLeaf(el, style)) return;

    leafContentElements.push(el);

    const clipped = clipToSlide(
      rect,
      slideRect.left,
      slideRect.top,
      slideWidth,
      slideHeight
    );
    if (clipped.right > clipped.left && clipped.bottom > clipped.top) {
      contentRects.push(clipped);
    }
  });

  const contentContainers = new Set();
  leafContentElements.forEach((leaf) => {
    let parent = leaf.parentElement;
    while (parent && parent !== slide) {
      const parentStyle = window.getComputedStyle(parent);
      const parentRect = parent.getBoundingClientRect();
      const hasBorder =
        parentStyle.borderWidth !== "0px" &&
        parentStyle.borderStyle !== "none";
      const hasBg =
        parentStyle.backgroundColor !== "rgba(0, 0, 0, 0)" &&
        parentStyle.backgroundColor !== "transparent";

      if (hasBorder || hasBg) {
        const areaRatio = (parentRect.width * parentRect.height) / slideArea;
        if (areaRatio < 0.7) {
          contentContainers.add(parent);
        }
      }
      parent = parent.parentElement;
    }
  });

  contentContainers.forEach((container) => {
    const rect = container.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const clipped = clipToSlide(
      rect,
      slideRect.left,
      slideRect.top,
      slideWidth,
      slideHeight
    );
    if (clipped.right > clipped.left && clipped.bottom > clipped.top) {
      contentRects.push(clipped);
    }
  });

  const unionArea = computeUnionArea(contentRects);
  const geometryCoverage = unionArea / slideArea;

  const finalCoverageRatio =
    pixelData && !pixelData.error ? coverageRatio : geometryCoverage;
  const finalBlankRatio =
    pixelData && !pixelData.error
      ? blankRatio
      : Math.max(0, 1 - geometryCoverage);

  const overflowElements = [];
  allElements.forEach((el) => {
    const style = window.getComputedStyle(el);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.opacity === "0" ||
      !hasVisualContent(el, style)
    ) {
      return;
    }

    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    overflowElements.push({
      el,
      rect,
      text: (el.innerText || "").trim().slice(0, 30) || `<${el.tagName.toLowerCase()}>`,
      style,
    });
  });

  let overflows = slideOverflow.detectSlideOverflow(
    overflowElements,
    slideRect,
    overflowTolerance
  );
  overflows = overflows.filter((overflow) => {
    const detailSides = (overflow.details || []).map((item) => item.side);
    const detailAmounts = (overflow.details || []).map((item) => item.amount || 0);
    const maxAmount = detailAmounts.length > 0 ? Math.max(...detailAmounts) : 0;
    const text = overflow.text || "";
    if (maxAmount <= minSlideSafeMargin && /^<div>$/.test(text)) {
      return false;
    }
    return true;
  });

  const clippedElements = [];
  overflowElements.forEach((element) => {
    let parent = element.el.parentElement;
    while (parent && parent !== slide) {
      const parentStyle = window.getComputedStyle(parent);
      const hasOverflowHidden =
        parentStyle.overflow === "hidden" ||
        parentStyle.overflowX === "hidden" ||
        parentStyle.overflowY === "hidden";

      if (hasOverflowHidden) {
        const parentRect = parent.getBoundingClientRect();
        const tol = 2;
        const details = [];

        if (element.rect.left < parentRect.left - tol) {
          details.push({ side: "left", amount: Math.round(parentRect.left - element.rect.left) });
        }
        if (element.rect.top < parentRect.top - tol) {
          details.push({ side: "top", amount: Math.round(parentRect.top - element.rect.top) });
        }
        if (element.rect.right > parentRect.right + tol) {
          details.push({ side: "right", amount: Math.round(element.rect.right - parentRect.right) });
        }
        if (element.rect.bottom > parentRect.bottom + tol) {
          details.push({ side: "bottom", amount: Math.round(element.rect.bottom - parentRect.bottom) });
        }

        if (details.length > 0) {
          const cls =
            typeof parent.className === "string"
              ? parent.className
              : parent.getAttribute("class");
          clippedElements.push({
            text: element.text,
            clippedBy: `<${parent.tagName.toLowerCase()}${cls ? "." + cls.split(" ")[0] : ""}>`,
            details,
          });
        }
        break;
      }

      parent = parent.parentElement;
    }
  });

  const textElements = [];
  allElements.forEach((el) => {
    const style = window.getComputedStyle(el);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.opacity === "0" ||
      !isLeafTextNode(el)
    ) {
      return;
    }

    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    textElements.push({
      el,
      rect: {
        left: rect.left - slideRect.left,
        top: rect.top - slideRect.top,
        right: rect.right - slideRect.left,
        bottom: rect.bottom - slideRect.top,
      },
      text: (el.innerText || "").trim().slice(0, 30),
    });
  });

  const textOverlaps = disableTextOverlap
    ? []
    : detectTextOverlap(textElements, textOverlapMinArea);

  const visibleElements = [];
  allElements.forEach((el) => {
    const style = window.getComputedStyle(el);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.opacity === "0" ||
      !isVisualLeaf(el, style)
    ) {
      return;
    }

    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    visibleElements.push({
      el,
      rect: {
        left: rect.left - slideRect.left,
        top: rect.top - slideRect.top,
        right: rect.right - slideRect.left,
        bottom: rect.bottom - slideRect.top,
      },
      domOrder: domOrderMap.get(el) || 0,
      desc: (el.innerText || "").trim().slice(0, 30) || `<${el.tagName.toLowerCase()}>`,
    });
  });

  const textOcclusions = detectTextOcclusion(
    textElements,
    visibleElements,
    domOrderMap,
    textOverlapMinArea
  );

  const childOverflowElements = childOverflow.buildOverflowElements(
    allElements,
    hasVisualContent,
    slide
  );
  const childOverflows = disableChildOverflow
    ? []
    : childOverflow.detectChildOverflow(childOverflowElements, slide, 2);

  const directChildren = [];
  for (const child of slide.children) {
    const style = window.getComputedStyle(child);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.opacity === "0"
    ) {
      continue;
    }

    const rect = child.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    if (rect.width >= slideWidth && rect.height >= slideHeight) continue;
    if (
      (rect.width >= slideWidth - 2 && rect.height <= 8) ||
      (rect.height >= slideHeight - 2 && rect.width <= 8)
    ) {
      continue;
    }

    const textContent = (child.innerText || "").trim();
    if (style.borderRadius === "50%" && textContent.length === 0) continue;
    if (
      style.borderRadius === "50%" &&
      parseFloat(style.opacity) < 0.7 &&
      textContent.length <= 2
    ) {
      continue;
    }

    directChildren.push({
      el: child,
      rect: {
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
      },
      desc: textContent.slice(0, 30) || `<${child.tagName.toLowerCase()}>`,
    });
  }

  const subtreeBlocks = [];
  directChildren.forEach((directChild, treeIdx) => {
    const descendants = directChild.el.querySelectorAll("*");
    descendants.forEach((el) => {
      const style = window.getComputedStyle(el);
      if (
        style.display === "none" ||
        style.visibility === "hidden" ||
        style.opacity === "0"
      ) {
        return;
      }
      if (!isVisualLeaf(el, style)) return;

      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;

      subtreeBlocks.push({
        el,
        rect: {
          left: rect.left,
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
        },
        treeIdx,
        desc: (el.innerText || "").trim().slice(0, 30) || `<${el.tagName.toLowerCase()}>`,
      });
    });
  });

  const blockOverlaps = disableBlockOverlap
    ? []
    : detectBlockOverlap(directChildren, subtreeBlocks, slideRect, 2, textOverlapMinArea);

  const textSafePaddingIssues = [];
  const seenTextContainerPairs = new Set();
  textElements.forEach((textElement) => {
    let parent = textElement.el.parentElement;
    while (parent && parent !== slide) {
      const parentStyle = window.getComputedStyle(parent);
      const borderWidths = [
        parseFloat(parentStyle.borderTopWidth) || 0,
        parseFloat(parentStyle.borderRightWidth) || 0,
        parseFloat(parentStyle.borderBottomWidth) || 0,
        parseFloat(parentStyle.borderLeftWidth) || 0,
      ];
      const borderSides = borderWidths.filter((width) => width > 0).length;
      const hasFullBorder = borderSides >= 3 && parentStyle.borderStyle !== "none";
      const bgColor = parentStyle.backgroundColor;
      const hasBg = bgColor !== "rgba(0, 0, 0, 0)" && bgColor !== "transparent";
      const parentRect = parent.getBoundingClientRect();
      const parentAreaRatio = (parentRect.width * parentRect.height) / slideArea;
      const paddingLeft = parseFloat(parentStyle.paddingLeft) || 0;
      const paddingTop = parseFloat(parentStyle.paddingTop) || 0;
      const paddingRight = parseFloat(parentStyle.paddingRight) || 0;
      const paddingBottom = parseFloat(parentStyle.paddingBottom) || 0;
      const hasRealPadding = Math.max(paddingLeft, paddingTop, paddingRight, paddingBottom) >= minTextSafePadding;
      if ((hasFullBorder || (hasBg && hasRealPadding)) && parentAreaRatio < 0.7) {
        const leftGap = textElement.rect.left - (parentRect.left - slideRect.left);
        const topGap = textElement.rect.top - (parentRect.top - slideRect.top);
        const rightGap = (parentRect.right - slideRect.left) - textElement.rect.right;
        const bottomGap = (parentRect.bottom - slideRect.top) - textElement.rect.bottom;
        const minGap = Math.min(leftGap, topGap, rightGap, bottomGap);
        const pairKey = `${textElement.text}|${parent.tagName}|${parent.className || ''}`;
        if (minGap < minTextSafePadding && !seenTextContainerPairs.has(pairKey)) {
          seenTextContainerPairs.add(pairKey);
          textSafePaddingIssues.push({
            text: textElement.text,
            container: `<${parent.tagName.toLowerCase()}${parent.className ? "." + String(parent.className).split(" ")[0] : ""}>`,
            minGap: Math.round(minGap * 10) / 10,
          });
        }
        break;
      }
      parent = parent.parentElement;
    }
  });

  const siblingAlignmentIssues = [];
  const summaryBandCollisions = [];
  const summaryBands = [];
  Array.from(slide.children || []).forEach((child) => {
    const cls = typeof child.className === "string" ? child.className : "";
    if (!/(summary|bottom|footer|tail)/i.test(cls)) return;
    const rect = child.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    summaryBands.push({
      el: child,
      rect: {
        left: rect.left - slideRect.left,
        top: rect.top - slideRect.top,
        right: rect.right - slideRect.left,
        bottom: rect.bottom - slideRect.top,
      },
      desc: (child.innerText || "").trim().slice(0, 40) || `<${child.tagName.toLowerCase()}>`,
    });
  });
  summaryBands.forEach((band) => {
    textElements.forEach((textElement) => {
      if (band.el.contains(textElement.el)) return;
      if (textElement.el.contains && textElement.el.contains(band.el)) return;
      const area = rectIntersectionArea(textElement.rect, band.rect);
      if (area <= textOverlapMinArea) return;
      const bottomGap = band.rect.top - textElement.rect.bottom;
      if (bottomGap > minTextSafePadding) return;
      summaryBandCollisions.push({
        text: textElement.text,
        band: band.desc,
        overlapArea: Math.round(area),
      });
    });
  });

  function collectAlignedSiblingGroups(root) {
    const groups = [];
    root.querySelectorAll("*").forEach((parent) => {
      const children = Array.from(parent.children || []).filter((child) => {
        const style = window.getComputedStyle(child);
        const rect = child.getBoundingClientRect();
        return !(
          style.display === "none" ||
          style.visibility === "hidden" ||
          style.opacity === "0" ||
          rect.width <= 0 ||
          rect.height <= 0
        );
      });
      if (children.length < 3) return;
      const rects = children.map((child) => child.getBoundingClientRect());
      const widths = rects.map((rect) => rect.width);
      const heights = rects.map((rect) => rect.height);
      const minWidth = Math.min(...widths);
      const maxWidth = Math.max(...widths);
      const minHeight = Math.min(...heights);
      const maxHeight = Math.max(...heights);
      const sameRow = rects.every((rect) => Math.abs(rect.top - rects[0].top) <= siblingAlignTolerance * 2.5);
      const similarWidth = maxWidth - minWidth <= Math.max(24, minWidth * 0.15);
      const similarHeight = maxHeight - minHeight <= Math.max(28, minHeight * 0.18);
      if (sameRow && similarWidth && similarHeight) {
        groups.push(children.map((child, idx) => ({
          el: child,
          rect: rects[idx],
          desc: (child.innerText || "").trim().slice(0, 30) || `<${child.tagName.toLowerCase()}>`,
        })));
      }
    });
    return groups;
  }
  const alignedGroups = collectAlignedSiblingGroups(slide);
  alignedGroups.forEach((group) => {
    const first = group[0];
    group.slice(1).forEach((item) => {
      const topDiff = Math.abs(item.rect.top - first.rect.top);
      const heightDiff = Math.abs((item.rect.bottom - item.rect.top) - (first.rect.bottom - first.rect.top));
      if (topDiff > siblingAlignTolerance || heightDiff > siblingAlignTolerance * 2) {
        siblingAlignmentIssues.push({
          block: item.desc,
          topDiff: Math.round(topDiff * 10) / 10,
          heightDiff: Math.round(heightDiff * 10) / 10,
        });
      }
    });
  });

  return [
    {
      index: 0,
      type: slide.getAttribute("type") || "content",
      coverageRatio: Math.round(finalCoverageRatio * 1000) / 10,
      blankRatio: Math.round(finalBlankRatio * 1000) / 10,
      pixelAnalysis:
        pixelData && !pixelData.error
          ? {
              backgroundColor: bgColorDebug,
              geometryCoverage: Math.round(geometryCoverage * 1000) / 10,
              pixelCoverage: Math.round(coverageRatio * 1000) / 10,
            }
          : null,
      overflows: overflows.slice(0, 10),
      clippedElements: clippedElements.slice(0, 10),
      textOverlaps: textOverlaps.slice(0, 10),
      textOcclusions: textOcclusions.slice(0, 10),
      childOverflows: childOverflows.slice(0, 10),
      blockOverlaps: blockOverlaps.slice(0, 10),
      textSafePaddingIssues: textSafePaddingIssues.slice(0, 10),
      siblingAlignmentIssues: siblingAlignmentIssues.slice(0, 10),
      summaryBandCollisions: summaryBandCollisions.slice(0, 10),
    },
  ];
}

module.exports = {
  detectLayoutIssues,
  createBrowserContext,
  loadConfig,
  findConfigPath,
  parseJsonc,
  geometricBlank,
  pixelBlank,
  slideOverflow,
  textOcclusion,
  elementOverlap,
  childOverflow,
  blockOverlap,
  recursiveBlank,
  utils,
};
