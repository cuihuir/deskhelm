# Repository Guidelines

## Project Structure & Module Organization

`agent-io` is a monorepo connecting physical controls to coding agents.

- `hardware/`: electronics, PCB, mechanical, production, and test assets.
- `firmware/`: device firmware and bootloader integration.
- `bridge/`: local service connecting devices to agent runtimes.
- `adapters/`: runtime-specific integrations for Codex, Claude Code, Gemini CLI, and others.
- `protocol/`: agent-event and device-transport specifications.
- `configurator/`: device setup and customization application.
- `tests/`: cross-component and hardware-in-the-loop tests.
- `docs/`: research, architecture, product, hardware, software, and ADR documentation.
- `tools/`: development and manufacturing utilities.

Keep third-party reference files outside version control under `references/vendor/`.

## Build, Test, and Development Commands

The implementation stacks and unified build system have not been selected. Do not introduce placeholder commands or a task runner without an ADR. Document component commands in its `README.md` and provide a repository-level wrapper when practical.

Current repository checks:

- `git diff --check`: detects whitespace errors.
- `find docs -name '*.md' -type f`: lists documentation for review.
- `rg '<term>' .`: searches code and documentation quickly.

## Coding Style & Naming Conventions

Follow `.editorconfig`: UTF-8, LF line endings, final newline, two-space indentation, and no trailing whitespace. Makefiles use tabs. Prefer descriptive names over abbreviations.

- Directories and files: `kebab-case`, for example `agent-state-machine.md`.
- ADRs: `docs/decisions/NNNN-short-title.md`.
- Dated research: `docs/research/YYYY-MM-DD-topic.md`.
- Protocol fields and configuration keys: `snake_case` unless an adopted ecosystem requires otherwise.

## Testing Guidelines

Add tests beside their component or under `tests/` for cross-component behavior. Name tests after observable behavior. Protocol changes require compatibility fixtures; firmware changes should include hardware-in-the-loop notes when necessary. Record untested hardware assumptions explicitly.

## Commit & Pull Request Guidelines

The repository has no commit history yet. Use short, imperative commit subjects, optionally with a conventional prefix: `docs: define agent event model` or `firmware: add RGB status driver`.

Pull requests should explain the problem, approach, validation performed, and affected components. Link relevant issues or ADRs. Include screenshots, recordings, schematics, or board renders for visible hardware and UI changes. Do not commit secrets, generated binaries, vendor archives, or files with unclear commercial licensing.

## Architecture & Safety

Keep integrations agent-agnostic and local-first. Consequential actions such as approval or command execution must clearly identify the target agent and state. Record significant protocol, MCU, transport, framework, or licensing decisions in an ADR before implementation spreads across components.
