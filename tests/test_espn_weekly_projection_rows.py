"""Extracting one week's projection rows out of an ESPN player-pool payload (#117).

`load_weekly_projections` now reads every archived player-pool snapshot (not just the newest) so
the weekly projection can be archived instead of overwritten. The per-file extraction — which stat
entry is "this week's projection" versus a season total or an actual — is the pure part of that,
pulled out of the loader so it can be checked against a small hand-built payload instead of a real
ESPN response.
"""

from src.silver.espn import _weekly_projection_rows


def _player(espn_id: int, position_id: int, stats: list[dict]) -> dict:
    return {"player": {"id": espn_id, "defaultPositionId": position_id, "stats": stats}}


# 1. The normal case: a projected (statSourceId=1), weekly (scoringPeriodId>0) stat for the
#    requested season becomes one row.
def test_extracts_a_weekly_projection_row():
    players = [_player(1, 2, [
        {"statSourceId": 1, "scoringPeriodId": 2, "seasonId": 2026, "appliedTotal": 14.5},
    ])]

    rows = _weekly_projection_rows(players, season=2026)

    assert rows == [
        {"espn_id": 1, "position": "RB", "season": 2026, "week": 2, "projected_points": 14.5},
    ]


# 2. scoringPeriodId=0 is the season-total row, not a week — must be excluded.
def test_excludes_the_season_total_row():
    players = [_player(1, 2, [
        {"statSourceId": 1, "scoringPeriodId": 0, "seasonId": 2026, "appliedTotal": 210.0},
    ])]

    assert _weekly_projection_rows(players, season=2026) == []


# 3. statSourceId != 1 is an actual, not a projection — must be excluded.
def test_excludes_actual_stats():
    players = [_player(1, 2, [
        {"statSourceId": 0, "scoringPeriodId": 2, "seasonId": 2026, "appliedTotal": 9.0},
    ])]

    assert _weekly_projection_rows(players, season=2026) == []


# 4. A stat for a different season than the one requested must be excluded.
def test_excludes_a_different_season():
    players = [_player(1, 2, [
        {"statSourceId": 1, "scoringPeriodId": 2, "seasonId": 2025, "appliedTotal": 14.5},
    ])]

    assert _weekly_projection_rows(players, season=2026) == []


# 5. A payload can carry more than one week's projection at once; every qualifying stat becomes
#    its own row rather than only the first match.
def test_extracts_every_qualifying_week():
    players = [_player(1, 2, [
        {"statSourceId": 1, "scoringPeriodId": 2, "seasonId": 2026, "appliedTotal": 14.5},
        {"statSourceId": 1, "scoringPeriodId": 3, "seasonId": 2026, "appliedTotal": 12.0},
    ])]

    rows = _weekly_projection_rows(players, season=2026)

    assert [r["week"] for r in rows] == [2, 3]


# 6. Multiple players in the pool each contribute their own rows.
def test_extracts_rows_for_every_player():
    players = [
        _player(1, 2, [{"statSourceId": 1, "scoringPeriodId": 2, "seasonId": 2026, "appliedTotal": 14.5}]),
        _player(2, 3, [{"statSourceId": 1, "scoringPeriodId": 2, "seasonId": 2026, "appliedTotal": 8.0}]),
    ]

    rows = _weekly_projection_rows(players, season=2026)

    assert {r["espn_id"] for r in rows} == {1, 2}
