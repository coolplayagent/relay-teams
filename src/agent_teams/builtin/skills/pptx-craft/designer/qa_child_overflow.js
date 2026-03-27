function detectChildOverflow(elements, slideEl, tolerance = 2) {
  const childOverflows = [];
  const processedSet = new WeakSet();

  for (const element of elements) {
    const { el, parent: parentEl, rect: elRect, text } = element;
    if (!parentEl || processedSet.has(el)) continue;

    const parentStyle = window.getComputedStyle(parentEl);
    const hasOverflowHidden =
      parentStyle.overflow === "hidden" ||
      parentStyle.overflowX === "hidden" ||
      parentStyle.overflowY === "hidden";

    if (
      (parentStyle.position === "static" || parentStyle.position === "") &&
      parentStyle.height === "auto" &&
      !hasOverflowHidden
    ) {
      continue;
    }

    const parentRect = parentEl.getBoundingClientRect();
    if (
      elRect.width <= 0 ||
      elRect.height <= 0 ||
      parentRect.width <= 0 ||
      parentRect.height <= 0
    ) {
      continue;
    }

    const details = [];
    let clipped = false;

    if (elRect.left < parentRect.left - tolerance) {
      details.push({ side: "left", amount: Math.round(parentRect.left - elRect.left) });
      clipped = clipped || hasOverflowHidden;
    }
    if (elRect.top < parentRect.top - tolerance) {
      details.push({ side: "top", amount: Math.round(parentRect.top - elRect.top) });
      clipped = clipped || hasOverflowHidden;
    }
    if (elRect.right > parentRect.right + tolerance) {
      details.push({ side: "right", amount: Math.round(elRect.right - parentRect.right) });
      clipped = clipped || hasOverflowHidden;
    }
    if (elRect.bottom > parentRect.bottom + tolerance) {
      details.push({ side: "bottom", amount: Math.round(elRect.bottom - parentRect.bottom) });
      clipped = clipped || hasOverflowHidden;
    }

    if (details.length > 0) {
      const parentDesc = `<${parentEl.tagName.toLowerCase()}${
        parentEl.className ? "." + parentEl.className.split(" ")[0] : ""
      }>`;
      childOverflows.push({
        child: text || `<${el.tagName.toLowerCase()}>`,
        parent: parentDesc,
        details,
        clipped,
        overflowType: "parent_overflow",
      });
      processedSet.add(el);
      continue;
    }

    let ancestor = parentEl.parentElement;
    while (ancestor && ancestor !== slideEl) {
      const ancestorStyle = window.getComputedStyle(ancestor);
      const hasAncestorOverflowHidden =
        ancestorStyle.overflow === "hidden" ||
        ancestorStyle.overflowX === "hidden" ||
        ancestorStyle.overflowY === "hidden";

      if (hasAncestorOverflowHidden) {
        const ancestorRect = ancestor.getBoundingClientRect();
        const ancestorDetails = [];

        if (elRect.left < ancestorRect.left - tolerance) {
          ancestorDetails.push({
            side: "left",
            amount: Math.round(ancestorRect.left - elRect.left),
          });
        }
        if (elRect.top < ancestorRect.top - tolerance) {
          ancestorDetails.push({
            side: "top",
            amount: Math.round(ancestorRect.top - elRect.top),
          });
        }
        if (elRect.right > ancestorRect.right + tolerance) {
          ancestorDetails.push({
            side: "right",
            amount: Math.round(elRect.right - ancestorRect.right),
          });
        }
        if (elRect.bottom > ancestorRect.bottom + tolerance) {
          ancestorDetails.push({
            side: "bottom",
            amount: Math.round(elRect.bottom - ancestorRect.bottom),
          });
        }

        if (ancestorDetails.length > 0) {
          const ancestorDesc = `<${ancestor.tagName.toLowerCase()}${
            ancestor.className ? "." + ancestor.className.split(" ")[0] : ""
          }>`;
          childOverflows.push({
            child: text || `<${el.tagName.toLowerCase()}>`,
            parent: ancestorDesc,
            details: ancestorDetails,
            clipped: true,
            overflowType: "ancestor_clip",
          });
          processedSet.add(el);
        }
        break;
      }

      ancestor = ancestor.parentElement;
    }
  }

  return childOverflows;
}

function buildOverflowElements(allElements, hasVisualContent, slideEl) {
  const elements = [];

  for (const el of allElements) {
    const parent = el.parentElement;
    if (!parent || parent === slideEl) continue;

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
      parent,
      rect,
      style,
      text: (el.innerText || "").trim().slice(0, 30) || `<${el.tagName.toLowerCase()}>`,
    });
  }

  return elements;
}

module.exports = {
  detectChildOverflow,
  buildOverflowElements,
};
