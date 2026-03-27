/**
 * fonteditor-core/woff2 模块类型声明
 */
interface WOFF2Module {
  init(url: string): Promise<void>;
  isInited(): boolean;
  decode(buffer: ArrayBuffer | Uint8Array): Uint8Array;
}
declare module 'fonteditor-core/woff2' {
  const woff2Module: WOFF2Module;
  export default woff2Module;
}
