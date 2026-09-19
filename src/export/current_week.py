"""Current-week resolution for the export step — issue #128, under the front-end-rebuild epic
(#111).

`schedules` isn't part of the static export set (#125's spike scoped exports to the tables the
existing pages read directly, and neither did at the time), so a page reading only exported JSON
has no way to work out which week is "current" the way `src.web.pages.lineup_optimizer._current_week`
does — the earliest week whose games aren't finished yet, not the warehouse's last-built week. This
precomputes that value once, at build time, for every (league_key, season) the export covers.

Pure: a per-week frame and a date in, an int out. The warehouse read, the `today` value and the
file write belong to `src/export/build.py`, the runnable edge that calls this.
"""

import pandas as pd


def resolve_current_week(weeks: pd.DataFrame, today: str) -> int:
    """`weeks` is one row per week for a single season — `week` and `ends` (that week's last game
    date, as an ISO date string) columns, the same shape `schedules` grouped by week already
    returns. `today` is an ISO date string.

    Returns the earliest week whose `ends` hasn't passed `today`, or the season's last week if
    every week has already finished — mirroring `_current_week`'s own fallback for a season that's
    over. Reasons about week numbers via `min`/`max` rather than row position, so an unsorted
    `weeks` frame resolves the same way a sorted one would.
    """
    upcoming = weeks[weeks["ends"] >= today]
    return int(upcoming["week"].min()) if not upcoming.empty else int(weeks["week"].max())
