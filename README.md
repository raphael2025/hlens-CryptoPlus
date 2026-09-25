# hlens CryptoPlus

**Status: reset — design starting over.** The previous product, feature,
architecture and data-source documents, and everything built against them
(M1 collector core, adapters, migrations, tests), have been removed. Nothing
here reflects current direction; treat any prior commit history as archive
only.

See [`AGENTS.md`](AGENTS.md) for the process rules coding agents follow in
this repo (branching, no secrets, offline tests, etc.) — those still apply
once design work resumes. Role definitions for specialized agents live in
[`.claude/agents/`](.claude/agents/).

## Current direction

A research framework for BTC perpetual-futures trend trading: 4H regime gate,
1H trade state, 15m execution, with spot/futures flow, open interest,
liquidations and positioning used as evidence layers. The framework is a
candidate and has not been validated.

| Document | Contents |
|---|---|
| [`docs/01-FRAMEWORK-V9.md`](docs/01-FRAMEWORK-V9.md) | V9.0 candidate framework (raphael's prompts, consolidated) |
| [`docs/02-RESEARCH-PLAN.md`](docs/02-RESEARCH-PLAN.md) | Decisions, frozen v1 rules, baselines, task order R0–R9, stop rules |
| [`docs/reports/`](docs/reports/) | Evidence reviews and feasibility reports |
