#!/usr/bin/env node

const assert = require("assert");

const { createBrowserContext } = require("../designer/qa_core");

function main() {
  const browserContext = createBrowserContext();

  const expectedKeys = [
    "rectIntersectionAreaStr",
    "isAncestorStr",
    "isBackgroundOrDecorStr",
    "detectSlideOverflowStr",
    "buildOverflowElementsStr",
    "detectChildOverflowStr",
    "detectTextOverlapStr",
    "detectTextOcclusionStr",
    "detectBlockOverlapStr",
    "detectInBrowserStr",
  ];

  for (const key of expectedKeys) {
    assert.ok(browserContext[key], `${key} should be present`);
    assert.strictEqual(typeof browserContext[key], "string", `${key} should be serialized`);
  }

  assert.match(
    browserContext.detectInBrowserStr,
    /slideOverflow\.detectSlideOverflow/,
    "detectInBrowser should still require the slideOverflow helper object"
  );
  assert.match(
    browserContext.detectInBrowserStr,
    /childOverflow\.buildOverflowElements/,
    "detectInBrowser should still require the childOverflow helper object"
  );
}

main();
