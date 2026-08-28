"""Turn what the seam returned into the screen the drafter reads.

Pure in the same way the two halves above it are: frames and dicts in, one string out. Nothing
here queries, fetches or prints, which is what makes the screen itself testable rather than
something that has to be eyeballed on a draft night to know whether it is right.

## What goes where, and why in that order

    seat, picks made, next pick        one line, because it is the thing checked against the app
    unmatched picks                    loud, and above everything else
    my roster                          short, and answers "what do I still need"
    best available                     the long list, last

The order is the only real decision here, and it is decided by what a wrong screen costs. An
unmatched pick means a player may have been drafted and still be sitting on this board looking
available — the exact failure the feature exists to prevent — so it goes where it cannot be
scrolled past, above the board rather than under thirty rows of it. Everything else is ordered by
how often it is read, which puts the candidate list at the bottom where the eye lands.

## Missing values print as gaps, never as values

A player with no bye week gets a dash. Pandas renders a missing nullable integer as `<NA>` and a
missing float as `nan`, and either one in a column of week numbers reads as a number that happens
to look odd rather than as an absence. The same rule applies to a missing team. Nothing here
substitutes a plausible value for a missing one — a guessed bye week would quietly pile a
drafter's starters into the same empty week, which is worse than an obvious blank.
"""

import pandas as pd

# Wide enough for the longest name a board actually carries (`Amon-Ra St. Brown`, `Marvin Harrison
# Jr.`) without wrapping, which would break the column alignment the eye is using to scan.
_NAME_WIDTH = 24

# How a value that is not there is written. One character, in a column of numbers, so that it
# cannot be misread as one.
_MISSING = "-"


def _text(value) -> str:
    """One cell, with pandas' spellings of absence turned into a visible gap."""
    if value is None or pd.isna(value):
        return _MISSING
    return str(value)


def _bye(value) -> str:
    """A bye week as a plain week number — the board stores it as a nullable integer."""
    if value is None or pd.isna(value):
        return _MISSING
    return str(int(value))


def _header(picks: dict, league: dict) -> str:
    """Seat, progress and next turn: the line that gets checked against the Sleeper app."""
    next_pick = picks["next_pick"]
    turn = f"next pick #{next_pick}" if next_pick is not None else "draft complete"
    return (
        f"seat {league['seat']} of {league['team_count']}"
        f"  ·  {picks['picks_made']} picks made"
        f"  ·  {turn}"
    )


def _unmatched(unmatched: list[dict]) -> list[str]:
    """The warning, or nothing at all when there is nothing wrong.

    Named rather than counted: "3 picks could not be matched" tells a drafter that something is
    wrong without telling him which player to distrust, and the name is the only part he can act
    on in the ninety seconds he has.
    """
    if not unmatched:
        return []

    lines = [
        "",
        f"!! {len(unmatched)} UNMATCHED "
        f"{'PICK' if len(unmatched) == 1 else 'PICKS'} — this board may be showing a drafted "
        "player as available",
    ]
    for pick in unmatched:
        number = pick.get("pick_no")
        where = f"pick {number}" if number is not None else "hand-marked"
        lines.append(
            f"!!   {where:<14}{_text(pick.get('player_name'))} "
            f"({_text(pick.get('position'))}, Sleeper {_text(pick.get('sleeper_id'))})"
        )
    return lines


def _roster(roster: pd.DataFrame, league: dict) -> list[str]:
    """My lineup, one row per slot the league starts, filled against how many it starts."""
    lines = ["", f"My roster — roster {league['roster_id']}"]
    for row in roster.itertuples():
        players = ", ".join(row.players) if row.players else _MISSING
        # The bench has no target to fill, so it is counted rather than scored out of anything.
        count = f"{row.filled:>3}" if row.starts == 0 else f"{row.filled}/{row.starts}"
        lines.append(f"  {row.slot:<12}{count:<5}{players}")
    return lines


def _candidates(candidates: pd.DataFrame, limit: int) -> list[str]:
    """The board that is left, best first, cut to what fits on a screen."""
    shown = candidates.head(limit)
    lines = [
        "",
        f"Best available — {len(shown)} of {len(candidates)}",
        f"  {'#':>3}  {'POS':<5}{'PLAYER':<{_NAME_WIDTH}}{'TM':<5}{'BYE':>3}{'PoR':>9}",
    ]
    if shown.empty:
        lines.append("  nobody left on the board")
        return lines

    for rank, row in enumerate(shown.itertuples(), start=1):
        lines.append(
            f"  {rank:>3}  {_text(row.position):<5}{_text(row.player_name):<{_NAME_WIDTH}}"
            f"{_text(row.team):<5}{_bye(row.bye_week):>3}"
            f"{row.points_over_replacement:>9.1f}"
        )
    return lines


def render_board(
    candidates: pd.DataFrame, picks: dict, league: dict, limit: int = 30
) -> str:
    """The whole screen as one string.

    `candidates` is what `rank_candidates` returned with the board's `bye_week` joined on,
    `picks` is what `ingest_picks` returned, and `league` is the shape `resolve_seat` read out
    of the draft record. `limit` is how much of the board to show; the count above the table
    always names the total, so a cut list never reads as a short one.
    """
    lines = [_header(picks, league)]
    lines += _unmatched(picks["unmatched"])
    lines += _roster(picks["roster"], league)
    lines += _candidates(candidates, limit)
    return "\n".join(lines)
