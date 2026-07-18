# AlphaVia contract compatibility

This directory pins the Gate-owned portion of the AlphaVia contract spine at
AlphaVia commit `fea64b1b582a08f7526c4d67de5f41aa02806403`.

The artifacts are compatibility fixtures only. They do not register routes,
enable delivery, authorize an external effect, or create a business master.

- `gate.inbound-intent@1.0.0` supersedes the legacy
  `gate.interaction.received.v1` vocabulary for new integrations.
- `gate.delivery-grant@1.0.0` is owned by Delivery Authority and remains
  `proposed-blocked`; Gate must not infer authority from a proposal.
- `gate.delivery-fact@1.0.0` supersedes the legacy
  `gate.delivery.outcome.v1` vocabulary for new integrations. The legacy event
  remains compatible and is not removed by this package.
- Cross-domain identifiers are opaque references. Provider identifiers and raw
  provider events remain inside Gate.
- An `unknown` delivery fact is terminal until explicit reconciliation and is
  never automatic retry authority.

Runtime activation requires a separate release gate and durable shared replay.
