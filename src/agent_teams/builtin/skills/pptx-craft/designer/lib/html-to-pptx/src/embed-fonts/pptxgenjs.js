import pptxgenjs from "pptxgenjs";
import JSZip from "jszip";
import PPTXEmbedFonts from "./index.js";
import { renameTTFInPlace, normalizeBuffer } from "./utils.js";
import { initWoff2 } from "./woff2.js";

/**
 * 字体嵌入增强的 pptxgenjs 包装器
 * @param {typeof pptxgenjs} pptxgen - 原始 pptxgenjs 类
 * @returns {typeof EmbedFontsPPTXGenJS} 增强后的 pptxgenjs 类
 */
function withPPTXEmbedFonts(pptxgen) {
  return class EmbedFontsPPTXGenJS extends pptxgen {
    _pptxEmbedFonts = new PPTXEmbedFonts();

    constructor(...args) {
      super(...args);
      this._setupExportPresentation();
    }

    _setupExportPresentation() {
      const originalExportPresentation = this.exportPresentation;

      this.exportPresentation = async (options = {}) => {
        // 调用原始导出方法
        const res = await originalExportPresentation.call(this, options);

        // 如果结果不是有效数据，直接返回
        if (!res) {
          return res;
        }

        try {
          // 加载到 JSZip
          const zip = await new JSZip().loadAsync(res);

          // 嵌入字体
          await this._pptxEmbedFonts.loadZip(zip);
          await this._pptxEmbedFonts.updateFiles();

          // 根据输出类型生成返回结果
          if (options.outputType === "STREAM") {
            return await zip.generateAsync({
              type: "nodebuffer",
              compression: options.compression ? "DEFLATE" : "STORE",
            });
          } else if (options.outputType) {
            return await zip.generateAsync({
              type: options.outputType,
            });
          } else {
            return await zip.generateAsync({
              type: "blob",
              compression: options.compression ? "DEFLATE" : "STORE",
            });
          }
        } catch (e) {
          console.error("[font] exportPresentation error:", e);
          // 如果嵌入字体失败，返回原始结果
          return res;
        }
      };
    }

    /**
     * 添加字体
     * @param {{ fontFace: string, fontFile: ArrayBuffer, fontType: "ttf" | "eot" | "woff" | "woff2" | "otf" }} options
     */
    async addFont(options) {
      if (options.fontType === "ttf") {
        await this._pptxEmbedFonts.addFontFromTTF(
          options.fontFace,
          options.fontFile
        );
      } else if (options.fontType === "eot") {
        await this._pptxEmbedFonts.addFontFromEOT(
          options.fontFace,
          options.fontFile
        );
      } else if (options.fontType === "woff") {
        await this._pptxEmbedFonts.addFontFromWOFF(
          options.fontFace,
          options.fontFile
        );
      } else if (options.fontType === "woff2") {
        // woff2: 解码后直接嵌入为 TTF（跳过 EOT 转换）
        const mod = await initWoff2();
        const decoded = mod.decode(options.fontFile);
        const ttfBuffer = normalizeBuffer(decoded);
        // 修复 TTF：重命名 name 表 + 修复 maxp.numGlyphs
        const renamedBuffer = renameTTFInPlace(ttfBuffer, options.fontFace);
        await this._pptxEmbedFonts.addFontFromRawTTF(options.fontFace, renamedBuffer);
      } else if (options.fontType === "otf") {
        await this._pptxEmbedFonts.addFontFromOTF(
          options.fontFace,
          options.fontFile
        );
      } else {
        throw new Error(`Invalid font type ${options.fontType}`);
      }
    }

    /**
     * 获取字体信息
     * @param {ArrayBuffer} fontFile - 字体文件数据
     * @returns {opentype.Font}
     */
    getFontInfo(fontFile) {
      return this._pptxEmbedFonts.getFontInfo(fontFile);
    }
  };
}

export default withPPTXEmbedFonts;
export { withPPTXEmbedFonts };
