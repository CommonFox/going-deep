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

# Empty for the same reason, and a state the warehouse can genuinely be in: with no plans for this
# seat the opening guidance is withdrawn and says so, which is a screen like any other.
PLANS = pd.DataFrame(columns=["draft_slot", "plan", "trials", "points_vs_field", "win_rate"])

CONTEXT = {
    "board": BOARD, "survival": SURVIVAL, "plans": PLANS,
    "draft": {"draft_id": "1"}, "league": LEAGUE,
}


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


# Issue #36's loop cases: the drafter typing at the same line the status is repainting on.
#
# The pure half of hand-marking — which name resolves to which player, and what is refused — is
# `test_draft_marks.py`'s. These are the half that only exists inside the loop: that a mark reaches
# the board at all, that it stays there for the rest of the session, and that it still works on a
# tick where Sleeper did not answer, which is the whole reason it exists.


def typist(*batches):
    """A keyboard handing back the lines typed since the last tick, one batch per tick."""
    remaining = list(batches)

    def keys():
        return remaining.pop(0) if remaining else []

    return keys


def available(drawn: str) -> str:
    """The candidate list out of one drawn board — who is actually still on it.

    A hand-marked player is deliberately *named* on the screen he has been subtracted from, under
    the block saying the drafter marked him, so "is he still on the board" is a question about
    this section and not about whether his name appears anywhere.
    """
    return drawn.split("Best available")[-1]


def run_typed(*outcomes, typed, interval: float = 0.5):
    """Drive `watch` with something typed at it, and give back everything it wrote."""
    written = []
    sleep, _ = ticker(len(outcomes))
    watch(
        CONTEXT, limit=5, poll=poller(*outcomes), sleep=sleep, now=clock(),
        write=written.append, keys=typist(*typed), interval=interval,
    )
    return written


# 18. The mark reaching the board, which is the whole feature: a player the API has not reported
# is off the board because the drafter said so.
def test_a_typed_name_marks_the_player_and_redraws_without_him():
    written = run_typed([pick(1, "4034")], typed=[["bravo wideout"]])

    drawn = boards(written)
    assert len(drawn) == 1
    assert "Bravo Wideout" not in available(drawn[0])


# 19. A mark that lasted one tick would be worse than none: the drafter would have to retype it
# every three seconds, and would not know it had lapsed.
def test_a_mark_survives_the_refreshes_that_follow_it():
    payload = [pick(1, "4034")]
    written = run_typed(payload, payload, [*payload, pick(2, "8146")],
                        typed=[["bravo wideout"], [], []])

    for drawn in boards(written):
        assert "Bravo Wideout" not in available(drawn)


# 20. Unioned with the API's picks, never instead of them — a mark that replaced them would put
# thirteen other managers' picks back on the board.
def test_a_mark_is_combined_with_the_picks_the_api_reports():
    written = run_typed([pick(1, "4034")], typed=[["bravo wideout"]])

    drawn = available(boards(written)[-1])
    assert "Alpha Back" not in drawn
    assert "Bravo Wideout" not in drawn
    assert "Charlie Ender" in drawn


# 21. A refusal has to be on screen. Typed at a line that repaints every three seconds, a name
# that quietly did nothing reads exactly like a name that worked.
def test_a_refused_name_draws_no_board_and_says_why():
    payload = [pick(1, "4034")]
    written = run_typed(payload, payload, typed=[[], ["nobody at all"]])

    assert len(boards(written)) == 1
    assert any("nobody at all" in chunk for chunk in written)


# 22. The case the feature was built for. Sleeper has stopped answering, the draft has not stopped,
# and the board still has to move.
def test_a_mark_redraws_the_board_on_a_tick_the_poll_failed():
    written = run_typed(
        [pick(1, "4034")], requests.ConnectionError("connection reset"),
        typed=[[], ["bravo wideout"]],
    )

    drawn = boards(written)
    assert len(drawn) == 2
    assert "Bravo Wideout" in available(drawn[0])
    assert "Bravo Wideout" not in available(drawn[1])


# 23. Enter on an empty line is how a drafter clears a half-typed name.
def test_an_empty_line_changes_nothing():
    payload = [pick(1, "4034")]
    written = run_typed(payload, payload, typed=[[], ["", "   "]])

    assert len(boards(written)) == 1


# 24. The way back from a mistyped mark, without restarting the tool mid-draft.
def test_an_unmark_puts_the_player_back_on_the_board():
    payload = [pick(1, "4034")]
    written = run_typed(payload, payload, typed=[["bravo wideout"], ["-bravo wideout"]])

    drawn = boards(written)
    assert len(drawn) == 2
    assert "Bravo Wideout" not in available(drawn[0])
    assert "Bravo Wideout" in available(drawn[1])


# Issue #29, user story 17: the filter reaching the running tool, which is the whole point of
# typing it rather than passing it at startup. The board is three players at three positions, so
# a narrowed screen is unambiguous: `wr` leaves exactly one of them.


# 25. Typed, and the board narrows on the same tick — not on the next pick, which in a quiet draft
# room could be five minutes away.
def test_a_typed_position_narrows_the_board_and_redraws_at_once():
    payload = [pick(1, "4034")]
    written = run_typed(payload, payload, typed=[[], ["wr"]])

    drawn = boards(written)
    assert len(drawn) == 2
    assert "Charlie Ender" in available(drawn[0])
    assert "Bravo Wideout" in available(drawn[1])
    assert "Charlie Ender" not in available(drawn[1])


# 26. A filter that reset itself on the next pick would be a filter nobody could read an answer
# out of, because the next pick lands while the answer is still being read.
def test_a_narrowed_board_stays_narrowed_as_picks_come_in():
    written = run_typed(
        [pick(1, "4034")], [pick(1, "4034"), pick(2, "6786")], typed=[["te"], []]
    )

    drawn = boards(written)
    assert len(drawn) == 2
    assert "Alpha Back" not in available(drawn[1])
    assert "Charlie Ender" in available(drawn[1])


# 27. The way back to the whole board, without restarting the tool mid-draft.
def test_all_puts_the_whole_board_back():
    payload = [pick(1, "4034")]
    written = run_typed(payload, payload, payload, typed=[[], ["te"], ["all"]])

    drawn = boards(written)
    assert len(drawn) == 3
    assert "Bravo Wideout" not in available(drawn[1])
    assert "Bravo Wideout" in available(drawn[2])


# 28. Same reasoning as the hand-mark case above it: narrowing is the drafter changing what he
# asked for, not Sleeper reporting something new, so it is drawn whether the poll answered or not.
def test_a_typed_position_narrows_the_board_on_a_tick_the_poll_failed():
    written = run_typed(
        [pick(1, "4034")], requests.ConnectionError("connection reset"),
        typed=[[], ["wr"]],
    )

    drawn = boards(written)
    assert len(drawn) == 2
    assert "Charlie Ender" not in available(drawn[1])
    assert "Bravo Wideout" in available(drawn[1])
