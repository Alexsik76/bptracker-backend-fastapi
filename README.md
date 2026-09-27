# BP Tracker — FastAPI backend

![CI](https://github.com/Alexsik76/bptracker-backend-fastapi/actions/workflows/ci.yml/badge.svg)

An async REST backend for tracking blood-pressure measurements and medication
prescriptions/intake. A Python/FastAPI rebuild of an earlier C# service.

## Tech stack

- [FastAPI](https://fastapi.tiangolo.com/) (`fastapi[standard]`)
- [SQLModel](https://sqlmodel.tiangolo.com/) over async SQLAlchemy 2.0
- [psycopg3](https://www.psycopg.org/psycopg3/) (async driver)
- PostgreSQL 18
- [Alembic](https://alembic.sqlalchemy.org/) for migrations
- [PyJWT](https://pyjwt.readthedocs.io/) for stateless access tokens
- [py_webauthn](https://pypi.org/project/webauthn/) for passkey/WebAuthn authentication
- Managed with [uv](https://docs.astral.sh/uv/), linted with [ruff](https://docs.astral.sh/ruff/)
- Tested with pytest + pytest-asyncio

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/) and Docker.

```bash
# 1. Bring up PostgreSQL (single service, see compose.yaml)
docker compose up -d

# 2. Copy the env template and fill in real values
cp .env.example .env

# 3. Apply database migrations
uv run alembic upgrade head

# 4. Run the app
uv run fastapi dev main.py

# 5. Create the test database (tests do not create it)
docker exec bptracker-fa-db createdb -U bp bp_tracker_test

# 6. Run tests (uses a separate <POSTGRES_DB>_test database)
uv run pytest
```

## Modules

- **auth** — magic-link and WebAuthn/passkey authentication, JWT access/refresh
  tokens, session management. All other endpoints require an
  `Authorization: Bearer <token>` header.
- **users** — user profile (`GET /users/me`, `PATCH /users/me`, `DELETE /users/me`).
- **measurements** — blood-pressure readings (systolic/diastolic/pulse). Plain CRUD
  plus photo recognition via Gemini API (`POST /measurements/analyze`).
- **prescriptions** — a prescription (doctor, date, active flag) with its
  medication items (dose, frequency, time-of-day slots, course).
- **reminders** — per-user reminder configuration (slot times) and a log of  confirmed medication intakes, one record 
  per user, slot and date.
- **export** — CSV and print-ready PDF export of measurement history (with optional date range), delivered via email.
- **email_infra** — SMTP delivery and async email outbox with retries.
- **cleanup** — background worker for expired magic links, challenges, sessions and old sent emails.
