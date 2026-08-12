# Task 12 Report: Typed WeChat Draft Client

## Scope

- Added a caller-token-only `WechatDraftClient` with typed body-image upload,
  permanent-cover upload, draft get/add/update operations.
- Added strict outbound draft-package schemas. `draft/get` validates the approved conflict
  fields while discarding unknown read-only remote fields rather than propagating them.
- Added deterministic remote normalization and UTF-8 canonical-JSON SHA-256 hashing.
- Did not add persistence, API routes, synchronization jobs, publishing, or live writes.

## TDD evidence

Each behavior was introduced with a focused failing test before production code:

1. Missing `media_id`: collection failed because the typed schema did not exist, then passed
   after the minimal schema and `add_draft` implementation.
2. Platform, malformed JSON, non-object JSON, timeout/network, 4xx/429/5xx, and missing
   success fields: each failed at the previously unhandled branch, then passed after a
   stable secret-free category was added.
3. Multipart request shape and WeChat-hosted body-image URL validation: failed because the
   upload methods did not exist, then passed with explicit multipart calls.
4. Typed get/add/update payloads: failed on missing methods or missing schema validation,
   then passed with the approved fields only.
5. Canonicalization: collection failed on missing helpers, then passed for attribute-order
   and insignificant inter-tag/edge-whitespace equivalence while preserving semantic changes.
6. Self-review regressions first failed for malformed 5xx classification and significant
   inline whitespace, then passed after tightening both branches.

Final focused result: `29 passed`.

## Official documentation boundary

Authority URLs recorded by the task brief:

- https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html
- https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Update_draft.html
- https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Get_draft.html
- https://developers.weixin.qq.com/doc/offiaccount/Asset_Management/Adding_Permanent_Assets.html

The available web tooling could not retrieve these pages on 2026-08-12. Implementation is
therefore intentionally limited to the plan-locked fields and endpoints. Task 18 must verify
the following against live official endpoints before production synchronization is enabled:

- exact multipart filename/MIME and permanent-material response details;
- whether `draft/get` adds other documented read-only fields needed for conflict UX;
- all live field lengths, allowed values, quotas, and retry/error-code tables;
- whether any platform-provided HTML normalization requires an additional compatibility rule.

## Endpoint and field assumptions

- `POST /cgi-bin/media/uploadimg`, multipart field `media`, returns HTTPS URL hosted exactly
  at `mmbiz.qpic.cn` (default/443 port, no URL credentials).
- `POST /cgi-bin/material/add_material?type=image`, multipart field `media`, returns non-empty
  `media_id`.
- `POST /cgi-bin/draft/get` with `media_id`, returns a non-empty `news_item` list.
- `POST /cgi-bin/draft/add` sends one `articles` item and returns non-empty `media_id`.
- `POST /cgi-bin/draft/update` sends `media_id`, `index`, and one typed `articles` object and
  succeeds only with explicit integer `errcode == 0`.
- Approved article fields are title, author, digest, content, thumb media ID, the two comment
  flags, and content-source URL. Outbound unknown fields are rejected.

## Error and secret handling

- Reuses `WechatIntegrationError` through a draft-specific subclass that adds only a
  sanitized `errmsg`.
- Keeps sanitized `errcode`, `errmsg`, `rid`, endpoint, and retryability; strips newlines,
  bounds text, and redacts token/secret assignments.
- Suppresses raw exception causes for malformed JSON and transport failures.
- Does not persist, retain on the client, log, or place access tokens in request JSON/files.
  The caller token is supplied only as an httpx query parameter for each call.
- Never includes request HTML, file bytes, filesystem paths, or raw response text in errors.

## Canonicalization rules

- Parses HTML using the Python standard-library parser and emits deterministic lower-level
  markup with attributes sorted lexically.
- Ignores only edge whitespace and whitespace-only nodes between block/container tags.
- Preserves inline significant whitespace, text, tag/node order, tag names, attribute values,
  comment flags, URLs, and media IDs.
- Does not fetch resources and does not mutate caller input.
- Hashes `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))` encoded
  as UTF-8 with SHA-256.

## Verification

```text
uv run pytest tests/test_wechat_draft_client.py -q
29 passed

uv run pytest tests/test_wechat_renderer.py tests/test_wechat_article_api.py tests/test_wechat_article_images.py -q
59 passed

uv run ruff check app/services/wechat_drafts.py app/schemas/wechat_article.py tests/test_wechat_draft_client.py
All checks passed

uv run ruff format --check app/services/wechat_drafts.py app/schemas/wechat_article.py tests/test_wechat_draft_client.py
3 files already formatted

uv run mypy app/services/wechat_drafts.py app/schemas/wechat_article.py
Success: no issues found in 2 source files

git diff --check
PASS
```

Secret review: staged diff was searched for credential assignments, access-token values,
private keys, raw response/body logging, and filesystem paths. Test fixtures use explicit fake
tokens only; production code contains parameter names but no secret values or logging.

## Concerns deferred to Task 18

- Official pages and a live test account remain required to validate the locked assumptions.
- Live WeChat may return additional error codes whose retryability must be derived from current
  official guidance rather than guessed here.
- This client does not provide idempotency or remote-conflict persistence; Task 13 owns both.
