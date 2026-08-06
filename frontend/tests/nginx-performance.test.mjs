import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const nginxConfig = readFileSync(
  new URL("../nginx.conf", import.meta.url),
  "utf8",
);
const localNginxConfig = readFileSync(
  new URL("../nginx.local.conf", import.meta.url),
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

test("re-resolves the Docker backend after container replacement", () => {
  for (const config of [nginxConfig, localNginxConfig]) {
    assert.match(config, /resolver\s+127\.0\.0\.11\s+valid=\d+s\s+ipv6=off\s*;/);
    assert.match(config, /upstream\s+backend_upstream\s*\{[^}]*zone\s+backend_upstream\s+64k\s*;/s);
    assert.match(config, /upstream\s+backend_upstream\s*\{[^}]*server\s+backend:8000\s+resolve\s*;/s);
    assert.doesNotMatch(config, /proxy_pass\s+http:\/\/backend:8000/);
    assert.equal(
      [...config.matchAll(/proxy_pass\s+http:\/\/backend_upstream/g)].length,
      3,
    );
  }
});
