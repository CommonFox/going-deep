"""Render the live draft board once, from the terminal, and exit.

    python -m src.draft.live
    python -m src.draft.live --limit 50

The first thing here that can be pointed at a real draft. It resolves my seat from the draft
order, reads the picks made so far, subtracts them from the board the warehouse built days ago,
and prints what is left. Then it stops — refreshing as picks come in is the next ticket.

## This module is the edge, and the only part of `src/draft/` that is

`picks`, `candidates`, `seat` and `render` are all pure: frames and payloads in, frames and
strings out. Everything that touches the world is here, in one file, so that "does this tool
write anything" is a question answered by reading one module rather than four.

What it touches, exhaustively:

- Four HTTP **GET**s to Sleeper — the user, the league's drafts, the draft, the picks. There is no
  POST anywhere in this package, which is what "never makes a pick" means in practice: the tool
  has no code path that could submit one, rather than a flag saying it shouldn't.
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
from datetime import datetime, timedelta

import pandas as pd
import requests

from src.draft.picks import ingest_picks
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
# failure can be retried by pressing up-enter and a hang cannot be anything.
TIMEOUT_SECONDS = 10


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


def render_once(limit: int = DEFAULT_LIMIT) -> str:
    """Everything, once: fetch, resolve, subtract, rank, and give back the screen."""
    board = load_board()
    survival = load_survival()
    draft = find_draft(LEAGUE_ID, SEASON)
    league = resolve_seat(draft, user_id(USERNAME))
    result = ingest_picks(_get(f"/draft/{draft['draft_id']}/picks"), board, league)
    ranked = rank_by_cost_of_waiting(board, survival, result)

    # The bye week is joined back on by ID rather than carried through the ranking: what the
    # ranking returns is a fixed contract, and a display column is not the ranking's business.
    # Joining on `player_id` is the same identity the whole feature runs on.
    candidates = ranked["candidates"].merge(
        board[["player_id", "bye_week"]], on="player_id", how="left"
    )
    return render_board(
        candidates, result, league, limit,
        degraded=ranked["degraded"], covers_to=ranked["covers_to"],
    )


def main(limit: int = DEFAULT_LIMIT) -> None:
    built_at = warehouse_built_at()
    check_recency(built_at, datetime.now())
    print(render_once(limit))
    print(f"\nboard built {built_at:%Y-%m-%d %H:%M} — read-only, no pick is ever submitted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Show what is still on the board in the live Sleeper draft."
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help=f"how many candidates to show (default {DEFAULT_LIMIT})",
    )
    arguments = parser.parse_args()

    try:
        main(arguments.limit)
    except RuntimeError as error:
        # A stale or missing warehouse is a thing the drafter has to fix, not a bug — a traceback
        # in front of a pick clock buries the one sentence that says what to do.
        print(f"\n{error}\n")
        raise SystemExit(1)
    except requests.RequestException as error:
        print(f"\nSleeper did not answer: {error}\n")
        raise SystemExit(1)
