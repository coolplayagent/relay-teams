#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { detectLayoutIssues, loadConfig } = require("./qa_core");

const BLANK_EXEMPT_TYPES = ["cover", "chapter", "final", "table_of_contents"];

let config = {
  blankThreshold: 0.25,
  overflowTolerance: 2,
  textOverlapMinArea: 16,
  pixelBlankThreshold: 0.4,
};

async function detectWithBrowser(htmlPath) {
  return detectLayoutIssues({ htmlPath, config });
}

function detectSimple(html) {
  const slides = [];
  const slideRegex =
    /<div[^>]*class="[^"]*ppt-slide[^"]*"[^>]*type="([^"]*)"[^>]*>([\s\S]*?)<\/div>/i;

  const match = slideRegex.exec(html);
  if (!match) {
    return slides;
  }

  const type = match[1];
  const content = match[2];
  const textMatches = content.match(/>([^<]+)</g) || [];
  const textContent = textMatches
    .map((item) => item.slice(1, -1).trim())
    .filter(Boolean)
    .join(" ");

  const imgCount = (content.match(/<img[^>]*>/g) || []).length;
  const chartCount = (content.match(/id="[^"]*chart[^"]*"/gi) || []).length;
  const svgCount = (content.match(/<svg/g) || []).length;

  const textDensity = Math.min(textContent.length / 500, 1);
  const mediaDensity = Math.min((imgCount + chartCount + svgCount) / 3, 1);
  let contentScore = textDensity * 0.6 + mediaDensity * 0.4;

  if (BLANK_EXEMPT_TYPES.includes(type)) {
    contentScore = Math.max(contentScore, 0.5);
  }

  slides.push({
    index: 1,
    type,
    coverageRatio: Math.round(contentScore * 1000) / 10,
    blankRatio: Math.round((1 - contentScore) * 1000) / 10,
    overflows: [],
    textOverlaps: null,
    textOcclusions: null,
    childOverflows: null,
    blockOverlaps: null,
    mode: "simple",
  });

  return slides;
}

function createJsonResult(totalFiles) {
  return {
    timestamp: new Date().toISOString(),
    total_files: totalFiles,
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

function pushIssue(jsonResult, failedPage, issue, severity) {
  failedPage.issues.push(issue);
  jsonResult.summary.total_issues++;
  if (severity === "error") {
    jsonResult.summary.errors++;
  } else {
    jsonResult.summary.warnings++;
  }
}

function formatIssue(issue) {
  return `  - [${issue.severity}] ${issue.type}: ${issue.detail}`;
}

function generateReport(results) {
  console.log(`\n${"=".repeat(60)}`);
  console.log("Slide Layout QA Report");
  console.log("=".repeat(60));

  let hasIssues = false;
  const jsonResult = createJsonResult(results.length);

  results.forEach((slide) => {
    const pageNumber = (slide.index || 0) + 1;
    const isExempt = BLANK_EXEMPT_TYPES.includes(slide.type);
    const failedPage = {
      file: slide.filePath || "unknown",
      type: slide.type,
      issues: [],
    };

    const geometryBlankRatio =
      slide.pixelAnalysis && slide.pixelAnalysis.geometryCoverage !== undefined
        ? 100 - slide.pixelAnalysis.geometryCoverage
        : null;
    const pixelBlankRatio = slide.blankRatio;
    const blankIssue =
      !isExempt &&
      ((geometryBlankRatio !== null &&
        geometryBlankRatio > config.blankThreshold * 100) ||
        pixelBlankRatio > config.pixelBlankThreshold * 100);

    if (blankIssue) {
      pushIssue(
        jsonResult,
        failedPage,
        {
          type: "blank_rate",
          severity: "error",
          detail: `blank ratio ${pixelBlankRatio.toFixed(1)}%`,
        },
        "error"
      );
    }

    (slide.overflows || []).forEach((overflow) => {
      pushIssue(
        jsonResult,
        failedPage,
        {
          type: "overflow",
          severity: "error",
          detail: `${overflow.text} overflows slide bounds`,
        },
        "error"
      );
    });

    (slide.textOverlaps || []).forEach((overlap) => {
      pushIssue(
        jsonResult,
        failedPage,
        {
          type: "text_overlap",
          severity: "warning",
          detail: `${overlap.textA} overlaps ${overlap.textB}`,
        },
        "warning"
      );
    });

    (slide.textOcclusions || []).forEach((occlusion) => {
      pushIssue(
        jsonResult,
        failedPage,
        {
          type: "text_occlusion",
          severity: "error",
          detail: `${occlusion.occludedText} is occluded by ${occlusion.occluder}`,
        },
        "error"
      );
    });

    (slide.childOverflows || []).forEach((overflow) => {
      pushIssue(
        jsonResult,
        failedPage,
        {
          type: overflow.overflowType === "ancestor_clip" ? "ancestor_clip" : "child_overflow",
          severity: "warning",
          detail: `${overflow.child} overflows ${overflow.parent}`,
        },
        "warning"
      );
    });

    (slide.blockOverlaps || []).forEach((overlap) => {
      pushIssue(
        jsonResult,
        failedPage,
        {
          type: "block_overlap",
          severity: "warning",
          detail: `${overlap.blockA} overlaps ${overlap.blockB}`,
        },
        "warning"
      );
    });

    (slide.textSafePaddingIssues || []).forEach((issue) => {
      pushIssue(
        jsonResult,
        failedPage,
        {
          type: "text_safe_padding",
          severity: "error",
          detail: `${issue.text} is too close to ${issue.container} (min gap ${issue.minGap}px)`,
        },
        "error"
      );
    });

    (slide.siblingAlignmentIssues || []).forEach((issue) => {
      pushIssue(
        jsonResult,
        failedPage,
        {
          type: "sibling_alignment",
          severity: "warning",
          detail: `${issue.block} misaligned (top diff ${issue.topDiff}px, height diff ${issue.heightDiff}px)`,
        },
        "warning"
      );
    });

    (slide.recursiveBlanks || []).forEach((blank) => {
      pushIssue(
        jsonResult,
        failedPage,
        {
          type: "recursive_blank",
          severity: "warning",
          detail: `${blank.selector} blank ratio ${blank.blankRatio}%`,
        },
        "warning"
      );
    });

    if (failedPage.issues.length > 0) {
      hasIssues = true;
      jsonResult.passed = false;
      jsonResult.failed_pages.push(failedPage);
      console.log(`Page ${pageNumber}: FAIL (${failedPage.issues.length} issues)`);
      failedPage.issues.forEach((issue) => {
        console.log(formatIssue(issue));
      });
    } else {
      jsonResult.passed_pages.push({
        file: slide.filePath || "unknown",
        type: slide.type,
      });
      console.log(`Page ${pageNumber}: PASS`);
    }
  });

  return { hasIssues, jsonResult };
}

function parseArgs(args) {
  const options = {
    htmlPath: null,
    configPath: null,
    outputJson: true,
  };

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--config" && i + 1 < args.length) {
      options.configPath = args[++i];
    } else if (arg === "--no-json") {
      options.outputJson = false;
    } else if (!arg.startsWith("-")) {
      options.htmlPath = arg;
    }
  }

  return options;
}

function findPageFiles(dirPath) {
  return fs
    .readdirSync(dirPath)
    .filter((fileName) => /\.pptx\.html$/.test(fileName))
    .sort((a, b) => {
      const numA = parseInt(a.match(/page-(\d+)/)?.[1] || "0", 10);
      const numB = parseInt(b.match(/page-(\d+)/)?.[1] || "0", 10);
      if (numA !== numB) {
        return numA - numB;
      }
      return a.localeCompare(b);
    })
    .map((fileName) => path.join(dirPath, fileName));
}

function mergeResults(allResults, outputDir) {
  const mergedResult = {
    timestamp: new Date().toISOString(),
    total_files: 0,
    passed_pages: [],
    failed_pages: [],
    summary: {
      total_issues: 0,
      errors: 0,
      warnings: 0,
    },
    passed: true,
    source_dir: outputDir,
  };

  allResults.forEach(({ filePath, result }) => {
    if (!result || !result.jsonResult) {
      return;
    }

    const current = result.jsonResult;
    mergedResult.total_files += current.total_files;
    mergedResult.passed_pages.push(...current.passed_pages);
    mergedResult.failed_pages.push(
      ...current.failed_pages.map((failedPage) => ({
        ...failedPage,
        file: path.basename(filePath),
      }))
    );
    mergedResult.summary.total_issues += current.summary.total_issues;
    mergedResult.summary.errors += current.summary.errors;
    mergedResult.summary.warnings += current.summary.warnings;

    if (!current.passed) {
      mergedResult.passed = false;
    }
  });

  return mergedResult;
}

function writeJsonOutput(jsonResult, outputPath) {
  const absoluteOutputPath = path.resolve(outputPath);
  fs.writeFileSync(absoluteOutputPath, JSON.stringify(jsonResult, null, 2), "utf8");
  console.log(`\nJSON saved to ${absoluteOutputPath}`);
  console.log(`JSON directory: ${path.dirname(absoluteOutputPath)}`);
}

async function main() {
  const options = parseArgs(process.argv.slice(2));

  if (!options.htmlPath) {
    console.log("Usage: node slide_layout_qa.js [--config <path>] [--no-json] <directory>");
    process.exit(1);
  }

  config = loadConfig(options.configPath);

  if (!fs.existsSync(options.htmlPath)) {
    console.error(`Path does not exist: ${options.htmlPath}`);
    process.exit(1);
  }

  const stat = fs.statSync(options.htmlPath);
  if (!stat.isDirectory()) {
    console.error("Please provide a directory containing .pptx.html files.");
    process.exit(1);
  }

  const pageFiles = findPageFiles(options.htmlPath);
  if (pageFiles.length === 0) {
    console.error("No .pptx.html files found.");
    process.exit(1);
  }

  const allResults = [];
  let hasAnyIssues = false;

  for (const filePath of pageFiles) {
    console.log(`\n${"=".repeat(60)}`);
    console.log(`Checking ${path.basename(filePath)}`);
    console.log("=".repeat(60));

    const results = await detectWithBrowser(filePath);
    results.forEach((result) => {
      result.filePath = path.basename(filePath);
    });

    const { hasIssues, jsonResult } = generateReport(results);
    if (hasIssues) {
      hasAnyIssues = true;
    }

    allResults.push({
      filePath,
      result: { hasIssues, jsonResult },
    });
  }

  if (options.outputJson) {
    const mergedResult = mergeResults(allResults, options.htmlPath);
    const jsonOutputPath = path.join(path.dirname(options.htmlPath), "layout_qa_result.json");
    writeJsonOutput(mergedResult, jsonOutputPath);
  }

  process.exit(hasAnyIssues ? 1 : 0);
}

module.exports = {
  detectWithBrowser,
  detectSimple,
  formatIssue,
  generateReport,
  parseArgs,
  findPageFiles,
  mergeResults,
  writeJsonOutput,
};

if (require.main === module) {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
