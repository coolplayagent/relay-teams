// fonteditor-core 在浏览器环境中需要从 CDN 动态加载
let FontModule = null;

async function getFontModule() {
  if (!FontModule) {
    FontModule = await import(/* webpackIgnore: true */ 'https://unpkg.com/fonteditor-core@2.6.3/index.js');
  }
  return FontModule;
}

import pako from "pako";
import { initWoff2 } from "./woff2.js";

/**
 * 直接修改 TTF 二进制数据中的字体名称 + 修复 maxp.numGlyphs
 * @param {ArrayBuffer} ttfBuffer - TTF 字体数据
 * @param {string} newName - 新的字体名称
 * @returns {ArrayBuffer} 修改后的 TTF 数据
 */
function renameTTFInPlace(ttfBuffer, newName) {
  const result = new Uint8Array(ttfBuffer);

  const numTables = (result[4] << 8) | result[5];

  let nameTableOffset = 0;
  let maxpOffset = 0;
  let locaLength = 0;

  // 找到需要的表
  for (let i = 0; i < numTables; i++) {
    const offset = 12 + i * 16;
    const tag = String.fromCharCode(result[offset], result[offset + 1], result[offset + 2], result[offset + 3]);
    if (tag === "name") {
      nameTableOffset = (result[offset + 8] << 24) | (result[offset + 9] << 16) | (result[offset + 10] << 8) | result[offset + 11];
    } else if (tag === "maxp") {
      maxpOffset = (result[offset + 8] << 24) | (result[offset + 9] << 16) | (result[offset + 10] << 8) | result[offset + 11];
    } else if (tag === "loca") {
      locaLength = (result[offset + 12] << 24) | (result[offset + 13] << 16) | (result[offset + 14] << 8) | result[offset + 15];
    }
  }

  // 修复 maxp.numGlyphs
  if (maxpOffset && locaLength) {
    const indexToLocFormat = (result[maxpOffset + 16] << 8) | result[maxpOffset + 17];
    const glyphCount = indexToLocFormat === 0 ? (locaLength / 2) - 1 : (locaLength / 4) - 1;
    result[maxpOffset + 12] = (glyphCount >> 8) & 0xff;
    result[maxpOffset + 13] = glyphCount & 0xff;
    console.log("[font] Fixed maxp.numGlyphs:", glyphCount);
  }

  // 重命名字体
  if (!nameTableOffset) {
    console.warn("[font] name table not found");
    return ttfBuffer;
  }

  const count = (result[nameTableOffset + 2] << 8) | result[nameTableOffset + 3];
  const stringOffset = (result[nameTableOffset + 4] << 8) | result[nameTableOffset + 5];
  const stringDataStart = nameTableOffset + stringOffset;

  // 新名称转为 UTF-16BE
  const newNameUtf16 = new Uint8Array(newName.length * 2);
  for (let i = 0; i < newName.length; i++) {
    newNameUtf16[i * 2] = (newName.charCodeAt(i) >> 8) & 0xff;
    newNameUtf16[i * 2 + 1] = newName.charCodeAt(i) & 0xff;
  }

  // 收集需要修改的记录
  const modifyNameIds = new Set([1, 3, 4, 6]);
  const recordsToModify = [];

  for (let i = 0; i < count; i++) {
    const recordOffset = nameTableOffset + 6 + i * 12;
    const platformID = (result[recordOffset] << 8) | result[recordOffset + 1];
    const nameID = (result[recordOffset + 6] << 8) | result[recordOffset + 7];
    if (platformID === 3 && modifyNameIds.has(nameID)) {
      recordsToModify.push(i);
    }
  }

  // 找到字符串数据末端
  let maxStringEnd = stringDataStart;
  for (let i = 0; i < count; i++) {
    const recordOffset = nameTableOffset + 6 + i * 12;
    const length = (result[recordOffset + 8] << 8) | result[recordOffset + 9];
    const strOffset = (result[recordOffset + 10] << 8) | result[recordOffset + 11];
    const stringEnd = stringDataStart + strOffset + length;
    if (stringEnd > maxStringEnd) {
      maxStringEnd = stringEnd;
    }
  }

  // 在字符串数据末端写入新字符串
  const newStringDataOffset = maxStringEnd - stringDataStart;
  for (let i = 0; i < newNameUtf16.length; i++) {
    result[stringDataStart + newStringDataOffset + i] = newNameUtf16[i];
  }
  for (let i = newNameUtf16.length; i < 50; i++) {
    result[stringDataStart + newStringDataOffset + i] = 0;
  }

  // 更新记录
  for (const idx of recordsToModify) {
    const recordOffset = nameTableOffset + 6 + idx * 12;
    result[recordOffset + 8] = (newNameUtf16.length >> 8) & 0xff;
    result[recordOffset + 9] = newNameUtf16.length & 0xff;
    result[recordOffset + 10] = (newStringDataOffset >> 8) & 0xff;
    result[recordOffset + 11] = newStringDataOffset & 0xff;
  }

  return result.buffer;
}

/**
 * 将字体转换为 EOT 格式
 * @param {"ttf"|"woff"|"woff2"|"otf"} type - 源字体类型
 * @param {ArrayBuffer|Uint8Array} fontBuffer - 字体数据
 * @param {string} [renameFontFace] - 可选的新字体名称
 * @returns {Promise<ArrayBuffer>} EOT 格式字体数据
 */
export async function fontToEot(type, fontBuffer, renameFontFace) {
  const Font = (await getFontModule()).Font;
  const options = {
    type,
    hinting: true,
  };
  if (type === "woff") {
    options.inflate = pako.inflate;
  }

  let buffer = fontBuffer;
  // woff2 需要先用 woff2.decode() 解压为 ttf
  if (type === "woff2") {
    console.log("[font] woff2 decode: starting, buffer size:", fontBuffer.byteLength);
    const mod = await initWoff2();
    const decoded = mod.decode(fontBuffer);
    buffer = normalizeBuffer(decoded);
    console.log("[font] woff2 decode: buffer size after decode:", buffer.byteLength);
    options.type = "ttf";
  }

  // 创建字体对象
  const font = Font.create(buffer, options);

  // 如果需要重命名字体，直接修改二进制数据
  if (renameFontFace) {
    console.log("[font] fontToEot: renaming font to:", renameFontFace);
    buffer = renameTTFInPlace(buffer, renameFontFace);
    // 重命名后 buffer 已被修改，直接复用 font 对象即可
  }

  const eotBuffer = font.write({
    type: "eot",
    toBuffer: true,
  });
  console.log("[font] fontToEot: EOT written, size:", eotBuffer.byteLength);

  if (eotBuffer instanceof ArrayBuffer) {
    return eotBuffer;
  }

  return eotBuffer.buffer.slice(
    eotBuffer.byteOffset,
    eotBuffer.byteOffset + eotBuffer.byteLength
  );
}

/**
 * 将 decoded buffer 归一化为独立的 ArrayBuffer
 * woff2.decode() 返回的 buffer 可能是 SharedArrayBuffer，需要复制到新 ArrayBuffer
 * @param {Uint8Array} decoded - 解码后的 Uint8Array
 * @returns {ArrayBuffer}
 */
export function normalizeBuffer(decoded) {
  return decoded.buffer.slice(
    decoded.byteOffset,
    decoded.byteOffset + decoded.byteLength
  );
}

export { renameTTFInPlace };
