"""What changed since the last look, and when we last heard anything at all.

The two pure decisions underneath the refresh loop. `live.watch` supplies the clock, the GET and
the writing; everything it actually decides is here, where it can be checked without either.

## The fingerprint, and which way it is allowed to be wrong

Sleeper returns the whole draft on every poll, so "has anything happened" is a question about two
payloads rather than something the API answers. Redrawing regardless would put thirty rows of
board through the terminal every few seconds, which makes the list unreadable at exactly the
moment it is being read — a drafter scanning row nine does not want row nine to move.

So the loop draws only on a change, and the fingerprint decides what a change is. The two failure
directions are not symmetric:

- Too sensitive costs a redraw nobody needed.
- Too blunt leaves a player who has just been drafted sitting on the board looking available,
  which is the exact failure this whole feature exists to prevent.

Hence the pick count *and* the most recent pick, rather than either alone. A count alone misses a
commissioner's correction and misses the moment the API catches up with a hand-marked player —
same number of picks, different players in them. The count is of distinct players rather than of
list entries, because a payload that repeats a pick describes the same board and `ingest_picks`
would dedupe it anyway; a redraw on that would be a flicker with no cause behind it.

The most recent pick is read as the highest pick number rather than the last element, so a payload
that arrives in a different order than it did last time is not mistaken for a draft that moved.

## The status line, and the one fact that makes it worth printing

A draft room can sit for three minutes while somebody takes a phone call. A dropped connection
looks identical from this side: no new picks, forever. The distinguishing fact is not on the board
and cannot be — it is whether the *last poll* succeeded, which is why the line reports the time of
the last successful check rather than the current time. A clock that ticked regardless would read
as healthy right up until the pick that never appeared.

When a poll does fail, the failure goes on the same line rather than into a log nobody is reading,
carrying what went wrong and — the part that matters at a pick clock — that the tool is still
going. A message that reads like the end of the session sends a drafter to another window.
"""

from datetime import datetime

# Separator between the facts on the status line, matching the header the board is drawn under so
# the two read as the same screen rather than as a tool and its log.
_DOT = "  ·  "


def fingerprint(picks: list[dict]) -> tuple:
    """What the board is made of right now, cheaply enough to compute every few seconds.

    Two payloads describing the same draft give equal fingerprints; anything that would change
    the board gives a different one. Compared, never inspected — the parts are meaningful only
    to the docstring above.
    """
    numbered = [entry for entry in picks if entry.get("pick_no") is not None]
    latest = max(numbered, key=lambda entry: entry["pick_no"], default=None)
    return (
        len({entry.get("player_id") for entry in picks}),
        latest and latest.get("player_id"),
        latest and latest.get("pick_no"),
    )


def _age(seconds: float) -> str:
    """How long ago, in the units a person waiting for a pick is counting in."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{seconds // 60:.0f}m{seconds % 60:02.0f}s"


def status_line(
    made: int,
    checked_at: datetime | None,
    now: datetime,
    error: BaseException | None = None,
) -> str:
    """The one live line under the board: how far the draft has got, and whether we can still see.

    `checked_at` is when a poll last *succeeded*, which is the whole point of the line — see the
    module docstring. `now` is only used to say how stale that is once something has gone wrong.
    It is None only when nothing has succeeded yet, which is the tool being started while the
    connection is already down; the line says so rather than reporting a check that never happened.
    """
    when = "not checked yet" if checked_at is None else f"last checked {checked_at:%H:%M:%S}"
    line = f"{made} picks made{_DOT}{when}"
    if error is None:
        return line

    ago = "" if checked_at is None else f" ({_age((now - checked_at).total_seconds())} ago)"
    return f"{line}{ago}{_DOT}Sleeper did not answer: {error} — retrying"
