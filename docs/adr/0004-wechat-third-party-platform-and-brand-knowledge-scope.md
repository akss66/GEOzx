# ADR 0004: WeChat Third-Party Platform And Brand Knowledge Scope

- Status: Accepted
- Date: 2026-08-11
- Decision owner: Product and engineering

## Context

The product must connect organization-owned WeChat Official Accounts without
collecting a separate AppSecret for every account. It also needs reusable brand
knowledge for content production. Platform authorization and brand knowledge
have different ownership, lifecycle, and permission boundaries; treating either
as an attribute of the other would permit accidental cross-organization access.

WeChat Open Platform delivers component tickets and authorization lifecycle
events to a public encrypted callback. Both the browser redirect and this event
endpoint are untrusted inputs and must remain safe under replay, tampering,
oversized requests, invalid encryption, and duplicate delivery.

## Decision

### Use only WeChat Open Platform third-party authorization

The supported connection path is the third-party platform component flow:

1. an organization administrator creates a short-lived authorization session;
2. the backend obtains a pre-authorization code with the component token;
3. WeChat redirects an authorization code to the backend;
4. the backend exchanges the code and stores the authorizer grant;
5. encrypted lifecycle callbacks update tickets or revoke authorization.

Per-account AppSecret collection is not supported. The component AppSecret is a
server-side secret reference. Component tickets, component access tokens, and
authorizer access/refresh tokens are encrypted at rest through the shared
credential-encryption boundary and are never returned to a browser.

Token refresh may refresh credentials only while an account is already
authorized. It cannot grant authorization. A successful authorization callback
may set `authorized`; an `unauthorized` lifecycle event sets `unauthorized`,
removes usable credentials, and cannot later be reversed by token refresh.

### Make browser authorization state opaque and one-time

Authorization state is a high-entropy opaque value. Only its SHA-256 digest is
persisted. The durable state record contains a non-secret state ID,
organization ID, initiating user ID, optional client/project/knowledge-base
intent, issuance time, and expiry time.

Consumption is recorded with a separate unique durable event before the
authorization code is exchanged. Consequently, concurrent or replayed
callbacks cannot exchange the same state twice. Redirect targets are
server-owned and fixed; neither tokens nor user-controlled redirect URLs are
carried in state or result redirects.

### Treat encrypted callbacks as hostile input

The callback endpoint enforces a bounded request body, a five-minute timestamp
window, bounded nonce and signature fields, WeChat message-signature
verification, AES-256-CBC decryption with WeChat's 32-byte PKCS#7 padding, and
constant-time Component AppID binding. XML declarations capable of defining
external entities are rejected.

The first release uses one runtime component security configuration:
`WECHAT_COMPONENT_VERIFY_TOKEN` and
`WECHAT_COMPONENT_ENCODING_AES_KEY`. These values are never persisted, logged,
or returned. Multiple component instances will require a future secret-reference
model and are outside this decision.

Only normalized, non-secret callback metadata is persisted. A stable digest in
the existing unique event idempotency key deduplicates
`component_verify_ticket`, `authorized`, `updateauthorized`, and `unauthorized`
deliveries. Ticket and authorization-code plaintext never enters the event log.

### Keep brand knowledge independent

A brand knowledge base belongs to an organization independently of any platform
account. Account-to-knowledge binding is a separate, explicitly authorized
operation that must verify both resources belong to the same organization.

An optional `knowledge_base_id` in authorization state records future binding
intent only. Completing WeChat authorization does not read that knowledge base,
create a binding, grant access, or reveal knowledge from another organization.
Revoking a platform account likewise does not delete or transfer brand
knowledge.

## Alternatives Considered

### Store one AppSecret per Official Account

Rejected. It bypasses the required third-party platform lifecycle, multiplies
long-lived secrets, and makes revocation and capability management inconsistent.

### Store signed state directly in the browser

Rejected. A signed payload remains replayable and exposes organization and
binding metadata. Opaque, hashed, one-time state provides confidentiality and
durable replay prevention.

### Bind knowledge automatically during OAuth completion

Rejected. OAuth proves platform authorization, not knowledge-base permission.
Automatic binding would collapse two authorization domains and risk cross-org
data access.

### Persist raw callback XML for audit

Rejected. Raw messages contain component tickets or authorization codes. The
normalized event and idempotency digest provide sufficient operational evidence
without retaining credentials.

## Consequences

Positive:

- organizations authorize accounts without sharing per-account AppSecrets;
- authorization and revocation have one authoritative lifecycle;
- browser state and callback delivery are replay resistant;
- event audit data contains no access, refresh, ticket, or authorization-code
  secrets;
- brand knowledge remains reusable and organization scoped.

Costs:

- the runtime must securely provision two component callback environment
  variables in addition to the component AppSecret reference;
- a consumed state is intentionally not reusable after an exchange failure;
- supporting multiple component instances requires a future secret-reference
  and routing migration.

## Related Documents

- `docs/superpowers/specs/2026-08-11-wechat-official-account-agent-design.md`
- `docs/superpowers/plans/2026-08-11-wechat-official-account-agent.md`
- `docs/adr/0001-production-agent-runtime-and-double-memory.md`
