# Gate — ecosystem role

**Status:** `CURRENT`  
**Observed main:** `a3f55c4b76a8df24171255748ff7a96c8568af0f`  
**Scope:** documentation-only ownership boundary.

## Definition

Gate is the external communication transport and delivery layer. It connects allowed channel/provider events to the correct product context and returns delivery state.

Gate does not own:

- the OMARK method;
- AQUB platform meaning or Cube lifecycle;
- Aku persona or instructions;
- portable Cube specifications;
- user goals, personal meaning or source data;
- cross-Cube relation semantics.

Provider, account, chat, installation and route identifiers are transport references. They do not name or own a Cube.

## Neighbor boundaries

- AQUB owns platform runtime, permissions and Cube instances.
- Cubes owns portable versioned instructions and stable Cube IDs.
- Data plus the source owner governs records and provenance.
- Miron owns the permissioned cross-Cube transfer contract.
- Gate may carry an already-authorized message or event but may not infer a new purpose, destination or product meaning.

## Current invariant

> **Gate transports; it does not define what the product means.**

This document changes no runtime, provider configuration, schema, secret, deployment or message behavior.
