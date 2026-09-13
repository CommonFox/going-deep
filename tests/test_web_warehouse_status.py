"""The advisory staleness notice shown on every page of the web app.

Mirrors src/draft/live.py's check_recency, but this is a warning rather than a refusal: the web app
is a "check this before an in-season decision" tool, not a draft-night one, and no pick can be
corrupted by a stale read the way one could there. The message still carries the build time it
found, for the same reason live.py's does — "the warehouse is stale" isn't actionable, "the
warehouse is from last Tuesday" is.
"""

from datetime import datetime, timedelta

from src.web.warehouse_status import MAX_WAREHOUSE_AGE, staleness_warning

NOW = datetime(2026, 9, 13, 12, 0)


def test_a_warehouse_built_today_gets_no_warning():
    assert staleness_warning(NOW - timedelta(hours=3), NOW) is None


def test_a_stale_warehouse_warns_and_reports_when_it_was_built():
    built = NOW - timedelta(days=6)
    message = staleness_warning(built, NOW)
    assert message is not None
    assert built.strftime("%Y-%m-%d") in message
    assert built.strftime("%H:%M") in message


def test_the_window_is_the_configured_one_and_its_edge_is_not_stale():
    # Exactly at the limit is still fresh; a minute past it warns.
    assert staleness_warning(NOW - MAX_WAREHOUSE_AGE, NOW) is None
    assert staleness_warning(NOW - MAX_WAREHOUSE_AGE - timedelta(minutes=1), NOW) is not None
