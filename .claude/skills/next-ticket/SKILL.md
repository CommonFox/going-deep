---
name: next-ticket
description: Pick the highest-priority ready ticket (or the one named), work it on a fresh branch, push, and open a PR that closes it.
argument-hint: "[issue number]"
disable-model-invocation: true
---

Take one ticket from open to pull request, unattended. `$ARGUMENTS` names the ticket; empty means
pick it. Tracker mechanics are in `docs/agents/issue-tracker.md` (`gh api`, never `gh issue view`):
read it before the first `gh` call.

## 1. Preconditions

`git status --porcelain` prints nothing and `gh auth status` succeeds. Otherwise stop and say what
is in the way; the branch in step 3 is cut from `origin/main` and a dirty tree would come with it.

## 2. Pick

When `$ARGUMENTS` names a ticket, skip the ranking but still run the eligibility checks, and stop
if one fails, naming which.

**Eligible**: an open issue (not a PR — they share one number space) carrying `ready-for-agent`,
with no assignee, that no open PR names as `Closes #N` or `Part of #N`. A `Blocked by:` line in its
body makes it ineligible while any named blocker is open; when the blocker is prose ("the three
table tickets in this epic"), resolve it against the parent epic's `## Children` list, and treat it
as blocked if that fails.

**Rank** the eligible set:

1. A ticket the roadmap issue (`roadmap` label) or its parent epic singles out ("do this first",
   "highest priority") beats everything.
2. The parent epic's wave in the roadmap's Epics table. The parent is the `Part of #N` line at the
   top of the ticket body; "1 (parallel)" ties with 1, and epics in the same wave rank in the
   table's row order; a ticket under no epic ranks after every wave.
3. The epic's "Ordering within the epic" paragraph, then its `## Children` list order.

**Stale check** on the top candidate before claiming it: search the codebase for what the ticket
asks for. If it already exists, close the ticket with a comment citing the files, and take the next
candidate.

Done when one ticket is chosen and you can say in a sentence why it outranks each eligible ticket
below it. That sentence goes in the report. If nothing is eligible, stop here and report the
highest-ranked open tickets with the reason each was ineligible; "lacks `ready-for-agent`" means the
user should run `/triage` on it.

## 3. Claim and branch

`gh issue edit <N> --add-assignee @me` before any other write, so a concurrent session skips it.
Then `git fetch origin && git switch -c <type>/<slug> origin/main`: `fix/` for `bug`, `docs/` for
`documentation`, `research/` for `research`, `feat/` otherwise; slug of two to four words from the
title.

## 4. Understand

The ticket body and comments, the parent epic's body (problem, out of scope), and the reading list
in `docs/agents/domain.md` for the area touched. Done when the ticket's Solution and Out of Scope
sections can be restated without re-reading them.

## 5. Tests first, then code

CLAUDE.md's testing rules apply unchanged, with one adaptation for running unattended: there is no
one to agree the test cases with, so enumerate them in writing before implementing. That list goes
into the PR body verbatim, so the ideation is reviewable even though the agreement happened alone.
Use the `tdd` skill for the pure modules. Commit as you go with conventional subjects; the first
push is `git push -u origin HEAD`.

Done when `pytest` is green and every new or changed warehouse table has been built and its
`console.table` row count observed, by running the module directly or `scripts/build_warehouse.sh`
when the change fans out.

If the ticket proves larger than one session (the roadmap flags some as likely to split), finish
and verify a coherent slice, ship it as a PR saying `Part of #N` rather than `Closes #N`, and
comment on the ticket with what remains.

## 6. Review

Invoke the `code-review` skill against `origin/main`. Fix what it finds; a finding you disagree
with goes in the PR body under `## Review notes` with the reason.

## 7. Pull request

`gh pr create --base main`, conventional subject ending `(#N)`, body in the house shape (PRs #105
and #138 set the tone): `## Summary` bullets saying what changed and why, `Closes #N`, `## Test
cases` (the step 5 list), `## Test plan` with boxes ticked only for what was actually run and the
numbers observed (pytest count, row counts), `## Out of scope` where the ticket had one. The body
is what the user reads to explain the change to someone else weeks later: write it to work cold.

## 8. Report

To the user, in order: the PR link; the pick sentence from step 2 and the eligible tickets it beat;
tickets passed over as claimed, blocked, already PR'd, or closed as stale, so a stale claim can be
cleared; then a plain-language explanation of the change at the depth the user's global CLAUDE.md
asks for: an overview, plus the two or three intricacies most likely to be asked about.
