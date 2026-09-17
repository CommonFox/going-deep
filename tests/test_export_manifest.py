"""The export manifest — issue #126, under the front-end-rebuild epic (#111).

`build_manifest` is pure: given the list of (table, league_key, season, week) combinations the
build actually wrote and a build timestamp, it returns the JSON-ready manifest document. No
warehouse, no file I/O — those belong to the runnable edge in `src/export/build.py`.
"""

import numpy as np

from src.export.manifest import build_manifest


# 7. schema_version and built_at pass through unchanged — the manifest doesn't own either value,
#    it just carries what the build tells it.
def test_schema_version_and_built_at_pass_through_unchanged():
    manifest = build_manifest(available=[], schema_version=3, built_at="2026-09-17T12:00:00+00:00")

    assert manifest["schema_version"] == 3
    assert manifest["built_at"] == "2026-09-17T12:00:00+00:00"


# 8. One `available` entry per combination passed in, with season/week coming out as plain ints
#    even if a numpy scalar slipped through from a DataFrame upstream — a numpy.int64 in the
#    manifest would make `json.dumps` raise the same way it would in a per-table export file.
def test_available_has_one_entry_per_combination_with_native_int_types():
    manifest = build_manifest(
        available=[
            {"table": "optimal_lineup", "league_key": "sleeper", "season": np.int64(2026), "week": np.int64(2)},
        ],
        schema_version=1,
        built_at="2026-09-17T12:00:00+00:00",
    )

    entry = manifest["available"][0]
    assert entry == {"table": "optimal_lineup", "league_key": "sleeper", "season": 2026, "week": 2}
    assert type(entry["season"]) is int
    assert type(entry["week"]) is int


# 9. `available` is sorted by (table, league_key, season, week) regardless of input order, so the
#    manifest is byte-identical across reruns even if the build loop's own iteration order ever
#    changes.
def test_available_is_sorted_by_table_league_season_week():
    manifest = build_manifest(
        available=[
            {"table": "waiver_rankings", "league_key": "sleeper", "season": 2026, "week": 2},
            {"table": "optimal_lineup", "league_key": "espn", "season": 2026, "week": 3},
            {"table": "optimal_lineup", "league_key": "espn", "season": 2026, "week": 2},
        ],
        schema_version=1,
        built_at="2026-09-17T12:00:00+00:00",
    )

    ordered = [(e["table"], e["league_key"], e["season"], e["week"]) for e in manifest["available"]]
    assert ordered == [
        ("optimal_lineup", "espn", 2026, 2),
        ("optimal_lineup", "espn", 2026, 3),
        ("waiver_rankings", "sleeper", 2026, 2),
    ]
