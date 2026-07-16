# Relationship CRM — standalone product architecture

## Decision

Relationship CRM is a standalone product and bounded context for relationships and
interactions between people. It serves personal communication, commercial/customer
work and Mayaki/Oko participation without making Gate, Alpha or Mayaki the owner of
relationship data.

The first implementation remains a separately owned module in the current repository,
process and PostgreSQL cluster. It has its own package, table namespace, contracts and
exports. A repository/deployment extraction is allowed only through these contracts and
must not change Telegram connector identities or public person references.

## Human product model

A person can interact with the owner or an organisation in several ways:

- private correspondence and personal relationships;
- lead, customer, partner, contractor or team relationships;
- a deal, request, agreement or other outcome-oriented process;
- participation in a Mayaki/Oko program;
- a call, meeting, payment, note, message or other interaction.

The system records facts and the human's explicit decisions. Message direction never
silently decides attention, relationship state, deal stage or program progress.

## Responsibility map

- **Gate Transport** owns channel receipt, connector identity, routing, idempotency,
  delivery attempts and short-lived transport audit.
- **Relationship CRM** owns people, channel identities, relationships, interaction
  history, notes, attention, work items, pipelines, stages and deals.
- **Alpha** consumes explicitly scoped relationship context and may propose summaries
  or actions; it never silently changes CRM truth.
- **Mayaki** owns programs, competencies, activities, evidence and learning progress.
  It references CRM people/relationships and exposes customer/program projections.
- **Billing** owns usage, costs, revenue, plans and entitlements.

## Spaces and privacy

Every CRM record belongs to one workspace and one relationship space.

Initial space kinds:

- `personal` — private relationships, thoughts, correspondence and follow-ups;
- `commercial` — leads, customers, partners, deals and pipelines;
- `program` — a Mayaki/Oko participant relationship and service lifecycle.

A person identity can be linked across spaces only by an explicit, auditable command.
The link does not copy notes, message content, attention, deals or program data. Access
to one space never grants access to another.

## Core aggregates

### Shared kernel

- `Person` — a human identity inside a workspace.
- `ChannelIdentity` — Telegram or another channel address; not itself a person.
- `RelationshipSpace` — privacy and product boundary.
- `Relationship` — the role and connection between a space and a person.
- `Interaction` — immutable message, call, meeting, payment or other event fact.

### Personal workflow

- `ConversationAttention` — explicit owner-controlled attention.
- `Note` / `Thought` — private knowledge independent of a reply task.
- `WorkItem` — follow-up, responsible party and next review time.

### Commercial workflow

- `Pipeline` and versioned `Stage`.
- `Deal` or case with value/currency optional and explicit owner.
- `StageTransition` as an immutable, actor-attributed command result.
- Roles such as lead, customer, partner, contractor or member.

### Program/customer workflow

- CRM keeps the customer relationship, enrollment/contact lifecycle and communication
  consent.
- Mayaki keeps the educational route, competence, activity, evidence and progress.
- The two modules exchange versioned references/events rather than reading each
  other's tables.

## Invariants

1. Workspace and relationship-space scope is required in every repository method.
2. Direction is a fact, not attention or stage.
3. AI output is a proposal with provenance and confidence, never a silent mutation.
4. Notes and thoughts are private by default.
5. Stage and attention changes require explicit actor, expected revision and idempotency key.
6. Interactions are immutable; corrections are new attributed facts.
7. Product history survives short-lived Gate transport-payload deletion.
8. No raw ORM model crosses the module boundary.
9. Every external effect and billable operation is idempotent.
10. Stable JSON export/import exists before repository extraction.

## Implementation sequence before OpenAI billing

1. Rename the existing Personal CRM domain into a compatibility-backed Relationship CRM
   kernel; preserve old imports temporarily.
2. Add space, relationship, interaction-kind, pipeline, stage and deal pure contracts.
3. Add module-owned additive PostgreSQL tables and migration tests.
4. Project existing conversations as `personal/untriaged` without guessed status.
5. Add repositories and workspace/space isolation tests.
6. Add owner read/write MCP actions for people, notes, attention and follow-ups.
7. Add commercial pipeline/deal fixtures and Mayaki integration contracts.
8. Add transactional outbox and rebuildable projections.
9. Add versioned export/import and extraction fitness tests.
10. Keep OpenAI, autonomous Oko and Telegram test sends disabled.

## Extraction rule

Relationship CRM is already a separate product and data owner. It becomes a separate
GitHub repository or deployment when one of these is true:

- it needs an independent release cadence or team;
- it needs independent scaling or availability;
- Mayaki and Alpha both consume its stable contracts in production;
- its schema can be restored/exported without Gate tables;
- extraction reduces measured operational coupling.

Until then, module isolation is enforced in code and CI rather than simulated with a
network boundary.
