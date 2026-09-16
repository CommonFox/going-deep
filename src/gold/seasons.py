"""Predicate for whether a season in the warehouse is complete or still being played.

Nothing marks this with a column. `src/silver/nfl_data.py` fetches every record-of-play feed
(`weekly_stats`, `snap_counts`, `pbp_punts`, ...) through the season in progress, so a season
simply appearing in one of those tables no longer implies it's finished — a season-total, a
per-season finish rank, or a walk-forward backtest fold built on it needs to say explicitly which
seasons it trusts.

Completeness is derived from `schedules` instead: a season is complete once every regular-season
game in it has a `result`. That self-maintains (no season number to bump every year here) and
correctly treats a season as complete the moment its last game is final, rather than waiting for
`_UPCOMING_SEASON` to be bumped the following year.

Every gold model that assumes a full season should filter to `completed_seasons(con)` (or embed
`COMPLETED_SEASONS_SQL`) rather than trust fetch scope to have kept a partial season out.
"""

COMPLETED_SEASONS_SQL = """
    SELECT season
    FROM schedules
    WHERE game_type = 'REG'
    GROUP BY season
    HAVING COUNT(*) = COUNT(result)
"""


def completed_seasons(con) -> list[int]:
    """Every season in `schedules` where every regular-season game has a `result`, ascending."""
    rows = con.execute(COMPLETED_SEASONS_SQL + "\n    ORDER BY season").fetchall()
    return [row[0] for row in rows]


def max_completed_season(con) -> int:
    """The most recent fully-played season — the natural upper bound for a season-total model."""
    return con.execute(f"SELECT MAX(season) FROM ({COMPLETED_SEASONS_SQL})").fetchone()[0]
