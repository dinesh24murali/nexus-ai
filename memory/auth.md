# Memory: Auth

> How authentication and authorization work in Nexus AI.

- Registration is **invite-only** by default (`REGISTRATION_MODE=invite|open`): single-use, expiring (7 d default) invite codes created with `python -m app.cli invite create`; stored as SHA-256 hashes; every invalid case returns the same `INVITE_INVALID`
- Email + password; argon2id hashes; email stored lower-cased (`citext`)
- Access token: JWT HS256, 15 min, sent as `Authorization: Bearer`; kept in memory on the client (never localStorage)
- Refresh token: opaque random, stored hashed in `refresh_tokens`, 7-day lifetime, cookie `nexus_refresh` (`HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`)
- Rotation on every refresh; tokens share a `family_id` — reuse of a revoked token revokes the entire family (all sessions from that login)
- Password change revokes all other sessions; account deletion (`DELETE /me`, TASK-028) revokes all sessions then enqueues `purge_user`
- `DELETE /me`: body `confirm_email` must equal the account email (case-insensitive, trimmed) else 400 `CONFIRMATION_MISMATCH` and nothing changes; then, in one transaction: status `pending_deletion` (sign-in is refused with the same answer as a wrong password, and the access token stops working at once because `current_user` requires `active`), every refresh token revoked, the user's queued jobs cancelled, `purge_user` queued; 202. A second request cannot authenticate, and the job key (`purge:<id>`) also makes a replay a no-op
- Login errors never reveal whether the email exists (`INVALID_CREDENTIALS`); 10 failed logins / 15 min per IP+email → `RATE_LIMITED`
- Authorization is owner-based only: every repository call takes `user_id`; Qdrant and memory-store queries add the user filter server-side; cross-user access → 404
- No OAuth, no email verification/reset in v1; admin actions are CLI-only (`python -m app.cli`): invites, reset password, disable/enable user; disabled users get 403 `ACCOUNT_DISABLED` only after a correct password
- **Implementation facts (TASK-006, 2026-11-21):**
  - Access JWT claims: `sub` (user id), `fid` (refresh-token family = the session), `iat`, `exp`. `fid` is how "change password" keeps the caller's own session even though the refresh cookie (`Path=/api/v1/auth`) is not sent to `/me/*`
  - Error codes: `TOKEN_EXPIRED` (expired access token; client should refresh), `UNAUTHORIZED` (anything else), `INVALID_CREDENTIALS`, `ACCOUNT_DISABLED` (403, only after a correct password), `INVALID_PASSWORD` (400, wrong current password), `EMAIL_TAKEN` (409), `INVITE_INVALID` (403)
  - Invite code = `nx_` + 32 hex chars (128 bit), stored as SHA-256; shown once at creation. Validated and row-locked (`FOR UPDATE`) **before** the user insert, so `EMAIL_TAKEN` is only reachable with a valid invite (no email enumeration) and a duplicate email does not burn the invite
  - Refresh rotation locks the token row (`FOR UPDATE`): two concurrent uses of one token serialise and the second counts as reuse, which revokes the whole family. **The frontend must single-flight refreshes (one refresh at a time across tabs, e.g. `navigator.locks`)** or a legitimate double-submit logs the user out
  - Rate limits (Postgres fixed-window counters, table `rate_limits`, window 15 min): 10 failed logins per IP+email (key uses a hash of the email), 10 failed invite attempts per IP. Both block even a correct credential until the window ends. Behind Caddy the IP is the last `X-Forwarded-For` entry (`TRUSTED_PROXY=true`, set by Compose); the API must never be reachable directly in production
  - Login runs one argon2 verification even for unknown emails (timing); disabled users' still-valid access tokens are rejected (401) and the CLI revokes their refresh tokens
  - Email is validated structurally only (one `@`, no whitespace, <=254), lower-cased and trimmed. No deliverability/reserved-domain check: v1 sends no email, and stricter validators reject names the spec's `format: email` allows
  - Admin CLI: `invite create|list|revoke`, `reset-password` (`--password-stdin` or prompt), `disable-user`/`enable-user`, `flags set|list`

