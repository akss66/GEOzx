# Main Agent Runtime Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore reliable model execution and conversation submission after backend container replacement, then prove the account-analysis flow in a real browser.

**Architecture:** Carry the trusted-provider DNS decision from the verified provider record into the OpenAI-compatible runtime adapter instead of weakening the global outbound policy. Configure Nginx to re-resolve the Docker `backend` service through Docker DNS so a backend container replacement does not require a frontend restart.

**Tech Stack:** FastAPI, SQLAlchemy, httpx, pytest, React, Vitest, Nginx, Docker Compose.

## Global Constraints

- Mixed public/private DNS is allowed only for the immutable official DeepSeek template endpoint.
- Custom provider endpoints continue to require every resolved address to be public.
- `/api/`, `/platform-integrations/`, and `/ws/` proxy URI semantics must remain unchanged.
- No production deployment until automated tests and real-browser account-analysis acceptance pass.

---

### Task 1: Trusted DeepSeek runtime DNS policy

**Files:**
- Modify: `backend/app/services/model_infrastructure.py`
- Modify: `backend/app/llm/gateway.py`
- Modify: `backend/app/llm/adapters/openai_compatible.py`
- Modify: `backend/app/llm/adapters/deepseek.py`
- Test: `backend/tests/test_llm_gateway.py`

**Interfaces:**
- Consumes: verified `ModelProvider` rows and `provider_runtime_for_target(...)`.
- Produces: adapter option `allow_mixed_dns: bool`, true only for the official DeepSeek template.

- [ ] Add a gateway test proving the official DeepSeek template constructs an adapter with mixed-DNS allowance.
- [ ] Add a gateway test proving a custom endpoint does not receive that allowance.
- [ ] Run the focused tests and verify the trusted-template case fails before implementation.
- [ ] Add the minimal runtime flag propagation and adapter request option.
- [ ] Run focused and surrounding LLM gateway/security tests.

### Task 2: Dynamic Docker backend resolution in Nginx

**Files:**
- Modify: `frontend/nginx.conf`
- Modify: `frontend/nginx.local.conf`
- Modify: `frontend/tests/nginx-performance.test.mjs`

**Interfaces:**
- Consumes: Docker embedded DNS at `127.0.0.11` and service name `backend`.
- Produces: shared `backend_upstream` whose address is re-resolved without restarting Nginx.

- [ ] Add a config test requiring a Docker resolver, shared upstream zone, and resolved backend server.
- [ ] Run the Nginx test and verify it fails before implementation.
- [ ] Add the upstream and switch all HTTP/WebSocket proxy locations to it.
- [ ] Run config tests and `nginx -t` in the built container.
- [ ] Recreate only the backend container and prove `/api/health/ready` recovers without restarting frontend.

### Task 3: Conversation and release acceptance

**Files:**
- Test: `frontend/src/pages/BrainHome.test.tsx`

**Interfaces:**
- Consumes: existing new-conversation UI and live local stack.
- Produces: regression evidence that a new conversation submits after leaving an existing terminal turn.

- [ ] Add or confirm a focused UI test for existing terminal turn → New conversation → submit.
- [ ] Run frontend unit, type, lint, and build checks.
- [ ] Run backend focused and full relevant tests.
- [ ] Rebuild the local stack and submit the real account-analysis prompt.
- [ ] Verify model, tool, expert, evidence, deliverable, and no-strategy outcomes in UI and database.
- [ ] Review the diff and deploy only if every release gate passes.
