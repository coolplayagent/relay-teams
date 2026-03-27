import JSZip from "jszip";
import { toZip } from "./parse.js";
import opentype from "opentype.js";
import { DOMParser } from "@xmldom/xmldom";
import { fontToEot } from "./utils.js";

const isNode =
  typeof process !== "undefined" &&
  !!process.versions?.node &&
  process.release?.name === "node";

const START_RID = 201314;

/**
 * @typedef {Object} Font
 * @property {string} name - 字体名称
 * @property {ArrayBuffer} data - 字体数据
 * @property {number} rid - 关系 ID
 * @property {"fntdata"|"ttf"|"otf"} [type] - 字体文件类型
 */

/**
 * PPTX 字体嵌入增强类
 * 用于在导出 PPTX 时嵌入自定义字体
 */
class PPTXEmbedFonts {
  zip;
  rId = START_RID;
  fonts = [];

  constructor(zip) {
    if (zip) {
      this.zip = zip;
    }
  }

  /**
   * 从 JSZip 对象加载
   * @param {JSZip} zip
   */
  async loadZip(zip) {
    this.zip = zip;
  }

  /**
   * 从 ArrayBuffer 加载
   * @param {ArrayBuffer} fileBuffer
   */
  async load(fileBuffer) {
    this.zip = await toZip(fileBuffer);
  }

  /**
   * 获取字体信息
   * @param {ArrayBuffer} fontBuffer
   * @returns {opentype.Font}
   */
  getFontInfo(fontBuffer) {
    const font = opentype.parse(fontBuffer);
    return font;
  }

  /**
   * 生成唯一 ID
   * @returns {number}
   */
  uniqueId() {
    return this.rId++;
  }

  /**
   * 将 EOT 转换为 fntData 格式
   * @param {ArrayBuffer} eotFile
   * @returns {Promise<ArrayBuffer>}
   */
  async eot2FntData(eotFile) {
    const unit8Array = new Uint8Array(eotFile);
    const blob = new Blob([unit8Array], {
      type: "font/opentype",
    });
    return await blob.arrayBuffer();
  }

  /**
   * 内部方法：添加字体
   * @param {string} fontFace - 字体名称
   * @param {ArrayBuffer} fntData - 字体数据
   * @param {"fntdata"|"ttf"|"otf"} [type] - 字体类型
   */
  async addFont(fontFace, fntData, type = "fntdata") {
    const rid = this.uniqueId();
    this.fonts.push({ name: fontFace, data: fntData, rid, type });
  }

  /**
   * 添加原始 TTF 字体
   * @param {string} fontFace - 字体名称
   * @param {ArrayBuffer} ttfFile - TTF 字体数据
   */
  async addFontFromRawTTF(fontFace, ttfFile) {
    await this.addFont(fontFace, ttfFile, "ttf");
  }

  /**
   * 从 EOT 格式添加字体
   * @param {string} fontFace - 字体名称
   * @param {ArrayBuffer} eotFile - EOT 字体数据
   */
  async addFontFromEOT(fontFace, eotFile) {
    const fontData = await this.eot2FntData(eotFile);
    await this.addFont(fontFace, fontData, "fntdata");
  }

  /**
   * 从 TTF 格式添加字体（会转换为 EOT）
   * @param {string} fontFace - 字体名称
   * @param {ArrayBuffer} ttfFile - TTF 字体数据
   */
  async addFontFromTTF(fontFace, ttfFile) {
    const eotFile = await fontToEot("ttf", ttfFile);
    await this.addFontFromEOT(fontFace, eotFile);
  }

  /**
   * 从 WOFF 格式添加字体（会转换为 EOT）
   * @param {string} fontFace - 字体名称
   * @param {ArrayBuffer} woffFile - WOFF 字体数据
   */
  async addFontFromWOFF(fontFace, woffFile) {
    const eotFile = await fontToEot("woff", woffFile);
    await this.addFontFromEOT(fontFace, eotFile);
  }

  /**
   * 从 OTF 格式添加字体（会转换为 EOT）
   * @param {string} fontFace - 字体名称
   * @param {ArrayBuffer} otfFile - OTF 字体数据
   */
  async addFontFromOTF(fontFace, otfFile) {
    const eotFile = await fontToEot("otf", otfFile);
    await this.addFontFromEOT(fontFace, eotFile);
  }

  /**
   * 从 WOFF2 格式添加字体（会转换为 EOT）
   * @param {string} fontFace - 字体名称
   * @param {ArrayBuffer} woff2File - WOFF2 字体数据
   */
  async addFontFromWOFF2(fontFace, woff2File) {
    const eotFile = await fontToEot("woff2", woff2File);
    await this.addFontFromEOT(fontFace, eotFile);
  }

  /**
   * 更新 Content_Types.xml，添加字体扩展名
   * @returns {Promise<void>}
   */
  async updateContentTypesXML() {
    if (!this.zip) {
      throw new Error("pptx file not loaded");
    }
    const contentTypes = this.zip.file("[Content_Types].xml");
    if (!contentTypes) {
      throw new Error("[Content_Types].xml not found");
    }
    const contentTypesXml = await contentTypes.async("string");
    const domParser = new DOMParser();
    const doc = domParser.parseFromString(contentTypesXml, "text/xml");
    const Types = doc.getElementsByTagName(`Types`)[0];
    if (!Types) {
      throw new Error("Types not found");
    }
    const defaultElements = doc.getElementsByTagName(`Default`);
    const fntdataExtensionElement = Array.from(defaultElements).find(
      (element) => {
        if (element.getAttribute("Extension") === "fntdata") {
          return element;
        }
      }
    );
    if (!fntdataExtensionElement) {
      const fntdataExtensionElement = doc.createElement("Default");
      fntdataExtensionElement.setAttribute("Extension", "fntdata");
      fntdataExtensionElement.setAttribute(
        "ContentType",
        "application/x-fontdata"
      );
      Types.insertBefore(fntdataExtensionElement, Types.firstChild);
    }

    // 检查是否已注册 ttf 扩展名
    const ttfExtensionElement = Array.from(defaultElements).find(
      (element) => {
        if (element.getAttribute("Extension") === "ttf") {
          return element;
        }
      }
    );
    if (!ttfExtensionElement) {
      const ttfExtensionElement = doc.createElement("Default");
      ttfExtensionElement.setAttribute("Extension", "ttf");
      ttfExtensionElement.setAttribute(
        "ContentType",
        "application/x-font-ttf"
      );
      Types.insertBefore(ttfExtensionElement, Types.firstChild);
    }
    this.zip.file("[Content_Types].xml", doc.toString());
  }

  /**
   * 更新 presentation.xml，添加嵌入字体引用
   * @returns {Promise<void>}
   */
  async updatePresentationXML() {
    if (!this.zip) throw new Error("pptx file not loaded");

    const presentation = this.zip.file("ppt/presentation.xml");
    if (!presentation) throw new Error("presentation.xml not found");

    const presentationXml = await presentation.async("string");
    const domParser = new DOMParser();
    const doc = domParser.parseFromString(presentationXml, "text/xml");
    const presentationNode = doc.getElementsByTagName(`p:presentation`)[0];
    if (!presentationNode) throw new Error("presentationNode not found");

    // 设置必要属性
    presentationNode.setAttribute("saveSubsetFonts", "true");
    presentationNode.setAttribute("embedTrueTypeFonts", "true");

    // 创建嵌入字体节点的函数
    const createEmbeddedFontNode = (font) => {
      const embeddedFontNode = doc.createElement("p:embeddedFont");
      const fontNode = doc.createElement("p:font");
      fontNode.setAttribute("typeface", font.name);
      embeddedFontNode.appendChild(fontNode);

      const regularNode = doc.createElement("p:regular");
      regularNode.setAttribute("r:id", `rId${font.rid}`);
      embeddedFontNode.appendChild(regularNode);
      return embeddedFontNode;
    };

    // 查找或创建 embeddedFontLst 节点
    let embeddedFontLstNode =
      presentationNode.getElementsByTagName("p:embeddedFontLst")[0];

    // 如果不存在则创建并插入到正确位置
    // https://www.iso.org/standard/71691.html 规范
    if (!embeddedFontLstNode) {
      embeddedFontLstNode = doc.createElement("p:embeddedFontLst");

      // 关键修改1: 确保插入在 defaultTextStyle 之前
      const defaultTextStyleNode =
        presentationNode.getElementsByTagName("p:defaultTextStyle")[0];

      if (defaultTextStyleNode) {
        // 如果存在 defaultTextStyle，则在它之前插入
        presentationNode.insertBefore(
          embeddedFontLstNode,
          defaultTextStyleNode
        );
      } else {
        // 否则插入到合理的位置 (sldSz/notesSz 之后)
        const sldSzNode = presentationNode.getElementsByTagName("p:sldSz")[0];
        const notesSzNode =
          presentationNode.getElementsByTagName("p:notesSz")[0];
        const referenceNode =
          notesSzNode || sldSzNode || presentationNode.lastChild;

        if (referenceNode) {
          presentationNode.insertBefore(
            embeddedFontLstNode,
            referenceNode.nextSibling
          );
        } else {
          presentationNode.appendChild(embeddedFontLstNode);
        }
      }
    }

    // 添加字体到 embeddedFontLst
    this.fonts.forEach((font) => {
      const existingFont = Array.from(
        embeddedFontLstNode.getElementsByTagName("p:font")
      ).find((node) => node.getAttribute("typeface") === font.name);

      if (!existingFont) {
        embeddedFontLstNode.appendChild(createEmbeddedFontNode(font));
      }
    });

    this.zip.file("ppt/presentation.xml", doc.toString());
  }

  /**
   * 更新 presentation.xml.rels，添加字体关系
   * @returns {Promise<void>}
   */
  async updateRelsPresentationXML() {
    if (!this.zip) {
      throw new Error("pptx file not loaded");
    }
    const relsPresentation = this.zip.file("ppt/_rels/presentation.xml.rels");
    if (!relsPresentation) {
      throw new Error("presentation.xml.rels not found");
    }
    const relsPresentationXml = await relsPresentation.async("string");
    const domParser = new DOMParser();
    const doc = domParser.parseFromString(relsPresentationXml, "text/xml");
    const relationshipsNode = doc.getElementsByTagName(`Relationships`)[0];
    if (!relationshipsNode) {
      throw new Error("Relationships not found");
    }
    this.fonts.forEach((font) => {
      const relationshipNode = doc.createElement("Relationship");
      relationshipNode.setAttribute("Id", `rId${font.rid}`);
      const ext = font.type === "ttf" ? ".ttf" : font.type === "otf" ? ".otf" : ".fntdata";
      relationshipNode.setAttribute("Target", `fonts/${font.rid}${ext}`);
      relationshipNode.setAttribute(
        "Type",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"
      );
      relationshipsNode.appendChild(relationshipNode);
    });
    this.zip.file("ppt/_rels/presentation.xml.rels", doc.toString());
  }

  /**
   * 更新字体文件到 ZIP
   */
  updateFontFiles() {
    if (!this.zip) {
      throw new Error("pptx file not loaded");
    }
    this.fonts.forEach((font) => {
      const ext = font.type === "ttf" ? ".ttf" : font.type === "otf" ? ".otf" : ".fntdata";
      this.zip.file(`ppt/fonts/${font.rid}${ext}`, font.data, {
        binary: true,
        compression: "DEFLATE",
      });
    });
  }

  /**
   * 更新所有 PPTX 文件
   * @returns {Promise<void>}
   */
  async updateFiles() {
    await this.updateContentTypesXML();
    await this.updatePresentationXML();
    await this.updateRelsPresentationXML();
    this.updateFontFiles();
  }

  /**
   * 保存并生成新的 PPTX
   * @returns {Promise<ArrayBuffer|Buffer>}
   */
  async save() {
    if (!this.zip) {
      throw new Error("pptx file not loaded");
    }
    await this.updateFiles();
    const outputType = isNode ? "nodebuffer" : "arraybuffer";
    return this.zip.generateAsync({
      type: outputType,
      compression: "DEFLATE",
      compressionOptions: {
        level: 6,
      },
    });
  }
}

export default PPTXEmbedFonts;
export { PPTXEmbedFonts };
export { withPPTXEmbedFonts } from './pptxgenjs.js';

/**
 * 获取 PPTXEmbedFonts 类（用于类型检查）
 */
export function getPPTXEmbedFontsClass() {
  return PPTXEmbedFonts;
}
