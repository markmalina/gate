# Gate Project Status

Last verified: 2026-08-30

Gate is the transport/runtime authority. The previous July status mixed historical
deployment observations with current repository state and is retained in Git
history; it must not be treated as fresh runtime evidence.

## Current repository evidence

- G3 repository contract is merged on `main` by PR #676; terminal receipt is Gate
  #675 comment `5464704940`.
- Its accepted candidate was `7b5d727ccb0faa0a7f9d35b137e4f125117aee4f`;
  merge/main at that receipt was `a215d1ea80c1dd948c2642d3f755f92a16af6d4d`.
- G3 provider-free checks do not prove live runtime health, staging readiness or
  product readiness.
- Work #677 prepares a default-off, product-neutral GATE-31 contract and disposable
  PostgreSQL rehearsal. Its maximum terminal state is
  `GATE-31_REPOSITORY_PREFLIGHT_COMPLETE`, not G4 acceptance.

## Operational boundary

This repository status does not authorize or attest changes to provider resources,
credentials, secrets, paid resources, live databases, deployments, Production,
real data or external messaging. Those effects require fresh exact evidence and a
separate owner grant under Center governance.
