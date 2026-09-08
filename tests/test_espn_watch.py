"""ESPN's counterpart to `test_draft_watch.py`: the same loop, driven over ESPN-shaped payloads.

Only a subset of the Sleeper suite's cases are restated here — the loop itself (`watch`) is a
straight port with the platform-specific bits (`combine`, `fingerprint`, `read_mark`, `status_line`,
`screen`) all threaded with ESPN's own field names, and that threading is exactly what these cases
exist to prove actually works end to end: a fingerprint built from `playerId`/`overallPickNumber`
that still catches a new pick, a hand-mark that resolves against `espn_id` and reaches the board,
and a status line that names ESPN rather than Sleeper on a failed poll.
"""

from datetime import datetime, timedelta

import pandas as pd
import requests

from src.draft.live_espn import watch

NOW = datetime(2026, 9, 3, 18, 42, 7)

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1, "K": 1, "P": 1, "DST": 1}

LEAGUE = {"seat": 1, "roster_id": 1, "team_count": 10, "rounds": 20, "slots": SLOTS}

BOARD = pd.DataFrame(
    [
        {
            "player_id": "00-0000001", "espn_id": "4034", "player_name": "Alpha Back",
            "position": "RB", "team": "SF", "points_over_replacement": 150.0, "bye_week": 9,
        },
        {
            "player_id": "00-0000002", "espn_id": "6786", "player_name": "Bravo Wideout",
            "position": "WR", "team": "KC", "points_over_replacement": 120.0, "bye_week": 6,
        },
        {
            "player_id": "00-0000003", "espn_id": "8146", "player_name": "Charlie Ender",
            "position": "TE", "team": "BUF", "points_over_replacement": 90.0, "bye_week": 12,
        },
    ]
)

SURVIVAL = pd.DataFrame(columns=["player_id", "overall_pick", "p_survives"])
PLANS = pd.DataFrame(columns=["draft_slot", "plan", "trials", "points_vs_field", "win_rate"])

CONTEXT = {
    "board": BOARD, "survival": SURVIVAL, "plans": PLANS,
    "draft": {"id": 1}, "league": LEAGUE,
}


def pick(overall: int, espn_id: str, team_id: int = 3) -> dict:
    """One entry of an ESPN `draftDetail.picks` payload, as the API hands it over."""
    return {"overallPickNumber": overall, "playerId": int(espn_id), "teamId": team_id}


def poller(*outcomes):
    remaining = list(outcomes)

    def poll():
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return poll


def ticker(ticks: int):
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        if len(waits) >= ticks:
            raise KeyboardInterrupt

    return sleep, waits


def clock(step=timedelta(seconds=3)):
    readings = [NOW - step]

    def now():
        readings.append(readings[-1] + step)
        return readings[-1]

    return now


def run(*outcomes, ticks: int = None, interval: float = 0.5):
    written = []
    sleep, waits = ticker(ticks if ticks is not None else len(outcomes))
    watch(
        CONTEXT, limit=5, poll=poller(*outcomes), sleep=sleep, now=clock(),
        write=written.append, interval=interval,
    )
    return written, waits


def boards(written: list[str]) -> list[str]:
    return [chunk for chunk in written if "Best available" in chunk]


# 1. There is nothing on screen until the first draw.
def test_the_first_tick_draws_the_board():
    written, _ = run([pick(1, "4034")])
    assert len(boards(written)) == 1
    assert "Bravo Wideout" in boards(written)[0]


# 2. A tick with nothing new draws nothing again.
def test_a_tick_that_changes_nothing_draws_nothing():
    payload = [pick(1, "4034")]
    written, _ = run(payload, list(payload))
    assert len(boards(written)) == 1


# 3. The change that matters: a new pick redraws, and the drafted player is off the board.
def test_a_tick_with_a_new_pick_draws_the_board_again():
    first = [pick(1, "4034")]
    second = [*first, pick(2, "6786")]
    written, _ = run(first, second)
    drawn = boards(written)
    assert len(drawn) == 2
    assert "Bravo Wideout" in drawn[0]
    assert "Bravo Wideout" not in drawn[1]


# 4. A dropped poll is a line, naming ESPN — not the end of the session.
def test_a_failed_poll_draws_no_board_and_names_espn():
    written, _ = run(requests.ConnectionError("connection reset"), [pick(1, "4034")])
    assert len(boards(written)) == 1
    assert any("ESPN did not answer" in chunk for chunk in written)
    assert any("connection reset" in chunk for chunk in written)


# 5. Ctrl-C ends the loop cleanly, same as the Sleeper edge.
def test_an_interrupt_ends_the_loop_cleanly():
    written, _ = run([pick(1, "4034")])
    assert "stopped" in written[-1].lower()


def typist(*batches):
    remaining = list(batches)

    def keys():
        return remaining.pop(0) if remaining else []

    return keys


def available(drawn: str) -> str:
    return drawn.split("Best available")[-1]


def run_typed(*outcomes, typed, interval: float = 0.5):
    written = []
    sleep, _ = ticker(len(outcomes))
    watch(
        CONTEXT, limit=5, poll=poller(*outcomes), sleep=sleep, now=clock(),
        write=written.append, keys=typist(*typed), interval=interval,
    )
    return written


# 6. A typed name marks the player against `espn_id` and redraws without him — the hand-mark path
#    threaded with `ID_COLUMN`/`_espn_pick_entry` actually working end to end.
def test_a_typed_name_marks_the_player_and_redraws_without_him():
    written = run_typed([pick(1, "4034")], typed=[["bravo wideout"]])

    drawn = boards(written)
    assert len(drawn) == 1
    assert "Bravo Wideout" not in available(drawn[0])


# 7. The mark survives a tick ESPN did not answer — the same reason the feature exists on the
#    Sleeper edge, proven against ESPN's own field names.
def test_a_mark_redraws_the_board_on_a_tick_the_poll_failed():
    written = run_typed(
        [pick(1, "4034")], requests.ConnectionError("connection reset"),
        typed=[[], ["bravo wideout"]],
    )

    drawn = boards(written)
    assert len(drawn) == 2
    assert "Bravo Wideout" in available(drawn[0])
    assert "Bravo Wideout" not in available(drawn[1])


# 8. A typed position narrows the board on the same tick.
def test_a_typed_position_narrows_the_board_and_redraws_at_once():
    written = run_typed([pick(1, "4034")], [pick(1, "4034")], typed=[[], ["wr"]])

    drawn = boards(written)
    assert len(drawn) == 2
    assert "Charlie Ender" in available(drawn[0])
    assert "Bravo Wideout" in available(drawn[1])
    assert "Charlie Ender" not in available(drawn[1])
