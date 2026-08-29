# FoodCube G3 isolated staging database contract

Status: repository-only, default off. Parent: Center #131. Child: Gate #675.

## Identity and version policy

The only admitted identity is environment `staging`, PostgreSQL major 17,
database `gate_g3_staging`, schema `gate_g3`. Railway PITR compatibility is
represented by the major-tag policy `postgres:17`; no minor image pin is
asserted.

## Roles

`gate_g3_migrator` owns the schema and performs migrations.
`gate_g3_runtime` receives only schema usage, table DML and sequence
usage/select. It is not database or schema owner and receives no CREATE, ALTER,
DROP, role-management, database-management or superuser authority.

The resolver requires two explicit isolated URLs, rejects generic
`DATABASE_URL` fallback, unknown/missing/Production environment identities,
Food databases, shared superusers, cross-role credentials and shared passwords.
It returns only redacted identity fields.

## Network and restore evidence

The machine-readable registry records the allow/deny network matrix and the
required `ENV-20 → REC-21` restore/smoke receipt fields. Negative tests cover
runtime DDL denial, Food database denial, shared-superuser denial, Production
fallback denial and unknown environment fail-closed. A disposable PostgreSQL
test proves CREATE/ALTER/DROP and Food-database CONNECT are denied.

No SQL in this candidate is applied outside hosted disposable CI. No provider,
database, role, secret, deployment, Production, real-data or Telegram mutation
is authorized or performed.
