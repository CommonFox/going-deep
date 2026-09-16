"""Mapping one Sleeper weekly-projection API row into a warehouse row (#117).

`load_projections` now reads every archived projections snapshot (not just the newest) so
revisions to a future week's projection can be archived instead of overwritten. The per-row
mapping — which fields come from the nested `player`/`stats` objects, and what a missing one
defaults to — is the pure part of that, pulled out of the loader so it can be checked directly.
"""

from src.silver.sleeper import _projection_row


# 1. The normal case: every field comes from where the API actually puts it.
def test_maps_a_normal_row():
    row = {
        "player_id": "4046",
        "player": {"first_name": "Josh", "last_name": "Allen", "position": "QB"},
        "team": "BUF",
        "season": "2026",
        "week": 2,
        "stats": {"pts_std": 18.5, "pts_half_ppr": 18.5, "pts_ppr": 18.5, "rec": 0.0},
    }

    assert _projection_row(row) == {
        "sleeper_id": "4046",
        "player_name": "Josh Allen",
        "position": "QB",
        "team": "BUF",
        "season": "2026",
        "week": 2,
        "pts_std": 18.5,
        "pts_half_ppr": 18.5,
        "pts_ppr": 18.5,
        "rec": 0.0,
    }


# 2. A missing `player` object (seen for some team defenses) doesn't raise — name/position fall
#    back to blank/None rather than the row being dropped.
def test_missing_player_object_falls_back_to_blanks():
    row = {"player_id": "SEA", "player": None, "team": "SEA", "season": "2026", "week": 2, "stats": {}}

    result = _projection_row(row)

    assert result["player_name"] == " "
    assert result["position"] is None


# 3. A missing `stats` object (a player projected for zero involvement) leaves every points/
#    reception field None rather than raising.
def test_missing_stats_object_leaves_points_none():
    row = {"player_id": "4046", "player": {"first_name": "Josh", "last_name": "Allen"}, "stats": None}

    result = _projection_row(row)

    assert result["pts_std"] is None
    assert result["pts_half_ppr"] is None
    assert result["pts_ppr"] is None
    assert result["rec"] is None
