#!/usr/bin/env node

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const skillRoot = path.resolve(__dirname, "..");
const nestedNodeModules = path.join(
  skillRoot,
  "designer",
  "lib",
  "html-to-pptx",
  "node_modules"
);

const DEFAULT_SLIDE_SIZE = {
  width: 12191999,
  height: 6858000,
};

const VISUAL_DIFF_GRID = {
  width: 160,
  height: 90,
};

const VISUAL_DIFF_THRESHOLD = 0.3;
const VISUAL_PIXEL_DELTA = 28;

function usage() {
  console.log("Usage: node scripts/export_qa.js <pages_dir> <pptx_path>");
}

function exists(targetPath) {
  return fs.existsSync(targetPath);
}

function requireNested(modulePath) {
  return require(path.join(nestedNodeModules, modulePath));
}

function requireJsZip() {
  return requireNested("jszip");
}

function requireXmldom() {
  return requireNested("@xmldom/xmldom");
}

function requirePngJs() {
  try {
    return require("pngjs");
  } catch (_error) {
    return require(path.join(skillRoot, "node_modules", "pngjs"));
  }
}

function requirePlaywright() {
  try {
    return require("playwright");
  } catch (_error) {
    return require(path.join(skillRoot, "node_modules", "playwright"));
  }
}

function parseArgs(args) {
  if (args.length < 2) {
    return null;
  }
  return {
    pagesDir: path.resolve(process.cwd(), args[0]),
    pptxPath: path.resolve(process.cwd(), args[1]),
  };
}

function localName(node) {
  if (!node || !node.nodeName) {
    return "";
  }
  const parts = String(node.nodeName).split(":");
  return parts[parts.length - 1];
}

function descendantElements(root, name) {
  const matches = [];
  if (!root || !root.childNodes) {
    return matches;
  }
  for (let index = 0; index < root.childNodes.length; index++) {
    const child = root.childNodes[index];
    if (!child || child.nodeType !== 1) {
      continue;
    }
    if (localName(child) === name) {
      matches.push(child);
    }
    matches.push(...descendantElements(child, name));
  }
  return matches;
}

function firstDescendant(root, name) {
  return descendantElements(root, name)[0] || null;
}

function parseIntSafe(value, fallback = 0) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseFloatSafe(value, fallback = 0) {
  const parsed = Number.parseFloat(String(value || ""));
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseHexByte(value) {
  const parsed = Number.parseInt(String(value || ""), 16);
  return Number.isFinite(parsed) ? parsed : 0;
}

function textFromShape(shapeNode) {
  return descendantElements(shapeNode, "t")
    .map((node) => node.textContent || "")
    .join("")
    .replace(/\s+/gu, " ")
    .trim();
}

function normalizeColor(color) {
  if (!color) {
    return null;
  }
  return String(color).toUpperCase();
}

function parseSolidFill(node) {
  if (!node) {
    return null;
  }

  const solidFill = firstDescendant(node, "solidFill");
  if (!solidFill) {
    if (firstDescendant(node, "noFill")) {
      return null;
    }
    return null;
  }

  const srgbClr = firstDescendant(solidFill, "srgbClr");
  if (srgbClr) {
    return normalizeColor(srgbClr.getAttribute("val"));
  }

  const schemeClr = firstDescendant(solidFill, "schemeClr");
  if (schemeClr) {
    return `SCHEME:${normalizeColor(schemeClr.getAttribute("val"))}`;
  }

  return null;
}

function parseShapeNode(shapeNode) {
  const shapeProps = firstDescendant(shapeNode, "spPr");
  const transform = firstDescendant(shapeProps, "xfrm");
  const offset = firstDescendant(transform, "off");
  const extent = firstDescendant(transform, "ext");
  const geometry = firstDescendant(shapeProps, "prstGeom");
  const outline = firstDescendant(shapeProps, "ln");

  return {
    name: firstDescendant(shapeNode, "cNvPr")?.getAttribute("name") || "Shape",
    text: textFromShape(shapeNode),
    x: parseIntSafe(offset?.getAttribute("x")),
    y: parseIntSafe(offset?.getAttribute("y")),
    width: parseIntSafe(extent?.getAttribute("cx")),
    height: parseIntSafe(extent?.getAttribute("cy")),
    shapeType: geometry?.getAttribute("prst") || "rect",
    fill: parseSolidFill(shapeProps),
    line: parseSolidFill(outline),
  };
}

function parseSlideXml(xmlText, slideSize) {
  const { DOMParser } = requireXmldom();
  const document = new DOMParser().parseFromString(xmlText, "text/xml");
  const shapes = descendantElements(document, "sp").map(parseShapeNode);
  return {
    width: slideSize.width,
    height: slideSize.height,
    shapes,
  };
}

function findSlideSize(zip, presentationXml) {
  const { DOMParser } = requireXmldom();
  const document = new DOMParser().parseFromString(presentationXml, "text/xml");
  const sizeNode = firstDescendant(document, "sldSz");
  if (!sizeNode) {
    return { ...DEFAULT_SLIDE_SIZE };
  }
  return {
    width: parseIntSafe(sizeNode.getAttribute("cx"), DEFAULT_SLIDE_SIZE.width),
    height: parseIntSafe(sizeNode.getAttribute("cy"), DEFAULT_SLIDE_SIZE.height),
  };
}

async function loadPptxModel(pptxPath) {
  const JSZip = requireJsZip();
  const zip = await JSZip.loadAsync(fs.readFileSync(pptxPath));
  const presentationXml = await zip.file("ppt/presentation.xml").async("text");
  const slideSize = findSlideSize(zip, presentationXml);
  const slideEntries = Object.keys(zip.files)
    .filter((name) => /^ppt\/slides\/slide\d+\.xml$/u.test(name))
    .sort((left, right) => {
      const leftNum = parseIntSafe(left.match(/slide(\d+)\.xml/u)?.[1]);
      const rightNum = parseIntSafe(right.match(/slide(\d+)\.xml/u)?.[1]);
      return leftNum - rightNum;
    });

  const slides = [];
  for (const slidePath of slideEntries) {
    const xmlText = await zip.file(slidePath).async("text");
    slides.push(parseSlideXml(xmlText, slideSize));
  }
  return { slideSize, slides };
}

function shapeCenterX(shape) {
  return shape.x + shape.width / 2;
}

function shapeCenterY(shape) {
  return shape.y + shape.height / 2;
}

function horizontalOverlapRatio(a, b) {
  const left = Math.max(a.x, b.x);
  const right = Math.min(a.x + a.width, b.x + b.width);
  if (right <= left) {
    return 0;
  }
  return (right - left) / Math.min(a.width, b.width);
}

function verticalOverlapRatio(a, b) {
  const top = Math.max(a.y, b.y);
  const bottom = Math.min(a.y + a.height, b.y + b.height);
  if (bottom <= top) {
    return 0;
  }
  return (bottom - top) / Math.min(a.height, b.height);
}

function isWhiteLike(color) {
  return color === "FFFFFF" || color === "FFF" || color === "SCHEME:BG1";
}

function isLightColor(color) {
  if (!color || color.startsWith("SCHEME:")) {
    return false;
  }
  const normalized = color.replace(/[^0-9A-F]/gu, "");
  if (normalized.length !== 6) {
    return false;
  }
  const r = parseHexByte(normalized.slice(0, 2));
  const g = parseHexByte(normalized.slice(2, 4));
  const b = parseHexByte(normalized.slice(4, 6));
  return r + g + b >= 560;
}

function detectBadgeHeaderIssues(slide) {
  const issues = [];
  const slideWidth = slide.width;
  const slideHeight = slide.height;
  const valueTexts = slide.shapes.filter(
    (shape) =>
      shape.text &&
      /(?:\d|元|%|亿)/u.test(shape.text) &&
      shape.height / slideHeight >= 0.035 &&
      shape.width / slideWidth <= 0.2
  );
  const labelTexts = slide.shapes.filter(
    (shape) =>
      shape.text &&
      shape.text.length <= 24 &&
      shape.height / slideHeight <= 0.05 &&
      !/(?:\d{4}|资料来源|THANK YOU)/u.test(shape.text)
  );
  const labelBackgrounds = slide.shapes.filter(
    (shape) =>
      !shape.text &&
      Boolean(shape.fill) &&
      !isWhiteLike(shape.fill) &&
      isLightColor(shape.fill) &&
      shape.width / slideWidth >= 0.035 &&
      shape.width / slideWidth <= 0.16 &&
      shape.height / slideHeight >= 0.02 &&
      shape.height / slideHeight <= 0.06
  );

  for (const background of labelBackgrounds) {
    const nearbyValue = valueTexts.some(
      (valueShape) =>
        horizontalOverlapRatio(valueShape, background) >= 0.5 &&
        valueShape.y > background.y &&
        valueShape.y - (background.y + background.height) <= slideHeight * 0.15
    );
    if (!nearbyValue) {
      continue;
    }

    const label = labelTexts
      .filter(
        (textShape) =>
          horizontalOverlapRatio(textShape, background) >= 0.55 &&
          verticalOverlapRatio(textShape, background) >= 0.55
      )
      .sort((left, right) => {
        const leftDistance =
          Math.abs(shapeCenterX(left) - shapeCenterX(background)) +
          Math.abs(shapeCenterY(left) - shapeCenterY(background));
        const rightDistance =
          Math.abs(shapeCenterX(right) - shapeCenterX(background)) +
          Math.abs(shapeCenterY(right) - shapeCenterY(background));
        return leftDistance - rightDistance;
      })[0];

    if (!label) {
      issues.push({
        type: "badge_label_missing",
        severity: "error",
        detail: `${background.name} has no matching centered label`,
      });
      continue;
    }

    const centerXDiff = Math.abs(shapeCenterX(label) - shapeCenterX(background));
    const centerYDiff = Math.abs(shapeCenterY(label) - shapeCenterY(background));
    const widthDiffRatio = Math.abs(label.width - background.width) / background.width;
    const heightDiffRatio = Math.abs(label.height - background.height) / background.height;

    if (
      centerXDiff > background.width * 0.12 ||
      centerYDiff > background.height * 0.22 ||
      widthDiffRatio > 0.2 ||
      heightDiffRatio > 0.3
    ) {
      issues.push({
        type: "badge_alignment",
        severity: "error",
        detail: `${label.text} is not aligned with ${background.name}`,
      });
    }
  }

  return issues;
}

function pickDominantBarGroup(slide) {
  const groups = new Map();
  for (const shape of slide.shapes) {
    if (
      shape.text ||
      !shape.fill ||
      isWhiteLike(shape.fill) ||
      shape.width / slide.width < 0.02 ||
      shape.width / slide.width > 0.08 ||
      shape.height / slide.height < 0.12 ||
      shape.height / slide.height > 0.75 ||
      shape.height / Math.max(shape.width, 1) < 1.5 ||
      shape.x < slide.width * 0.1
    ) {
      continue;
    }
    const key = `${shape.fill}:${Math.round(shape.width / 10000)}`;
    if (!groups.has(key)) {
      groups.set(key, []);
    }
    groups.get(key).push(shape);
  }

  let best = [];
  for (const shapes of groups.values()) {
    if (shapes.length > best.length) {
      best = shapes;
    }
  }
  return best.length >= 4 ? best.sort((left, right) => left.x - right.x) : [];
}

function detectBarBoundsIssues(slide, bars) {
  const issues = [];
  if (bars.length === 0) {
    return issues;
  }

  const barLeft = Math.min(...bars.map((bar) => bar.x));
  const barTop = Math.min(...bars.map((bar) => bar.y));
  const barRight = Math.max(...bars.map((bar) => bar.x + bar.width));
  const barBottom = Math.max(...bars.map((bar) => bar.y + bar.height));

  const containers = slide.shapes
    .filter(
      (shape) =>
        !shape.text &&
        isWhiteLike(shape.fill) &&
        shape.width >= (barRight - barLeft) * 0.7 &&
        shape.height >= (barBottom - barTop) * 0.8 &&
        horizontalOverlapRatio(shape, {
          x: barLeft,
          y: barTop,
          width: Math.max(barRight - barLeft, 1),
          height: Math.max(barBottom - barTop, 1),
        }) >= 0.6 &&
        verticalOverlapRatio(shape, {
          x: barLeft,
          y: barTop,
          width: Math.max(barRight - barLeft, 1),
          height: Math.max(barBottom - barTop, 1),
        }) >= 0.8
    )
    .sort((left, right) => {
      const leftDistance =
        Math.abs(shapeCenterX(left) - (barLeft + barRight) / 2) +
        Math.abs(shapeCenterY(left) - (barTop + barBottom) / 2);
      const rightDistance =
        Math.abs(shapeCenterX(right) - (barLeft + barRight) / 2) +
        Math.abs(shapeCenterY(right) - (barTop + barBottom) / 2);
      if (leftDistance !== rightDistance) {
        return leftDistance - rightDistance;
      }
      return left.width * left.height - right.width * right.height;
    });

  const container = containers[0];
  if (!container) {
    return issues;
  }

  const toleranceX = slide.width * 0.003;
  const toleranceY = slide.height * 0.004;
  for (const bar of bars) {
    const overflowSides = [];
    if (bar.x < container.x - toleranceX) {
      overflowSides.push("left");
    }
    if (bar.x + bar.width > container.x + container.width + toleranceX) {
      overflowSides.push("right");
    }
    if (bar.y < container.y - toleranceY) {
      overflowSides.push("top");
    }
    if (bar.y + bar.height > container.y + container.height + toleranceY) {
      overflowSides.push("bottom");
    }
    if (overflowSides.length > 0) {
      issues.push({
        type: "chart_bar_overflow",
        severity: "error",
        detail: `${bar.name} exceeds chart container on ${overflowSides.join(", ")}`,
      });
    }
  }

  return issues;
}

function detectBarChartIssues(slide) {
  const issues = [];
  const bars = pickDominantBarGroup(slide);
  if (bars.length === 0) {
    return issues;
  }

  issues.push(...detectBarBoundsIssues(slide, bars));

  const yearLabels = slide.shapes
    .filter((shape) => /^\d{4}$/u.test(shape.text || ""))
    .sort((left, right) => left.x - right.x);
  const valueLabels = slide.shapes.filter((shape) => {
    const text = (shape.text || "").trim();
    return /^\d+(?:\.\d+)?$/u.test(text) && !/^\d{4}$/u.test(text);
  });

  const usedYears = new Set();
  const usedValues = new Set();

  for (const bar of bars) {
    const centerX = shapeCenterX(bar);
    const year = yearLabels
      .filter((label) => !usedYears.has(label))
      .sort((left, right) => Math.abs(shapeCenterX(left) - centerX) - Math.abs(shapeCenterX(right) - centerX))[0];
    if (!year) {
      issues.push({
        type: "chart_year_missing",
        severity: "error",
        detail: `${bar.name} has no year label`,
      });
    } else {
      usedYears.add(year);
      if (
        Math.abs(shapeCenterX(year) - centerX) > Math.max(bar.width * 0.45, slide.width * 0.01)
      ) {
        issues.push({
          type: "chart_year_alignment",
          severity: "error",
          detail: `${year.text} is not centered under ${bar.name}`,
        });
      }
    }

    const value = valueLabels
      .filter(
        (label) =>
          !usedValues.has(label) &&
          label.y + label.height <= bar.y + slide.height * 0.06
      )
      .sort((left, right) => Math.abs(shapeCenterX(left) - centerX) - Math.abs(shapeCenterX(right) - centerX))[0];
    if (!value) {
      issues.push({
        type: "chart_value_missing",
        severity: "error",
        detail: `${bar.name} has no numeric value label`,
      });
    } else {
      usedValues.add(value);
      if (
        Math.abs(shapeCenterX(value) - centerX) > Math.max(bar.width * 0.5, slide.width * 0.012)
      ) {
        issues.push({
          type: "chart_value_alignment",
          severity: "error",
          detail: `${value.text} is not centered over ${bar.name}`,
        });
      }
    }
  }

  const yearPositions = yearLabels.map((label) => shapeCenterX(label));
  for (let index = 1; index < yearPositions.length; index++) {
    if (yearPositions[index] <= yearPositions[index - 1]) {
      issues.push({
        type: "chart_year_order",
        severity: "error",
        detail: "year labels are not in ascending left-to-right order",
      });
      break;
    }
  }

  return issues;
}

function evaluateStructuralIssues(slide) {
  return [...detectBadgeHeaderIssues(slide), ...detectBarChartIssues(slide)];
}

function findExecutable(command) {
  const probe =
    process.platform === "win32"
      ? spawnSync("where", [command], { encoding: "utf8", shell: false })
      : spawnSync("sh", ["-lc", `command -v ${command}`], {
          encoding: "utf8",
          shell: false,
        });

  if (probe.status !== 0) {
    return null;
  }

  const candidate = String(probe.stdout || "")
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .find(Boolean);
  return candidate || null;
}

function getVisualCapabilities() {
  const soffice = findExecutable("soffice") || findExecutable("libreoffice");
  const pdftoppm = findExecutable("pdftoppm");

  try {
    requirePlaywright();
  } catch (_error) {
    return {
      enabled: false,
      soffice,
      pdftoppm,
      reason: "playwright_unavailable",
    };
  }

  if (!soffice || !pdftoppm) {
    return {
      enabled: false,
      soffice,
      pdftoppm,
      reason: "render_tools_unavailable",
    };
  }

  return {
    enabled: true,
    soffice,
    pdftoppm,
    reason: null,
  };
}

function parseSlideType(htmlText) {
  const match = htmlText.match(/class="[^"]*ppt-slide[^"]*"[^>]*type="([^"]+)"/u);
  return match?.[1] || "content";
}

function findPageFiles(pagesDir) {
  return fs
    .readdirSync(pagesDir)
    .filter((fileName) => /^page-\d+\.pptx\.html$/u.test(fileName))
    .sort((left, right) => {
      const leftNum = parseIntSafe(left.match(/page-(\d+)/u)?.[1]);
      const rightNum = parseIntSafe(right.match(/page-(\d+)/u)?.[1]);
      return leftNum - rightNum;
    })
    .map((fileName) => path.join(pagesDir, fileName));
}

async function renderHtmlSlides(pageFiles, targetDir) {
  const { chromium } = requirePlaywright();
  const browser = await chromium.launch({ headless: true });
  const renderedFiles = [];

  try {
    for (let index = 0; index < pageFiles.length; index++) {
      const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
      const filePath = pageFiles[index];
      await page.goto(`file://${filePath}`, {
        waitUntil: "domcontentloaded",
        timeout: 60000,
      });
      await new Promise((resolve) => setTimeout(resolve, 1500));
      const slide = page.locator(".ppt-slide").first();
      const screenshotPath = path.join(targetDir, `html-${index + 1}.png`);
      await slide.screenshot({ path: screenshotPath });
      renderedFiles.push(screenshotPath);
      await page.close();
    }
  } finally {
    await browser.close();
  }

  return renderedFiles;
}

function renderPptxSlides(pptxPath, targetDir, capabilities) {
  const pdfPath = path.join(targetDir, path.basename(pptxPath, ".pptx") + ".pdf");
  const prefix = path.join(targetDir, "ppt");

  const sofficeResult = spawnSync(
    capabilities.soffice,
    ["--headless", "--convert-to", "pdf", "--outdir", targetDir, pptxPath],
    {
      encoding: "utf8",
      shell: false,
    }
  );
  if (sofficeResult.status !== 0 || !exists(pdfPath)) {
    throw new Error(
      `soffice render failed: ${String(sofficeResult.stderr || sofficeResult.stdout || "").trim()}`
    );
  }

  const pdftoppmResult = spawnSync(
    capabilities.pdftoppm,
    ["-png", pdfPath, prefix],
    {
      encoding: "utf8",
      shell: false,
    }
  );
  if (pdftoppmResult.status !== 0) {
    throw new Error(
      `pdftoppm render failed: ${String(pdftoppmResult.stderr || pdftoppmResult.stdout || "").trim()}`
    );
  }

  return fs
    .readdirSync(targetDir)
    .filter((fileName) => /^ppt-\d+\.png$/u.test(fileName))
    .sort((left, right) => {
      const leftNum = parseIntSafe(left.match(/ppt-(\d+)\.png/u)?.[1]);
      const rightNum = parseIntSafe(right.match(/ppt-(\d+)\.png/u)?.[1]);
      return leftNum - rightNum;
    })
    .map((fileName) => path.join(targetDir, fileName));
}

function loadPng(pathname) {
  const { PNG } = requirePngJs();
  return PNG.sync.read(fs.readFileSync(pathname));
}

function grayscaleAt(png, x, y) {
  const clampedX = Math.min(png.width - 1, Math.max(0, x));
  const clampedY = Math.min(png.height - 1, Math.max(0, y));
  const offset = (clampedY * png.width + clampedX) * 4;
  const alpha = png.data[offset + 3] / 255;
  const value =
    png.data[offset] * 0.299 + png.data[offset + 1] * 0.587 + png.data[offset + 2] * 0.114;
  return value * alpha + 255 * (1 - alpha);
}

function computeVisualDiffRatio(leftPath, rightPath) {
  const left = loadPng(leftPath);
  const right = loadPng(rightPath);
  const diffWidth = VISUAL_DIFF_GRID.width;
  const diffHeight = VISUAL_DIFF_GRID.height;
  const scaleLeftX = left.width / diffWidth;
  const scaleLeftY = left.height / diffHeight;
  const scaleRightX = right.width / diffWidth;
  const scaleRightY = right.height / diffHeight;

  let changed = 0;
  let samples = 0;

  for (let gridY = 0; gridY < diffHeight; gridY++) {
    for (let gridX = 0; gridX < diffWidth; gridX++) {
      const leftGray = grayscaleAt(
        left,
        Math.floor((gridX + 0.5) * scaleLeftX),
        Math.floor((gridY + 0.5) * scaleLeftY)
      );
      const rightGray = grayscaleAt(
        right,
        Math.floor((gridX + 0.5) * scaleRightX),
        Math.floor((gridY + 0.5) * scaleRightY)
      );
      if (Math.abs(leftGray - rightGray) > VISUAL_PIXEL_DELTA) {
        changed += 1;
      }
      samples += 1;
    }
  }

  return samples === 0 ? 0 : changed / samples;
}

async function runVisualQa(results, pageFiles, pptxPath, capabilities) {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "pptx-craft-export-qa-"));
  try {
    const htmlSlidesDir = path.join(tempRoot, "html");
    const pptSlidesDir = path.join(tempRoot, "ppt");
    fs.mkdirSync(htmlSlidesDir, { recursive: true });
    fs.mkdirSync(pptSlidesDir, { recursive: true });

    try {
      const htmlImages = await renderHtmlSlides(pageFiles, htmlSlidesDir);
      const pptImages = renderPptxSlides(pptxPath, pptSlidesDir, capabilities);
      for (let index = 0; index < results.length; index++) {
        const htmlImage = htmlImages[index];
        const pptImage = pptImages[index];
        if (!htmlImage || !pptImage) {
          continue;
        }
        const diffRatio = computeVisualDiffRatio(htmlImage, pptImage);
        results[index].visualDiffRatio = Number(diffRatio.toFixed(4));
        if (diffRatio > VISUAL_DIFF_THRESHOLD) {
          results[index].issues.push({
            type: "visual_regression",
            severity: "error",
            detail: `visual diff ratio ${diffRatio.toFixed(3)} exceeds ${VISUAL_DIFF_THRESHOLD}`,
          });
        }
      }
      return results;
    } catch (error) {
      for (const result of results) {
        result.visualWarning = `visual QA skipped: ${error.message}`;
      }
      return results;
    }
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
}

function createJsonResult(totalFiles, mode, capabilities) {
  return {
    timestamp: new Date().toISOString(),
    total_files: totalFiles,
    mode,
    capabilities,
    passed_pages: [],
    failed_pages: [],
    summary: {
      total_issues: 0,
      errors: 0,
      warnings: 0,
    },
    passed: true,
  };
}

function pushIssue(jsonResult, failedPage, issue) {
  failedPage.issues.push(issue);
  jsonResult.summary.total_issues += 1;
  if (issue.severity === "error") {
    jsonResult.summary.errors += 1;
  } else {
    jsonResult.summary.warnings += 1;
  }
}

function formatIssue(issue) {
  return `  - [${issue.severity}] ${issue.type}: ${issue.detail}`;
}

function generateReport(results, options = {}) {
  const { pageCountMismatch = null, mode = "structural_only", capabilities = {} } = options;

  console.log(`\n${"=".repeat(60)}`);
  console.log("Export QA Report");
  console.log("=".repeat(60));
  console.log(`Mode: ${mode}`);

  let hasIssues = false;
  const jsonResult = createJsonResult(results.length, mode, capabilities);

  if (pageCountMismatch) {
    hasIssues = true;
    jsonResult.passed = false;
    const deckFailure = {
      file: "deck",
      type: "deck",
      issues: [
        {
          type: "slide_count_mismatch",
          severity: "error",
          detail: pageCountMismatch,
        },
      ],
    };
    jsonResult.failed_pages.push(deckFailure);
    jsonResult.summary.total_issues += 1;
    jsonResult.summary.errors += 1;
    console.log(`Deck: FAIL`);
    console.log(`  - [error] slide_count_mismatch: ${pageCountMismatch}`);
  }

  results.forEach((slide, index) => {
    const failedPage = {
      file: slide.file,
      type: slide.type,
      issues: [],
    };

    for (const issue of slide.issues) {
      pushIssue(jsonResult, failedPage, issue);
    }

    if (failedPage.issues.length > 0) {
      hasIssues = true;
      jsonResult.passed = false;
      jsonResult.failed_pages.push(failedPage);
      console.log(`Page ${index + 1}: FAIL (${failedPage.issues.length} issues)`);
      failedPage.issues.forEach((issue) => {
        console.log(formatIssue(issue));
      });
    } else {
      jsonResult.passed_pages.push({
        file: slide.file,
        type: slide.type,
      });
      const visualSuffix =
        slide.visualDiffRatio !== undefined ? ` (visual diff ${slide.visualDiffRatio})` : "";
      console.log(`Page ${index + 1}: PASS${visualSuffix}`);
    }

    if (slide.visualWarning) {
      console.log(`  - [warning] visual_skip: ${slide.visualWarning}`);
    }
  });

  return { hasIssues, jsonResult };
}

function writeJsonOutput(jsonResult, outputPath) {
  const absoluteOutputPath = path.resolve(outputPath);
  fs.writeFileSync(absoluteOutputPath, JSON.stringify(jsonResult, null, 2), "utf8");
  console.log(`\nJSON saved to ${absoluteOutputPath}`);
}

async function runExportQa(options) {
  const { pagesDir, pptxPath } = options;
  const pageFiles = findPageFiles(pagesDir);
  if (pageFiles.length === 0) {
    throw new Error("No .pptx.html files found.");
  }
  if (!exists(pptxPath)) {
    throw new Error(`PPTX file not found: ${pptxPath}`);
  }

  const pageTypes = pageFiles.map((filePath) =>
    parseSlideType(fs.readFileSync(filePath, "utf8"))
  );
  const model = await loadPptxModel(pptxPath);
  const slideCountMismatch =
    model.slides.length === pageFiles.length
      ? null
      : `expected ${pageFiles.length} slides but found ${model.slides.length} in ${path.basename(
          pptxPath
        )}`;

  const results = model.slides.map((slide, index) => ({
    index,
    file: path.basename(pageFiles[index] || `slide-${index + 1}`),
    type: pageTypes[index] || "content",
    issues: evaluateStructuralIssues(slide),
  }));

  const capabilities = getVisualCapabilities();
  const mode = capabilities.enabled ? "visual+structural" : "structural_only";
  const qaResults = capabilities.enabled
    ? await runVisualQa(results, pageFiles, pptxPath, capabilities)
    : results;
  const { hasIssues, jsonResult } = generateReport(qaResults, {
    pageCountMismatch: slideCountMismatch,
    mode,
    capabilities: {
      visual: capabilities.enabled,
      soffice: Boolean(capabilities.soffice),
      pdftoppm: Boolean(capabilities.pdftoppm),
      reason: capabilities.reason,
    },
  });

  const jsonOutputPath = path.join(path.dirname(pagesDir), "export_qa_result.json");
  writeJsonOutput(jsonResult, jsonOutputPath);
  return { hasIssues, jsonResult, jsonOutputPath };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (!options) {
    usage();
    process.exit(1);
  }

  try {
    const result = await runExportQa(options);
    process.exit(result.hasIssues ? 1 : 0);
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}

module.exports = {
  computeVisualDiffRatio,
  detectBadgeHeaderIssues,
  detectBarChartIssues,
  evaluateStructuralIssues,
  formatIssue,
  generateReport,
  getVisualCapabilities,
  parseSlideXml,
  runExportQa,
  writeJsonOutput,
};

if (require.main === module) {
  main();
}
