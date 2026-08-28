"""Cases 1-10 of the refresh ticket: what changed, and when we last heard anything.

Two pure decisions sit under a loop that is otherwise just a clock and a GET, and both of them
are the difference between a screen that can be trusted and one that cannot:

- **The fingerprint** decides whether the board on screen is still the board. Redrawing thirty
  rows every three seconds makes the list unreadable exactly when it is being read, so the loop
  redraws only on a change — which means a fingerprint that misses a change leaves a drafted
  player sitting on the board looking available. It is allowed to be too sensitive. It is not
  allowed to be too blunt.
- **The status line** is the whole of "is this thing still working". A draft room can go three
  minutes without a pick, and a dead connection looks exactly the same from the outside. The
  distinguishing fact is the time of the last *successful* check, which is why it is reported
  rather than the current time — a clock that ticks regardless would say everything is fine right
  up until the pick that isn't there.
"""

from datetime import datetime, timedelta

import requests

from src.draft.refresh import fingerprint, status_line

NOW = datetime(2026, 9, 3, 18, 42, 7)


def pick(number: int | None, player_id: str, roster_id: int = 1) -> dict:
    """One entry of a Sleeper picks payload, trimmed to what the fingerprint can see."""
    return {"pick_no": number, "player_id": player_id, "roster_id": roster_id}


# 1. The tick that changes nothing must be recognisable as such, or the board redraws forever.
def test_the_same_payload_twice_fingerprints_the_same():
    payload = [pick(1, "4034"), pick(2, "6786")]
    assert fingerprint(payload) == fingerprint(list(payload))


# 2. The change the loop exists for.
def test_a_new_pick_changes_the_fingerprint():
    before = [pick(1, "4034"), pick(2, "6786")]
    after = [*before, pick(3, "8146")]
    assert fingerprint(after) != fingerprint(before)


# 3. Same count, different last pick. A commissioner correction does this, and so does the API
# catching up with a hand-marked player. Counting alone would call this no change.
def test_the_same_count_with_a_different_last_pick_changes_the_fingerprint():
    before = [pick(1, "4034"), pick(2, "6786")]
    after = [pick(1, "4034"), pick(2, "8146")]
    assert fingerprint(after) != fingerprint(before)


# 4. Sleeper returns the payload sorted, but nothing here should depend on that: an order that
# wobbled would redraw the board on a tick where nothing happened.
def test_picks_out_of_order_fingerprint_the_same_as_in_order():
    ordered = [pick(1, "4034"), pick(2, "6786"), pick(3, "8146")]
    shuffled = [ordered[2], ordered[0], ordered[1]]
    assert fingerprint(shuffled) == fingerprint(ordered)


# 5. The tool can be started before the first pick, which is the normal way to start it.
def test_an_empty_payload_fingerprints_and_differs_from_one_pick():
    assert fingerprint([]) == fingerprint([])
    assert fingerprint([]) != fingerprint([pick(1, "4034")])


# 6. A hand-marked player (issue #36) carries no pick number. He is still a change to the board,
# and a fingerprint that only watched pick numbers would not redraw for him.
def test_a_pick_with_no_number_still_moves_the_fingerprint():
    api_only = [pick(1, "4034")]
    with_hand_mark = [*api_only, pick(None, "6786")]
    assert fingerprint(with_hand_mark) != fingerprint(api_only)


# 7. The two facts the line exists to carry.
def test_the_status_line_names_the_picks_made_and_the_time_of_the_last_check():
    line = status_line(27, checked_at=NOW, now=NOW)
    assert "27" in line
    assert "18:42:07" in line


# 8. A quiet draft room still gets a moving clock, because the poll succeeded even though the
# payload did not change. This is what says the connection is alive.
def test_a_later_successful_check_moves_the_time_the_line_reports():
    later = NOW + timedelta(seconds=6)
    line = status_line(27, checked_at=later, now=later)
    assert "18:42:13" in line
    assert "18:42:07" not in line


# 9. A broken connection is the same silence with a frozen clock. The time reported is the last
# one that succeeded, never the current one, and how long ago it was is on screen.
def test_a_failed_check_keeps_the_last_successful_time_and_shows_it_ageing():
    line = status_line(
        27, checked_at=NOW, now=NOW + timedelta(seconds=14),
        error=requests.ConnectionError("connection reset"),
    )
    assert "18:42:07" in line
    assert "18:42:21" not in line
    assert "14s" in line


# 10. A failure that reads like the end of the session sends a drafter to another window. It has
# to say what went wrong and that the tool is still going.
def test_a_failed_check_says_what_failed_and_that_it_will_retry():
    line = status_line(
        27, checked_at=NOW, now=NOW + timedelta(seconds=3),
        error=requests.ConnectionError("connection reset"),
    )
    assert "connection reset" in line
    assert "retry" in line.lower()
