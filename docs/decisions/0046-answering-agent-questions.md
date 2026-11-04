# ADR 0046: Answering Agent Questions on the Page

Date: 2026-11-04

Status: Accepted. Amends ADR 0043 and ADR 0045.

## Context

On 2026-11-04 Codex asked the user a question with three suggested answers in
a session run by `deskhelm codex`. The Codex TUI offered a picker. The page
showed the question as a plain list in a reply, with no way to answer, and
after the user picked an answer in the TUI, the page showed the TUI's raw
reply: a `<send_user_message_question_reply>` message holding JSON.

Codex CLI 0.166.0 asks in two ways, as the user's rollout of that session and
the strings in the Codex binary show:

- `item/tool/requestUserInput` is a server request that waits for its
  answer. DeskHelm showed a fixed notice, and a prompt answered it only when
  it held a single question that was not secret.
- The `request_user_input_async` tool does not wait. It returns
  `{"accepted":true}` at once, and Codex goes on working. Its call reaches
  app-server clients as an `agentMessage` whose `questions` field holds each
  question's title and suggested answers. The TUI answers later with a user
  message whose JSON names each question as
  `["request_user_input_async", call id, index]`.

Protocol v2 had no place for a question's content or its answers, and the
`answerable` input capability had no command behind it, so ADR 0043 left
multi-question and secret input in the vendor's own interface.

## Decision

1. An input request may carry its `questions` as content: at most 8, each
   with an id, a title, an optional header, up to 16 options with a label and
   an optional description, whether a free-text answer is allowed, and
   whether it is secret. Like a request's detail (ADR 0045), questions reach
   only the operator's interaction stream; console tickets stay content-free.
2. A request that is not `blocking` lets the vendor go on without its answer.
   It outlives its turn, and the session waits for input only once no turn
   runs.
3. An operator-only `answer` command names an input request's approval id
   and answers by question id. The Bridge checks the question ids and the
   session's `user_input` capability, sends an `answer` effect, and publishes
   `input_resolved` with the answers. An answer given in the vendor's own
   interface reaches the operator as `answers` in `request_resolved`.
4. The Codex driver declares `user_input: answerable`. A `requestUserInput`
   request is answered on its own JSON-RPC channel unless one of its
   questions is secret, and a prompt still answers a single open question.
   An async question becomes a non-blocking request in place of its
   `agentMessage`, stays answerable for an hour, and is answered with the
   same user message the TUI sends, steering the active turn or starting
   one. A TUI answer resolves the question with what was chosen, the echo of
   a DeskHelm answer is dropped, and an answer to a question DeskHelm never
   saw reads as what the user said. The reply's wrapper is never shown.
5. The page shows each question with its options, the first one chosen as in
   Codex's own picker, a free-text field where one is allowed, and one
   submit. A half-typed answer lives outside the DOM, so the agent's other
   activity cannot take it away.

## Consequences

- A Codex question is answered from the page, or shows how it was answered
  in the TUI, and the page and the dashboard say the same.
- Secret questions stay in the TUI. Claude Code's `AskUserQuestion` is still
  declined at once, because its session takes no input, and Grok asks none.
  Voice still answers only approvals (D3).
- Checked live with Codex CLI 0.166.0 on 2026-11-04: an answer from the page
  reached Codex, and the TUI took the question off its queue and showed the
  answer as its own (`↪ Re: …`); an answer chosen in the TUI (`shift+←`,
  then Enter) showed on the page as answered there, with its answer.
- An async question answered while DeskHelm's host was not running stays
  open on the page until it expires.
- A 0.2.0 Bridge refuses a host that sends questions, so upgrading still
  means restarting every DeskHelm process (ADR 0045).
