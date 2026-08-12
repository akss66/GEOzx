# WeChat Official Account Rollout Runbook

## Boundary

- Service path: WeChat third-party platform authorization, capability probing, structured article workspace, image generation, and draft sync.
- Backend entry points:
  - `GET /platform-integrations/wechat/oauth/callback`
  - `POST /platform-integrations/wechat/events`
  - `GET /accounts/{account_id}/platform-capabilities`
  - `POST /wechat-articles/{article_id}/draft-syncs`
  - `GET /wechat-articles/{article_id}/draft-sync-context`
- Data plane:
  - `platform_integrations`
  - `platform_account_auth`
  - `wechat_component_credentials`
  - `article_working_copies`
  - `deliverables`
  - `wechat_draft_mappings`
  - `platform_publish_jobs`
  - `events`
- First release boundary: draft sync only. Never publish publicly from this path.

## Confirmed Facts

- The platform value is `wechat_official_account`.
- The current code path persists only draft synchronization. It does not call `freepublish_submit`.
- Capability snapshots intentionally keep `freepublish.reason == "disabled_by_product_policy"`.
- OAuth callback security depends on two environment variables:
  - `WECHAT_COMPONENT_VERIFY_TOKEN`
  - `WECHAT_COMPONENT_ENCODING_AES_KEY`
- The component app secret is expected as the integration secret reference `env:WECHAT_COMPONENT_APP_SECRET`.
- The typed runtime rollout flags still exist and remain default-off:
  - `main_agent_v2_enabled`
  - `main_agent_typed_runtime_enabled`
- As of Wednesday, August 12, 2026, the available web tooling could not open the required WeChat developer documentation pages. All official contract checks below remain unverified until re-checked on an accessible network.

## Feature-Flag Status

- No dedicated backend feature flag currently exists for WeChat authorization or draft sync.
- Production status must therefore remain `OFF` until launch approval by keeping the release operationally gated:
  - do not expose the workflow to production operators outside the approved rollout group;
  - do not run the real smoke flow unless the operator explicitly names one test organization and one test公众号;
  - keep the real smoke test environment variables unset by default.
- This is a rollout-control gap, not an excuse to widen access. Treat any ungated production exposure as a stop-ship concern.

## Official Documentation Recheck

The following authority URLs must be re-opened manually before enabling production traffic. On August 12, 2026 they were unreachable from the available tooling and therefore remain unverified:

- `https://developers.weixin.qq.com/doc/oplatform/Third-party_Platforms/2.0/product/Third_party_platform_appid.html`
- `https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add`
- `https://developers.weixin.qq.com/doc/service/api/material/permanent/api_uploadimage`
- `https://developers.weixin.qq.com/doc/service/api/material/permanent/api_addmaterial`
- `https://developers.weixin.qq.com/doc/service/api/public/api_freepublish_submit`

Live recheck steps:

1. Open each authority URL from a network that can reach `developers.weixin.qq.com`.
2. Confirm the page title, last-updated date, and endpoint path still match the implementation.
3. Re-verify request method, required fields, response fields, retry guidance, and quotas.
4. Confirm that `freepublish_submit` remains intentionally unused by this release.
5. Record screenshots or exported PDFs in the launch ticket. If any page is still unavailable, keep production rollout `OFF`.

## Third-Party Platform Configuration

Configure one WeChat third-party platform application before any account binding:

1. Register the component app ID in `platform_integrations.client_key`.
2. Store the component app secret as the secret reference `env:WECHAT_COMPONENT_APP_SECRET`.
3. Set the HTTPS OAuth callback:
   - `https://<origin>/api/platform-integrations/wechat/oauth/callback`
4. Set the HTTPS message/event callback:
   - `https://<origin>/api/platform-integrations/wechat/events`
5. Configure the callback verification token from `WECHAT_COMPONENT_VERIFY_TOKEN`.
6. Configure the callback `EncodingAESKey` from `WECHAT_COMPONENT_ENCODING_AES_KEY`.
7. Keep `freepublish` disabled in policy and do not request or depend on it for release approval.

## Secret References

Never record secret values in tickets, logs, screenshots, or chat. Allowed secret-reference names:

- `env:WECHAT_COMPONENT_APP_SECRET`
- `WECHAT_COMPONENT_VERIFY_TOKEN`
- `WECHAT_COMPONENT_ENCODING_AES_KEY`
- `credential_encryption_key`

Historical or incident-only references that may appear in old fixtures but should not be required for the normal rollout:

- `env:WECHAT_ACCESS_TOKEN`
- `env:WECHAT_REFRESH_TOKEN`

## Migration And Restart Order

1. Capture a restore-tested database backup.
2. Apply the additive WeChat migrations.
3. Restart backend API processes first so new schemas and routes agree with the migrated database.
4. Restart background workers second so any queued image-generation or sync orchestration uses the new code.
5. Verify the API is healthy before allowing any operator traffic.
6. Keep the operational rollout gate `OFF` during the entire migration and restart window.

## Capability And Permission Procedure

1. Confirm the target account is authorized through the WeChat component login flow.
2. Run `GET /accounts/{account_id}/platform-capabilities`.
3. Record the typed snapshot and verify these content capabilities are `can_use=true`:
   - `upload_article_image`
   - `add_permanent_material`
   - `draft_add`
   - `draft_get` when updating an existing mapped draft
   - `draft_update` when updating an existing mapped draft
4. Confirm `freepublish.can_use == false` and `freepublish.reason == "disabled_by_product_policy"`.
5. Stop rollout if any required capability is missing or if the probe cannot be completed.

## Controlled Rollout

1. Keep production exposure off until all automated gates and manual documentation rechecks pass.
2. Limit the first live operator to one explicitly named test organization and one explicitly named test公众号.
3. Use only draft sync. Do not attempt public publish or browser-driven final publish.
4. Sample these observability records during rollout:
   - `wechat.authorization.completed`
   - `wechat.capabilities.checked`
   - `wechat.article.created`
   - `wechat.article.initial_draft_ready`
   - `wechat.article.version_saved`
   - `wechat.draft.sync_requested`
   - `wechat.draft.sync_conflicted`
   - `wechat.draft.sync_completed`
   - `wechat.draft.sync_failed`
   - `wechat_api_request` structured logs from `app.services.wechat_drafts`

## Alert Thresholds

Use the pure evaluator in `backend/app/services/wechat_rollout_alerts.py` and wire it to the existing monitoring stack. Thresholds are fixed:

- `WECHAT_COMPONENT_TICKET_STALE`: component ticket age strictly greater than 20 minutes.
- `WECHAT_COMPONENT_REFRESH_FAILURES_REPEATED`: 3 or more consecutive component-token refresh failures.
- `WECHAT_AUTHORIZER_REFRESH_FAILURES_REPEATED`: 3 or more consecutive authorizer-token refresh failures.
- `WECHAT_DRAFT_SYNC_FAILURE_RATE_HIGH`: 5-minute draft-sync failure rate strictly above 5%.
- `WECHAT_DRAFT_SYNC_IDEMPOTENCY_CONFLICT`: any reuse of one idempotency key with a different request digest.
- `WECHAT_SCOPE_DENIAL_ANOMALY`: any scope mismatch or cross-organization denial anomaly.

If any alert fires during the initial rollout, stop new authorization and draft-sync attempts immediately.

## Real Smoke Gate

Do not perform the real smoke test until the operator explicitly supplies both:

- the exact test organization name
- the exact target test公众号 name

If either name is missing, stop. Do not guess and do not continue with a live account.

When approval exists, the smoke flow is:

1. Verify the names in the launch ticket.
2. Authorize the named test公众号 through the component login page.
3. Run capability probe and capture the safe typed response.
4. Create one non-sensitive test article with one selected cover image.
5. Freeze one immutable version.
6. Sync one draft only.
7. Verify the result remains in draft state and that no public publish path was triggered.
8. Revoke the authorization after validation if the rollout owner requires a clean rollback rehearsal.

## Evidence Collection

Collect only allowlisted evidence:

- capability snapshot JSON
- safe event rows and timestamps
- `platform_publish_jobs` status transitions
- `wechat_draft_mappings` media ID and remote hash
- structured `wechat_api_request` log fields:
  - `endpoint`
  - `outcome`
  - `duration_ms`
  - `error_code`
  - `retryable`
  - `rid`

Never collect or attach:

- access tokens
- refresh tokens
- app secrets
- raw request bodies
- rendered article HTML
- filesystem paths
- raw provider responses

## Rollback

Immediate rollback must disable new authorization and draft sync without deleting tokens or mappings:

1. Remove operational access to the WeChat rollout entry points or UI affordances.
2. Stop all new real-smoke or operator sync attempts.
3. Leave `platform_account_auth`, `wechat_component_credentials`, `wechat_draft_mappings`, and historical `events` intact for audit and recovery.
4. Do not delete stored mappings just to stop traffic.
5. Do not delete encrypted credentials unless incident response explicitly requires credential rotation.
6. If credentials are suspected compromised, rotate secrets separately after rollout is already disabled.

## Explicit Prohibition

- Do not call `freepublish_submit`.
- Do not automate public publish confirmation from this release path.
- Do not treat inaccessible official documentation as implicitly verified.
