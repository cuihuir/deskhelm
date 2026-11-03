# ADR 0044: A Server-Built Transcript and a No-Build Web Page

Date: 2026-11-04

Status: Accepted. Amends ADR 0036.

## Context

The loopback page (ADR 0036) was one script of about 1,400 lines. It rebuilt
each transcript from raw interaction events in the browser and redrew the
whole transcript on every event. It showed agent replies as plain text,
labelled a tool by its fixed kind instead of its command or path, and read
session fields that the server mapped back into the v1 shape. No JavaScript
runs in the test suite, so none of that logic was tested. The core also sent
every session view again after each fact, so a streaming reply resent the
whole session list to every client. On 2026-11-04 the user asked for a page
built like a mature agent client that stays light and extensible.

## Decision

1. `deskhelm_client.transcript` folds one session's interaction events into
   bounded items (message, tool, approval, input, and turn) and reports each
   change as an `upsert`, `append`, or `remove` patch. It is plain Python
   with unit tests, so any surface can reuse it.
2. The web server keeps one transcript per session. A browser receives one
   snapshot with the sessions in protocol v2 terms and their transcripts,
   then ordered `sessions` and `patch` events. Every event carries a
   revision, and a page drops an event its snapshot already holds.
3. The page is plain ES modules under `deskhelm_web/assets/js/`, with no
   build step, framework, or dependency: a store that alone changes state,
   views that render keyed nodes, and a Markdown renderer that builds DOM
   nodes and never parses HTML. The CSP stays `script-src 'self'`.
4. The page names each command id, so a prompt it sent settles when the
   user message with that id arrives. It never retries, and a prompt whose
   outcome is unknown says so.
5. The Bridge sends a session in a console delta only when its view changed.
6. `tools/check-web-page.py` drives the page in Chrome against a real Bridge
   and scripted session hosts. It is an optional check that runs Playwright
   from a throwaway uv environment.

A review of the rebuilt page on the same day added decisions 7 to 11.

7. Surfaces decide approvals from the console, so the page also shows an
   approval its transcript does not hold, such as one a host reopened after
   a Bridge restart. When an approval ends, the core publishes
   `approval_resolved` (decided through DeskHelm, by the vendor, with its
   turn, or by expiry), and the fold records it, so every surface says the
   same thing.
8. The fold applies each event once by its sequence, so a replay after a
   reconnect changes nothing. A message of another role under the same id,
   such as a notice about a refused steer, is its own item. What is still
   running stops when its turn ends or its host leaves.
9. A message's complete fact carries its whole text, and the Bridge drops a
   completed message's deltas from the window. A tool output fact extends
   the output, or replaces it when marked `replace`.
10. The session view carries `catalog_rev`, which counts the catalogs a host
    has pushed; a surface reads the catalog again when it changes and never
    polls.
11. The page receives the shared wording from `deskhelm_client.texts` with
    each snapshot and keeps only its own codes.

## Consequences

- The page's HTTP API changed without a compatibility layer; the page ships
  with the server and is its only client.
- A streaming reply costs one small patch per delta, not a redrawn
  transcript or a resent session list.
- Replies render Markdown, tool rows show their command or path and fold
  their output, approvals sit inline with a countdown, and session details
  move to a side panel.
- The browser keeps no transcript logic or wording of its own that could
  drift from other surfaces, and the dashboard shows the same items and
  approval endings as the page.
- A surface that joins late, such as a restarted web process, gets whole
  replies from the window rather than whatever deltas still fit.
- Browser behaviour is checked only when someone runs the tool; the unit
  suite covers the transcript fold and the server API.
