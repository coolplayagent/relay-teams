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

function isAncestor(a, b) {
  let node = b.parentElement;
  while (node) {
    if (node === a) return true;
    node = node.parentElement;
  }
  return false;
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

function clipToSlide(rect, slideLeft, slideTop, slideWidth, slideHeight) {
  return {
    left: Math.max(0, rect.left - slideLeft),
    top: Math.max(0, rect.top - slideTop),
    right: Math.min(slideWidth, rect.right - slideLeft),
    bottom: Math.min(slideHeight, rect.bottom - slideTop),
  };
}

function isBackgroundOrDecor(elementInfo, slideWidth, slideHeight) {
  const { rect } = elementInfo;
  if (rect.width >= slideWidth && rect.height >= slideHeight) return true;
  if (
    (rect.width >= slideWidth - 2 && rect.height <= 8) ||
    (rect.height >= slideHeight - 2 && rect.width <= 8)
  ) {
    return true;
  }
  return false;
}

module.exports = {
  rectIntersectionArea,
  isAncestor,
  isVisualLeaf,
  hasVisualContent,
  isLeafTextNode,
  clipToSlide,
  isBackgroundOrDecor,
};
