# Task: implement the `reminders` module (BP Tracker backend)

## Project context
FastAPI + SQLModel + async SQLAlchemy + psycopg3 + PostgreSQL 18, managed with `uv`, linted with `ruff`. Flat project structure (no `app/` package): top-level modules `config.py`, `db.py`, `main.py`, plus domain packages `measurements/` and `prescriptions/`, each already implemented and merged. `reminders/` is the next domain package, following the exact same conventions.

Reference existing modules before writing anything:
- `measurements/` — simplest CRUD example (flat `models.py`/`crud.py`/`router.py`/`tests/`).
- `prescriptions/` — the pattern to actually mirror, since it also has multiple entities: `prescriptions/models/` (package: `enums.py`, `prescription.py`, `medication_item.py`, `__init__.py` re-exporting), `prescriptions/crud/` (package, same split + re-export `__init__.py`), `prescriptions/router/` (package: `deps.py` for the shared `get_current_user_id` stub, `prescription.py`, `medication_item.py`, `__init__.py` combining sub-routers).
- `alembic/env.py` — every table-owning module gets one import line (`from X import models as _x_models  # noqa: F401`).
- `main.py` — every module's router gets `app.include_router(...)`.
- `conftest.py` — every module's `get_current_user_id` stub gets imported under a unique alias and overridden alongside the others in `client_factory`.

## Auth stub
There is no real auth yet. Each module currently defines its own `get_current_user_id` dev stub returning a hardcoded UUID, following the `prescriptions/router/deps.py` seam pattern (`_DEV_USER_ID`, `get_current_user_id()`, `CurrentUserId = Annotated[UUID, Depends(...)]`). Do the same for `reminders`: put it in `reminders/router/deps.py`, own local hardcoded UUID, no attempt to unify with the other two modules' stubs (that unification is a deliberate future TODO, out of scope here).

## Domain model — two entities, both new

### `reminder_config` — one row per user (not per prescription)
Deliberately 1:1 with `users`, not with `prescriptions`. Rationale already settled: a user may have multiple simultaneously active prescriptions (no "single active prescription" invariant in this project), but "when is my morning" is a property of the person, not of any one prescription. Putting slot times on `prescriptions` would force the user to reconcile multiple different "morning times" — nonsensical.

| Column | Type | Key | Notes |
|---|---|---|---|
| user_id | uuid | PK + FK → users (FK deferred, same stub pattern as elsewhere — plain uuid, indexed, no real FK constraint until auth module lands) | one row per user |
| morning_time | time | | |
| day_time | time | | |
| evening_time | time | | |
| max_reminders | int | | push notification count, not used by any logic yet — just stored |
| duration_minutes | int | | length of the reminder window in minutes, starting at the relevant slot time |

No `late_confirm_days` field — deliberately unbounded, late confirmation has no time limit.

### `intake_reports` — append-only log of confirmed intakes
Independent third table, not part of `reminder_config` or `prescriptions`/`medication_items`. Never updated after creation except that it may exist with `is_late=True` vs `False` depending on when it was created — no field is ever edited by the user after the row exists (a confirmation is created once and never mutated).

| Column | Type | Key | Notes |
|---|---|---|---|
| id | uuid | PK | server-default `uuidv7()`, same as other tables |
| user_id | uuid | indexed | plain uuid, same stub pattern |
| prescription_id | uuid, nullable | FK → prescriptions.id, `ON DELETE SET NULL` | reference only, never joined on for reading "what was taken" — see snapshot below |
| period | enum (Morning/Day/Evening) | | reuse the same slot concept as `WhenSlot` in `prescriptions.models.enums` — either import that enum directly, or duplicate it locally if that creates an awkward cross-module dependency; prefer importing it if it doesn't create a circular import, since it's the same concept (a time-of-day slot) |
| date | date | | the calendar date this intake belongs to, from the client's timezone — NOT necessarily today; this is the date the slot belongs to |
| confirmed_at | timestamptz | server-default `now()` | when the confirmation actually happened; never edited by the user |
| is_late | bool | | computed once at creation time: was `confirmed_at` after the end of that slot's reminder window (`slot_time + duration_minutes` from that user's `reminder_config`)? Stored, not recomputed later. |
| snapshot | jsonb | | self-contained copy of what was taken at confirm time: array of `{medicine, amount, condition}` pulled from the active prescription's medication items for that slot at the moment of confirmation — this table must never require a join back to `medication_items` to know what was taken |

Uniqueness constraint: `(user_id, period, date)` — one confirmation per user per slot per day, protects against duplicate confirms. (Note: earlier drafts included `prescription_id` in this uniqueness tuple — drop that; the uniqueness is per user/day/slot regardless of prescription, since a slot can have multiple medication items from multiple active prescriptions collapsed into a single snapshot array.)

## Explicitly NOT part of this module
- No "Missed" status stored anywhere. A slot is only ever `confirmed` (a row exists) or computed as `missed`/`pending`/`upcoming` at read time by comparing `now()` against `reminder_config` slot times — that computation is business logic for a future `/reminders/today` endpoint, not something to build in this pass. This module only needs the two tables, their models, plain CRUD for `reminder_config` (get/create-or-update — it's 1:1, so no separate list/create split needed, consider a single upsert-style operation), and CRUD for `intake_reports` limited to: create (confirm), get by id, list (no filters needed yet, history endpoint is explicitly out of scope for now — just make sure nothing about the schema prevents building it later, which it doesn't since nothing is deleted or bounded).
- No "today" projection endpoint, no "missed" computation endpoint, no schedule engine. Out of scope for this task entirely.
- No course-duration unwinding logic (courses vs ongoing meds) — that's already deferred in the `prescriptions` module and stays deferred here too.
- Do not add any field for limiting how far back a late confirmation can happen.

## Deliverables, mirroring the `prescriptions` package structure exactly

reminders/
models/
init.py          # re-exports
reminder_config.py    # ReminderConfigBase / ReminderConfig(table=True) / Create / Read / Update
intake_report.py      # IntakeReportBase / IntakeReport(table=True) / Create / Read (no Update — append-only)
crud/
init.py           # re-exports
reminder_config.py
intake_report.py
router/
init.py           # combines sub-routers, re-exports get_current_user_id
deps.py                # local dev auth stub
reminder_config.py     # GET/PUT on /reminders/config (singular, since it's 1:1 per user)
intake_report.py       # POST /reminders/intake-reports (confirm), GET list, GET by id
tests/
test_reminder_config.py
test_intake_reports.py

Plus the usual integration points:
- `alembic/env.py`: add `from reminders import models as _reminders_models  # noqa: F401`
- `main.py`: `app.include_router(reminders_router)`
- `conftest.py`: import and alias `reminders`'s `get_current_user_id`, add to `client_factory` overrides alongside the other two

## Style requirements (hard constraints)
- Code and config comments: English only, no exceptions, even though this task description itself may be discussed in Ukrainian.
- No unstated fields, no "just in case" columns — every column above is exactly what was agreed; don't add anything not listed (e.g. no status enum, no extra timestamps, no soft-delete flags).
- Follow the exact same SQLModel multi-class pattern as `measurements`/`prescriptions`: `Base` (shared fields + validation) → `table=True` entity (adds id/server-defaults/FK) → `Create` → `Read` → `Update` (only where mutation makes sense — `intake_reports` gets no `Update` class, it's append-only).
- `id` columns: `Column(Uuid, primary_key=True, server_default=text("uuidv7()"))`, same as existing tables. `reminder_config.user_id` is the PK directly (no separate `id` column), matching how the old `reminder_config` spec always treated it as PK+FK on the owning entity.
- Ownership/scoping checks (a user can only touch their own `reminder_config` and their own `intake_reports`) follow the same pattern as `get_measurement`/`get_prescription` — scope every query by `user_id` from the auth dependency.
- After building, run `uv run pytest -v` and report all results before considering the task done. Do not silently skip failing tests.

## One open implementation detail to resolve during coding, not before
How exactly `is_late` gets computed at confirm time needs `reminder_config` for that user to determine the slot's scheduled time and window. If no `reminder_config` exists yet for the user, decide what `confirm` should do (e.g. 404/422 asking the user to set up their reminder config first) — pick the option consistent with how `prescriptions`/`medication_items` in this codebase 404 when a parent resource is missing, and flag the decision explicitly when done rather than assuming it silently.