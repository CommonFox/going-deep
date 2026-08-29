# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the
codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the shared vocabulary for the models in `src/gold/`.
  `format`, `scoring basis` and friends are defined there precisely because they are used loosely
  elsewhere in the fantasy world.
- **`docs/adr/`** — read the ADRs that touch the area you are about to work in. Currently:
  `0001-roster-format-is-a-gold-concern.md`,
  `0002-late-positions-are-held-at-display-time.md`.

This is a single-context repo: one `CONTEXT.md`, one `docs/adr/`, both at the root. There is no
`CONTEXT-MAP.md` and no per-package context.

If a file does not exist, **proceed silently**. Don't flag its absence; don't suggest creating it
upfront. The `/domain-modeling` skill creates these lazily, when a term or a decision actually gets
resolved.

## Use the glossary's vocabulary

When your output names a domain concept — an issue title, a refactor proposal, a hypothesis, a test
name — use the term as `CONTEXT.md` defines it. Don't drift to the synonyms it explicitly lists
under _Avoid_.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language
the project doesn't use (reconsider), or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding
it:

> _Contradicts ADR-0001 (roster format is a gold concern), but worth reopening because…_
