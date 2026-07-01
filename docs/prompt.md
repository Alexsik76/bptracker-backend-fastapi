# Task: build the `auth` module for BP Tracker

Project: FastAPI + SQLModel + async SQLAlchemy (psycopg3), PostgreSQL 18, uv, Windows.
Flat project root: `config.py`, `db.py`, `main.py`, plus module packages
`measurements/`, `prescriptions/`, `reminders/`. Alembic configured for sync migrations.
Reference module patterns: `measurements` (flat single-entity), `prescriptions`/`reminders`
(sub-packages for multi-entity). This module has ONE entity (`users`) — keep it FLAT
like `measurements`, do NOT create `models/`, `crud/`, `router/` sub-packages.

## Scope of THIS task
A self-contained auth module: `users` table, password auth (register + login),
JWT issuance, a REAL `get_current_user_id` dependency, migration for the `users`
table, and tests. Do NOT touch `measurements`, `prescriptions`, `reminders` or their
auth stubs — they stay on their own hardcoded-UUID stubs in this task. Replacing those
stubs and adding FK constraints is a SEPARATE later task; leave a clear seam, add nothing
toward it here.

## Forward-compatibility constraints (hard — the module must not close these doors)
The long-term primary auth method will be WebAuthn/passkey, with an email magic-link
fallback. Password is only the initial method. Therefore:
- `password_hash` MUST be nullable on `users` (a future passkey-only user has no password).
- Token issuance (`create_access_token`) MUST take only a `user_id` and MUST NOT depend
  on passwords or on any login method. Passkey/magic-link will reuse it unchanged.
- `get_current_user_id` MUST depend ONLY on decoding the bearer token. No DB lookup, no
  knowledge of how the token was obtained.
- Do NOT add any passkey/magic-link scaffolding, tables, or fields now. Additive later.

## Libraries
- Password hashing: `bcrypt` used directly (NOT passlib — unmaintained). Add to deps via
  `uv add bcrypt`. Note bcrypt's 72-byte password limit in a code comment.
- JWT: `PyJWT` (NOT python-jose). Add via `uv add pyjwt`.
- Email type: use pydantic `EmailStr` (email-validator already present via fastapi[standard]).

## Config additions (`config.py`, in `Settings`)
Add:
- `jwt_secret: str`                      # required, NO default — read from env JWT_SECRET
- `jwt_algorithm: str = "HS256"`
- `access_token_expire_minutes: int = 60 * 24`   # 1 day for now
Add `JWT_SECRET=<a long random dev value>` to `.env`, and a placeholder line to
`.env.example`. `get_settings()` stays `@lru_cache`d as-is.

## Files to create (all flat under `auth/`)

auth/
init.py
models.py       # UserBase / User(table=True) / UserCreate / UserRead
security.py     # password hashing + JWT encode/decode primitives
crud.py         # create_user, get_user_by_email
deps.py         # bearer extraction + get_current_user_id -> UUID + CurrentUserId
router.py       # POST /auth/register, POST /auth/login
tests/
init.py
test_auth.py

### `auth/models.py`
- `UserBase(SQLModel)`: `email: EmailStr` (unique+indexed on the table),
  `timezone: str | None = None`  # IANA, per domain spec; not used by auth logic yet.
- `User(UserBase, table=True)`, `__tablename__: ClassVar[str] = "users"`:
  - `id: UUID | None` — `Column(Uuid, primary_key=True, server_default=text("uuidv7()"))`
    (same pattern as other tables).
  - `email` — enforce uniqueness + index via `Field(sa_column=Column(..., unique=True, index=True))`
    or `Field(index=True, sa_column_kwargs={"unique": True})`; pick the canonical SQLModel way.
  - `password_hash: str | None = None`  # NULLABLE — future passkey-only users.
  - `created_at: datetime | None` — `Column(DateTime(timezone=True), server_default=func.now())`.
- `UserCreate(SQLModel)`: `email: EmailStr`, `password: str = Field(min_length=8)`,
  `timezone: str | None = None`.  # `password` is plaintext input, never persisted as-is.
- `UserRead(SQLModel)`: `id: UUID`, `email: EmailStr`, `timezone: str | None`,
  `created_at: datetime`.  # NEVER expose password_hash.
- No `UserUpdate` in this task.
- Add `TokenResponse(SQLModel)`: `access_token: str`, `token_type: str = "bearer"`.

### `auth/security.py`
Pure functions, no FastAPI, no DB:
- `hash_password(password: str) -> str`  (bcrypt gensalt + hashpw, decode to str)
- `verify_password(password: str, password_hash: str) -> bool`
- `create_access_token(user_id: UUID) -> str`  — payload `{"sub": str(user_id),
  "exp": now + access_token_expire_minutes}`, signed with settings.jwt_secret /
  jwt_algorithm. Takes ONLY user_id.
- `decode_access_token(token: str) -> UUID`  — decode, return UUID(sub);
  raise a dedicated error on `jwt.ExpiredSignatureError` / `jwt.InvalidTokenError`
  (raise `ValueError` or a small custom exc; deps.py maps it to 401).

### `auth/crud.py`
Async, mirror the style of `measurements/crud.py`:
- `create_user(session, data: UserCreate) -> User` — hash the password, build `User`,
  add/commit/refresh. Let a duplicate-email DB IntegrityError propagate (router maps it).
- `get_user_by_email(session, email: str) -> User | None`.

### `auth/deps.py`
- `oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")`  # tokenUrl is docs-only;
  login uses a JSON body (below), extraction just reads the Bearer header.
- `async def get_current_user_id(token: Annotated[str, Depends(oauth2_scheme)]) -> UUID`:
  call `decode_access_token`; on failure raise `HTTPException(401, "Invalid or expired token",
  headers={"WWW-Authenticate": "Bearer"})`. No DB access (stateless — deliberate;
  DB-existence checks are a possible future addition, do not add now).
- `CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]`.
This module is the import target the three domain modules will later switch to; keep it
importing only `auth.security` + fastapi (no circular imports, no DB).

### `auth/router.py`
`router = APIRouter(prefix="/auth", tags=["auth"])`
- `POST /register` (body `UserCreate`) -> `TokenResponse`, 201: create user; on duplicate
  email raise `HTTPException(409, "Email already registered")`; then return
  `create_access_token(user.id)`. (Auto-login on register.)
- `POST /login` (JSON body `LoginRequest{email: EmailStr, password: str}`) -> `TokenResponse`:
  fetch by email; if missing OR `password_hash is None` OR `verify_password` fails,
  raise `HTTPException(401, "Incorrect email or password")` (same message for all —
  no user enumeration). Else return a token.
  Define `LoginRequest` in `router.py` or `models.py`. JSON (not form) — native mobile client.

## Integration points
- `main.py`: `app.include_router(auth_router)`.
- `alembic/env.py`: add `from auth import models as _auth_models  # noqa: F401`.
- Generate the migration: `alembic revision --autogenerate -m "add users table"`.
  Review it: it must create ONLY `users` (no FK changes to other tables — their models
  still declare plain UUID). Fix autogenerate gaps the project has hit before: ensure
  `import sqlmodel.sql.sqltypes` if the generated file references it; `created_at` uses
  `DateTime(timezone=True)`; `downgrade()` drops the table.

## Tests (`auth/tests/test_auth.py`), pytest + pytest-asyncio, live `_test` DB
Follow the existing test setup (SelectorEventLoop hook for psycopg3 on Windows; session
override to the test DB). IMPORTANT: auth tests use REAL auth — do NOT apply any
`get_current_user_id` override here.
Cover:
1. register success -> 201, returns a bearer token.
2. register duplicate email -> 409.
3. register password too short -> 422.
4. login success -> 200, returns token.
5. login wrong password -> 401; login unknown email -> 401 (same message).
6. `create_access_token` / `decode_access_token` roundtrip returns the same UUID.
7. decode of a tampered/expired token raises (unit-level).
8. dependency end-to-end: mount a throwaway route `Depends(get_current_user_id)` on a
   minimal app (or reuse the main app) and assert: valid token -> the correct user_id;
   missing/invalid token -> 401.

## Style (hard constraints)
- All code and comments in English.
- Canonical solutions first; no `# type: ignore` workarounds — use `ClassVar[str]` for
  `__tablename__`, `col()` from SQLModel where needed for typed `order_by`.
- No unstated / "just in case" fields or endpoints. Exactly what is listed above.
- Match the multi-class SQLModel pattern of `measurements`: Base -> table entity -> Create -> Read.
- Do not modify the other three modules or their stubs.