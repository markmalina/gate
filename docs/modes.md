# Gate Reply Modes

Gate supports four reply modes.

## `manual`

Gate suggests a reply.

Mark manually copies and sends it.

Best for:

- v0;
- sensitive conversations;
- uncertain context;
- high-risk replies.

## `approval`

Gate prepares a reply and can send it only after confirmation.

Best for:

- common conversations;
- known recipients;
- semi-automated workflow;
- controlled acceleration.

## `auto-safe`

Gate sends replies automatically only for predefined safe scenarios.

Allowed examples:

- greeting;
- confirmation;
- contact collection;
- simple navigation;
- basic predefined answer.

Not allowed without explicit expansion:

- financial promises;
- relationship-sensitive messages;
- legal/medical advice;
- high-emotion conflict;
- messages from client accounts.

## `auto-full`

Gate replies autonomously within a project boundary.

Requirements:

- explicit permission;
- project-level policy;
- full audit log;
- rollback/disable control;
- clear owner accountability.

## v0 default

```text
GATE_DEFAULT_MODE=manual
```

v0 may also support `approval`, but `manual` remains the safest default.
