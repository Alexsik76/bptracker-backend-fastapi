# Agent prompt — SMTP timeout fix + magic-link login (step 2 of 2)

This prompt has two parts. Part A is a small hardening fix to the email sender built in
step 1. Part B implements magic-link login on top of it. Do both in one working session,
Part A first.

## Project rules (non-negotiable)

- **Language.** All code, config string literals, and comments — English only. No Ukrainian
  anywhere in code files. (This rule has already cost a rework — do not repeat.)
- **Module boundaries.** No module imports another module's internals.
- **Dependencies.** No new third-party package beyond what is already installed. Token
  generation and hashing use the standard library (`secrets`, `hashlib`).
- **Canonical solutions.** No `type: ignore`, no workarounds.
- **Tooling.** `uv`, `ruff` (lint + format must be clean). Windows dev host.

## Before writing anything

Inspect the current layout of the `auth` module and the existing password-login flow, and
follow whatever structure is already there. Do **not** assume file names. Specifically locate
and reuse:
- the settings class (`Settings` in `config.py`) and its `get_settings` provider;
- the JWT helper `create_access_token(user_id)`;
- the existing user lookup by email used by password login (e.g. a `get_user_by_email` CRUD
  helper) — reuse it, do not duplicate;
- the response schema returned by the existing token endpoint (e.g. `POST /auth/token`) —
  the magic-link confirm endpoint must return the **same** shape;
- the `email_infra` sender from step 1 (`EmailSender`, `get_email_sender`).

Report the actual paths you find before editing, so the mapping is explicit.

---

## Part A — bound the SMTP send timeout

Inline email delivery happens inside the request (see `DECISIONS.md`, D-001). `aiosmtplib`
defaults to a 60 s timeout; 60 s of blocking on a login request is too long. Make it explicit
and short.

1. Add to `Settings`: `smtp_timeout: int = 10`.
2. `SmtpEmailSender.__init__` accepts `smtp_timeout: int` and stores it; `get_email_sender`
   passes `settings.smtp_timeout`.
3. `send(...)` passes `timeout=self.smtp_timeout` to `aiosmtplib.send(...)`.
4. Add `SMTP_TIMEOUT=10` to `.env.example` with a one-line English comment.
5. Extend the existing transport-parameters test to assert `timeout` is forwarded.

---

## Part B — magic-link login

### Scope

Login for **existing users only**. An unknown email produces the same response as a known
one but sends nothing and creates no row. Registration-by-link (creating a user from an
unknown address) is explicitly out of scope.

The token layer is unaware of the login method: the ceremony ends by calling the existing
`create_access_token(user_id)` and returning the existing token response schema. No queue,
no retries, no outbox (D-002) — email is sent inline via `EmailSender`.

### New settings (`config.py`)

- `magic_link_base_url: str` — client route the email links to (the client extracts the token
  and issues the confirm POST). No default.
- `magic_link_ttl_minutes: int = 15`
- `magic_link_token_bytes: int = 32`

Add matching entries to `.env.example` (`MAGIC_LINK_BASE_URL`, `MAGIC_LINK_TTL_MINUTES`,
`MAGIC_LINK_TOKEN_BYTES`) with brief English comments.

### Data model — `magic_links` table

- `email: str` — **primary key**. One active link per email; a new request replaces the old.
  No foreign key to `users` (the row is keyed by email; the user is resolved at confirm time).
- `token_hash: str` — SHA-256 hex digest of the raw token. **Unique index** (confirm looks
  the row up by this). The raw token is never stored.
- `created_at: datetime | None` — `timestamptz`, DB server default `now()`.
- `expires_at: datetime` — `timestamptz`.

Follow the project's existing model conventions (SQLModel, `ClassVar[str]` for
`__tablename__`, `timestamptz` columns). Generate an Alembic migration (sync), review it
line-by-line, apply it. `users.password_hash` is already nullable — magic-link-only users
need no `users` migration; confirm this rather than altering `users`.

### Token helpers (in the auth security layer)

- Generate: `secrets.token_urlsafe(settings.magic_link_token_bytes)` → raw token.
- Hash: `hashlib.sha256(raw.encode()).hexdigest()`. Used both when storing and when looking
  up on confirm.

### CRUD

- **Upsert by email.** Insert a row for `email` with the new `token_hash` and `expires_at`;
  on existing `email`, replace `token_hash`, `created_at`, `expires_at`. Because `email` is
  the primary key this is a single upsert and doubles as lazy cleanup of the previous link.
- **Get by `token_hash`** → `MagicLink | None`.
- **Delete by email** — used to consume the link on successful confirm.

### Endpoints (auth router)

**`POST /auth/magic-link/request`** — body `{ "email": EmailStr }`.
1. Resolve the user by email.
2. If the user exists: generate a raw token, hash it, upsert the `magic_links` row with
   `expires_at = now(UTC) + ttl`, build the link as
   `f"{settings.magic_link_base_url}?token={raw_token}"`, and send it via
   `email_sender.send(to=email, subject=..., text=..., html=...)`. The raw token appears only
   in the email — never logged, never persisted.
3. **Always** return `202 Accepted` with a generic body (e.g. a fixed "if the address is
   registered, a link has been sent" message), identical whether or not the user exists.
   If the user does not exist, do not call the sender and do not create a row.
4. A send failure propagates as a 5xx (inline delivery, D-001) — the client retries.

Email format validation via `EmailStr` is acceptable (that reports malformed input, not
account existence).

**`POST /auth/magic-link/confirm`** — body `{ "token": str }`.
1. Hash the token and fetch the row by `token_hash`.
2. Reject if: no row, or `expires_at < now(UTC)`. On an expired row, delete it.
3. Resolve the user by the row's `email`; reject if the user no longer exists.
4. On success, within one transaction: delete the row (single use), then issue
   `create_access_token(user.id)` and return the **same** response schema as the password
   token endpoint.
5. Rejections return a single generic auth error (e.g. `401`), not distinguishing
   "unknown" / "expired" / "already used", to avoid leaking link state.

`POST` (not `GET`) for confirm is deliberate: the email links to the client route, so mail
clients pre-fetching the URL cannot consume the token.

### Tests

Override the `get_email_sender` dependency with an async fake/`AsyncMock` that records calls —
no real SMTP. Use the project's existing override-based fixture style and the real
`bp_tracker_test` database.

Request:
- known email → `202`; sender called exactly once with the correct recipient; exactly one row
  exists for that email; the stored `token_hash` equals `sha256(raw)` and is **not** the raw
  token (assert the raw token does not appear in storage);
- unknown email → `202`; sender **not** called; no row created;
- two requests for the same email → one row remains, with the second request's `token_hash`.

Confirm:
- valid token → `200`, returns an access token in the expected schema, and the row is gone;
- expired token → generic `401`, no token issued, row removed;
- reused token (confirm the same token twice) → second attempt fails generically;
- unknown/malformed token → generic `401`.

### Out of scope

Rate limiting (D-003), outbox/retries (D-002), registration-by-link, constant-time hardening
of the request endpoint. Do not add stubs for these.

---

## Acceptance criteria

- `ruff` clean (lint + format); the full existing test suite still passes; new tests pass.
- No Ukrainian in code or comments.
- Raw magic-link tokens are never stored or logged — only their SHA-256 hash is persisted.
- `EmailSender` remains the only path through which mail is sent; the router does not touch
  `aiosmtplib`.
- The confirm endpoint returns byte-for-byte the same response schema as password login.

## After completion

Report: the actual auth-module paths you found and followed, the files created/changed, new
`.env` variables, the migration revision id, and the test summary (count, status).
