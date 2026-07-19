# Gate Work instructions

Gate Work follows the canonical AlphaVia Work bootstrap policy in
[AlphaVia issue #67](https://github.com/markmalina/alphavia/issues/67).
That issue owns the cross-repository policy; this repository only records the
Gate-specific entrypoint.

On the first turn of every newly opened Gate Work:

1. Read `docs/work-bootstrap.md`.
2. Read the current GitHub issue, its latest implementation receipt, and its
   `next-work` pointer.
3. List scheduled tasks visible to the current chat before creating anything.
4. Ensure exactly one enabled automation titled `Gate Work heartbeat` exists
   for this Work. Never create a duplicate.
5. Use the fastest recurrence actually supported by the current Automations
   surface. The desired active cadence is ten minutes; the current callable API
   caps recurrence at hourly. Report the schedule that was really created.
6. Follow the lease and state protocol in `docs/work-bootstrap.md`.

A heartbeat never grants authority. Stop at `waiting_user` and before every
production deployment, provider call, credential or OAuth action, message,
publication, real-person-data operation, paid-resource action, or other
external effect unless the owner gives exact authorization for that action.
