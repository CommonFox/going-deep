"""Cases 11-17 of the refresh ticket: the loop that keeps the board current for a whole draft.

`watch` is the one thing in this package that has a clock and a network in it, so the four things
it touches the world with — the poll, the sleep, the time and the writing — are handed in. The
real ones are supplied by `main`; these cases supply fakes and then assert what a drafter would
have seen on screen, which is the only thing about a loop that actually matters.

The board itself is rendered through the real pure pipeline against a hand-written three-player
board, rather than being stubbed out. A loop test that stubbed the rendering could not tell the
difference between redrawing and redrawing the same thing.

## What each case is protecting

A draft night has exactly two failure modes here and they pull in opposite directions. Redrawing
on every tick makes thirty rows scroll past while they are being read; redrawing on too few makes
the board quietly wrong, which is the failure the whole feature exists to prevent. And a session
that ends on the first dropped packet is a session that ends, because nobody restarts a tool at
pick eleven — so a failed poll has to be a line on screen and nothing more.
"""

from datetime import datetime, timedelta

import pandas as pd
import requests

from src.draft.live import watch

NOW = datetime(2026, 9, 3, 18, 42, 7)

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1, "K": 1, "DST": 1}

LEAGUE = {"seat": 1, "roster_id": 6, "team_count": 14, "rounds": 15, "slots": SLOTS}

BOARD = pd.DataFrame(
    [
        {
            "player_id": "00-0000001", "sleeper_id": "4034", "player_name": "Alpha Back",
            "position": "RB", "team": "SF", "points_over_replacement": 150.0, "bye_week": 9,
        },
        {
            "player_id": "00-0000002", "sleeper_id": "6786", "player_name": "Bravo Wideout",
            "position": "WR", "team": "KC", "points_over_replacement": 120.0, "bye_week": 6,
        },
        {
            "player_id": "00-0000003", "sleeper_id": "8146", "player_name": "Charlie Ender",
            "position": "TE", "team": "BUF", "points_over_replacement": 90.0, "bye_week": 12,
        },
    ]
)

# Empty rather than populated: these cases are about the loop, and an empty survival frame ranks
# by points over replacement and says so on screen, which renders exactly as fully as the other.
SURVIVAL = pd.DataFrame(columns=["player_id", "overall_pick", "p_survives"])

CONTEXT = {"board": BOARD, "survival": SURVIVAL, "draft": {"draft_id": "1"}, "league": LEAGUE}


def pick(number: int, sleeper_id: str, roster_id: int = 3) -> dict:
    """One entry of a Sleeper picks payload, as the API hands it over."""
    return {
        "pick_no": number,
        "player_id": sleeper_id,
        "roster_id": roster_id,
        "metadata": {"first_name": "Someone", "last_name": "Else", "position": "RB"},
    }


def poller(*outcomes):
    """A poll handing back each payload in turn; an exception among them is raised instead."""
    remaining = list(outcomes)

    def poll():
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return poll


def ticker(ticks: int):
    """A sleep that records what it was asked to wait, and interrupts after `ticks` of them.

    A real loop only ever ends by Ctrl-C, which lands in the sleep. Ending these the same way
    keeps the loop under test the loop that runs on the night.
    """
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        if len(waits) >= ticks:
            raise KeyboardInterrupt

    return sleep, waits


def clock(step=timedelta(seconds=3)):
    """A time that advances one tick per reading."""
    readings = [NOW - step]

    def now():
        readings.append(readings[-1] + step)
        return readings[-1]

    return now


def run(*outcomes, ticks: int = None, interval: float = 0.5):
    """Drive `watch` over the given poll outcomes and give back everything it wrote."""
    written = []
    sleep, waits = ticker(ticks if ticks is not None else len(outcomes))
    watch(
        CONTEXT, limit=5, poll=poller(*outcomes), sleep=sleep, now=clock(),
        write=written.append, interval=interval,
    )
    return written, waits


def boards(written: list[str]) -> list[str]:
    """Just the chunks that are a drawn board, which is what a drafter reads."""
    return [chunk for chunk in written if "Best available" in chunk]


# 11. There is nothing on screen until the first draw, so it is never skipped.
def test_the_first_tick_draws_the_board():
    written, _ = run([pick(1, "4034")])
    assert len(boards(written)) == 1
    assert "Bravo Wideout" in boards(written)[0]


# 12. Thirty rows redrawn under a reading eye, every three seconds, for two hours.
def test_a_tick_that_changes_nothing_draws_nothing():
    payload = [pick(1, "4034")]
    written, _ = run(payload, list(payload))
    assert len(boards(written)) == 1


# 13. The change that matters: the drafted player is off the board on the next draw.
def test_a_tick_with_a_new_pick_draws_the_board_again():
    first = [pick(1, "4034")]
    second = [*first, pick(2, "6786")]
    written, _ = run(first, second)
    drawn = boards(written)
    assert len(drawn) == 2
    assert "Bravo Wideout" in drawn[0]
    assert "Bravo Wideout" not in drawn[1]


# 14. A dropped poll is a line, not an ending. The loop must come back round and ask again.
def test_a_failed_poll_draws_no_board_and_does_not_end_the_session():
    written, _ = run(requests.ConnectionError("connection reset"), [pick(1, "4034")])
    assert len(boards(written)) == 1
    assert any("connection reset" in chunk for chunk in written)


# 15. And the board that was missed during the outage is on screen the moment it comes back.
def test_the_tick_after_a_failure_draws_the_board_it_missed():
    first = [pick(1, "4034")]
    written, _ = run(first, requests.ConnectionError("timed out"), [*first, pick(2, "6786")])
    drawn = boards(written)
    assert len(drawn) == 2
    assert "Bravo Wideout" not in drawn[1]


# 16. Fixed interval, as configured — a loop that polled as fast as it could would be rate limited
# off the API in the middle of a draft.
def test_the_loop_waits_the_configured_interval_between_polls():
    payload = [pick(1, "4034")]
    _, waits = run(payload, list(payload), list(payload), interval=0.5)
    assert waits == [0.5, 0.5, 0.5]


# 17. Ctrl-C at pick 40 is how this tool ends every time it is ever used.
def test_an_interrupt_ends_the_loop_cleanly():
    written, _ = run([pick(1, "4034")])
    assert "stopped" in written[-1].lower()
