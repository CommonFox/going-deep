"""Recovering (season, week) from a fantasypros.py weekly-rankings snapshot's filename (#117).

`fetch_weekly_rankings` archives one snapshot per (position, season, week, captured_at); the raw
payload carries none of those four (week is a query parameter, not a response field, and the
response has no season at all), so `load_weekly_rankings` has to read season and week back out of
the filename it was stamped into rather than out of the file's contents.
"""

import pytest

from src.silver.fantasypros import _season_and_week_from_snapshot_name


# 1. The normal case: a well-formed snapshot filename yields back exactly what was stamped into it.
def test_parses_season_and_week_from_a_wellformed_name(tmp_path):
    path = tmp_path / "weekly_qb_2026_2_20260901T000000000000.json"
    assert _season_and_week_from_snapshot_name(path) == (2026, 2)


# 2. Double-digit weeks parse the same way as single-digit ones.
def test_parses_a_double_digit_week(tmp_path):
    path = tmp_path / "weekly_dst_2026_14_20260901T000000000000.json"
    assert _season_and_week_from_snapshot_name(path) == (2026, 14)


# 3. Anything that isn't shaped like a snapshot this module wrote is a caller bug, not a silent
#    wrong answer.
def test_raises_on_a_name_that_does_not_match(tmp_path):
    path = tmp_path / "weekly_qb_2026.json"
    with pytest.raises(ValueError, match="weekly rankings snapshot"):
        _season_and_week_from_snapshot_name(path)
