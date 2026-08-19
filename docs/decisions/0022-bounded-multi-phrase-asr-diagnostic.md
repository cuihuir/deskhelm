# ADR 0022: Use a Bounded Multi-Phrase ASR Diagnostic

- Status: Accepted
- Date: 2026-08-19

## Context

One public phrase is not enough to choose an ASR for coding commands. DeskHelm's
versioned corpus already contains negation, paths, symbols, numbers, agent
names, and shell commands, but the single-phrase diagnostic could not exercise
several of them in one controlled session.

## Decision

- Allow `--utterance-id` to be repeated up to eight times. When repeated, the
  diagnostic runs a bounded batch and opens a fresh PipeWire capture session for
  every phrase.
- Reuse one lazily loaded ASR provider across the batch to expose warm-path
  behavior, while keeping each capture and result independently bounded.
- Keep the existing immediate-capture behavior. Optional lead-in applies before
  the first phrase; an optional bounded inter-phrase pause applies before later
  phrases. No hidden countdown or audio cue is introduced.
- Emit one parent status plus per-phrase signal, advisory VAD, accuracy, length,
  timing, and fixed error metadata. Never emit or save recognized text or PCM.
- Mark every per-phrase result as requiring post-run confirmation. The batch
  parent explicitly says confirmation scope is “each utterance”.
- Compare providers using the same ordered phrase IDs, but do not call separate
  microphone captures paired audio. Results remain repeated human utterances,
  not a laboratory matched-recording comparison.

## Consequences

- Chinese and mixed coding-command coverage can be collected with one bounded
  user-coordinated workflow.
- Warm model behavior is visible without pretending the captures are identical
  across providers.
- A failed phrase does not prevent later phrases from running; the batch exits
  nonzero if any phrase failed and retains only fixed metadata.
- User confirmation remains necessary for interpreting every result, including
  a successful provider response.

## Implementation Status

The repeatable diagnostic arguments, eight-phrase bound, fresh-capture loop,
privacy-safe batch summary, and fake-boundary tests are implemented. Multi-phrase
live measurements are still pending.
