# Douyin Production OAuth Design

## Goal

Use `https://tzxai.top` as the production origin and complete a real Douyin account-binding flow: generate an official authorization URL, receive the HTTPS callback, exchange the code, securely persist account tokens, load the real account profile, and make later data sync use those tokens.

## Scope

- Production callback: `https://tzxai.top/platform-integrations/douyin/oauth/callback`
- Initial scope: `user_info`; other scopes are requested only after platform approval.
- Webhooks and JSBridge are independent follow-up capabilities and do not block account binding.
- Cloudflare Worker callback forwarding remains disabled.

## Capability Map From Official Documentation

- Website OAuth/OpenAPI is the current account-binding route.
- The mobile open SDK is reserved for a future native iOS/Android application; it is not needed by this website application.
- Webhooks will later use a dedicated HTTPS endpoint with challenge response, signature verification, `Msg-Id` idempotency, and fast acknowledgement. Events are enabled only after their required scopes are approved.
- The documented material repository is an enterprise IM material capability with separate permission and limits. It stays outside the publishing package flow until the matching business permission is approved.

## Architecture

1. The frontend requests a signed authorization URL from the backend.
2. Douyin redirects to the backend with a one-time code and signed state.
3. The backend exchanges the code using the server-side Client Secret.
4. Access and refresh tokens are encrypted with Fernet before PostgreSQL persistence. The encryption key exists only in `CREDENTIAL_ENCRYPTION_KEY` on the server.
5. The callback fetches the official user profile and updates the account matrix nickname/avatar when available.
6. Sync operations decrypt the access token. If it is near expiry, they use the refresh token, persist the rotated tokens, and continue.

## Security Rules

- Client Secret, access token, refresh token, and encryption key never appear in API responses, events, browser storage, or logs.
- OAuth state is signed, expires in 15 minutes, and binds the organization, flow, and target account.
- The callback URL must exactly match the URL configured in Douyin Open Platform.
- Authorization requests use no more scopes than the operation currently needs.
- Existing environment/vault token references remain readable for migration compatibility, but new callbacks use encrypted database credentials.

## Acceptance

- A real Douyin scan returns to `tzxai.top` without a redirect error.
- The account matrix receives a connected account with real `open_id` and profile data.
- Database values are ciphertext and API responses contain no token material.
- A data sync can use the stored token and refresh it when expired.
