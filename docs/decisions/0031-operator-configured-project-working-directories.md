# ADR 0031: Operator-Configured Project Working Directories

Date: 2026-08-26

Status: Accepted

## Context

The text Agent Gateway originally used one process-wide working directory for
every complete session. That is safe for the single-project Alpha but cannot
route sessions from different repositories. Treating `project_id` as a path or
accepting a path from an adapter would let an untrusted publisher select local
filesystem scope and would couple protocol identity to one machine layout.

## Decision

DeskHelm keeps `project_id` as an opaque identity and maps it to a local working
directory only through Bridge startup configuration. Repeated
`--agent-project PROJECT_ID=PATH` options build an immutable registry before the
Agent Gateway starts. The registry resolves and validates every directory,
rejects duplicate identities, and retains at most the configured bounded number
of projects, 64 by default and 1,024 at most.

Once any project mapping is supplied, the registry is strict: every submitted
session's exact `project_id` must have a configured entry. An unmapped project
fails before an Agent run or provider session record is created. There is no
fallback to the current directory, `--agent-workdir`, another project, or a path
derived from protocol content.

The existing `--agent-workdir` mode remains compatible when no project mappings
are configured. It continues to apply one validated directory to every session.
The two modes are mutually exclusive. Changing mappings requires a Bridge
restart; version 1 adds no runtime mutation, persistence, or new wire field.

## Consequences

- One Bridge can safely route concurrent sessions to explicitly approved local
  repositories while provider state remains keyed by complete session identity.
- Adapter publishers cannot inject or infer filesystem paths through
  `project_id` or session registration.
- Configuration errors and unmapped projects fail closed before provider work.
- The initial registry is process-local and immutable. Dynamic project
  enrollment or per-user policy requires a later decision.
