"""The export manifest — issue #126, under the front-end-rebuild epic (#111).

Per the #125 spike's freshness-contract answer: a `built_at` timestamp the SPA fetches with
`cache: 'no-store'`, a `schema_version` it can refuse to render against a mismatch, and an
`available` list so it never has to probe with a failed fetch to know which (table, league,
season, week) combinations actually have a file. `build_manifest` is pure — the runnable edge in
`src/export/build.py` is what decides `built_at` and walks the warehouse to build `available`.
"""

from typing import Any


def build_manifest(
    available: list[dict[str, Any]], schema_version: int, built_at: str
) -> dict[str, Any]:
    """The manifest document for `available`, `schema_version` and `built_at` as given.

    `available` is sorted by (table, league_key, season, week) regardless of the order the build
    loop happened to write files in, and each entry's `season`/`week` are coerced to plain `int` —
    the same "no numpy scalar reaches json.dumps" rule `to_json_rows` applies to every other export
    file, applied here too so the manifest can't be the one file that breaks the rule.
    """
    normalized = [
        {**entry, "season": int(entry["season"]), "week": int(entry["week"])}
        for entry in available
    ]
    normalized.sort(key=lambda entry: (entry["table"], entry["league_key"], entry["season"], entry["week"]))

    return {
        "built_at": built_at,
        "schema_version": schema_version,
        "available": normalized,
    }
