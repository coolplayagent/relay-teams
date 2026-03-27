import esbuild from "esbuild";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const outDir = path.join(__dirname, "dist");
const nodeOutDir = path.join(__dirname, "node", "dist");
const bundleName = "dom-to-pptx.bundle.js";
const mapName = `${bundleName}.map`;

for (const dir of [outDir, nodeOutDir]) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

esbuild
  .build({
    entryPoints: [path.join(__dirname, "src/index.js")],
    bundle: true,
    outfile: path.join(outDir, bundleName),
    format: "iife",
    globalName: "domToPptx",
    platform: "browser",
    target: ["es2020"],
    minify: false,
    keepNames: true,
    sourcemap: true,
    external: ["fonteditor-core"],
    loader: {
      ".js": "js",
      ".wasm": "binary",
    },
  })
  .then(() => {
    fs.copyFileSync(path.join(outDir, bundleName), path.join(nodeOutDir, bundleName));
    if (fs.existsSync(path.join(outDir, mapName))) {
      fs.copyFileSync(path.join(outDir, mapName), path.join(nodeOutDir, mapName));
    }
    console.log("dom-to-pptx.bundle.js build complete");
  })
  .catch((err) => {
    console.error("Build failed:", err);
    process.exit(1);
  });
