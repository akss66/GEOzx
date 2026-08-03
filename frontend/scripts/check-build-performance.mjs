import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const INITIAL_GZIP_BUDGET_BYTES = 500 * 1024;
const MAX_WOFF2_FILES = 5;

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const frontendDirectory = join(scriptDirectory, "..");
const distDirectory = join(frontendDirectory, "dist");
const indexPath = join(distDirectory, "index.html");

if (!existsSync(indexPath)) {
  throw new Error("Missing dist/index.html. Run pnpm build before pnpm perf:check.");
}

const html = readFileSync(indexPath, "utf8");
const assetPaths = [
  ...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g),
].map((match) => match[1]);

if (assetPaths.length === 0) {
  throw new Error("Initial HTML did not expose any build assets.");
}

if (assetPaths.some((assetPath) => assetPath.includes("vendor-charts"))) {
  throw new Error("Initial HTML must not reference vendor-charts.");
}

let initialRawBytes = 0;
let initialGzipBytes = 0;

for (const assetPath of assetPaths) {
  const absolutePath = join(distDirectory, assetPath.replace(/^\//, ""));
  if (!existsSync(absolutePath)) {
    throw new Error(`Initial asset is missing: ${assetPath}`);
  }
  const content = readFileSync(absolutePath);
  initialRawBytes += content.byteLength;
  initialGzipBytes += gzipSync(content, { level: 9 }).byteLength;
}

const assetsDirectory = join(distDirectory, "assets");
const woff2Files = existsSync(assetsDirectory)
  ? readdirSync(assetsDirectory).filter((fileName) => fileName.endsWith(".woff2"))
  : [];
const woff2Bytes = woff2Files.reduce(
  (total, fileName) => total + statSync(join(assetsDirectory, fileName)).size,
  0,
);

const summary = {
  initialAssets: assetPaths,
  initialRawBytes,
  initialGzipBytes,
  initialGzipBudgetBytes: INITIAL_GZIP_BUDGET_BYTES,
  woff2Files: woff2Files.length,
  woff2Bytes,
  maxWoff2Files: MAX_WOFF2_FILES,
};

console.log(JSON.stringify(summary, null, 2));

if (initialGzipBytes > INITIAL_GZIP_BUDGET_BYTES) {
  throw new Error(
    `Initial gzip size ${initialGzipBytes} exceeds budget ${INITIAL_GZIP_BUDGET_BYTES}.`,
  );
}

if (woff2Files.length > MAX_WOFF2_FILES) {
  throw new Error(
    `WOFF2 file count ${woff2Files.length} exceeds limit ${MAX_WOFF2_FILES}.`,
  );
}
