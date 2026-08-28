"""Keep the live draft board current from the terminal, until it is interrupted.

    python -m src.draft.live
    python -m src.draft.live --limit 50
    python -m src.draft.live --once

It resolves my seat from the draft order, reads the picks made so far, subtracts them from the
board the warehouse built days ago, and prints what is left — then does that again every few
seconds for the length of the draft, redrawing only when the picks have actually changed. What
counts as a change, and what the live line under the board says, are both in `refresh`.

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

`picks`, `candidates`, `seat` and `render` are all pure: frames and payloads in, frames and
strings out. Everything that touches the world is here, in one file, so that "does this tool
write anything" is a question answered by reading one module rather than four.

What it touches, exhaustively:

- HTTP **GET**s to Sleeper — the user, the league's drafts and the draft once each, then the
  picks on every tick. There is no POST anywhere in this package, which is what "never makes a
  pick" means in practice: the tool has no code path that could submit one, rather than a flag
  saying it shouldn't.
- Two reads of `data/warehouse.duckdb` through `src.query.q`, which opens read-only and closes
  before each returns. A draft-night process cannot corrupt what the pipeline built, and cannot
  hold the file lock against a rebuild either.

## Nothing is typed in

The league ID and season come from `src.silver.sleeper`, so the repo holds one league ID rather
than two that can disagree. Everything else — seat, roster, team count, rounds, starting slots —
is read out of the draft record. The one configured name is the Sleeper username below, which is
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
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from src.draft.picks import ingest_picks, picks_made
from src.draft.refresh import fingerprint, status_line
from src.draft.render import render_board
from src.draft.seat import resolve_seat
from src.draft.waiting import rank_by_cost_of_waiting
from src.query import WAREHOUSE_PATH, q
from src.silver.sleeper import LEAGUE_ID, SEASON

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

# About a screenful. The count above the table always names the total, so a cut list never reads
# as a short one, and `--limit` is there for looking deeper.
DEFAULT_LIMIT = 30

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
    """
    drafts = [
        draft for draft in _get(f"/league/{league_id}/drafts")
        if str(draft.get("season")) == str(season)
    ]
    if not drafts:
        raise RuntimeError(f"League {league_id} has no {season} draft.")

    # Newest first, for the case where a league has a redrafted or restarted draft on record.
    newest = max(drafts, key=lambda draft: draft.get("created") or 0)
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


def prepare() -> dict:
    """Everything that cannot change once the draft is under way, read once.

    The board and the survival frame were computed by a rebuild days ago; the draft record and the
    seat were settled when the order was drawn. Nothing on the night moves any of them, so the
    loop above carries this rather than re-reading it — see the module docstring on the file lock.
    """
    draft = find_draft(LEAGUE_ID, SEASON)
    return {
        "board": load_board(),
        "survival": load_survival(),
        "draft": draft,
        "league": resolve_seat(draft, user_id(USERNAME)),
    }


def fetch_picks(context: dict) -> list[dict]:
    """The one thing that changes: every pick made so far, the whole draft, every poll."""
    return _get(f"/draft/{context['draft']['draft_id']}/picks")


def screen(context: dict, picks: list[dict], limit: int = DEFAULT_LIMIT) -> str:
    """One picks payload against the frozen half, drawn — subtract, rank, render.

    Pure given the context, which is what lets the loop be driven over hand-written payloads
    without a network or a warehouse behind it.
    """
    result = ingest_picks(picks, context["board"], context["league"])
    ranked = rank_by_cost_of_waiting(context["board"], context["survival"], result)

    # The bye week is joined back on by ID rather than carried through the ranking: what the
    # ranking returns is a fixed contract, and a display column is not the ranking's business.
    # Joining on `player_id` is the same identity the whole feature runs on.
    candidates = ranked["candidates"].merge(
        context["board"][["player_id", "bye_week"]], on="player_id", how="left"
    )
    return render_board(
        candidates, result, context["league"], limit,
        degraded=ranked["degraded"], covers_to=ranked["covers_to"],
    )


def render_once(limit: int = DEFAULT_LIMIT) -> str:
    """Everything, once: fetch, resolve, subtract, rank, and give back the screen."""
    context = prepare()
    return screen(context, fetch_picks(context), limit)


def _write(text: str) -> None:
    """Straight at the terminal, unbuffered, adding no newline of its own.

    The loop decides where lines end, because the status line deliberately does not end: it is
    written over on the next tick rather than under.
    """
    print(text, end="", flush=True)


def _rule(made: int) -> str:
    """The line between one board and the next, naming the pick that brought the new one."""
    return f"── pick {made} " + "─" * 48


def watch(
    context: dict,
    limit: int = DEFAULT_LIMIT,
    poll=None,
    sleep=time.sleep,
    now=datetime.now,
    write=_write,
    interval: float = REFRESH_SECONDS,
) -> None:
    """Draw the board, then keep it current until interrupted.

    `context` is what `prepare` returned. The four effects default to the real ones and are
    parameters so the loop can be driven without a clock, a network or a terminal.

    A poll that fails is a line on the screen and nothing more: it is reported with the time of
    the last check that did work, and the next tick asks again. Nothing short of Ctrl-C ends this,
    because a session that ends at pick eleven is a session nobody restarts in time.
    """
    poll = poll or (lambda: fetch_picks(context))

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
                payload = poll()
            except requests.RequestException as failure:
                error = failure
            else:
                checked_at = moment
                made = picks_made(payload)
                mark = fingerprint(payload)
                if mark != drawn:
                    # End whatever status line is sitting on the terminal before drawing past it.
                    lead = "\n" if drawn is None else f"\n{_rule(made)}\n"
                    write(f"{lead}\n{screen(context, payload, limit)}\n")
                    drawn = mark
                    status_width = 0

            line = status_line(made, checked_at, moment, error)
            # Padded to the last line's width: a shorter line written over a longer one otherwise
            # leaves the tail of the old one behind, which reads as part of the new one.
            write("\r" + line + " " * max(status_width - len(line), 0))
            status_width = len(line)

            sleep(interval)
    except KeyboardInterrupt:
        write(f"\n\nstopped at {made} picks made — no pick was ever submitted\n")


def main(limit: int = DEFAULT_LIMIT, once: bool = False) -> None:
    built_at = warehouse_built_at()
    check_recency(built_at, datetime.now())
    built = f"board built {built_at:%Y-%m-%d %H:%M} — read-only, no pick is ever submitted"

    if once:
        print(render_once(limit))
        print(f"\n{built}")
        return

    context = prepare()
    print(f"{built}\nrefreshing every {REFRESH_SECONDS:.0f}s — Ctrl-C to stop")
    watch(context, limit)


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
    arguments = parser.parse_args()

    try:
        main(arguments.limit, arguments.once)
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
