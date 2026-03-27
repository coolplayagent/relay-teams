// src/utils.js

// canvas context for color normalization
let _ctx;
function getCtx() {
  if (!_ctx) _ctx = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  return _ctx;
}

function getTableBorder(style, side, scale) {
  const widthStr = style.getPropertyValue(`border-${side.toLowerCase()}-width`) || style[`border${side}Width`];
  const styleStr = style.getPropertyValue(`border-${side.toLowerCase()}-style`) || style[`border${side}Style`];
  const colorStr = style.getPropertyValue(`border-${side.toLowerCase()}-color`) || style[`border${side}Color`];
  
  const width = parseFloat(widthStr) || 0;
  if (width === 0 || styleStr === 'none' || styleStr === 'hidden') {
    return { type: 'none' };
  }

  const color = parseColor(colorStr);
  if (!color.hex || color.opacity === 0) return { type: 'none' };

  let dash = 'solid';
  if (styleStr === 'dashed') dash = 'dash';
  if (styleStr === 'dotted') dash = 'dot';

  return {
    pt: width * 0.75 * scale,
    color: color.hex,
    type: dash,
  };
}

/**
 * Extracts native table data for PptxGenJS.
 */
export function extractTableData(node, scale) {
  const rows = [];
  const colWidths = [];
  const rowHeights = [];

  // 1. Calculate Column Widths based on the first row of cells
  // We look at the first <tr>'s children to determine visual column widths.
  // Note: This assumes a fixed grid. Complex colspan/rowspan on the first row
  // might skew widths, but getBoundingClientRect captures the rendered result.
  const firstRow = node.querySelector('tr');
  if (firstRow) {
    const cells = Array.from(firstRow.children);
    cells.forEach((cell) => {
      const rect = cell.getBoundingClientRect();
      const wIn = rect.width * (1 / 96) * scale;
      colWidths.push(wIn);
    });
  }

  // 2. Iterate Rows
  const trList = node.querySelectorAll('tr');
  trList.forEach((tr) => {
    const rowData = [];
    const cellList = Array.from(tr.children).filter((c) => ['TD', 'TH'].includes(c.tagName));
    
    const trRect = tr.getBoundingClientRect();
    const trHeightIn = trRect.height * (1 / 96) * scale;
    rowHeights.push(trHeightIn);

    const trStyle = window.getComputedStyle(tr);
    const trBorderBottom = getTableBorder(trStyle, 'Bottom', scale);

    cellList.forEach((cell) => {
      const style = window.getComputedStyle(cell);
      const cellText = cell.innerText.replace(/[\n\r\t]+/g, ' ').trim();

      // A. Text Style
      const textStyle = getTextStyle(style, scale);

      // B. Cell Background
      // PptxGenJS expects fill to be a string (hex color), not an object
      // Check cell's own background first, then inherit from parent elements
      let bg = parseColor(style.backgroundColor);
      
      // If cell has no background, check parent elements in order: <tr>, <thead>/<tbody>, <table>, container
      let parentRow = null;
      if (!bg.hex || bg.opacity === 0) {
        parentRow = cell.closest('tr');
        if (parentRow) {
          const rowStyle = window.getComputedStyle(parentRow);
          bg = parseColor(rowStyle.backgroundColor);
        }
      }

      // 检查 <thead> 或 <tbody> 的背景色（表格章节元素的特殊处理）
      if (!bg.hex || bg.opacity === 0) {
        const section = parentRow ? parentRow.closest('thead, tbody') : null;
        if (section) {
          const sectionStyle = window.getComputedStyle(section);
          bg = parseColor(sectionStyle.backgroundColor);
        }
      }

      // If still no background, check table container (e.g., div.bg-gray-50)
      if (!bg.hex || bg.opacity === 0) {
        const table = cell.closest('table');
        if (table && table.parentElement) {
          const containerStyle = window.getComputedStyle(table.parentElement);
          bg = parseColor(containerStyle.backgroundColor);
        }
      }

      // PptxGenJS 支持带透明度的 fill：{ color: 'hex', transparency: 0-100 }
      // transparency: 0 = 完全不透明，100 = 完全透明
      let fill = null;
      if (bg.hex && bg.opacity > 0) {
        if (bg.opacity >= 1) {
          fill = bg.hex; // 完全不透明，直接用 hex 字符串
        } else {
          fill = { color: bg.hex, transparency: Math.round((1 - bg.opacity) * 100) };
        }
      }

      // C. Alignment
      let align = 'left';
      if (style.textAlign === 'center') align = 'center';
      if (style.textAlign === 'right' || style.textAlign === 'end') align = 'right';

      let valign = 'top';
      if (style.verticalAlign === 'middle') valign = 'middle';
      if (style.verticalAlign === 'bottom') valign = 'bottom';

      // D. Padding (Margins in PPTX)
      // CSS Padding px -> PPTX Margin pt
      const padding = getPadding(style, scale);
      // getPadding returns [top, right, bottom, left] in inches relative to scale
      // PptxGenJS expects points (pt) for margin: [t, r, b, l]
      // or discrete properties. Let's use discrete for clarity.
      const margin = [
        padding[0] * 72, // top
        padding[1] * 72, // right
        padding[2] * 72, // bottom
        padding[3] * 72, // left
      ];

      // E. Borders - 优先使用 tr 的边框（行与行之间的分隔线）
      let borderTop = getTableBorder(style, 'Top', scale);
      let borderRight = getTableBorder(style, 'Right', scale);
      let borderBottom = getTableBorder(style, 'Bottom', scale);
      let borderLeft = getTableBorder(style, 'Left', scale);

      if (borderBottom.type === 'none' && trBorderBottom.type !== 'none') {
        borderBottom = trBorderBottom;
      }

      // F. Construct Cell Object
      rowData.push({
        text: cellText,
        options: {
          color: textStyle.color,
          fontFace: textStyle.fontFace,
          fontSize: textStyle.fontSize,
          bold: textStyle.bold,
          italic: textStyle.italic,
          underline: textStyle.underline,

          fill: fill,
          align: align,
          valign: valign,
          margin: margin,

          rowspan: parseInt(cell.getAttribute('rowspan')) || null,
          colspan: parseInt(cell.getAttribute('colspan')) || null,

          // PptxGenJS expects border as [top, right, bottom, left] array
          border: [borderTop, borderRight, borderBottom, borderLeft],
        },
      });
    });

    if (rowData.length > 0) {
      rows.push(rowData);
    }
  });

  return { rows, colWidths, rowHeights };
}

// Checks if any parent element has overflow: hidden which would clip this element
export function isClippedByParent(node) {
  let parent = node.parentElement;
  while (parent && parent !== document.body) {
    const style = window.getComputedStyle(parent);
    const overflow = style.overflow;
    if (overflow === 'hidden' || overflow === 'clip') {
      return true;
    }
    parent = parent.parentElement;
  }
  return false;
}

// Helper to save gradient text
// Helper to save gradient text: extracts the first color from a gradient string
export function getGradientFallbackColor(bgImage) {
  if (!bgImage || bgImage === 'none') return null;

  // 1. Extract content inside function(...)
  // Handles linear-gradient(...), radial-gradient(...), repeating-linear-gradient(...)
  const match = bgImage.match(/gradient\((.*)\)/);
  if (!match) return null;

  const content = match[1];

  // 2. Split by comma, respecting parentheses (to avoid splitting inside rgb(), oklch(), etc.)
  const parts = [];
  let current = '';
  let parenDepth = 0;

  for (const char of content) {
    if (char === '(') parenDepth++;
    if (char === ')') parenDepth--;
    if (char === ',' && parenDepth === 0) {
      parts.push(current.trim());
      current = '';
    } else {
      current += char;
    }
  }
  if (current) parts.push(current.trim());

  // 3. Find first part that is a color (skip angle/direction)
  for (const part of parts) {
    // Ignore directions (to right) or angles (90deg, 0.5turn)
    if (/^(to\s|[\d.]+(deg|rad|turn|grad))/.test(part)) continue;

    // Extract color: Remove trailing position (e.g. "red 50%" -> "red")
    // Regex matches whitespace + number + unit at end of string
    const colorPart = part.replace(/\s+(-?[\d.]+(%|px|em|rem|ch|vh|vw)?)$/, '');

    // Check if it's not just a number (some gradients might have bare numbers? unlikely in standard syntax)
    if (colorPart) return colorPart;
  }

  return null;
}

function mapDashType(style) {
  if (style === 'dashed') return 'dash';
  if (style === 'dotted') return 'dot';
  return 'solid';
}

/**
 * Checks if a border side is valid (visible and non-transparent).
 */
function isValidBorder(side) {
  return side.width > 0 &&
         side.color !== null &&
         side.style !== 'none' &&
         side.style !== 'hidden';
}

/**
 * Analyzes computed border styles and determines the rendering strategy.
 */
export function getBorderInfo(style, scale) {
  const topColor = parseColor(style.getPropertyValue('border-top-color') || style.borderTopColor);
  const rightColor = parseColor(style.getPropertyValue('border-right-color') || style.borderRightColor);
  const bottomColor = parseColor(style.getPropertyValue('border-bottom-color') || style.borderBottomColor);
  const leftColor = parseColor(style.getPropertyValue('border-left-color') || style.borderLeftColor);

  const top = {
    width: parseFloat(style.getPropertyValue('border-top-width') || style.borderTopWidth) || 0,
    style: style.getPropertyValue('border-top-style') || style.borderTopStyle,
    color: topColor.hex,
    opacity: topColor.opacity,
  };
  const right = {
    width: parseFloat(style.getPropertyValue('border-right-width') || style.borderRightWidth) || 0,
    style: style.getPropertyValue('border-right-style') || style.borderRightStyle,
    color: rightColor.hex,
    opacity: rightColor.opacity,
  };
  const bottom = {
    width: parseFloat(style.getPropertyValue('border-bottom-width') || style.borderBottomWidth) || 0,
    style: style.getPropertyValue('border-bottom-style') || style.borderBottomStyle,
    color: bottomColor.hex,
    opacity: bottomColor.opacity,
  };
  const left = {
    width: parseFloat(style.getPropertyValue('border-left-width') || style.borderLeftWidth) || 0,
    style: style.getPropertyValue('border-left-style') || style.borderLeftStyle,
    color: leftColor.hex,
    opacity: leftColor.opacity,
  };

  const hasAnyBorder = isValidBorder(top) || isValidBorder(right) || isValidBorder(bottom) || isValidBorder(left);
  if (!hasAnyBorder) return { type: 'none' };

  // Check if all sides are uniform
  const isUniform =
    top.width === right.width &&
    top.width === bottom.width &&
    top.width === left.width &&
    top.style === right.style &&
    top.style === bottom.style &&
    top.style === left.style &&
    top.color === right.color &&
    top.color === bottom.color &&
    top.color === left.color &&
    top.opacity === right.opacity &&
    top.opacity === bottom.opacity &&
    top.opacity === left.opacity;

  if (isUniform) {
    // 优化边框宽度：根据 Alice 反馈，边框过于明显，减小宽度系数
    const borderWidthMultiplier = 0.65; // 从 0.75 降低到 0.65
    return {
      type: 'uniform',
      options: {
        width: top.width * borderWidthMultiplier * scale,
        color: top.color,
        transparency: (1 - top.opacity) * 100,
        dashType: mapDashType(top.style),
      },
    };
  } else {
    return {
      type: 'composite',
      sides: { top, right, bottom, left },
    };
  }
}

/**
 * Generates an SVG image for composite borders that respects border-radius.
 */
export function generateCompositeBorderSVG(w, h, radius, sides) {
  // CSS 规范：border-radius 不能超过 min(width, height) / 2
  const maxRadius = Math.min(w, h) / 2;
  const clampedRadius = Math.min(radius, maxRadius);
  const adjustedRadius = clampedRadius / 2; // Adjust for SVG rendering
  const clipId = 'clip_' + Math.random().toString(36).substr(2, 9);
  let borderRects = '';

  if (sides.top.width > 0 && sides.top.color) {
    borderRects += `<rect x="0" y="0" width="${w}" height="${sides.top.width}" fill="#${sides.top.color}" />`;
  }
  if (sides.right.width > 0 && sides.right.color) {
    borderRects += `<rect x="${w - sides.right.width}" y="0" width="${sides.right.width}" height="${h}" fill="#${sides.right.color}" />`;
  }
  if (sides.bottom.width > 0 && sides.bottom.color) {
    borderRects += `<rect x="0" y="${h - sides.bottom.width}" width="${w}" height="${sides.bottom.width}" fill="#${sides.bottom.color}" />`;
  }
  if (sides.left.width > 0 && sides.left.color) {
    borderRects += `<rect x="0" y="0" width="${sides.left.width}" height="${h}" fill="#${sides.left.color}" />`;
  }

  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
        <defs>
            <clipPath id="${clipId}">
                <rect x="0" y="0" width="${w}" height="${h}" rx="${adjustedRadius}" ry="${adjustedRadius}" />
            </clipPath>
        </defs>
        <g clip-path="url(#${clipId})">
            ${borderRects}
        </g>
    </svg>`;

  return 'data:image/svg+xml;base64,' + btoa(svg);
}

/**
 * Generates an SVG data URL for a solid shape with non-uniform corner radii.
 */
export function generateCustomShapeSVG(w, h, color, opacity, radii) {
  let { tl, tr, br, bl } = radii;

  // Clamp radii using CSS spec logic (avoid overlap)
  const factor = Math.min(
    w / (tl + tr) || Infinity,
    h / (tr + br) || Infinity,
    w / (br + bl) || Infinity,
    h / (bl + tl) || Infinity
  );

  if (factor < 1) {
    tl *= factor;
    tr *= factor;
    br *= factor;
    bl *= factor;
  }

  const path = `
    M ${tl} 0
    L ${w - tr} 0
    A ${tr} ${tr} 0 0 1 ${w} ${tr}
    L ${w} ${h - br}
    A ${br} ${br} 0 0 1 ${w - br} ${h}
    L ${bl} ${h}
    A ${bl} ${bl} 0 0 1 0 ${h - bl}
    L 0 ${tl}
    A ${tl} ${tl} 0 0 1 ${tl} 0
    Z
  `;

  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
      <path d="${path}" fill="#${color}" fill-opacity="${opacity}" />
    </svg>`;

  return 'data:image/svg+xml;base64,' + btoa(svg);
}

// --- REPLACE THE EXISTING parseColor FUNCTION ---
export function parseColor(str) {
  if (!str || str === 'transparent' || str.trim() === 'rgba(0, 0, 0, 0)') {
    return { hex: null, opacity: 0 };
  }

  // 优先处理输入的 Hex 格式，避免 Canvas API 转换导致的精度损失
  if (str.trim().startsWith('#')) {
    let hex = str.trim().slice(1);
    let opacity = 1;
    if (hex.length === 3) {
      hex = hex.split('').map(c => c + c).join('');
    }
    if (hex.length === 4) {
      hex = hex.split('').map(c => c + c).join('');
    }
    if (hex.length === 8) {
      opacity = parseInt(hex.slice(6), 16) / 255;
      hex = hex.slice(0, 6);
    }
    return { hex: hex.toUpperCase(), opacity };
  }

  // 直接解析 rgba/rgb 格式，保留 alpha 通道
  // 避免依赖 Canvas API 转换导致 alpha 丢失
  // 匹配 rgba(255, 255, 255, 0.5) 或 rgb(255, 255, 255) 格式
  const colorMatch = str.match(/rgba?\s*\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)/i);
  if (colorMatch) {
    const r = Math.round(parseFloat(colorMatch[1]));
    const g = Math.round(parseFloat(colorMatch[2]));
    const b = Math.round(parseFloat(colorMatch[3]));
    // colorMatch[4] 存在时为 rgba，不存在时为 rgb（alpha 默认为 1）
    const a = colorMatch[4] !== undefined ? parseFloat(colorMatch[4]) : 1;
    const hex = ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1).toUpperCase();
    return { hex, opacity: a };
  }

  const ctx = getCtx();
  ctx.fillStyle = str;
  const computed = ctx.fillStyle;

  // 1. Handle Hex Output (e.g. #ff0000) - Fast Path
  if (computed.startsWith('#')) {
    let hex = computed.slice(1);
    let opacity = 1;
    if (hex.length === 3)
      hex = hex
        .split('')
        .map((c) => c + c)
        .join('');
    if (hex.length === 4)
      hex = hex
        .split('')
        .map((c) => c + c)
        .join('');
    if (hex.length === 8) {
      opacity = parseInt(hex.slice(6), 16) / 255;
      hex = hex.slice(0, 6);
    }
    return { hex: hex.toUpperCase(), opacity };
  }

  // 2. Handle RGB/RGBA Output (standard) - Fast Path
  if (computed.startsWith('rgb')) {
    const match = computed.match(/[\d.]+/g);
    if (match && match.length >= 3) {
      const r = parseInt(match[0]);
      const g = parseInt(match[1]);
      const b = parseInt(match[2]);
      const a = match.length > 3 ? parseFloat(match[3]) : 1;
      const hex = ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1).toUpperCase();
      return { hex, opacity: a };
    }
  }

  // 3. Fallback: Browser returned a format we don't parse (oklch, lab, color(srgb...), etc.)
  // Use Canvas API to convert to sRGB
  ctx.clearRect(0, 0, 1, 1);
  ctx.fillRect(0, 0, 1, 1);
  const data = ctx.getImageData(0, 0, 1, 1).data;
  // data = [r, g, b, a]
  const r = data[0];
  const g = data[1];
  const b = data[2];
  const a = data[3] / 255;

  if (a === 0) return { hex: null, opacity: 0 };

  const hex = ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1).toUpperCase();
  return { hex, opacity: a };
}

export function getPadding(style, scale) {
  const pxToInch = 1 / 96;
  // 优化间距：根据 Alice 反馈，所有页面间距过大，减小 padding 系数
  const paddingMultiplier = 0.85; // 减少 15% 的 padding
  return [
    (parseFloat(style.paddingTop) || 0) * pxToInch * scale * paddingMultiplier,
    (parseFloat(style.paddingRight) || 0) * pxToInch * scale * paddingMultiplier,
    (parseFloat(style.paddingBottom) || 0) * pxToInch * scale * paddingMultiplier,
    (parseFloat(style.paddingLeft) || 0) * pxToInch * scale * paddingMultiplier,
  ];
}

export function getSoftEdges(filterStr, backdropFilterStr, scale) {
  // 优先从 backdropFilter 获取 blur 值（Tailwind blur-3xl 在这里）
  if (backdropFilterStr && backdropFilterStr !== 'none') {
    const match = backdropFilterStr.match(/blur\(([\d.]+)px\)/);
    if (match) return parseFloat(match[1]) * 0.75 * scale;
  }
  // 回退到 filter
  if (filterStr && filterStr !== 'none') {
    const match = filterStr.match(/blur\(([\d.]+)px\)/);
    if (match) return parseFloat(match[1]) * 0.75 * scale;
  }
  return null;
}

export function getTextStyle(style, scale) {
  let colorObj = parseColor(style.color);

  const bgClip = style.webkitBackgroundClip || style.backgroundClip;
  if (colorObj.opacity === 0 && bgClip === 'text') {
    const fallback = getGradientFallbackColor(style.backgroundImage);
    if (fallback) colorObj = parseColor(fallback);
  }

  const fontSizePx = parseFloat(style.fontSize);
  const fontWeight = parseInt(style.fontWeight) || 400;

  // 优化字体大小转换：px -> pt 转换系数
  // CSS px 到 PPT pt 的标准转换是 0.75 (1px = 0.75pt)
  let fontSizeMultiplier = 0.75;

  const calculatedFontSize = Math.floor(fontSizePx * fontSizeMultiplier * scale);

  // 字重判断：CSS font-weight 值
  // 400 = normal, 500 = medium, 600 = semibold, 700 = bold
  // semibold (600) 在视觉上已经是加粗效果，应该设为 bold
  const shouldBeBold = fontWeight >= 600;

  return {
    color: colorObj.hex || '000000',
    fontFace: style.fontFamily.split(',')[0].replace(/['"]/g, ''),
    fontSize: calculatedFontSize,
    bold: shouldBeBold,
    italic: style.fontStyle === 'italic',
    underline: style.textDecoration.includes('underline'),
    // Map background color to highlight if present (only for sufficiently opaque backgrounds)
    ...(() => {
      const bgColor = parseColor(style.backgroundColor);
      return bgColor.hex && bgColor.opacity >= 0.3
        ? { highlight: bgColor.hex }
        : {};
    })(),
  };
}

/**
 * Determines if a given DOM node is primarily a text container.
 * Updated to correctly reject Icon elements so they are rendered as images.
 */
export function isTextContainer(node) {
  const hasText = node.textContent.trim().length > 0;
  if (!hasText) return false;

  const children = Array.from(node.children);
  if (children.length === 0) return true;

  // Check for flex/grid layouts - these should NOT be treated as single text containers
  // because flex/grid layout changes how children are positioned, and whitespace text nodes
  // between items should not be preserved
  const nodeStyle = window.getComputedStyle(node);
  const isFlexContainer = nodeStyle.display === 'flex' || nodeStyle.display === 'inline-flex';
  const isGridContainer = nodeStyle.display === 'grid' || nodeStyle.display === 'inline-grid';
  
  // All flex/grid containers should NOT be treated as text containers
  // because they position children independently regardless of direction or justification
  if (isFlexContainer || isGridContainer) {
    return false;
  }

  const isSafeInline = (el) => {
    // 1. Reject Web Components / Custom Elements
    if (el.tagName.includes('-')) return false;
    // 2. Reject Explicit Images/SVGs
    if (el.tagName === 'IMG' || el.tagName === 'SVG') return false;

    // 3. Reject Class-based Icons (FontAwesome, Material, Bootstrap, etc.)
    // If an <i> or <span> has icon classes, it is a visual object, not text.
    if (el.tagName === 'I' || el.tagName === 'SPAN') {
      const elCls = el.getAttribute('class') || '';
      if (
        elCls.includes('fa-') ||
        elCls.includes('fas') ||
        elCls.includes('far') ||
        elCls.includes('fab') ||
        elCls.includes('material-icons') ||
        elCls.includes('bi-') ||
        elCls.includes('icon')
      ) {
        return false;
      }
    }

    const style = window.getComputedStyle(el);
    const display = style.display;

    // 4. Reject flex/grid containers that act as layout blocks
    const isLayoutContainer = display === 'flex' || display === 'inline-flex' || display === 'grid' || display === 'inline-grid';
    if (isLayoutContainer) {
      // Check if this container has icon children
      const hasIconChild = Array.from(el.children).some(child => {
        if (child.tagName === 'I') {
          const childCls = child.getAttribute('class') || '';
          return childCls.includes('fa-') || childCls.includes('fas') || childCls.includes('far') || childCls.includes('fab');
        }
        return false;
      });
      if (hasIconChild) {
        return false;
      }
    }

    // 5. Standard Inline Tag Check
    const isInlineTag = ['SPAN', 'B', 'STRONG', 'EM', 'I', 'A', 'SMALL', 'MARK'].includes(
      el.tagName
    );
    const isInlineDisplay = display.includes('inline');

    if (!isInlineTag && !isInlineDisplay) return false;

    // 5. Structural Styling Check
    // If a child has a background or border, it's a layout block, not a simple text span.
    const bgColor = parseColor(style.backgroundColor);
    const hasVisibleBg = bgColor.hex && bgColor.opacity > 0;
    const hasBorder =
      parseFloat(style.borderWidth) > 0 && parseColor(style.borderColor).opacity > 0;

    if (hasVisibleBg || hasBorder) {
      return false;
    }

    // 4. Check for empty shapes (visual objects without text, like dots)
    const hasContent = el.textContent.trim().length > 0;
    if (!hasContent && (hasVisibleBg || hasBorder)) {
      return false;
    }

    return true;
  };

  return children.every(isSafeInline);
}

export function getRotation(transformStr) {
  if (!transformStr || transformStr === 'none') return 0;
  const values = transformStr.split('(')[1].split(')')[0].split(',');
  if (values.length < 4) return 0;
  const a = parseFloat(values[0]);
  const b = parseFloat(values[1]);
  return Math.round(Math.atan2(b, a) * (180 / Math.PI));
}

/**
 * Converts an SVG node to a PNG data URL (rasterized)
 */
export function svgToPng(node) {
  return new Promise((resolve) => {
    const clone = node.cloneNode(true);
    const rect = node.getBoundingClientRect();
    const width = rect.width || 300;
    const height = rect.height || 150;

    inlineSvgStyles(node, clone);
    clone.setAttribute('width', width);
    clone.setAttribute('height', height);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');

    const xml = new XMLSerializer().serializeToString(clone);
    const svgUrl = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(xml)}`;
    const img = new Image();
    img.crossOrigin = 'Anonymous';
    img.onload = () => {
      const canvas = document.createElement('canvas');
      const scale = 3;
      canvas.width = width * scale;
      canvas.height = height * scale;
      const ctx = canvas.getContext('2d');
      ctx.scale(scale, scale);
      ctx.clearRect(0, 0, width, height);
      ctx.drawImage(img, 0, 0, width, height);
      resolve(canvas.toDataURL('image/png'));
    };
    img.onerror = () => resolve(null);
    img.src = svgUrl;
  });
}

/**
 * Converts an SVG node to an SVG data URL (preserves vector format)
 * This allows "Convert to Shape" in PowerPoint
 */
export function svgToSvg(node) {
  return new Promise((resolve) => {
    try {
      const clone = node.cloneNode(true);
      const rect = node.getBoundingClientRect();
      const width = rect.width || 300;
      const height = rect.height || 150;

      inlineSvgStyles(node, clone);
      clone.setAttribute('width', width);
      clone.setAttribute('height', height);
      clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');

      // Ensure xmlns:xlink is present for any xlink:href attributes
      if (clone.querySelector('[*|href]') || clone.innerHTML.includes('xlink:')) {
        clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
      }

      const xml = new XMLSerializer().serializeToString(clone);
      // Use base64 encoding for better compatibility with PowerPoint
      const svgUrl = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(xml)))}`;
      resolve(svgUrl);
    } catch (e) {
      console.warn('SVG serialization failed:', e);
      resolve(null);
    }
  });
}

/**
 * Helper to inline computed styles into an SVG clone
 */
function inlineSvgStyles(source, target) {
  const computed = window.getComputedStyle(source);
  const properties = [
    'fill',
    'stroke',
    'stroke-width',
    'stroke-linecap',
    'stroke-linejoin',
    'opacity',
    'font-family',
    'font-size',
    'font-weight',
  ];

  if (computed.fill === 'none') target.setAttribute('fill', 'none');
  else if (computed.fill) target.style.fill = computed.fill;

  if (computed.stroke === 'none') target.setAttribute('stroke', 'none');
  else if (computed.stroke) target.style.stroke = computed.stroke;

  properties.forEach((prop) => {
    if (prop !== 'fill' && prop !== 'stroke') {
      const val = computed[prop];
      if (val && val !== 'auto') target.style[prop] = val;
    }
  });

  for (let i = 0; i < source.children.length; i++) {
    if (target.children[i]) inlineSvgStyles(source.children[i], target.children[i]);
  }
}

export function getVisibleShadow(shadowStr, scale) {
  if (!shadowStr || shadowStr === 'none') return null;
  const shadows = shadowStr.split(/,(?![^()]*\))/);
  for (let s of shadows) {
    s = s.trim();
    if (s.startsWith('rgba(0, 0, 0, 0)')) continue;
    const match = s.match(
      /(rgba?\([^)]+\)|#[0-9a-fA-F]+)\s+(-?[\d.]+)px\s+(-?[\d.]+)px\s+([\d.]+)px/
    );
    if (match) {
      const colorStr = match[1];
      const x = parseFloat(match[2]);
      const y = parseFloat(match[3]);
      const blur = parseFloat(match[4]);
      const distance = Math.sqrt(x * x + y * y);
      let angle = Math.atan2(y, x) * (180 / Math.PI);
      if (angle < 0) angle += 360;
      const colorObj = parseColor(colorStr);
      return {
        type: 'outer',
        angle: angle,
        blur: blur * 0.75 * scale,
        offset: distance * 0.75 * scale,
        color: colorObj.hex || '000000',
        opacity: colorObj.opacity,
      };
    }
  }
  return null;
}

/**
 * Generates an SVG image for gradients, supporting degrees and keywords.
 */
export function generateGradientSVG(w, h, bgString, radius, border) {
  try {
    const match = bgString.match(/linear-gradient\((.*)\)/);
    if (!match) return null;
    const content = match[1];

    // Split by comma, ignoring commas inside parentheses (e.g. rgba())
    const parts = content.split(/,(?![^()]*\))/).map((p) => p.trim());
    if (parts.length < 2) return null;

    let x1 = '0%',
      y1 = '0%',
      x2 = '0%',
      y2 = '100%';
    let stopsStartIndex = 0;
    const firstPart = parts[0].toLowerCase();

    // 1. Check for Keywords (to right, etc.)
    if (firstPart.startsWith('to ')) {
      stopsStartIndex = 1;
      const direction = firstPart.replace('to ', '').trim();
      switch (direction) {
        case 'top':
          y1 = '100%';
          y2 = '0%';
          break;
        case 'bottom':
          y1 = '0%';
          y2 = '100%';
          break;
        case 'left':
          x1 = '100%';
          y1 = '0%';
          x2 = '0%';
          y2 = '0%';
          break;
        case 'right':
          x1 = '0%';
          y1 = '0%';
          x2 = '100%';
          y2 = '0%';
          break;
        case 'top right':
          x1 = '0%';
          y1 = '100%';
          x2 = '100%';
          y2 = '0%';
          break;
        case 'top left':
          x1 = '100%';
          y1 = '100%';
          x2 = '0%';
          y2 = '0%';
          break;
        case 'bottom right':
          x1 = '0%';
          y1 = '0%';
          x2 = '100%';
          y2 = '100%';
          break;
        case 'bottom left':
          x1 = '100%';
          y1 = '0%';
          x2 = '0%';
          y2 = '100%';
          break;
      }
    }
    // 2. Check for Degrees (45deg, 90deg, etc.)
    else if (firstPart.match(/^-?[\d.]+(deg|rad|turn|grad)$/)) {
      stopsStartIndex = 1;
      const val = parseFloat(firstPart);
      // CSS linear-gradient 角度规范：
      // - 0deg = 向上（从下到上）
      // - 90deg = 向右（从左到右）
      // - 135deg = 向右下（从左上到右下）
      // - 角度顺时针增加
      // 
      // SVG linearGradient 使用 x1,y1 到 x2,y2 定义渐变向量
      // CSS 角度转换为 SVG 方向向量：
      // dx = sin(cssAngle), dy = -cos(cssAngle)
      if (!isNaN(val)) {
        let deg = val;
        if (firstPart.includes('rad')) deg = val * (180 / Math.PI);
        if (firstPart.includes('turn')) deg = val * 360;
        if (firstPart.includes('grad')) deg = val * 0.9;

        const cssRad = (deg * Math.PI) / 180;
        const dx = Math.sin(cssRad);
        const dy = -Math.cos(cssRad);

        // 从中心点向两个方向延伸，形成渐变向量
        const scale = 50;
        x1 = (50 - dx * scale).toFixed(1) + '%';
        y1 = (50 - dy * scale).toFixed(1) + '%';
        x2 = (50 + dx * scale).toFixed(1) + '%';
        y2 = (50 + dy * scale).toFixed(1) + '%';
      }
    }

    // 3. Process Color Stops
    let stopsXML = '';
    const stopParts = parts.slice(stopsStartIndex);

    // Pre-parse all colors to handle 'transparent' keyword
    const parsedStops = stopParts.map((part, idx) => {
      let color = part;
      let offset = Math.round((idx / (stopParts.length - 1)) * 100) + '%';

      const posMatch = part.match(/^(.*?)\s+(-?[\d.]+(?:%|px)?)$/);
      if (posMatch) {
        color = posMatch[1];
        offset = posMatch[2];
      }

      return { color: color.trim(), offset, idx };
    });

    // Generate stopsXML, handling 'transparent' by using adjacent color's RGB with opacity=0
    parsedStops.forEach((stop, idx) => {
      let color = stop.color;
      let opacity = 1;

      // 颜色校准：确保 RGB 值精确匹配（修复颜色偏移问题）
      // 使用 parseColor 统一处理所有颜色格式
      const normalizeColor = (colorStr) => {
        const parsed = parseColor(colorStr);
        if (parsed.hex) {
          const r = parseInt(parsed.hex.slice(0, 2), 16);
          const g = parseInt(parsed.hex.slice(2, 4), 16);
          const b = parseInt(parsed.hex.slice(4, 6), 16);
          return { rgb: `rgb(${r},${g},${b})`, opacity: parsed.opacity };
        }
        return { rgb: colorStr, opacity: 1 };
      };

      // Handle 'transparent' keyword and 'rgba(0, 0, 0, 0)': use adjacent color's RGB with opacity=0
      const isTransparent = color.toLowerCase() === 'transparent' ||
                            color === 'rgba(0, 0, 0, 0)' ||
                            color === 'rgba(0,0,0,0)';

      if (isTransparent) {
        // Find adjacent color (prefer previous, otherwise next)
        let neighborColor = null;
        const isStopTransparent = (c) => {
          const lower = c.toLowerCase();
          return lower === 'transparent' || c === 'rgba(0, 0, 0, 0)' || c === 'rgba(0,0,0,0)';
        };
        for (let i = idx - 1; i >= 0; i--) {
          if (!isStopTransparent(parsedStops[i].color)) {
            neighborColor = parsedStops[i].color;
            break;
          }
        }
        if (!neighborColor) {
          for (let i = idx + 1; i < parsedStops.length; i++) {
            if (!isStopTransparent(parsedStops[i].color)) {
              neighborColor = parsedStops[i].color;
              break;
            }
          }
        }

        // Convert neighbor color to rgb format with opacity=0
        if (neighborColor) {
          const normalized = normalizeColor(neighborColor);
          color = normalized.rgb;
          opacity = 0;
        } else {
          color = 'rgb(255,255,255)';
          opacity = 0;
        }
      } else if (color.includes('rgba')) {
        // Handle RGBA/RGB for SVG compatibility - 使用统一的颜色规范化
        const rgbaMatch = color.match(/[\d.]+/g);
        if (rgbaMatch && rgbaMatch.length >= 4) {
          opacity = parseFloat(rgbaMatch[3]);
          // 确保 RGB 值精确（修复颜色偏移）
          const r = Math.round(parseFloat(rgbaMatch[0]));
          const g = Math.round(parseFloat(rgbaMatch[1]));
          const b = Math.round(parseFloat(rgbaMatch[2]));
          color = `rgb(${r},${g},${b})`;
        }
      } else if (color.includes('rgb')) {
        // Handle RGB - 确保颜色精确
        const normalized = normalizeColor(color);
        color = normalized.rgb;
        opacity = normalized.opacity;
      } else {
        // 对于其他颜色格式（hex, named colors），使用 parseColor 提取透明度
        const normalized = normalizeColor(color);
        color = normalized.rgb;
        opacity = normalized.opacity;
      }

      stopsXML += `<stop offset="${stop.offset}" stop-color="${color}" stop-opacity="${opacity}"/>`;
    });

    let strokeAttr = '';
    if (border) {
      strokeAttr = `stroke="#${border.color}" stroke-width="${border.width}"`;
    }

    // CSS 规范：border-radius 不能超过 min(width, height) / 2
    // 当 border-radius 值过大时（如 9999px），实际渲染效果是胶囊形状
    const maxRadius = Math.min(w, h) / 2;
    const clampedRadius = Math.min(radius, maxRadius);

    const svg = `
      <svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
          <defs>
            <linearGradient id="grad" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}">
              ${stopsXML}
            </linearGradient>
          </defs>
          <rect x="0" y="0" width="${w}" height="${h}" rx="${clampedRadius}" ry="${clampedRadius}" fill="url(#grad)" ${strokeAttr} />
      </svg>`;

    // Check if any stop has opacity < 1, if so we need PNG for proper transparency
    // 修复：检查所有透明度值，不仅仅是 opacity="0"
    const hasTransparency = parsedStops.some(stop => {
      if (stop.color.toLowerCase() === 'transparent' ||
          stop.color === 'rgba(0, 0, 0, 0)' ||
          stop.color === 'rgba(0,0,0,0)') {
        return true;
      }
      if (stop.color.includes('rgba')) {
        const rgbaMatch = stop.color.match(/[\d.]+/g);
        if (rgbaMatch && rgbaMatch.length >= 4) {
          const alpha = parseFloat(rgbaMatch[3]);
          return alpha < 1;
        }
      }
      return false;
    });

    if (hasTransparency) {
      // Return SVG string for async PNG conversion
      return { svg, width: w, height: h, needsPngConversion: true };
    }

    return 'data:image/svg+xml;base64,' + btoa(svg);
  } catch (e) {
    console.warn('Gradient generation failed:', e);
    return null;
  }
}

/**
 * Converts an SVG string to PNG data URL (for gradients with transparency)
 * 优化：提高渲染质量以保留半透明效果
 */
export function svgStringToPng(svgString, width, height) {
  return new Promise((resolve) => {
    const svgUrl = 'data:image/svg+xml;base64,' + btoa(svgString);
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      // 提高 scale 到 4 以获得更好的半透明渲染质量
      const scale = 4;
      canvas.width = width * scale;
      canvas.height = height * scale;
      const ctx = canvas.getContext('2d', {
        alpha: true,  // 确保支持 alpha 通道
        willReadFrequently: false
      });
      // 禁用图像平滑以保持锐度
      ctx.imageSmoothingEnabled = false;
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0, width, height);
      resolve(canvas.toDataURL('image/png'));
    };
    img.onerror = () => {
      resolve(null);
    };
    img.src = svgUrl;
  });
}

export function generateBlurredSVG(w, h, color, opacity, radius, blurPx) {
  const padding = blurPx * 3;
  const fullW = w + padding * 2;
  const fullH = h + padding * 2;
  const x = padding;
  const y = padding;
  let shapeTag = '';

  // CSS 规范：border-radius 不能超过 min(width, height) / 2
  const maxRadius = Math.min(w, h) / 2;
  const clampedRadius = Math.min(radius, maxRadius);
  const isCircle = clampedRadius >= maxRadius - 1 && Math.abs(w - h) < 2;

  if (isCircle) {
    const cx = x + w / 2;
    const cy = y + h / 2;
    const rx = w / 2;
    const ry = h / 2;
    shapeTag = `<ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}" fill="#${color}" fill-opacity="${opacity}" filter="url(#f1)" />`;
  } else {
    shapeTag = `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${clampedRadius}" ry="${clampedRadius}" fill="#${color}" filter="url(#f1)" />`;
  }

  const svg = `
  <svg xmlns="http://www.w3.org/2000/svg" width="${fullW}" height="${fullH}" viewBox="0 0 ${fullW} ${fullH}">
    <defs>
      <filter id="f1" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="${blurPx}" />
      </filter>
    </defs>
    ${shapeTag}
  </svg>`;

  return {
    data: 'data:image/svg+xml;base64,' + btoa(svg),
    padding: padding,
  };
}

// ============ Line Break Detection Utilities ============

/**
 * Normalizes text by replacing newlines/tabs with spaces and collapsing multiple spaces.
 */
function normalizeText(text) {
  return text.replace(/[\n\r\t]+/g, ' ').replace(/\s{2,}/g, ' ');
}

/**
 * Builds a mapping from processed text position to original DOM position.
 */
function buildCharToDomMap(textNode, processedText, startIdx = 0) {
  const originalText = textNode.textContent;
  const map = [];
  let origIdx = 0, procIdx = 0;
  
  while (origIdx < originalText.length && procIdx < processedText.length) {
    const origChar = originalText[origIdx];
    const procChar = processedText[procIdx];
    
    if (origChar === procChar) {
      map.push({ node: textNode, localIdx: origIdx, globalIdx: startIdx + procIdx });
      origIdx++; procIdx++;
    } else if (origChar === '\n' || origChar === '\r' || origChar === '\t') {
      origIdx++;
    } else if (origChar === ' ' && procChar === ' ') {
      map.push({ node: textNode, localIdx: origIdx, globalIdx: startIdx + procIdx });
      origIdx++; procIdx++;
    } else if (origChar === ' ') {
      origIdx++;
    } else {
      map.push({ node: textNode, localIdx: origIdx, globalIdx: startIdx + procIdx });
      origIdx++; procIdx++;
    }
  }
  return map;
}

/**
 * Detects line breaks from a character-to-DOM mapping.
 * `isVertical` 为 true 时用于竖排文字（writing-mode: vertical-*），
 * 此时只根据 top 变化判断换行；横排文字则同时结合 left 判断是否“回到行首”。
 */
function detectBreaksFromMap(charToDomMap, processedText, isVertical = false) {
  const range = document.createRange();
  const breaks = [];
  let prevTop = null;
  let prevLeft = null;
  
  for (let i = 0; i < charToDomMap.length; i++) {
    const { node, localIdx, globalIdx } = charToDomMap[i];
    const procChar = processedText[globalIdx];
    
    if (procChar === ' ' || procChar === '\t' || procChar === '\n') continue;
    
    range.setStart(node, localIdx);
    range.setEnd(node, localIdx + 1);
    const rect = range.getBoundingClientRect();
    
    if (prevTop !== null) {
      let isNewLine = false;

      if (isVertical) {
        // 竖排文字：只要下一字符明显更靠下即可视为新行
        isNewLine = rect.top > prevTop + 2;
      } else {
        // 横排文字：只有当“下一字符显著更靠下，且水平位置回到本行更靠左的位置”时才认为是新的一行，
        // 这样可以避免因为不同字号/基线导致的小幅 top 变化被误判为换行。
        isNewLine =
          rect.top > prevTop + 2 &&
          (prevLeft === null || rect.left < prevLeft - 1);
      }

      if (isNewLine) {
        let breakPos = globalIdx;
        while (
          breakPos > 0 &&
          (processedText[breakPos - 1] === ' ' ||
            processedText[breakPos - 1] === '\t')
        ) {
          breakPos--;
        }
        breaks.push(breakPos);
      }
    }

    prevTop = rect.top;
    prevLeft = rect.left;
  }
  
  range.detach();
  return breaks;
}

/**
 * Finds the first or last non-whitespace character position in a node.
 */
function findNonWhitespaceChar(node, findFirst) {
  if (node.nodeType === 3) {
    const text = node.textContent;
    for (let i = findFirst ? 0 : text.length - 1; findFirst ? i < text.length : i >= 0; findFirst ? i++ : i--) {
      if (text[i] !== ' ' && text[i] !== '\t' && text[i] !== '\n') {
        return { node, index: i };
      }
    }
    return null;
  }
  
  if (node.nodeType === 1) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT, null, false);
    const textNodes = [];
    let textNode;
    while ((textNode = walker.nextNode()) !== null) {
      if (textNode.textContent.trim()) textNodes.push(textNode);
    }
    if (textNodes.length === 0) return null;
    return findNonWhitespaceChar(findFirst ? textNodes[0] : textNodes[textNodes.length - 1], findFirst);
  }
  
  return null;
}

/**
 * Detects actual visual line breaks in a text element using the Range API.
 */
export function detectLineBreaks(element) {
  const style = window.getComputedStyle(element);
  if (style.whiteSpace === 'nowrap' || style.whiteSpace === 'pre') return null;
  const writingMode = style.writingMode || style.webkitWritingMode || '';
  const isVertical = /vertical/i.test(writingMode);

  const textNodes = [];
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null, false);
  let node;
  while ((node = walker.nextNode()) !== null) {
    if (node.textContent && node.textContent.trim().length > 0) {
      textNodes.push(node);
    }
  }

  if (textNodes.length === 0) return null;

  let processedText = '';
  const charToDomMap = [];
  let trimNextLeading = false;
  
  for (let i = 0; i < textNodes.length; i++) {
    const textNode = textNodes[i];
    let textVal = normalizeText(textNode.textContent);
    
    if (i === 0) textVal = textVal.trimStart();
    if (trimNextLeading) textVal = textVal.trimStart();
    trimNextLeading = false;
    if (i === textNodes.length - 1) textVal = textVal.trimEnd();
    
    if (textVal.length > 0) {
      const startIdx = processedText.length;
      processedText += textVal;
      charToDomMap.push(...buildCharToDomMap(textNode, textVal, startIdx));
    }
  }

  if (processedText.length === 0) return null;

  const breaks = detectBreaksFromMap(charToDomMap, processedText, isVertical);
  return breaks.length > 0 ? { breaks, processedText } : null;
}

/**
 * Detects line break positions within a single text node.
 */
export function detectLineBreaksForTextNode(textNode) {
  if (!textNode || textNode.nodeType !== 3) return null;
  
  const processedText = normalizeText(textNode.textContent);
  if (processedText.length < 2) return null;

  let isVertical = false;
  if (textNode.parentElement) {
    const parentStyle = window.getComputedStyle(textNode.parentElement);
    const writingMode = parentStyle.writingMode || parentStyle.webkitWritingMode || '';
    isVertical = /vertical/i.test(writingMode);
  }

  const charToDomMap = buildCharToDomMap(textNode, processedText, 0);
  const breaks = detectBreaksFromMap(charToDomMap, processedText, isVertical);
  
  return breaks.length > 0 ? { breaks, processedText } : null;
}

/**
 * Detects if there's a line break between two adjacent nodes.
 */
export function detectLineBreakBetweenNodes(node1, node2) {
  if (!node1 || !node2) return false;
  
  const lastChar = findNonWhitespaceChar(node1, false);
  const firstChar = findNonWhitespaceChar(node2, true);
  
  if (!lastChar || !firstChar) return false;
  
  const range1 = document.createRange();
  range1.setStart(lastChar.node, lastChar.index);
  range1.setEnd(lastChar.node, lastChar.index + 1);
  const rect1 = range1.getBoundingClientRect();
  
  const range2 = document.createRange();
  range2.setStart(firstChar.node, firstChar.index);
  range2.setEnd(firstChar.node, firstChar.index + 1);
  const rect2 = range2.getBoundingClientRect();
  
  range1.detach();
  range2.detach();
  
  return rect2.top > rect1.top + 2;
}

/**
 * Detects line break positions within an inline element.
 */
export function detectLineBreaksForInlineElement(element) {
  if (!element || element.nodeType !== 1) return null;

  const style = window.getComputedStyle(element);
  const writingMode = style.writingMode || style.webkitWritingMode || '';
  const isVertical = /vertical/i.test(writingMode);
  
  const textNodes = [];
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null, false);
  let node;
  while ((node = walker.nextNode()) !== null) {
    if (node.textContent && node.textContent.trim().length > 0) {
      textNodes.push(node);
    }
  }
  
  if (textNodes.length === 0) return null;
  
  let processedText = '';
  const charToDomMap = [];
  
  for (const textNode of textNodes) {
    const nodeText = normalizeText(textNode.textContent);
    if (nodeText.length > 0) {
      const startIdx = processedText.length;
      processedText += nodeText;
      charToDomMap.push(...buildCharToDomMap(textNode, nodeText, startIdx));
    }
  }

  if (processedText.length < 2) return null;

  const breaks = detectBreaksFromMap(charToDomMap, processedText, isVertical);
  return breaks.length > 0 ? { breaks, processedText } : null;
}

// ============ Font Embedding Utilities ============

/**
 * 字体 CDN URL 映射表
 * 支持跨域的字体文件 CDN
 */
export const FONT_URL_MAP = {
  'MiSans': 'https://statics.moonshot.cn/kimi-ppt/fonts/css/MiSans.woff2',
  'MiSans-Bold': 'https://statics.moonshot.cn/kimi-ppt/fonts/css/MiSans-Bold.woff2',
  'MiSans-Medium': 'https://statics.moonshot.cn/kimi-ppt/fonts/css/MiSans-Medium.woff2',
  'MiSans-Semibold': 'https://statics.moonshot.cn/kimi-ppt/fonts/css/MiSans-Semibold.woff2',
  'MiSans-Light': 'https://statics.moonshot.cn/kimi-ppt/fonts/css/MiSans-Light.woff2',
  'Liter': 'https://statics.moonshot.cn/kimi-ppt/fonts/css/Liter-Regular.ttf',
  'Arial': null, // 系统字体，无需嵌入
  'Helvetica': null,
  'sans-serif': null,
  'serif': null,
  'monospace': null,
};

/**
 * 从 CDN CSS 动态获取字体 URL 映射（缓存）
 * @returns {Promise<Map<string, string>>}
 */
let fontUrlCache = null;

export async function fetchFontUrlMap() {
  if (fontUrlCache) return fontUrlCache;

  console.log('[font] Fetching font CSS from CDN...');
  const cssUrl = 'https://statics.moonshot.cn/kimi-ppt/html-gen/static/font-v2.css';
  try {
    const response = await fetch(cssUrl);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const cssText = await response.text();
    console.log('[font] CSS fetched, length:', cssText.length);

    // 解析 @font-face 规则
    fontUrlCache = new Map(Object.entries(FONT_URL_MAP)); // 从基础 map 开始

    const fontFaceRegex = /@font-face\s*\{([^}]+)\}/g;
    let match;
    let parsedCount = 0;
    while ((match = fontFaceRegex.exec(cssText)) !== null) {
      const block = match[1];

      // 提取 font-family
      const familyMatch = block.match(/font-family\s*:\s*(['"]?)([^;'"]+)\1/);
      // 提取 src (取第一个 url)
      const srcMatch = block.match(/src\s*:\s*[^;]*url\s*\(\s*(['"]?)([^)'"]+)\1/);

      if (familyMatch && srcMatch) {
        const family = familyMatch[2].trim();
        const url = srcMatch[2].trim();
        fontUrlCache.set(family, url);
        fontUrlCache.set(family.toLowerCase(), url); // 小写版本也添加
        parsedCount++;
      }
    }

    console.log('[font] Font URL map loaded, total entries:', fontUrlCache.size, 'parsed from CSS:', parsedCount);
    console.log('[font] Sample entries:', Array.from(fontUrlCache.entries()).slice(0, 5));
  } catch (e) {
    console.warn('[font] Failed to fetch font CSS, using fallback map:', e);
    fontUrlCache = new Map(Object.entries(FONT_URL_MAP));
  }

  return fontUrlCache;
}

/**
 * 根据字体族名解析 CDN URL
 * @param {string} fontFamily - 字体族名
 * @param {Map<string, string>} [urlMap] - 可选的字体 URL 映射
 * @returns {string|null} 字体文件 URL 或 null（系统字体或不支持）
 */
export function resolveFontUrl(fontFamily, urlMap = null) {
  if (!fontFamily) return null;
  // 清理字体名称（去除引号等）
  const cleanName = fontFamily.replace(/['"]/g, '').trim();
  console.log('[font] resolveFontUrl: input=', fontFamily, 'clean=', cleanName);

  const map = urlMap || FONT_URL_MAP;
  console.log('[font] resolveFontUrl: map type=', map instanceof Map ? 'Map' : 'Object', 'size=', map instanceof Map ? map.size : Object.keys(map).length);

  // 直接查找
  if (map instanceof Map) {
    if (map.has(cleanName)) {
      const url = map.get(cleanName);
      console.log('[font] Direct hit:', cleanName, '->', url);
      return url;
    }
    // 忽略大小写查找
    const lowerName = cleanName.toLowerCase();
    for (const [key, url] of map) {
      if (key.toLowerCase() === lowerName) {
        console.log('[font] Case-insensitive hit:', cleanName, '->', url);
        return url;
      }
    }
  } else {
    // 旧的对象格式
    if (map[cleanName] !== undefined) {
      console.log('[font] Direct hit:', cleanName, '->', map[cleanName]);
      return map[cleanName];
    }
    // 忽略大小写查找
    const lowerName = cleanName.toLowerCase();
    for (const [key, url] of Object.entries(map)) {
      if (key.toLowerCase() === lowerName) {
        console.log('[font] Case-insensitive hit:', cleanName, '->', url);
        return url;
      }
    }
  }
  console.log('[font] No match for:', cleanName);
  return null;
}

/**
 * 遍历 DOM 收集所有使用的字体族名
 * @param {HTMLElement|HTMLElement[]|string} root - DOM 根元素或选择器
 * @returns {Set<string>} 字体族名集合
 */
export function getUsedFontFamilies(root) {
  const families = new Set();

  function scan(node) {
    if (node.nodeType === 1) {
      const style = window.getComputedStyle(node);
      const fontList = style.fontFamily.split(',');
      const primary = fontList[0].trim().replace(/['"]/g, '');
      if (primary) families.add(primary);
    }
    for (const child of node.childNodes) {
      scan(child);
    }
  }

  const elements = Array.isArray(root) ? root : [root];
  elements.forEach((el) => {
    const node = typeof el === 'string' ? document.querySelector(el) : el;
    if (node) scan(node);
  });

  return families;
}

/**
 * 根据字体族名列表获取可嵌入的字体信息
 * @param {Set|string[]} usedFamilies - 使用的字体族名集合或数组
 * @returns {Promise<Array<{name: string, buffer: ArrayBuffer, type: string}>>}
 */
export async function getAutoDetectedFonts(usedFamilies) {
  const results = [];

  const families = usedFamilies instanceof Set
    ? Array.from(usedFamilies)
    : usedFamilies;

  console.log('[font] getAutoDetectedFonts called with families:', families);

  // 先获取字体 URL 映射（从 CDN CSS 解析）
  const urlMap = await fetchFontUrlMap();

  for (const family of families) {
    const url = resolveFontUrl(family, urlMap);
    console.log('[font] resolveFontUrl:', family, '->', url);
    if (!url) {
      console.log('[font] No URL for font:', family);
      continue;
    }

    try {
      console.log('[font] Fetching:', url);
      const response = await fetch(url);
      if (!response.ok) {
        console.warn(`[font] Failed to fetch font ${family}: ${response.status}`);
        continue;
      }
      const buffer = await response.arrayBuffer();

      // 根据 URL 扩展名判断类型
      const ext = url.split('.').pop().split(/[?#]/)[0].toLowerCase();
      let type = 'ttf';
      if (ext === 'woff') type = 'woff';
      else if (ext === 'woff2') type = 'woff2';
      else if (ext === 'otf') type = 'otf';
      else if (ext === 'eot') type = 'eot';

      results.push({ name: family, buffer, type });
      console.log(`[font] Loaded: ${family} (${type}, ${buffer.byteLength} bytes)`);
    } catch (e) {
      console.warn(`[font] Failed to load font: ${family}`, e);
    }
  }

  return results;
}

/**
 * 字体嵌入增强的 pptxgenjs 包装器
 * 从本地 embed-fonts 模块导入
 * @param {Function} PptxGenJS - 原始 pptxgenjs 构造函数
 * @returns {Promise<Function>} 增强后的 pptxgenjs 构造函数
 */
export async function withPPTXEmbedFonts(PptxGenJS) {
  const { withPPTXEmbedFonts: wrap } = await import('./embed-fonts/pptxgenjs.js');
  return wrap(PptxGenJS);
}
