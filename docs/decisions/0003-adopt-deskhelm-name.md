# ADR 0003: Adopt the DeskHelm Name

- Status: Accepted
- Date: 2026-08-17

## Context

The project began under the working names `next_keyboard` and `agent-io`.
Its scope now includes a local Bridge, Agent adapters, voice interaction,
desktop clients, and future physical controls. The keyboard-specific codename no
longer describes the product, while multiple public projects already use
`agent-io`, including one with a closely overlapping Agent CLI normalization
scope.

The repository has been created as `cuihuir/deskhelm`, but the Python package,
CLI, default socket path, documentation, and tests still use the old name.

## Decision

Adopt `DeskHelm` as the product name and `deskhelm` as the repository, Python
distribution, CLI, and runtime-path prefix.

Use these canonical identifiers:

- Product: `DeskHelm`
- Distribution: `deskhelm`
- Python package: `deskhelm_bridge`
- CLI: `deskhelm`
- Codex hook CLI: `deskhelm-codex-hook`
- Runtime directory: `deskhelm`

Because the project is still at version `0.1.0.dev0`, make the runtime-path and
Python package rename before external releases. Preserve these temporary
compatibility entry points during the pre-release migration:

- `agent-io`
- `agent-io-codex-hook`
- `python -m agent_io_bridge`

The legacy Python package is only a module-execution shim. Direct imports from
undocumented `agent_io_bridge.*` modules are not a supported compatibility
surface.

Historical ADRs and research quotations keep the names that were accurate when
they were written. Current architecture, setup, and usage documentation uses
DeskHelm.

## Consequences

- New users see one consistent product, package, CLI, and runtime name.
- Existing documented CLI commands continue to work during the transition.
- The default Unix socket moves from an `agent-io` runtime directory to a
  `deskhelm` runtime directory.
- Existing processes must be restarted after upgrading; the Bridge will not
  listen on both socket paths.
- Compatibility aliases may be removed before the first stable release after a
  documented deprecation review.
