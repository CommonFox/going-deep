"""Recompute real fantasy points from `weekly_stats`' raw counting stats, under a league's own
scoring rules — not nflverse's canned `fantasy_points_ppr`, which won't match either league's
actual pass-TD/INT/reception values.

Shared by `points_over_replacement.py` (full completed seasons) and `ros_points.py` (the season
in progress, cut off at whichever week has actually been played) so the counting-stat ->
`league_settings` coefficient mapping is defined exactly once. Neither module's own scope — which
rows get summed, what else gets computed from the result — lives here; this is only the
stat-to-points arithmetic itself.
"""

import pandas as pd

# weekly_stats raw counting stat -> the league_settings column with its per-unit point value.
STAT_COEFFICIENTS = {
    "passing_yards": "pass_yd_pts",
    "passing_tds": "pass_td_pts",
    "passing_2pt_conversions": "pass_2pt_pts",
    "passing_interceptions": "pass_int_pts",
    "rushing_yards": "rush_yd_pts",
    "rushing_tds": "rush_td_pts",
    "rushing_2pt_conversions": "rush_2pt_pts",
    "receptions": "rec_pts",
    "receiving_yards": "rec_yd_pts",
    "receiving_tds": "rec_td_pts",
    "receiving_2pt_conversions": "rec_2pt_pts",
}
# Every league scores all three fumble-lost types (pass-sack/rush/rec) with one shared value.
FUMBLE_LOST_COLUMNS = ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost")

# Every raw weekly_stats column a caller needs summed before calling league_points().
STAT_COLUMNS = (*STAT_COEFFICIENTS, *FUMBLE_LOST_COLUMNS)


def league_points(stat_totals: pd.DataFrame, league: pd.Series) -> pd.Series:
    """Each row's fantasy points in one league's scoring, from summed raw counting stats.

    `stat_totals` must carry every column named in `STAT_COLUMNS` (e.g. a `weekly_stats` GROUP BY
    that sums them); `league` is one row of `league_settings`.
    """
    points = sum(
        stat_totals[stat] * league[coefficient]
        for stat, coefficient in STAT_COEFFICIENTS.items()
    )
    fumbles_lost = stat_totals[list(FUMBLE_LOST_COLUMNS)].sum(axis=1)
    return points + fumbles_lost * league["fum_lost_pts"]
