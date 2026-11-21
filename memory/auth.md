# Memory: Auth

> How authentication and authorization work in Nexus AI.

- Registration is **invite-only** by default (`REGISTRATION_MODE=invite|open`): single-use, expiring (7 d default) invite codes created with `python -m app.cli invite create`; stored as SHA-256 hashes; every invalid case returns the same `INVITE_INVALID`
- Email + password; argon2id hashes; email stored lower-cased (`citext`)
- Access token: JWT HS256, 15 min, sent as `Authorization: Bearer`; kept in memory on the client (never localStorage)
- Refresh token: opaque random, stored hashed in `refresh_tokens`, 7-day lifetime, cookie `nexus_refresh` (`HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`)
- Rotation on every refresh; tokens share a `family_id` — reuse of a revoked token revokes the entire family (all sessions from that login)
- Password change revokes all other sessions; account deletion revokes all sessions then enqueues `purge_user`
- Login errors never reveal whether the email exists (`INVALID_CREDENTIALS`); 10 failed logins / 15 min per IP+email → `RATE_LIMITED`
- Authorization is owner-based only: every repository call takes `user_id`; Qdrant and memory-store queries add the user filter server-side; cross-user access → 404
- No OAuth, no email verification/reset in v1; admin actions are CLI-only (`python -m app.cli`): invites, reset password, disable/enable user; disabled users get 403 `ACCOUNT_DISABLED` only after a correct password
