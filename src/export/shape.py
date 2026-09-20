"""Shaping a warehouse frame into the rows an export file writes — issue #126, under the
front-end-rebuild epic (#111).

Pure: a frame in, plain-Python-typed rows or grouped frames out. No warehouse read, no file write
— those belong to `src/export/build.py`, the runnable edge that calls these.

## Why values need converting at all

A DataFrame pulled through `src.query.q()` carries pandas/numpy dtypes: `NaN` for a missing value,
`numpy.int64`/`numpy.float64`/`numpy.bool_` for numbers and flags. `json.dumps` chokes on both —
`NaN` serializes to the bare token `NaN`, which is not valid JSON a browser's `JSON.parse` can read
back, and a numpy scalar raises `TypeError: Object of type int64 is not JSON serializable` outright.
Every row is normalized to native Python types (`None` for missing, `int`/`float`/`bool` for
numpy scalars) before it ever reaches `json.dumps`.
"""

import pandas as pd


def _native(value):
    """One cell, normalized to a type `json.dumps` accepts. `pd.isna` catches every missing-value
    spelling pandas uses (`NaN`, `None`, `NaT`) in one check; `np.generic` is the common base class
    every numpy scalar type shares, so `.item()` covers int64/float64/bool_/etc. without needing a
    case per dtype. `pd.Timestamp` (weekly_player_context's `game_kickoff`) needs its own branch:
    it implements no `.item()`, being a pandas type rather than a numpy scalar, so it would
    otherwise fall through to `json.dumps` unconverted and raise.
    """
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def to_json_rows(frame: pd.DataFrame, sort_by: list[str]) -> list[dict]:
    """`frame`'s rows as JSON-ready dicts, sorted by `sort_by`.

    Sorted explicitly rather than left in whatever order DuckDB returned, so re-running the export
    against an unchanged warehouse produces byte-identical files — the acceptance criterion this
    ticket is built around. `kind="stable"` so two rows tied on `sort_by` keep their relative order
    instead of shuffling from one run to the next.
    """
    ordered = frame.sort_values(by=sort_by, kind="stable").reset_index(drop=True)
    return [
        {column: _native(value) for column, value in row.items()}
        for row in ordered.to_dict(orient="records")
    ]


def partition_by_key(frame: pd.DataFrame, key_columns: list[str]) -> dict[tuple, pd.DataFrame]:
    """`frame` split into one group per distinct combination of `key_columns`, keyed by that
    combination as a tuple — the (league_key, season, week) split that decides one export file
    from the next.

    Returned in ascending key order (`groupby(..., sort=True)`), for the same reproducibility
    reason `to_json_rows` sorts within a group: the build loop that calls this writes files, and
    reports on them via `console.archived`, in whatever order this dict iterates.

    Every column stays on each group's frame, key columns included — this ticket exports a table's
    full row shape as already produced by its gold model, not a reshaped or stripped one, so a
    consumer never has to reconstruct a row's own key from the file path it came from.
    """
    return {key: group for key, group in frame.groupby(key_columns, sort=True, dropna=False)}
