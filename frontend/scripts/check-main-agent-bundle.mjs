import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const distDir = new URL("../dist/", import.meta.url);
const assetsDir = new URL("./assets/", distDir);
const assetsPath = fileURLToPath(assetsDir);
const html = await readFile(new URL("./index.html", distDir), "utf8");
const initialAssets = [
  ...html.matchAll(/<(?:script|link)[^>]+(?:src|href)="([^"]+\.js)"/g),
].map((match) => match[1]);

if (initialAssets.some((asset) => asset.includes("vendor-charts"))) {
  throw new Error("Chart vendor chunk must not be preloaded on the main-agent entry page.");
}

let initialBytes = 0;
for (const asset of new Set(initialAssets)) {
  const relativePath = asset.replace(/^\//, "").replace(/^assets\//, "");
  initialBytes += (await stat(join(assetsPath, relativePath))).size;
}
if (initialBytes > 900_000) {
  throw new Error(`Initial JavaScript is ${initialBytes} bytes; budget is 900000 bytes.`);
}

const brainChunks = (await readdir(assetsDir)).filter(
  (name) => /^BrainHome-.*\.js$/.test(name),
);
if (brainChunks.length !== 1) {
  throw new Error(`Expected one lazy BrainHome chunk, found ${brainChunks.length}.`);
}
const brainBytes = (await stat(new URL(`./assets/${brainChunks[0]}`, distDir))).size;
if (brainBytes > 180_000) {
  throw new Error(`BrainHome chunk is ${brainBytes} bytes; budget is 180000 bytes.`);
}

console.log(
  `Main-agent bundle gate passed: initial=${initialBytes}B, BrainHome=${brainBytes}B, charts=lazy.`,
);
