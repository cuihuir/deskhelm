# Tests

Cross-component fixtures, protocol conformance tests, and hardware-in-the-loop test assets.

Protocol compatibility fixtures live under `tests/fixtures/protocol/`. Keep
fixtures as complete wire objects and round-trip them through the corresponding
versioned model.

Current fixture sets cover `InteractionEvent v1` and all `ControlCommand v1`
kinds, plus state snapshot and live-update subscription frames.
Runtime-specific adapter fixtures remain separate future work.
