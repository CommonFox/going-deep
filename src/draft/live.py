"""Keep the live draft board current from the terminal, until it is interrupted.

    python -m src.draft.live
    python -m src.draft.live --limit 50
    python -m src.draft.live --once
    python -m src.draft.live --draft-id 1399447972411912192   # a mock, to rehearse on

It resolves my seat from the draft order, reads the picks made so far, subtracts them from the
board the warehouse built days ago, and prints what is left — then does that again every few
seconds for the length of the draft, redrawing only when the picks have actually changed. What
counts as a change, and what the live line under the board says, are both in `refresh`.

## What the drafter can type at it

A name typed at the running tool marks that player taken by hand, and a name after a dash takes
the mark back. It is the fallback for the night Sleeper stops answering: marks are held for the
session and unioned into every board drawn from then on, so the draft stays followable with the
network down — which is the one failure the rest of this file cannot do anything about.

Lines are read without ever waiting for one, on the same tick as the poll, so typing costs the
board nothing and a half-typed name is simply still being typed. The terminal echoes it after the
status line, where the next repaint writes over the start of the line rather than the tail — and
if a failure message makes that line long enough to swallow the echo, the characters are still in
the terminal's own buffer and Enter still submits exactly what was typed.

## The screen appends, and the status line does not

A changed board is drawn *below* the last one rather than over a cleared screen, so the whole
draft stays in scrollback: what the board looked like at pick 14 is worth being able to scroll
back to at pick 40, and a tool that clears itself every three seconds has thrown that away. The
thing that would make an appending screen unreadable is the status line, which changes every tick
and says almost nothing new — so it is written with a carriage return and no newline of its own,
and each tick writes over the last one rather than under it. Boards are the only thing that
accumulates.

## Why the loop's four effects are handed in

`poll`, `sleep`, `now` and `write` are parameters with the real ones as defaults. A loop is the
one shape of code here that cannot be checked by reading it — whether it redraws too often,
whether it survives a dropped poll, whether it ends cleanly — and none of those questions should
have to be answered by pointing the tool at a real draft, which happens once a year.

## What is read once, and what is read on a tick

The board, the survival frame, the draft record and my seat are read once, before the loop: they
were computed by a rebuild days ago or drawn before the first pick, and nothing on the night
changes them. That is not only saved work — re-opening the warehouse every three seconds for two
hours would hold its file lock against any rebuild for the length of the draft. Only the picks are
fetched on a tick.

The staleness refusal is likewise once, at startup. A warehouse that ages past the limit *during*
a draft would otherwise shut the tool down at pick 60, which is far worse than a board a couple of
hours older than the rule prefers. That decision is taken before the draft, deliberately, and is
not revisited under a pick clock.

## This module is the edge, and the only part of `src/draft/` that is

`picks`, `candidates`, `seat`, `composition`, `cliff`, `filter` and `render` are all pure: frames
and payloads in, frames and strings out. Everything that touches the world is here, in one file,
so that "does this tool write anything" is a question answered by reading one module rather than
four.

What it touches, exhaustively:

- HTTP **GET**s to Sleeper — the user, the league's drafts and the draft once each, then the
  picks on every tick. There is no POST anywhere in this package, which is what "never makes a
  pick" means in practice: the tool has no code path that could submit one, rather than a flag
  saying it shouldn't.
- Three reads of `data/warehouse.duckdb` through `src.query.q`, which opens read-only and closes
  before each returns. A draft-night process cannot corrupt what the pipeline built, and cannot
  hold the file lock against a rebuild either.
- Whatever has been typed at **stdin**, read without blocking and resolved against the board by
  `filter` and then `marks`. Nothing is written anywhere as a result: a hand-mark and a narrowed
  board both live in memory until Ctrl-C.

## Nothing is configured by typing

The league ID and season come from `src.silver.sleeper`, so the repo holds one league ID rather
than two that can disagree. `--draft-id` is the single exception, and it is a draft to *watch*
rather than a setting: a Sleeper mock carries `league_id: null` and appears in no league's draft
list, so a rehearsal cannot be reached any other way. It is also the only way the draft on screen
can be a different shape from the board it is read against, so `prepare` checks the two agree —
see `seat.check_priced_for`. A player's name and a position are the only things ever typed at this
tool, and both are resolved against the board rather than configuring anything: one says a player
is gone, the other says which part of the board to look at, and neither changes a number.
Everything else — seat, roster, team count, rounds, starting slots — is read out of the draft
record. The one configured name is the Sleeper username below, which is
resolved to a user ID against the API and then to a seat against the draft order.

## Why it refuses to run against an old warehouse

Every number on screen was computed by a warehouse rebuild and is exactly as current as that
rebuild. A board built a week ago still prices a player who has since been ruled out for the
season, and prices him with the same confidence as everyone else — there is nothing on the screen
to suggest the file is old. So staleness is a refusal, not a warning, and the tool reports the
build time it found so the answer to "how old?" comes from the tool rather than from `ls -l`.

Console output goes through a plain `print` here rather than `src.console`, for the same reason
`src.summary` does: those helpers format build progress — a table name and a row count — and this
is a screen being drawn.
"""

import argparse
import select
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from src.draft.candidates import rank_candidates
from src.draft.cliff import position_cliffs
from src.draft.composition import composition_guidance
from src.draft.filter import ALL, read_position
from src.draft.marks import combine, read_mark
from src.draft.picks import ingest_picks, picks_made
from src.draft.refresh import fingerprint, status_line
from src.draft.render import render_board
from src.draft.seat import check_priced_for, resolve_seat
from src.draft.waiting import rank_by_cost_of_waiting
from src.query import WAREHOUSE_PATH, q
from src.silver.sleeper import LEAGUE_ID, SEASON, select_draft

BASE_URL = "https://api.sleeper.app/v1"

# Which board to read: `draft_board` holds a row per player *per league*, priced in that league's
# scoring and slots, so the key picks out the superflex league's prices rather than the ESPN one's.
LEAGUE_KEY = "sleeper"

# The one thing configured by hand. A username rather than a user ID because it is the string
# that can be checked by eye against the app; it is resolved to an ID before anything uses it.
USERNAME = "commonfox"

# How old a warehouse may be and still be drafted off. A day means the board was built with the
# last of the preseason news in it — the window in which starters are named and seasons end.
MAX_WAREHOUSE_AGE = timedelta(hours=24)

# About a screenful. A board that has to be scrolled is a board that gets skimmed, and the
# scrolling is what the first mock rehearsal turned up. The count above the table always names
# the total, so a cut list never reads as a short one, and `--limit` is there for looking deeper.
DEFAULT_LIMIT = 15

# A draft night has a pick clock. A request that hangs is worse than one that fails, because a
# failure is reported and retried on the next tick and a hang is a screen that has stopped.
TIMEOUT_SECONDS = 10

# How often to look. A Sleeper pick takes a manager tens of seconds at best, so a few seconds is
# already faster than the draft can move; the cost of going faster is a rate limit in the middle
# of a draft, and the cost of going slower is advice priced against a board that has moved on.
REFRESH_SECONDS = 3.0


def _get(path: str):
    response = requests.get(f"{BASE_URL}{path}", timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def check_recency(
    built_at: datetime, now: datetime, max_age: timedelta = MAX_WAREHOUSE_AGE
) -> None:
    """Refuse to draft off a warehouse older than `max_age`, saying when it was built.

    Pure, so the rule is checkable without a clock or a file. The build time is in the message
    because "the warehouse is stale" is not actionable and "the warehouse is from last Tuesday"
    is.
    """
    age = now - built_at
    if age <= max_age:
        return
    raise RuntimeError(
        f"The warehouse was last built {built_at:%Y-%m-%d %H:%M}, "
        f"{age.total_seconds() / 3600:.0f}h ago — older than the "
        f"{max_age.total_seconds() / 3600:.0f}h this tool will draft off. Every value it would "
        "show was computed by that build. Run scripts/build_warehouse.sh and start again."
    )


def warehouse_built_at() -> datetime:
    """When the warehouse was last written, which is when its numbers were computed."""
    if not WAREHOUSE_PATH.exists():
        raise RuntimeError(
            f"{WAREHOUSE_PATH} does not exist — run scripts/build_warehouse.sh before drafting."
        )
    return datetime.fromtimestamp(WAREHOUSE_PATH.stat().st_mtime)


def user_id(username: str) -> str:
    """The Sleeper ID behind a username, which is what the draft order is keyed on."""
    user = _get(f"/user/{username}")
    if not user or not user.get("user_id"):
        raise RuntimeError(f"Sleeper has no user called {username!r}.")
    return user["user_id"]


def find_draft(league_id: str, season: int) -> dict:
    """This season's draft for one league, fetched whole.

    Two calls, deliberately. The league's draft list carries the draft order but *not*
    `slot_to_roster_id`, so the record has to be fetched by ID to learn which roster a seat's
    picks land on — and without that, my own picks cannot be told from anyone else's.

    Which of the league's drafts is this season's is `src.silver.sleeper`'s rule, read from there
    rather than restated here. That module archives the picks once the draft is done, and a tool
    watching one draft while the archive kept another is a disagreement neither screen would show.
    """
    newest = select_draft(_get(f"/league/{league_id}/drafts"), season)
    return _get(f"/draft/{newest['draft_id']}")


def load_board(league_key: str = LEAGUE_KEY) -> pd.DataFrame:
    """One league's priced board, read-only, exactly as the last rebuild left it.

    Only the columns the draft night needs: enough to recognise a pick (`sleeper_id`), to rank
    what is left (`points_over_replacement`) and to show it (the rest).
    """
    board = q(
        """
        SELECT player_id, sleeper_id, player_name, position, team,
               points_over_replacement, bye_week
        FROM draft_board
        WHERE league_key = ?
        """,
        [league_key],
    )
    if board.empty:
        raise RuntimeError(
            f"draft_board holds no rows for league {league_key!r} — the warehouse has been built "
            "without it. Run scripts/build_warehouse.sh."
        )
    return board


def load_survival(league_key: str = LEAGUE_KEY) -> pd.DataFrame:
    """How likely each player is to still be there at each pick, as built days ago.

    `draft_availability` is keyed by seat as well as by pick, but the probability itself is not:
    it is a tail of the player's own ADP distribution and depends only on the overall pick number,
    so the same (player, pick) pair carries the same number under every seat. `DISTINCT` collapses
    that, and the frame then covers *every* overall pick rather than only this seat's — which the
    conditional needs, because its denominator is read at the pick about to be made and that pick
    usually belongs to somebody else.

    The rename happens here, at the one place that touches the warehouse, so nothing above this
    line has to know that the table spells it with the word the glossary has already spent.
    """
    survival = q(
        """
        SELECT DISTINCT player_id, overall_pick, p_available AS p_survives
        FROM draft_availability
        WHERE league_key = ?
        """,
        [league_key],
    )
    if survival.empty:
        # Not a refusal: the board is still worth reading, and the ranking says on screen that it
        # has fallen back to value alone. A draft is not worth losing to a missing table.
        print(
            f"\ndraft_availability holds no rows for league {league_key!r} — ranking by points "
            "over replacement alone. Run scripts/build_warehouse.sh to price waiting.\n"
        )
    return survival


def load_plans(league_key: str = LEAGUE_KEY) -> pd.DataFrame:
    """What each opening plan was worth from each seat, as the simulation scored it days ago.

    The one table here that prices a *roster shape* rather than a player. It is read whole for the
    league and filtered to the seat above, in `composition`, because the seat is not known until
    the draft record has been fetched — and because a pure function that does its own filtering is
    a pure function whose filtering can be tested.
    """
    plans = q(
        """
        SELECT draft_slot, plan, trials, points_vs_field, win_rate
        FROM draft_plans
        WHERE league_key = ?
        """,
        [league_key],
    )
    if plans.empty:
        # Not a refusal, for the same reason a missing survival table is not: the board is still
        # worth reading, and the screen says the opening guidance has nothing behind it.
        print(
            f"\ndraft_plans holds no rows for league {league_key!r} — the board will show no "
            "opening guidance. Run scripts/build_warehouse.sh to price roster shapes.\n"
        )
    return plans


def load_priced_shape(league_key: str = LEAGUE_KEY) -> dict:
    """The league shape this board's numbers were priced for, as the build recorded it.

    Read so `check_priced_for` has something to compare a hand-given draft against. The spelling
    is translated here, at the warehouse boundary, for the same reason `load_survival` renames its
    column there: nothing above this line should have to know that `league_settings` writes the
    superflex slot `superflex_slots` while a draft record writes it `slots_super_flex`.
    """
    settings = q(
        """
        SELECT team_count, qb_slots, rb_slots, wr_slots, te_slots, flex_slots,
               superflex_slots, k_slots, p_slots, dst_slots
        FROM league_settings
        WHERE league_key = ?
        """,
        [league_key],
    )
    if settings.empty:
        raise RuntimeError(
            f"league_settings holds no row for league {league_key!r}, so there is nothing to "
            "check a draft against. Run scripts/build_warehouse.sh."
        )
    row = settings.iloc[0]
    slots = {
        "QB": row["qb_slots"], "RB": row["rb_slots"], "WR": row["wr_slots"],
        "TE": row["te_slots"], "FLEX": row["flex_slots"],
        "SUPER_FLEX": row["superflex_slots"], "K": row["k_slots"],
        "P": row["p_slots"], "DST": row["dst_slots"],
    }
    return {
        "team_count": int(row["team_count"]),
        # Absent rather than zero, matching what `seat._slots` builds from the other side.
        "slots": {slot: int(n) for slot, n in slots.items() if int(n or 0) > 0},
    }


def prepare(draft_id: str | None = None) -> dict:
    """Everything that cannot change once the draft is under way, read once.

    The board and the survival frame were computed by a rebuild days ago; the draft record and the
    seat were settled when the order was drawn. Nothing on the night moves any of them, so the
    loop above carries this rather than re-reading it — see the module docstring on the file lock.

    `draft_id` names a draft directly instead of finding this season's through the league. That is
    the only way to reach a Sleeper mock, which carries `league_id: null` and so appears in no
    league's draft list. It is also the only way the draft on screen can disagree with the board it
    is read against, which is why `check_priced_for` runs below.
    """
    draft = _get(f"/draft/{draft_id}") if draft_id else find_draft(LEAGUE_ID, SEASON)
    league = resolve_seat(draft, user_id(USERNAME))
    check_priced_for(league, load_priced_shape())
    return {
        "board": load_board(),
        "survival": load_survival(),
        "plans": load_plans(),
        "draft": draft,
        "league": league,
    }


def fetch_picks(context: dict) -> list[dict]:
    """The one thing that changes: every pick made so far, the whole draft, every poll."""
    return _get(f"/draft/{context['draft']['draft_id']}/picks")


def screen(
    context: dict, picks: list[dict], limit: int = DEFAULT_LIMIT, marked=(),
    position: str | None = None,
) -> str:
    """One picks payload against the frozen half, drawn — subtract, rank, render.

    Pure given the context, which is what lets the loop be driven over hand-written payloads
    without a network or a warehouse behind it.

    `marked` is the players the drafter has taken off the board by hand. They are unioned into the
    payload here rather than handled separately, so that everything below this line sees one list
    of who is gone and `ingest_picks` does the deduplication it was written to do.

    `position` narrows the candidate list to one position. It is handed to the ranking rather than
    applied to what the ranking returned, because cost of waiting is measured against the whole
    board's remaining players — a narrowed screen must show the same prices as an unnarrowed one,
    not the prices a position would have if the rest of the board had been drafted.
    """
    marked = list(marked)
    result = ingest_picks(combine(picks, marked), context["board"], context["league"])
    ranked = rank_by_cost_of_waiting(context["board"], context["survival"], result, position)

    # The bye week is joined back on by ID rather than carried through the ranking: what the
    # ranking returns is a fixed contract, and a display column is not the ranking's business.
    # Joining on `player_id` is the same identity the whole feature runs on.
    candidates = ranked["candidates"].merge(
        context["board"][["player_id", "bye_week"]], on="player_id", how="left"
    )

    # Beside the ranking, never in it: the guidance is read from the plan table and says what a
    # position keeps open or closes off, and the order below it is the same order either way.
    guidance = composition_guidance(context["plans"], result, context["league"])

    # Depth is computed from the unnarrowed board on purpose, which is why it is ranked again here
    # rather than read off `ranked`. It is the reason to look away from the position being shown,
    # so a screen filtered to quarterbacks that reported only quarterback depth could never answer
    # the question it is read for. The second pass is a sort over a few hundred rows.
    cliffs = position_cliffs(rank_candidates(context["board"], result["taken"]))

    return render_board(
        candidates, result, context["league"], limit,
        degraded=ranked["degraded"], covers_to=ranked["covers_to"], marked=marked,
        guidance=guidance, position=position, cliffs=cliffs,
    )


def starting_position(typed: str | None, board: pd.DataFrame) -> str | None:
    """A `--position` flag resolved against the board, or the whole board and a reason why.

    Read through exactly the path a typed line takes, so a flag cannot recognise a position the
    running tool would not. A flag naming nothing is a note rather than an exit: the board is
    still worth reading, the drafter can narrow it by typing once the tool is up, and a session
    that refused to start over a typo is a session that starts late.
    """
    if typed is None:
        return None
    outcome = read_position(typed, board)
    if outcome is None or outcome["action"] != "show":
        carried = ", ".join(sorted(str(value) for value in board["position"].unique()))
        print(
            f"\nno position on this board is called {typed!r} — showing the whole board. "
            f"It carries {carried}.\n"
        )
        return None
    return outcome["position"]


def render_once(
    limit: int = DEFAULT_LIMIT, position: str | None = None, draft_id: str | None = None
) -> str:
    """Everything, once: fetch, resolve, subtract, rank, and give back the screen."""
    context = prepare(draft_id)
    return screen(
        context, fetch_picks(context), limit,
        position=starting_position(position, context["board"]),
    )


def _write(text: str) -> None:
    """Straight at the terminal, unbuffered, adding no newline of its own.

    The loop decides where lines end, because the status line deliberately does not end: it is
    written over on the next tick rather than under.
    """
    print(text, end="", flush=True)


def _typed(stream=None) -> list[str]:
    """Every whole line the drafter has typed since the last look, without waiting for one.

    The terminal hands a line over on the Enter key and not before, so a name half typed when a
    tick comes round is not seen and not lost — it is still being typed, and arrives on the tick
    after the drafter finishes it. Nothing here ever blocks: the poll cadence is the tool's, and a
    read that waited for a name would stop the board dead every time nobody was typing one.

    A stream that cannot be selected on — not a terminal, already closed, a pipe on a platform
    that will not have it — gives back nothing rather than raising. Typing is the fallback, and a
    fallback that could take the tool down with it would be worth less than not having it.
    """
    stream = sys.stdin if stream is None else stream
    lines = []
    try:
        while select.select([stream], [], [], 0)[0]:
            line = stream.readline()
            if not line:
                # End of input: stdin is closed or redirected from something exhausted. Nothing
                # more will ever be typed, and select would keep saying ready forever.
                break
            lines.append(line)
    except (OSError, ValueError):
        pass
    return lines


def _rule(made: int, marked: int = 0) -> str:
    """The line between one board and the next, naming the pick that brought the new one.

    A hand-mark brings a new board without moving the draft on, so the count is named beside the
    pick rather than folded into it — two boards in a row under the same pick number is exactly
    what marking a player looks like, and the rule has to say why.
    """
    label = f"pick {made}" if not marked else f"pick {made} + {marked} by hand"
    return f"── {label} " + "─" * 48


def watch(
    context: dict,
    limit: int = DEFAULT_LIMIT,
    poll=None,
    sleep=time.sleep,
    now=datetime.now,
    write=_write,
    keys=_typed,
    interval: float = REFRESH_SECONDS,
    position: str | None = None,
) -> None:
    """Draw the board, then keep it current until interrupted.

    `context` is what `prepare` returned. The five effects default to the real ones and are
    parameters so the loop can be driven without a clock, a network, a terminal or a keyboard.

    A poll that fails is a line on the screen and nothing more: it is reported with the time of
    the last check that did work, and the next tick asks again. Nothing short of Ctrl-C ends this,
    because a session that ends at pick eleven is a session nobody restarts in time.

    A line typed at it is read on the next tick as a position first and a name second — see
    `filter` for why that way round, and `marks` for what a name resolves against and what it
    refuses. Both are held here, for the session, and both take effect on the tick they are read:
    the last payload a poll returned is kept, so a mark or a filter still redraws the board on a
    tick Sleeper did not answer, which is the whole reason the drafter is typing in the first
    place.

    `position` is what the board starts narrowed to, for a session begun with `--position`. It is
    a starting state and not a fixed one — the drafter can widen or narrow it at any point without
    restarting, which is the only reason the filter is worth having.
    """
    poll = poll or (lambda: fetch_picks(context))

    marked = {}         # board player_id -> the board row, in the order they were marked by hand
    picks = []          # the last payload a poll actually returned, kept across a failed one
    drawn = None        # the fingerprint of the board currently on screen
    checked_at = None   # when a poll last succeeded, which is what the status line reports
    made = 0
    status_width = 0    # how much of the last status line there is to write over

    try:
        while True:
            # Read once per tick, so the time on screen is the time of the check it describes.
            moment = now()
            error = None
            try:
                picks = poll()
            except requests.RequestException as failure:
                error = failure
            else:
                checked_at = moment

            for line in keys():
                if not line.strip():
                    # Enter on an empty line is how a half-typed name gets cleared.
                    continue
                # A position first, a name second. `read_position` hands back None for a line
                # that is not one of the board's positions, which is every name a drafter types.
                outcome = read_position(line, context["board"], position)
                if outcome is None:
                    outcome = read_mark(line, context["board"], marked)
                if outcome["action"] == "mark":
                    marked[outcome["player"]["player_id"]] = outcome["player"]
                elif outcome["action"] == "unmark":
                    marked.pop(outcome["player"]["player_id"], None)
                elif outcome["action"] in ("show", "clear"):
                    position = outcome["position"]
                # Every outcome, refusals included: a mark that said nothing would be read as one
                # that worked, and a filter that said nothing would be a narrowed board read as a
                # whole one. Ends the status line rather than being written over it.
                write(f"\n{outcome['message']}\n")
                status_width = 0

            combined = combine(picks, marked.values())
            made = picks_made(combined)
            # What is on screen is the picks *and* what the board is narrowed to: typing `qb`
            # changes the screen without changing a single pick, and a fingerprint that ignored it
            # would leave the drafter's own request undrawn until somebody else made a selection.
            seen = (fingerprint(combined), position)

            # A failed poll on its own draws nothing — the board has not changed and the status
            # line says the connection has. A mark or a filter is the drafter changing what he
            # asked for, and gets drawn whether Sleeper answered or not.
            if (error is None or marked or position) and seen != drawn:
                # End whatever status line is sitting on the terminal before drawing past it.
                lead = "\n" if drawn is None else f"\n{_rule(made, len(marked))}\n"
                write(
                    f"{lead}\n"
                    f"{screen(context, picks, limit, marked.values(), position)}\n"
                )
                drawn = seen
                status_width = 0

            line = status_line(made, checked_at, moment, error)
            # Padded to the last line's width: a shorter line written over a longer one otherwise
            # leaves the tail of the old one behind, which reads as part of the new one.
            write("\r" + line + " " * max(status_width - len(line), 0))
            status_width = len(line)

            sleep(interval)
    except KeyboardInterrupt:
        write(f"\n\nstopped at {made} picks made — no pick was ever submitted\n")


def main(
    limit: int = DEFAULT_LIMIT,
    once: bool = False,
    position: str | None = None,
    draft_id: str | None = None,
) -> None:
    built_at = warehouse_built_at()
    check_recency(built_at, datetime.now())
    built = f"board built {built_at:%Y-%m-%d %H:%M} — read-only, no pick is ever submitted"

    if once:
        print(render_once(limit, position, draft_id))
        print(f"\n{built}")
        return

    context = prepare(draft_id)
    print(
        f"{built}\nrefreshing every {REFRESH_SECONDS:.0f}s — type a position to show only it "
        f'("qb", "{ALL}" for everyone), a name to mark him taken by hand, -name to undo, '
        "Ctrl-C to stop"
    )
    watch(context, limit, position=starting_position(position, context["board"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Keep what is still on the board in the live Sleeper draft on screen."
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help=f"how many candidates to show (default {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="draw the board once and exit, instead of refreshing until interrupted",
    )
    parser.add_argument(
        "--position", default=None,
        help="show only one position (QB, RB, ...); can be changed by typing at the running tool",
    )
    parser.add_argument(
        "--draft-id",
        help="watch this draft instead of the league's — the only way to reach a Sleeper mock, "
             "which belongs to no league. Refused unless its settings match the board's league.",
    )
    arguments = parser.parse_args()

    try:
        main(arguments.limit, arguments.once, arguments.position, arguments.draft_id)
    except KeyboardInterrupt:
        # Ctrl-C before the loop starts — during the warehouse read or the first fetch. The loop
        # has its own, which stops cleanly rather than unwinding.
        print("\nstopped before the board was drawn\n")
        raise SystemExit(0)
    except RuntimeError as error:
        # A stale or missing warehouse is a thing the drafter has to fix, not a bug — a traceback
        # in front of a pick clock buries the one sentence that says what to do.
        print(f"\n{error}\n")
        raise SystemExit(1)
    except requests.RequestException as error:
        print(f"\nSleeper did not answer: {error}\n")
        raise SystemExit(1)
