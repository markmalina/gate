# Railway auto-deploy negative-test marker

- Scope: `AlphaCenter/gate` main.
- Purpose: prove that a docs-only main update does not create a Railway deployment after provider-native automatic deployment was disabled.
- Railway project: `972b6ab7-988a-421c-abfa-425451ad714f` (`gate-backend`).
- Environment: `a148b138-44b3-4349-9d4b-292880503055` (`production`).
- Service: `099cbf8c-1d94-43fe-8714-90725bf93998` (`gate`).
- Pre-test Gate main: `9e73f842a8cc3bb3805119c3a82f8c5530329508`.
- Pre-test active Railway deployment: `63daee92-eb2d-48d7-bf11-ffe935d366ae`.
- Provider precondition: automatic GitHub deployments disabled.
- Runtime effect: none. This file changes documentation only.
- Forbidden effects: deployment, restart, migration, variables, domains, volumes, routes, source branch, data, or Production mutation.
- Recorded: 23.08.26 22:27:49 МСК.
