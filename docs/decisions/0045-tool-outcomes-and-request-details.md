# ADR 0045: Tool Outcomes and Request Details

Date: 2026-11-04

Status: Accepted. Amends ADR 0043 and ADR 0044. ADR 0046 lets a request also
carry its questions, and a resolution its answers.

## Context

On 2026-11-04 the rebuilt page (ADR 0044) was checked live with Claude Code
2.1.312 and Grok 1.0.60. An approval card named only a tool, such as
"请求使用工具 Bash", so the page asked the operator to approve a command it
did not show; only a tool row above it, if any, said what would run. A tool
that the operator refused read "exit code 1".

Both came from protocol v2 having no place for either fact. A `request_opened`
fact carried no content, and a tool's `complete` fact could say how it ended
only through `exit_code`. Drivers therefore invented exit codes for vendors
that have none, and the transcript fold treated error output as failure, so a
driver sent an empty error output to mark one. Neither Claude, ACP, nor Codex
file changes report exit codes, and Codex reports a refused command as
`declined`, which no surface could show.

Earlier changes avoided such protocol changes to stay compatible with
DeskHelm 0.1.0. DeskHelm has no users yet, and on 2026-11-04 the user asked
for breaking changes made the correct way instead.

## Decision

1. A tool's `complete` fact states its `status`: `completed`, `failed`, or
   `declined`, which means a person or a policy refused the tool before it
   ran. Only a `complete` fact has a status, and it is required there.
   `exit_code` stays optional and only ever comes from the vendor. Error
   output does not make a tool fail.
2. Drivers map their vendor's own outcome: Codex reports `declined` and
   `failed` items, and a nonzero exit code fails a command. Claude and ACP
   report an error result or a failed tool; the driver calls it `declined`
   when it refused that tool's permission request itself, for the operator,
   at expiry, or because too many requests were open, and `failed`
   otherwise.
3. A request may carry a `detail`: what it would do, such as a command line,
   file paths, or a URL, in the vendor's own terms and at most 8 KiB. It is
   content: `request_opened` carries it in `content.detail`, and an entry of
   `session_open.open_requests` may carry it too. The Bridge puts it only in
   the operator's `approval_request` and `user_input_required` events. Console
   tickets stay content-free, so the voice and device tiers never receive a
   detail.
4. The Bridge announces requests rebuilt from `open_requests` as interaction
   events, so a page that meets an approval after a Bridge restart shows it
   in the transcript with its detail.
5. The Bridge, hosts, and surfaces of one release run together. A 0.1.0
   process is not compatible with 0.2.0, and upgrading means restarting every
   DeskHelm process.

## Consequences

- An approval card shows the command or paths it would allow, also after a
  Bridge restart, and the voice tier still hears only fixed notices.
- A refused tool reads "已拒绝" and a failed one "失败" or its exit code, the
  same on the page and the dashboard, which share the words from
  `deskhelm_client.texts`.
- Claude and ACP drivers keep a bounded record of the tools they refused.
- After a host reconnects to the same Bridge, the transcript holds the old
  card of a rebuilt approval, ended, and a new live card under its new id.
- A 0.1.0 host on a 0.2.0 Bridge is disconnected at its first tool
  completion, and a 0.2.0 host on a 0.1.0 Bridge at its first approval
  detail. No component checks the release of its peer.
