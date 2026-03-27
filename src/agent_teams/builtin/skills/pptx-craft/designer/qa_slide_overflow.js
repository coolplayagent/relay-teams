function detectSlideOverflow(elements, slideRect, tolerance = 5) {
  const overflows = [];
  const {
    left: slideLeft,
    top: slideTop,
    width: slideWidth,
    height: slideHeight,
  } = slideRect;

  for (const element of elements) {
    const { rect, text } = element;
    if (rect.width <= 0 || rect.height <= 0) continue;

    const relLeft = rect.left - slideLeft;
    const relTop = rect.top - slideTop;
    const relRight = relLeft + rect.width;
    const relBottom = relTop + rect.height;
    const details = [];

    if (relLeft < -tolerance) details.push({ side: "left", amount: Math.abs(relLeft) });
    if (relTop < -tolerance) details.push({ side: "top", amount: Math.abs(relTop) });
    if (relRight > slideWidth + tolerance) {
      details.push({ side: "right", amount: relRight - slideWidth });
    }
    if (relBottom > slideHeight + tolerance) {
      details.push({ side: "bottom", amount: relBottom - slideHeight });
    }

    if (details.length > 0) {
      overflows.push({
        text: text || "<unknown>",
        details,
      });
    }
  }

  return overflows;
}

function buildOverflowElements(allElements, hasVisualContent) {
  const elements = [];

  for (const el of allElements) {
    const style = window.getComputedStyle(el);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.opacity === "0" ||
      !hasVisualContent(el, style)
    ) {
      continue;
    }

    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;

    elements.push({
      el,
      rect,
      text: (el.innerText || "").trim().slice(0, 30) || `<${el.tagName.toLowerCase()}>`,
      style,
    });
  }

  return elements;
}

module.exports = {
  detectSlideOverflow,
  buildOverflowElements,
};
