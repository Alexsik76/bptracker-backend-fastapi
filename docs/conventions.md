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
5. The backend uses the user's timezone (`users.timezone`) for server-side rendering.
   There is exactly one exception to the client-normalization rule: when the backend
   renders a human-readable file server-side with no client in the loop, it must use
   `users.timezone` to format times. Specifically, the CSV export (`export`) reads
   `users.timezone` to adjust timestamps before writing them to the CSV. Beyond this
   exception, the timezone is NOT used to record intakes, compute statuses, or perform
   read-time database projections.

Consequence: no module bakes a timezone-dependent value into a row. Derived
states ("late", "missed", "manually entered") are computed at read time from
honest stored moments, never stored.