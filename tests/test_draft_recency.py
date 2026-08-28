"""The guard that stops the tool drafting off a stale board.

Cases 8 and 9 of the terminal render ticket, plus the boundary that defines "older than".

Every number the assistant shows was computed by a warehouse rebuild, so the board is exactly as
current as that rebuild and no more. A board built before the last week of preseason still shows a
player who has since been ruled out for the year, and it shows him with the same confident value
as everyone else — there is nothing on screen to suggest the file is old. So the check is a refusal
rather than a warning, and it reports the build time it found so the answer to "how stale?" comes
from the tool rather than from `ls -l`.
"""

from datetime import datetime, timedelta

import pytest

from src.draft.live import MAX_WAREHOUSE_AGE, check_recency

NOW = datetime(2026, 9, 3, 18, 30)


# 8. A warehouse inside the window passes.
def test_a_warehouse_built_today_is_accepted():
    check_recency(NOW - timedelta(hours=3), NOW)


# 9. An older one raises, and the message carries the build time it found.
def test_a_stale_warehouse_refuses_and_reports_when_it_was_built():
    built = NOW - timedelta(days=6)
    with pytest.raises(RuntimeError) as caught:
        check_recency(built, NOW)
    message = str(caught.value)
    assert built.strftime("%Y-%m-%d") in message
    assert built.strftime("%H:%M") in message


def test_the_window_is_the_configured_one_and_its_edge_is_not_stale():
    # Exactly at the limit still runs; a minute past it does not. Stated as a case because
    # "older than 24 hours" has to mean something exact at 6pm on a draft night.
    check_recency(NOW - MAX_WAREHOUSE_AGE, NOW)
    with pytest.raises(RuntimeError):
        check_recency(NOW - MAX_WAREHOUSE_AGE - timedelta(minutes=1), NOW)
