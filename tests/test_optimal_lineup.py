"""Combining the roster, the slot-filler and weekly projections into one lineup — issue #89, under
the lineup-optimizer epic (#86).

Every fixture is hand-built and small, in the same spirit as `test_lineup_fill.py` and
`test_my_roster.py`: no warehouse, no DataFrame read from disk, so each expected value can be
checked by hand.
"""

import pandas as pd

from src.gold.optimal_lineup import (
    bench_rows,
    flag_close_calls,
    resolve_player_ids,
    split_by_projection,
)


def roster(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["league_key", "platform_player_id", "player_name", "position"])


def identity(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["player_id", "sleeper_id", "espn_id"])


def roster_ids(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["league_key", "platform_player_id", "player_name", "position", "player_id"],
    )


def projections(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["player_id", "sleeper_points"])


def missing(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["player_name", "position"])


# 1. A Sleeper roster row resolves to the warehouse-wide player_id through draft_board's sleeper_id
#    column, not through its own platform ID directly.
def test_sleeper_roster_resolves_via_sleeper_id():
    resolved = resolve_player_ids(
        roster({"league_key": "sleeper", "platform_player_id": "100", "player_name": "A Back",
                "position": "RB"}),
        identity({"player_id": "00-1111", "sleeper_id": "100", "espn_id": "900"}),
    )

    assert resolved["player_id"].tolist() == ["00-1111"]


# 2. An ESPN roster row resolves the same way, through espn_id instead.
def test_espn_roster_resolves_via_espn_id():
    resolved = resolve_player_ids(
        roster({"league_key": "espn", "platform_player_id": "900", "player_name": "A Back",
                "position": "RB"}),
        identity({"player_id": "00-1111", "sleeper_id": "100", "espn_id": "900"}),
    )

    assert resolved["player_id"].tolist() == ["00-1111"]


# 3. A platform ID the identity crosswalk doesn't cover resolves to a null player_id rather than
#    being dropped — it still needs to reach the "missing a projection" report downstream.
def test_unmatched_platform_id_resolves_to_null_player_id():
    resolved = resolve_player_ids(
        roster({"league_key": "sleeper", "platform_player_id": "999", "player_name": "Deep Bench",
                "position": "WR"}),
        identity({"player_id": "00-1111", "sleeper_id": "100", "espn_id": "900"}),
    )

    assert pd.isna(resolved["player_id"].iloc[0])


# 4. A resolved player with a projection this week becomes a fill_lineup candidate.
def test_player_with_a_projection_becomes_a_candidate():
    candidates, missing = split_by_projection(
        roster_ids({"league_key": "sleeper", "platform_player_id": "100", "player_name": "A Back",
                    "position": "RB", "player_id": "00-1111"}),
        projections({"player_id": "00-1111", "sleeper_points": 12.5}),
    )

    assert candidates == [("00-1111", "RB", 12.5)]
    assert missing.empty


# 5. A resolved player with no weekly_projections row this week (a bye, or a position no weekly
#    source prices) is reported by name, not handed to fill_lineup as a 0.
def test_player_with_no_projection_row_is_reported_not_zeroed():
    candidates, missing = split_by_projection(
        roster_ids({"league_key": "sleeper", "platform_player_id": "100", "player_name": "On Bye",
                    "position": "RB", "player_id": "00-1111"}),
        projections(),
    )

    assert candidates == []
    assert missing["player_name"].tolist() == ["On Bye"]


# 6. A roster row the identity crosswalk never resolved is reported the same way, by the name
#    already carried on the roster — it never had a player_id to look a projection up with.
def test_player_with_unresolved_id_is_reported_not_zeroed():
    candidates, missing = split_by_projection(
        roster_ids({"league_key": "sleeper", "platform_player_id": "999",
                    "player_name": "Deep Bench", "position": "WR", "player_id": None}),
        projections(),
    )

    assert candidates == []
    assert missing["player_name"].tolist() == ["Deep Bench"]


# 7. A starter with no bench player eligible for his slot at all is never a close call.
def test_no_bench_alternative_is_not_a_close_call():
    rows = flag_close_calls({"RB1": "rb1"}, [("rb1", "RB", 20.0)])

    row = rows.iloc[0]
    assert not row["is_close_call"]
    assert pd.isna(row["bench_player_id"])


# 8. A benched player within the margin (a field goal's worth of points) is a close call, and both
#    players are named on the row.
def test_bench_player_within_margin_flags_close_call_naming_both():
    rows = flag_close_calls(
        {"RB1": "rb1"}, [("rb1", "RB", 20.0), ("rb2", "RB", 18.0)],
    )

    row = rows.iloc[0]
    assert row["is_close_call"]
    assert row["bench_player_id"] == "rb2"
    assert row["bench_projected_points"] == 18.0


# 9. A benched player beyond the margin is not flagged.
def test_bench_player_beyond_margin_is_not_flagged():
    rows = flag_close_calls(
        {"RB1": "rb1"}, [("rb1", "RB", 20.0), ("rb2", "RB", 16.0)],
    )

    assert not rows.iloc[0]["is_close_call"]


# 10. A FLEX slot's bench comparison pool is RB/WR/TE leftover only — a benched QB doesn't count,
#     mirroring fill_lineup's own FLEX eligibility.
def test_flex_bench_pool_excludes_non_flex_positions():
    rows = flag_close_calls(
        {"FLEX1": "rb1"}, [("rb1", "RB", 10.0), ("qb1", "QB", 9.0)],
    )

    assert not rows.iloc[0]["is_close_call"]


# 11. A SUPERFLEX slot's bench pool includes leftover QB, mirroring fill_lineup's own superflex
#     eligibility.
def test_superflex_bench_pool_includes_leftover_qb():
    rows = flag_close_calls(
        {"SUPERFLEX1": "qb1"}, [("qb1", "QB", 25.0), ("qb2", "QB", 23.0)],
    )

    row = rows.iloc[0]
    assert row["is_close_call"]
    assert row["bench_player_id"] == "qb2"


# 12. A slot fill_lineup left empty (too few eligible players) is never a close call — nothing to
#     compare a bench player against.
def test_empty_slot_is_never_a_close_call():
    rows = flag_close_calls({"WR1": None}, [("rb1", "RB", 10.0)])

    row = rows.iloc[0]
    assert not row["is_close_call"]
    assert row["player_id"] is None


# 13. A candidate fill_lineup started is never listed on the bench — the same `assignment` that
#     seats him is what excludes him here.
def test_started_candidate_is_not_on_the_bench():
    rows = bench_rows(
        [("rb1", "RB", 20.0)], {"RB1": "rb1"}, missing(), {"rb1": "A Back"},
    )

    assert rows.empty


# 14. A candidate fill_lineup passed over shows up on the bench with his own projected points, so
#     "by how much" is visible next to a starter at the same position.
def test_unstarted_candidate_is_on_the_bench_with_his_points():
    rows = bench_rows(
        [("rb1", "RB", 20.0), ("rb2", "RB", 14.0)], {"RB1": "rb1"}, missing(),
        {"rb1": "A Back", "rb2": "Backup Back"},
    )

    row = rows.iloc[0]
    assert row["player_id"] == "rb2"
    assert row["player_name"] == "Backup Back"
    assert row["position"] == "RB"
    assert row["projected_points"] == 14.0


# 15. A roster player `split_by_projection` pulled out for missing a projection lands on the bench
#     too, with a null `projected_points` rather than vanishing or reading as a real 0.
def test_missing_projection_player_is_on_the_bench_with_null_points():
    rows = bench_rows(
        [], {}, missing({"player_name": "On Bye", "position": "RB"}), {},
    )

    row = rows.iloc[0]
    assert row["player_id"] is None
    assert row["player_name"] == "On Bye"
    assert row["position"] == "RB"
    assert pd.isna(row["projected_points"])
