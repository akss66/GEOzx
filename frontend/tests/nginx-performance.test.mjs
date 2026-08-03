import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const nginxConfig = readFileSync(
  new URL("../nginx.conf", import.meta.url),
  "utf8",
);

test("compresses and caches content-addressed frontend assets", () => {
  assert.match(nginxConfig, /\bgzip\s+on\s*;/);
  assert.match(nginxConfig, /\bgzip_vary\s+on\s*;/);
  assert.match(nginxConfig, /\bgzip_types\b[^;]*application\/javascript[^;]*;/s);
  assert.match(
    nginxConfig,
    /~\^\/assets\/\s+"public, max-age=31536000, immutable"\s*;/,
  );
  assert.match(nginxConfig, /location\s+\^~\s+\/assets\/\s*\{/);
  assert.match(
    nginxConfig,
    /add_header\s+Cache-Control\s+\$frontend_cache_control\s+always\s*;/,
  );
});

test("requires the application shell to revalidate after a release", () => {
  assert.match(
    nginxConfig,
    /\/index\.html\s+"no-cache"\s*;/,
  );
  assert.match(nginxConfig, /location\s+=\s+\/index\.html\s*\{/);
});
