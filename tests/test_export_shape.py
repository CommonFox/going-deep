"""Shaping a warehouse frame into JSON-ready rows — issue #126, under the front-end-rebuild epic
(#111).

Every fixture is hand-built and small, in the same spirit as `test_optimal_lineup.py`: no
warehouse, no DataFrame read from disk, so each expected value can be checked by hand.
"""

import json

import numpy as np
import pandas as pd

from src.export.shape import partition_by_key, to_json_rows


# 1. A NaN in the frame becomes JSON null, not the bare token `NaN` — `json.dumps` would emit that
#    literally, and it isn't valid JSON for a browser's `JSON.parse` to read back.
def test_nan_becomes_json_null():
    frame = pd.DataFrame({"player_id": ["00-1111"], "projected_points": [np.nan]})

    rows = to_json_rows(frame, sort_by=["player_id"])

    assert rows[0]["projected_points"] is None
    assert "NaN" not in json.dumps(rows)


# 2. A numpy scalar dtype (int64, float64, bool_) is converted to a native Python type, so
#    `json.dumps` doesn't raise `TypeError: Object of type int64 is not JSON serializable`.
def test_numpy_scalars_become_native_python_types():
    frame = pd.DataFrame({
        "season": pd.array([2026], dtype="int64"),
        "projected_points": pd.array([12.5], dtype="float64"),
        "is_close_call": pd.array([True], dtype="bool"),
    })

    rows = to_json_rows(frame, sort_by=["season"])
    row = rows[0]

    assert type(row["season"]) is int
    assert type(row["projected_points"]) is float
    assert type(row["is_close_call"]) is bool
    json.dumps(rows)  # must not raise


# 3. Rows come out sorted by the given columns regardless of the input frame's row order, so the
#    same table produces the same row order on every rebuild rather than whatever DuckDB happened
#    to return this time.
def test_rows_are_sorted_by_sort_by_columns_regardless_of_input_order():
    frame = pd.DataFrame({"slot": ["WR1", "QB", "RB1"], "player_id": ["c", "a", "b"]})

    rows = to_json_rows(frame, sort_by=["slot"])

    assert [row["slot"] for row in rows] == ["QB", "RB1", "WR1"]


# 4. Grouping splits a frame into one group per distinct combination of the key columns.
def test_partition_splits_into_one_group_per_distinct_key():
    frame = pd.DataFrame({
        "league_key": ["sleeper", "sleeper", "espn"],
        "season": [2026, 2026, 2026],
        "week": [2, 2, 2],
        "player_id": ["a", "b", "c"],
    })

    groups = partition_by_key(frame, key_columns=["league_key", "season", "week"])

    assert set(groups.keys()) == {("sleeper", 2026, 2), ("espn", 2026, 2)}
    assert len(groups[("sleeper", 2026, 2)]) == 2
    assert len(groups[("espn", 2026, 2)]) == 1


# 5. Groups come out in ascending key order regardless of the input frame's row order — the same
#    reproducibility guarantee `to_json_rows` gives within a group, at the group level, so the
#    build's own file-write order (and therefore the manifest's `available` order) doesn't depend
#    on DuckDB's row order either.
def test_partition_returns_groups_in_ascending_key_order():
    frame = pd.DataFrame({
        "league_key": ["sleeper", "espn", "sleeper"],
        "season": [2026, 2026, 2026],
        "week": [3, 2, 2],
        "player_id": ["a", "b", "c"],
    })

    groups = partition_by_key(frame, key_columns=["league_key", "season", "week"])

    assert list(groups.keys()) == [("espn", 2026, 2), ("sleeper", 2026, 2), ("sleeper", 2026, 3)]


# 6. Every column, including the key columns themselves, stays on each group's frame — this ticket
#    exports each table's full row shape as already produced by its gold model, not a reshaped or
#    stripped-down one.
def test_partition_keeps_every_column_including_the_key_columns():
    frame = pd.DataFrame({
        "league_key": ["sleeper"], "season": [2026], "week": [2], "player_id": ["a"],
    })

    groups = partition_by_key(frame, key_columns=["league_key", "season", "week"])
    group = groups[("sleeper", 2026, 2)]

    assert list(group.columns) == ["league_key", "season", "week", "player_id"]
