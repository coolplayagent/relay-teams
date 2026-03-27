import JSZip from "jszip";

/**
 * 将 ArrayBuffer 加载为 JSZip 对象
 * @param {ArrayBuffer} file - ZIP 文件数据
 * @returns {Promise<JSZip>}
 */
export async function toZip(file) {
  const zip = new JSZip();
  return zip.loadAsync(file);
}
