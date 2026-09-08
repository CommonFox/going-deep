"""Keep the live ESPN draft board current from the terminal, until it is interrupted.

    python -m src.draft.live_espn
    python -m src.draft.live_espn --limit 50
    python -m src.draft.live_espn --once

The ESPN counterpart to `live.py` — same loop, same pure pipeline underneath it
(`espn_picks`/`candidates`/`cliff`/`composition`/`filter`/`hold`/`marks`/`refresh`/`render`), a
genuinely different edge. See `live.py`'s module docstring for the reasoning behind the loop's
shape (why the four effects are handed in, why the screen appends, why staleness is checked once);
none of that changes here. What's different is everything this module actually touches:

- HTTP **GET**s to ESPN's fantasy API — `mSettings` + `mTeam` + `mDraftDetail` once, to resolve my
  seat, then `mDraftDetail` alone on every tick for the picks. There is no POST anywhere in this
  package, same as `live.py` — the tool has no code path that could submit a pick.
- The same three warehouse reads through `src.query.q`, this time filtered to `league_key='espn'`.
- Whatever has been typed at **stdin**, resolved the same way `live.py`'s is.

## Why there is no `--draft-id`

Sleeper's league and its draft are two different objects reachable by two different IDs, which is
what makes a mock (a draft with no league behind it) reachable at all. ESPN's league record *is*
the draft record — `mDraftDetail` is one more view on the same league URL — so there is nothing a
second ID would even point at. If a standalone ESPN mock draft turns up that's reachable outside
this private league's API, this is the file to add it to; until then, verification is the rehearsal
agreed with the drafter: run this against the real league once the draft room opens but picks
haven't started, and confirm `espn_seat`/`espn_picks`'s assumptions about the live payload hold.

## Why the draft order can't be trusted before the room opens

`espn_seat.resolve_seat` refuses until `draftDetail.inProgress` or `draftDetail.drafted` is true —
see that module's docstring. `prepare` is where that refusal actually fires, which means running
this tool before the room opens is expected to fail loudly, on purpose, rather than resolve a seat
from a `pickOrder` that ESPN may still randomize.
"""

import argparse
import select
import sys
import time
from datetime import datetime

import requests

from src.draft.candidates import rank_candidates
from src.draft.cliff import position_cliffs
from src.draft.composition import composition_guidance
from src.draft.espn_picks import ingest_picks, picks_made
from src.draft.espn_seat import resolve_seat
from src.draft.filter import ALL, read_position
from src.draft.hold import held_positions, withhold
from src.draft.live import (
    MAX_WAREHOUSE_AGE,
    check_priced_for,
    check_recency,
    load_board,
    load_plans,
    load_priced_shape,
    load_survival,
    starting_position,
    warehouse_built_at,
)
from src.draft.marks import combine, read_mark
from src.draft.refresh import fingerprint, status_line
from src.draft.render import render_board
from src.draft.waiting import rank_by_cost_of_waiting
from src.silver.espn import ESPN_S2, LEAGUE_ID, SEASON, SWID

BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

# Which board to read — see `live.py`'s `LEAGUE_KEY` for why this is a key rather than an inferred
# value: `draft_board` holds a row per player *per league*, priced in that league's own scoring.
LEAGUE_KEY = "espn"

# `MAX_WAREHOUSE_AGE` is imported directly from `live.py` above rather than restated — same rule,
# same reasoning, one definition.

# Screen size and poll cadence — identical to `live.py`'s; nothing about either is Sleeper-specific.
DEFAULT_LIMIT = 15
TIMEOUT_SECONDS = 10
REFRESH_SECONDS = 3.0

# Fields ESPN's raw pick entries carry in place of Sleeper's `player_id`/`pick_no`, threaded into
# every shared module that reads a raw entry directly rather than the ingested result.
ID_FIELD = "playerId"
NUMBER_FIELD = "overallPickNumber"
ID_COLUMN = "espn_id"
PLATFORM_LABEL = "ESPN"


def _league_url() -> str:
    return f"{BASE_URL}/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"


def _cookies() -> dict:
    """`ESPN_S2`/`SWID` from `.env`, read through `src.silver.espn`'s own constants — see that
    module for why a private league needs them at all."""
    if not ESPN_S2 or not SWID:
        raise RuntimeError(
            "ESPN_S2 and SWID environment variables must be set to access a private league "
            "(see .env.example)."
        )
    return {"espn_s2": ESPN_S2, "SWID": SWID}


def _get(views: list[str]) -> dict:
    response = requests.get(
        _league_url(), params={"view": views}, cookies=_cookies(), timeout=TIMEOUT_SECONDS
    )
    response.raise_for_status()
    return response.json()


def _espn_pick_entry(_player: dict, platform_id: str) -> dict:
    """A hand-mark as ESPN's own empty-pick shape.

    See `marks.as_picks`'s `pick_entry` parameter — the player itself is unused here, unlike
    Sleeper's default builder, since ESPN's raw pick shape carries no name or position at all.
    """
    return {"playerId": int(platform_id), "teamId": None, "overallPickNumber": None}


def prepare() -> dict:
    """Everything that cannot change once the draft is under way, read once.

    See `live.py`'s `prepare` for what this mirrors. The one thing that can fail here that cannot
    fail there is `resolve_seat` refusing because the draft room has not opened yet — expected, and
    the reason `main` reports it as a refusal rather than a traceback.
    """
    raw = _get(["mSettings", "mTeam", "mDraftDetail"])
    league = resolve_seat(raw, SWID)
    check_priced_for(league, load_priced_shape(LEAGUE_KEY))
    return {
        "board": load_board(LEAGUE_KEY),
        "survival": load_survival(LEAGUE_KEY),
        "plans": load_plans(LEAGUE_KEY),
        "draft": raw,
        "league": league,
    }


def fetch_picks(context: dict) -> list[dict]:
    """The one thing that changes: every pick made so far, the whole draft, every poll."""
    return (_get(["mDraftDetail"]).get("draftDetail") or {}).get("picks") or []


def screen(
    context: dict, picks: list[dict], limit: int = DEFAULT_LIMIT, marked=(),
    position: str | None = None,
) -> str:
    """One picks payload against the frozen half, drawn — see `live.py`'s `screen`."""
    marked = list(marked)
    combined = combine(picks, marked, ID_COLUMN, _espn_pick_entry)
    result = ingest_picks(combined, context["board"], context["league"])
    ranked = rank_by_cost_of_waiting(context["board"], context["survival"], result, position)

    candidates = ranked["candidates"].merge(
        context["board"][["player_id", "bye_week"]], on="player_id", how="left"
    )
    guidance = composition_guidance(context["plans"], result, context["league"])
    cliffs = position_cliffs(rank_candidates(context["board"], result["taken"]))
    hold = held_positions(result, context["league"], position)

    return render_board(
        withhold(candidates, hold), result, context["league"], limit,
        degraded=ranked["degraded"], covers_to=ranked["covers_to"], marked=marked,
        guidance=guidance, position=position, cliffs=withhold(cliffs, hold), hold=hold,
        id_key=ID_COLUMN, platform_label=PLATFORM_LABEL,
    )


def render_once(limit: int = DEFAULT_LIMIT, position: str | None = None) -> str:
    """Everything, once: fetch, resolve, subtract, rank, and give back the screen."""
    context = prepare()
    return screen(
        context, fetch_picks(context), limit,
        position=starting_position(position, context["board"]),
    )


def _write(text: str) -> None:
    print(text, end="", flush=True)


def _typed(stream=None) -> list[str]:
    """Every whole line the drafter has typed since the last look — see `live.py`'s `_typed`."""
    stream = sys.stdin if stream is None else stream
    lines = []
    try:
        while select.select([stream], [], [], 0)[0]:
            line = stream.readline()
            if not line:
                break
            lines.append(line)
    except (OSError, ValueError):
        pass
    return lines


def _rule(made: int, marked: int = 0) -> str:
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
    """Draw the board, then keep it current until interrupted — see `live.py`'s `watch`."""
    poll = poll or (lambda: fetch_picks(context))

    marked = {}
    picks = []
    drawn = None
    checked_at = None
    made = 0
    status_width = 0

    try:
        while True:
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
                    continue
                outcome = read_position(line, context["board"], position)
                if outcome is None:
                    outcome = read_mark(line, context["board"], marked, ID_COLUMN, PLATFORM_LABEL)
                if outcome["action"] == "mark":
                    marked[outcome["player"]["player_id"]] = outcome["player"]
                elif outcome["action"] == "unmark":
                    marked.pop(outcome["player"]["player_id"], None)
                elif outcome["action"] in ("show", "clear"):
                    position = outcome["position"]
                write(f"\n{outcome['message']}\n")
                status_width = 0

            combined = combine(picks, marked.values(), ID_COLUMN, _espn_pick_entry)
            made = picks_made(combined)
            seen = (fingerprint(combined, ID_FIELD, NUMBER_FIELD), position)

            if (error is None or marked or position) and seen != drawn:
                lead = "\n" if drawn is None else f"\n{_rule(made, len(marked))}\n"
                write(
                    f"{lead}\n"
                    f"{screen(context, picks, limit, marked.values(), position)}\n"
                )
                drawn = seen
                status_width = 0

            line = status_line(made, checked_at, moment, error, PLATFORM_LABEL)
            write("\r" + line + " " * max(status_width - len(line), 0))
            status_width = len(line)

            sleep(interval)
    except KeyboardInterrupt:
        write(f"\n\nstopped at {made} picks made — no pick was ever submitted\n")


def main(limit: int = DEFAULT_LIMIT, once: bool = False, position: str | None = None) -> None:
    built_at = warehouse_built_at()
    check_recency(built_at, datetime.now())
    built = f"board built {built_at:%Y-%m-%d %H:%M} — read-only, no pick is ever submitted"

    if once:
        print(render_once(limit, position))
        print(f"\n{built}")
        return

    context = prepare()
    print(
        f"{built}\nrefreshing every {REFRESH_SECONDS:.0f}s — type a position to show only it "
        f'("qb", "{ALL}" for everyone), a name to mark him taken by hand, -name to undo, '
        "Ctrl-C to stop"
    )
    watch(context, limit, position=starting_position(position, context["board"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Keep what is still on the board in the live ESPN draft on screen."
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
    arguments = parser.parse_args()

    try:
        main(arguments.limit, arguments.once, arguments.position)
    except KeyboardInterrupt:
        print("\nstopped before the board was drawn\n")
        raise SystemExit(0)
    except RuntimeError as error:
        print(f"\n{error}\n")
        raise SystemExit(1)
    except ValueError as error:
        # espn_seat.resolve_seat's refusals (draft not open yet, wrong shape) — a drafter's problem
        # to fix, not a bug, same treatment `live.py` gives a stale-warehouse RuntimeError.
        print(f"\n{error}\n")
        raise SystemExit(1)
    except requests.RequestException as error:
        print(f"\nESPN did not answer: {error}\n")
        raise SystemExit(1)
