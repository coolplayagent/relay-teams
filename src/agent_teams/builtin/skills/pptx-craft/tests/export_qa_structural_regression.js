#!/usr/bin/env node

const assert = require("assert");

const {
  detectBadgeHeaderIssues,
  detectBarChartIssues,
} = require("../scripts/export_qa");

const SLIDE = {
  width: 12191999,
  height: 6858000,
};

function makeShape(overrides) {
  return {
    name: overrides.name || "Shape",
    text: overrides.text || "",
    x: overrides.x || 0,
    y: overrides.y || 0,
    width: overrides.width || 0,
    height: overrides.height || 0,
    fill: overrides.fill || null,
    line: overrides.line || null,
    shapeType: overrides.shapeType || "rect",
  };
}

function makeBadgeSlide(offsetX = 0, offsetY = 0) {
  return {
    ...SLIDE,
    shapes: [
      makeShape({
        name: "Badge Background",
        x: 1243584,
        y: 3986784,
        width: 786384,
        height: 182880,
        fill: "FDECEF",
        shapeType: "roundRect",
      }),
      makeShape({
        name: "Badge Label",
        text: "近7年低点",
        x: 1243584 + offsetX,
        y: 3986784 + offsetY,
        width: 786384,
        height: 182880,
      }),
      makeShape({
        name: "Badge Value",
        text: "1.05元",
        x: 1243584,
        y: 4279392,
        width: 1408176,
        height: 310896,
      }),
    ],
  };
}

function makeChartSlide(valueOffsetX = 0, yearOffsetX = 0, lastBarOverflowX = 0) {
  return {
    ...SLIDE,
    shapes: [
      makeShape({
        name: "Chart Container",
        x: 822960,
        y: 1417320,
        width: 6629400,
        height: 4069080,
        fill: "FFFFFF",
        line: "E6EAF0",
        shapeType: "roundRect",
      }),
      makeShape({
        name: "Bar 2018",
        x: 1499616,
        y: 3600309,
        width: 530352,
        height: 1410603,
        fill: "C70024",
        shapeType: "roundRect",
      }),
      makeShape({
        name: "Bar 2019",
        x: 2446673,
        y: 2176272,
        width: 530352,
        height: 2834640,
        fill: "C70024",
        shapeType: "roundRect",
      }),
      makeShape({
        name: "Bar 2020",
        x: 3393730,
        y: 2512130,
        width: 530352,
        height: 2498782,
        fill: "C70024",
        shapeType: "roundRect",
      }),
      makeShape({
        name: "Bar 2021",
        x: 4340787 + lastBarOverflowX,
        y: 2888291,
        width: 530352,
        height: 2122621,
        fill: "C70024",
        shapeType: "roundRect",
      }),
      makeShape({
        name: "Value 2018",
        text: "1.05",
        x: 1481328 + valueOffsetX,
        y: 3380853,
        width: 566928,
        height: 146304,
      }),
      makeShape({
        name: "Value 2019",
        text: "2.11",
        x: 2428385,
        y: 1956816,
        width: 566928,
        height: 146304,
      }),
      makeShape({
        name: "Value 2020",
        text: "1.86",
        x: 3375442,
        y: 2292674,
        width: 566928,
        height: 146304,
      }),
      makeShape({
        name: "Value 2021",
        text: "1.58",
        x: 4322499,
        y: 2668835,
        width: 566928,
        height: 146304,
      }),
      makeShape({
        name: "Year 2018",
        text: "2018",
        x: 1481328 + yearOffsetX,
        y: 5230368,
        width: 566928,
        height: 146304,
      }),
      makeShape({
        name: "Year 2019",
        text: "2019",
        x: 2428385,
        y: 5230368,
        width: 566928,
        height: 146304,
      }),
      makeShape({
        name: "Year 2020",
        text: "2020",
        x: 3375442,
        y: 5230368,
        width: 566928,
        height: 146304,
      }),
      makeShape({
        name: "Year 2021",
        text: "2021",
        x: 4322499,
        y: 5230368,
        width: 566928,
        height: 146304,
      }),
    ],
  };
}

function main() {
  const alignedBadgeIssues = detectBadgeHeaderIssues(makeBadgeSlide());
  assert.strictEqual(alignedBadgeIssues.length, 0, "aligned badge should pass");

  const misalignedBadgeIssues = detectBadgeHeaderIssues(makeBadgeSlide(240000, 50000));
  assert.ok(
    misalignedBadgeIssues.some((issue) => issue.type === "badge_alignment"),
    "misaligned badge should be reported"
  );

  const alignedChartIssues = detectBarChartIssues(makeChartSlide());
  assert.strictEqual(alignedChartIssues.length, 0, "aligned chart labels should pass");

  const misalignedChartIssues = detectBarChartIssues(makeChartSlide(600000, -500000));
  assert.ok(
    misalignedChartIssues.some(
      (issue) => issue.type === "chart_value_alignment" || issue.type === "chart_year_alignment"
    ),
    "misaligned chart labels should be reported"
  );

  const overflowChartIssues = detectBarChartIssues(makeChartSlide(0, 0, 3000000));
  assert.ok(
    overflowChartIssues.some((issue) => issue.type === "chart_bar_overflow"),
    "bars outside the chart container should be reported"
  );
}

main();
