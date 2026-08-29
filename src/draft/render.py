"""Turn what the seam returned into the screen the drafter reads.

Pure in the same way the two halves above it are: frames and dicts in, one string out. Nothing
here queries, fetches or prints, which is what makes the screen itself testable rather than
something that has to be eyeballed on a draft night to know whether it is right.

## What goes where, and why in that order

    seat, picks made, next pick        one line, because it is the thing checked against the app
    unmatched picks                    loud, and above everything else
    marked by hand                     short, and only when there is one
    my roster                          short, and answers "what do I still need"
    opening shape                      short, and answers "what am I still on track for"
    best available                     the long list, last

The order is the only real decision here, and it is decided by what a wrong screen costs. An
unmatched pick means a player may have been drafted and still be sitting on this board looking
available — the exact failure the feature exists to prevent — so it goes where it cannot be
scrolled past, above the board rather than under thirty rows of it. Everything else is ordered by
how often it is read, which puts the candidate list at the bottom where the eye lands.

The hand-marked block sits under the warning for the same reason the warning is where it is: those
players are gone on the drafter's own say-so, with nothing from Sleeper behind them, and a mistyped
mark hides a player who is actually available. It is above the roster because it is the part of the
screen most likely to be wrong.

The opening-shape block sits between the roster and the board deliberately. It is a claim about
the roster rather than about a player, so it belongs with the roster; and it must be *beside* the
ranking rather than inside it, because the ranking is the recommendation and this is the thing a
drafter overrules it with. A column of it on a candidate row would fold the two together and leave
neither auditable — which is the same reason the repo keeps a blended metric's inputs visible.

## Missing values print as gaps, never as values

A player with no bye week gets a dash. Pandas renders a missing nullable integer as `<NA>` and a
missing float as `nan`, and either one in a column of week numbers reads as a number that happens
to look odd rather than as an absence. The same rule applies to a missing team, and to a player
the survival model does not cover: no probability rather than a confident-looking zero. Nothing
here substitutes a plausible value for a missing one — a guessed bye week would quietly pile a
drafter's starters into the same empty week, which is worse than an obvious blank.

## The screen has to say which rule it ranked by

`rank_by_cost_of_waiting` falls back to points over replacement on its own, past the point the
survival model covers, and the rows look identical either way. So the heading names the rule and
the pick the probabilities are anchored to, and a fallback says on screen how far past the model
the draft has got. A drafter who cannot tell the two rankings apart is reading a number he has no
way to judge.

Both numbers stay on the row beside the value they were computed from, for the same reason the
repo keeps a blended metric's inputs individually visible: a ranking is auditable or it is obeyed.

## Vocabulary

*Survives* and *survival probability*, never *availability* — the glossary spends that word on how
much of a season a player can play, and two meanings under one word on a screen read against a
pick clock is how a supply number gets read as an injury number.
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


def _percent(value) -> str:
    """A survival probability, rounded to where it can actually be read.

    Whole percent, because the third decimal place of a tail probability is not a thing anyone
    should be deciding a pick on and a wider column costs a name its space.
    """
    if value is None or pd.isna(value):
        return _MISSING
    return f"{value * 100:.0f}%"


def _cost(value) -> str:
    """Cost of waiting, in the same units and to the same precision as the value beside it."""
    if value is None or pd.isna(value):
        return _MISSING
    return f"{value:.1f}"


def _header(picks: dict, league: dict, marked: list[dict]) -> str:
    """Seat, progress and next turn: the line that gets checked against the Sleeper app.

    The hand-marked count sits beside the picks made rather than inside it, because a mark is not
    a pick: the draft has got exactly as far as Sleeper says it has. But the board below has had
    both subtracted from it, and a board short of two players with nothing on screen to say so
    reads as a board that has lost them.
    """
    next_pick = picks["next_pick"]
    turn = f"next pick #{next_pick}" if next_pick is not None else "draft complete"
    by_hand = f"  ·  {len(marked)} by hand" if marked else ""
    return (
        f"seat {league['seat']} of {league['team_count']}"
        f"  ·  {picks['picks_made']} picks made{by_hand}"
        f"  ·  {turn}"
    )


def _marked(marked: list[dict]) -> list[str]:
    """Who is off the board on the drafter's own say-so, named.

    Named for the same reason an unmatched pick is: a mistyped mark hides a player who is actually
    available, which is the failure this feature exists to prevent wearing the other face, and the
    name is the only part of it a drafter can act on. It is also how he knows what to type after a
    dash to take one back.
    """
    if not marked:
        return []

    lines = ["", f"Marked taken by hand — {len(marked)}"]
    for player in marked:
        lines.append(
            f"  {_text(player.get('player_name'))} "
            f"({_text(player.get('position'))}, {_text(player.get('team'))})"
        )
    return lines


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


def _signed(value) -> str:
    """A band's score, with the sign written out — the direction is the whole of the reading."""
    if value is None or pd.isna(value):
        return _MISSING
    return f"{value:+.1f}"


def _either(positions: list[str]) -> str:
    """A list of positions as a drafter would say it: `RB, WR or TE`."""
    if len(positions) < 2:
        return "".join(positions)
    return f"{', '.join(positions[:-1])} or {positions[-1]}"


def _band(row) -> str:
    """A band named the way the guidance states it — a count of a position, never a plan."""
    return f"{int(row['count'])} {row['position']}"


def _guidance(guidance: dict | None) -> list[str]:
    """What the opening is still on track for, beside the ranking and never inside it.

    Bands rather than plans, because the leading compositions cannot be told apart from simulation
    noise — the plan table records the standard error behind every mean, and at most seats the top
    two openings sit inside it. Every open band is printed, not only
    the best one: being warned away from a shape that scored badly is half of what this is for,
    and a leader on its own does not say what the alternatives cost.

    A block that has nothing to say still says that. Guidance the research does not support is
    withdrawn rather than extrapolated, and an opening no plan covers is reported as such — either
    one silently vanishing would read as guidance that agreed with whatever the drafter just did.
    """
    if guidance is None:
        return []

    if guidance["withdrawn"]:
        return ["", "Opening shape — withdrawn", f"  {guidance['reason']}"]

    spent = [f"{guidance['picks_spent']} of {guidance['opening_rounds']} opening picks"]
    if guidance["taken"]:
        spent.append(
            ", ".join(f"{position} {count}" for position, count in guidance["taken"].items())
        )
    spent.append(f"{guidance['open_plans']} of {guidance['total_plans']} plans still open")
    lines = ["", "Opening shape — " + "  ·  ".join(spent)]

    best = guidance["best"]
    if best is None:
        lines.append(f"  {guidance['reason']}")
        return lines

    plans = int(best["plans"])
    lines.append(
        f"  best band still open   {_band(best)}   {_signed(best['points_vs_field'])} vs field"
        f"  ·  {_percent(best['win_rate'])} wins  ·  over {plans} plan"
        f"{'' if plans == 1 else 's'}"
    )
    if guidance["closes_best"]:
        lines.append(
            f"  {_either(guidance['closes_best'])} now closes it — only "
            f"{_either(guidance['keeps_best'])} keeps it open"
        )
    for position, group in guidance["bands"].groupby("position", sort=False):
        counts = "   ".join(
            f"{int(count)} {_signed(score):>6}"
            for count, score in zip(group["count"], group["points_vs_field"])
        )
        lines.append(f"  {position:<6}{counts}")
    return lines


def _ranking(picks: dict, degraded: bool, covers_to: int | None) -> list[str]:
    """How the list below was ordered, and — when it is not the good rule — why not.

    The pick named is `pick_after_next`, not the turn being decided: waiting means still being
    there at the turn *after* this one, and a probability anchored to the pick in hand would be a
    certainty dressed up as a forecast.
    """
    wait_to = picks["pick_after_next"]
    if not degraded and wait_to is not None:
        return [
            f", ranked by cost of waiting to pick #{wait_to}",
            "  SURV — survival probability, the chance he survives to that pick;"
            " COST — what waiting gives up",
        ]

    heading = ", ranked by points over replacement"
    if wait_to is None:
        return [heading, "  no turn after this one, so there is nothing to wait for"]
    if covers_to is None:
        return [heading, "  no survival data was supplied, so nothing can be priced for waiting"]
    return [
        heading,
        f"  pick #{wait_to} is past #{covers_to}, the last the survival model covers",
    ]


def _candidates(
    candidates: pd.DataFrame, picks: dict, limit: int, degraded: bool, covers_to: int | None,
    position: str | None = None,
) -> list[str]:
    """The board that is left, most expensive to pass on first, cut to what fits on a screen.

    `position` is what the board has been narrowed to, and is named in the heading rather than
    left to be inferred from the rows. A drafter who typed `qb` four picks ago and forgot is
    otherwise reading three quarterbacks as the whole of what is left.
    """
    shown = candidates.head(limit)
    rule, note = _ranking(picks, degraded, covers_to)
    scope = f" ({position} only)" if position else ""
    lines = [
        "",
        f"Best available{scope} — {len(shown)} of {len(candidates)}{rule}",
        note,
        f"  {'#':>3}  {'POS':<5}{'PLAYER':<{_NAME_WIDTH}}{'TM':<5}{'BYE':>3}{'PoR':>9}"
        f"{'SURV':>7}{'COST':>9}",
    ]
    if shown.empty:
        # Which position is empty, never just that something is. "Nobody left on the board" under
        # a quarterback filter describes a finished draft, and reads like one.
        empty = f"no {position} left on the board" if position else "nobody left on the board"
        lines.append(f"  {empty}")
        return lines

    for rank, row in enumerate(shown.itertuples(), start=1):
        lines.append(
            f"  {rank:>3}  {_text(row.position):<5}{_text(row.player_name):<{_NAME_WIDTH}}"
            f"{_text(row.team):<5}{_bye(row.bye_week):>3}"
            f"{row.points_over_replacement:>9.1f}"
            f"{_percent(row.p_survives):>7}{_cost(row.cost_of_waiting):>9}"
        )
    return lines


def render_board(
    candidates: pd.DataFrame,
    picks: dict,
    league: dict,
    limit: int = 30,
    degraded: bool = False,
    covers_to: int | None = None,
    marked: list[dict] | tuple = (),
    guidance: dict | None = None,
    position: str | None = None,
) -> str:
    """The whole screen as one string.

    `candidates` is what `rank_by_cost_of_waiting` returned with the board's `bye_week` joined on,
    `picks` is what `ingest_picks` returned, and `league` is the shape `resolve_seat` read out
    of the draft record. `limit` is how much of the board to show; the count above the table
    always names the total, so a cut list never reads as a short one.

    `degraded` and `covers_to` are the rest of what the ranking returned. They are separate
    arguments rather than a dict because they change the words above the table and nothing else,
    and a renderer that had to be handed a ranking result could not be pointed at anything else.

    `marked` is the players the drafter has taken off the board by hand. They are already gone
    from `candidates` and already counted in `picks` — this is what puts them on screen, so that
    a subtraction Sleeper never reported is visible rather than inferred from a shorter board.

    `guidance` is what `composition_guidance` returned, or None for a screen without it. It is
    printed beside the ranking and changes nothing about it: the candidate list is the same list,
    in the same order, whether it is passed or not.

    `position` is the position the board has been narrowed to, or None for the whole board. It
    names the scope of the candidate list and touches nothing above it — the roster, the warning
    and the opening shape are claims about the draft, not about what the drafter is looking at.
    """
    marked = list(marked)
    lines = [_header(picks, league, marked)]
    lines += _unmatched(picks["unmatched"])
    lines += _marked(marked)
    lines += _roster(picks["roster"], league)
    lines += _guidance(guidance)
    lines += _candidates(candidates, picks, limit, degraded, covers_to, position)
    return "\n".join(lines)
