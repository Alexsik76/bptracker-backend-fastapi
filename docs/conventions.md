# Cross-cutting conventions

## Time & timezone: the client normalizes

The client is the single authority for timezone. This is a project-wide rule,
not a per-module decision — every timestamp everywhere follows it.

1. All timestamp columns are `timestamptz` (never naive `timestamp`).
   PostgreSQL stores them as UTC internally; the offset is only used at I/O.
2. The client always sends moments as a full `timestamptz` (a UTC instant or a
   local time with explicit offset). It never sends a naive local time.
3. Slot/date classification is the client's job. Fields like `date` and
   `period` on `intake_reports` are client-supplied, not derived server-side
   from a timestamp — because "20:09 on Jun 30" maps to a different slot and
   possibly a different calendar date depending on timezone, which only the
   client knows.
4. The backend stores moments as received and does not interpret timezone.
   It never guesses a user's offset in order to store or classify a moment.
5. The backend needs a user timezone (`users.timezone`) only for future
   server-side reminders/push (firing at the user's local slot time). It is
   NOT needed to record intakes, compute "early/on-time/late" status, or any
   read-time projection — those compare stored `timestamptz` moments directly.

Consequence: no module bakes a timezone-dependent value into a row. Derived
states ("late", "missed", "manually entered") are computed at read time from
honest stored moments, never stored.