# Adapters

Runtime-specific integrations translate hooks, plugins, notifications, and
structured output into DeskHelm state and interaction events.

Modern adapters declare their adapter/runtime versions, complete session
identity, and capabilities through `adapter_session_v1`. Vendor formats remain
inside the adapter boundary; the Bridge consumes only versioned DeskHelm
protocol objects.

Compatibility evidence belongs under `tests/fixtures/adapters/<adapter>/` with
source type, runtime version, retrieval date, and provenance recorded in a
manifest. Official examples and synthetic boundary fixtures must remain clearly
distinguished.
