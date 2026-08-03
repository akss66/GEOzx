# Production Performance Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the desktop operations-brain initial transfer cost, add enforceable bundle budgets, and release the verified commit safely to `https://tzxai.top`.

**Architecture:** Keep the existing route-level lazy loading, remove bundled Noto Sans SC slices in favor of the existing system-font fallbacks, and configure Nginx compression plus content-hash caching. A deterministic Node build check guards the initial dependency graph, compressed byte budget, and font-file count before an immutable-directory production deployment.

**Tech Stack:** React 18, TypeScript, Vite 6, Node.js, Nginx, Docker Compose, PowerShell, Vitest, Playwright.

## Global Constraints

- Desktop only; mobile remains deferred.
- Preserve all API, WebSocket, TLS, authentication, navigation, and visual interaction behavior.
- Do not add dependencies or a custom Nginx build.
- Do not modify database schema or production data.
- Initial directly referenced resources must remain at or below 500 KiB gzip.
- Initial HTML must not reference `vendor-charts`.
- Production build output must contain no more than five WOFF2 files.
- Every production switch must retain `/home/admin/releases/dyflow-20260731-import-retry-01af9de` as a rollback target.

---

### Task 1: Build-performance contract

**Files:**
- Create: `frontend/scripts/check-build-performance.mjs`
- Modify: `frontend/package.json`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `frontend/dist/index.html` and files under `frontend/dist/assets/`.
- Produces: `pnpm perf:check`, which exits nonzero when the initial graph, gzip budget, or font count violates the approved design.

- [ ] **Step 1: Create a deliberately failing performance check**

Implement a Node script that parses `dist/index.html`, resolves all `/assets/` `src` and `href` entries, rejects any entry containing `vendor-charts`, totals raw and `gzipSync(..., { level: 9 })` sizes, counts `.woff2` files, prints a JSON summary, and initially uses a zero-byte budget to prove failure.

- [ ] **Step 2: Run the check and verify RED**

Run: `pnpm perf:check`

Expected: nonzero exit with an explicit initial-gzip budget error.

- [ ] **Step 3: Set the approved thresholds**

Use constants `INITIAL_GZIP_BUDGET_BYTES = 500 * 1024` and `MAX_WOFF2_FILES = 5`. Keep missing build output and missing referenced assets as hard errors.

- [ ] **Step 4: Wire the check into CI after the production build**

Add `"perf:check": "node scripts/check-build-performance.mjs"` to `frontend/package.json`, and run `pnpm perf:check` immediately after `pnpm build` in the existing frontend CI job.

- [ ] **Step 5: Leave the check RED until font optimization is applied**

Run: `pnpm perf:check`

Expected: nonzero exit because the existing build has 103 WOFF2 files.

### Task 2: Font payload reduction

**Files:**
- Modify: `frontend/src/main.tsx`
- Test: `frontend/src/theme/tokens.test.ts`

**Interfaces:**
- Consumes: the existing `dyTheme.token.fontFamily` and CSS custom-property font stacks.
- Produces: the same public font-family stack without shipping Noto Sans SC font slices.

- [ ] **Step 1: Add a source contract test**

Extend `frontend/src/theme/tokens.test.ts` to read `frontend/src/main.tsx` and assert that it imports Geist but does not import `@fontsource-variable/noto-sans-sc/wght.css`.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `pnpm test -- src/theme/tokens.test.ts`

Expected: failure because `main.tsx` still imports Noto Sans SC.

- [ ] **Step 3: Remove only the Noto Sans SC CSS import**

Delete `import "@fontsource-variable/noto-sans-sc/wght.css";` from `frontend/src/main.tsx`. Keep the Geist import and existing theme/font CSS declarations unchanged.

- [ ] **Step 4: Rebuild and verify the performance contract GREEN**

Run: `pnpm build && pnpm perf:check`

Expected: build succeeds; initial resources are at or below 500 KiB gzip; no chart reference; WOFF2 count is at or below five.

- [ ] **Step 5: Commit the independently reversible frontend optimization**

Stage the script, package metadata, CI file, test, and `main.tsx`; inspect the staged diff for secrets; commit as `perf: enforce lean desktop initial bundle`.

### Task 3: Nginx compression and cache policy

**Files:**
- Modify: `frontend/nginx.conf`
- Create: `frontend/tests/nginx-performance.test.mjs`
- Modify: `frontend/package.json`

**Interfaces:**
- Produces: gzip for compressible assets, immutable caching for `/assets/`, and revalidation for `index.html`.

- [ ] **Step 1: Write a failing configuration contract**

Create a Node test that reads `frontend/nginx.conf` and asserts the presence of `gzip on`, `gzip_vary on`, a `/assets/` location with `max-age=31536000, immutable`, and an exact `/index.html` location with `no-cache`.

- [ ] **Step 2: Run the configuration test and verify RED**

Run: `node --test tests/nginx-performance.test.mjs`

Expected: failure because none of the approved delivery directives exist.

- [ ] **Step 3: Add the minimal Nginx directives**

Enable gzip at the HTTPS server, declare common text/JS/CSS/SVG MIME types, add `gzip_vary on`, add the immutable `/assets/` location, and add an exact `/index.html` no-cache location. Preserve existing proxy and SPA fallback locations.

- [ ] **Step 4: Verify Nginx syntax in the production image**

Run a temporary `nginx:alpine` container with the configuration mounted and execute `nginx -t`.

Expected: syntax is successful.

- [ ] **Step 5: Add `test:nginx` and run the contract GREEN**

Add `"test:nginx": "node --test tests/nginx-performance.test.mjs"`, then run `pnpm test:nginx`.

Expected: one passing Node test file and zero failures.

- [ ] **Step 6: Commit the independently reversible delivery optimization**

Stage only Nginx configuration, its test, and package metadata; inspect the staged diff for secrets; commit as `perf: compress and cache hashed frontend assets`.

### Task 4: Verification and immutable production release

**Files:**
- Verify: `frontend/`
- Verify: affected backend health and repository hygiene
- Deploy: Git archive built from the final verified commit

**Interfaces:**
- Consumes: Tasks 1–3 commits.
- Produces: a healthy production release and recorded before/after delivery evidence.

- [ ] **Step 1: Run the complete local quality gate**

Run frontend tests, `pnpm test:nginx`, lint, production build, `pnpm perf:check`, and desktop Playwright. Run `git diff --check`, secret-pattern inspection, and relevant backend health tests.

- [ ] **Step 2: Record the final artifact metrics**

Capture the performance-check JSON, initial raw/gzip bytes, WOFF2 count, build chunk listing, and confirm `dist/index.html` omits `vendor-charts`.

- [ ] **Step 3: Package and checksum the exact commit**

Create `C:\tmp\dyflow-20260803-performance-<commit>.tar.gz` with `git archive`, calculate SHA-256, and confirm the archive includes `docker-compose.prod.yml`, updated Nginx configuration, and no `.env`.

- [ ] **Step 4: Prepare the production rollback record**

Over SSH to `admin@39.106.100.147`, record the active release, Compose status, Alembic revision, database/Redis health, and available disk space. Stop if the active link differs unexpectedly or health is not ready.

- [ ] **Step 5: Build a new immutable release**

Upload archive and checksum to `/home/admin/releases/`, verify SHA-256, extract to `/home/admin/releases/dyflow-20260803-performance-<commit>`, copy the active `.env`, build Compose images, and run `docker compose -f docker-compose.prod.yml run -T --rm backend alembic upgrade head`.

- [ ] **Step 6: Atomically activate and verify**

Atomically switch `/home/admin/dyflow`, run Compose with `--remove-orphans`, then verify readiness, root TLS, container health, migration head, gzip response headers, immutable asset caching, and no-cache HTML.

- [ ] **Step 7: Run desktop production smoke tests**

Verify login, operations-brain first paint, account switching, composer submission, streaming response, and latest-message following. Confirm no new browser console errors. If browser automation is unavailable, run the existing desktop Playwright suite against the production-equivalent build and record the production limitation explicitly.

- [ ] **Step 8: Roll back on any red gate**

If any critical gate fails, atomically restore `/home/admin/releases/dyflow-20260731-import-retry-01af9de`, restart Compose, and re-run public health checks before reporting the failed release.

- [ ] **Step 9: Record final deployment evidence**

Report the deployed commit, release directory, rollback directory, SHA-256, Compose state, migration head, public health, asset headers, and before/after initial resource figures.
