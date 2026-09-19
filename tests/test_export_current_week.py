"""Current-week resolution for the export step — issue #128, under the front-end-rebuild epic
(#111).

Ported from `src.web.pages.lineup_optimizer._current_week`: the earliest week whose games aren't
finished yet is "current" to a drafter setting a lineup today, not the warehouse's last-built
week. `resolve_current_week` is pure — a per-week frame and a date string in, an int out — so the
rule is checkable without a clock or a warehouse, unlike the Streamlit page's `date.today()` call.
"""

import pandas as pd

from src.export.current_week import resolve_current_week


# 1. Some weeks have already finished (`ends` before `today`), some haven't — the earliest
#    unfinished week is "current", not the earliest week overall.
def test_returns_earliest_week_whose_games_are_not_finished():
    weeks = pd.DataFrame({
        "week": [1, 2, 3],
        "ends": ["2026-09-14", "2026-09-21", "2026-09-28"],
    })

    assert resolve_current_week(weeks, today="2026-09-18") == 2


# 2. Every week has already finished — the season is over, so the last week is "current" rather
#    than nothing at all, mirroring `_current_week`'s own fallback.
def test_returns_last_week_when_season_already_over():
    weeks = pd.DataFrame({
        "week": [1, 2, 3],
        "ends": ["2026-09-14", "2026-09-21", "2026-09-28"],
    })

    assert resolve_current_week(weeks, today="2027-01-01") == 3


# 3. Every week is still upcoming — the season hasn't started, so the first week is "current".
def test_returns_first_week_when_today_is_before_season_start():
    weeks = pd.DataFrame({
        "week": [1, 2, 3],
        "ends": ["2026-09-14", "2026-09-21", "2026-09-28"],
    })

    assert resolve_current_week(weeks, today="2026-08-01") == 1


# 4. A week whose `ends` is exactly `today` still counts as upcoming (`>=`, not `>`) — the last
#    day of a week's games is still "current", not yet past.
def test_boundary_today_equal_to_ends_counts_as_upcoming():
    weeks = pd.DataFrame({
        "week": [1, 2],
        "ends": ["2026-09-14", "2026-09-21"],
    })

    assert resolve_current_week(weeks, today="2026-09-21") == 2


# 5. Row order in `weeks` doesn't matter — the function reasons about week numbers via min/max,
#    not positionally, so a frame that didn't come out of `ORDER BY week` still resolves correctly.
def test_row_order_does_not_affect_the_result():
    weeks = pd.DataFrame({
        "week": [3, 1, 2],
        "ends": ["2026-09-28", "2026-09-14", "2026-09-21"],
    })

    assert resolve_current_week(weeks, today="2026-09-18") == 2
