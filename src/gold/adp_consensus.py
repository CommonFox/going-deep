"""Build a consensus ADP table blending FantasyPros' and FantasyFootballCalculator's historical
average draft position onto one shared `gsis_id`.

Pure warehouse-to-warehouse SQL/Python — no fetch step, no network. Built from `fantasypros_adp`
and `ffc_adp` (already loaded by fantasypros.py's load_adp_manual / fantasyfootballcalculator.py),
resolved onto the nflverse player crosswalk (`ids`, loaded by nfl_data.py) via the `merge_name`
normalization in players.py, since neither ADP source carries a platform ID `ids` already knows
the way ESPN/Sleeper/CBS projections do in consensus.py.

Scoped to skill positions (QB/RB/WR/TE) only — team defenses aren't in the player crosswalk at
all (see consensus.py's DST split), and aren't relevant to the RB-focused work this table exists
for.

Each source counts as one vote regardless of how many scoring formats it offers: FantasyPros
contributes its single blended (format-unlabeled) ADP, and FFC contributes its PPR-format ADP
specifically (the most complete FFC format across all seasons — half-PPR has no data before
2018). FFC's standard/half-PPR numbers are still exposed as their own columns for reference, but
excluded from `consensus_adp` so a single site's multiple formats can't outvote the other site's
one blended number.
"""

from pathlib import Path

import duckdb

from src.silver.players import merge_name

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

_BUILD_SQL = f"""
CREATE OR REPLACE TABLE adp_consensus AS
WITH ids_normalized AS (
    SELECT gsis_id, name, merge_name,
           CASE WHEN position = 'PK' THEN 'K' ELSE position END AS position
    FROM ids
),
fp_resolved AS (
    SELECT ids.gsis_id, ids.name, ids.position, fp.season, fp.adp AS fantasypros_adp
    FROM fantasypros_adp fp
    JOIN ids_normalized ids
        ON ids.merge_name = to_merge_name(fp.name) AND ids.position = fp.position
    WHERE fp.position IN {_SKILL_POSITIONS}
),
ffc_resolved AS (
    SELECT
        ids.gsis_id, ids.name, ids.position, f.season,
        MAX(CASE WHEN f.scoring_format = 'standard' THEN f.adp END) AS ffc_adp_standard,
        MAX(CASE WHEN f.scoring_format = 'half-ppr' THEN f.adp END) AS ffc_adp_half_ppr,
        MAX(CASE WHEN f.scoring_format = 'ppr' THEN f.adp END) AS ffc_adp_ppr
    FROM ffc_adp f
    JOIN ids_normalized ids
        ON ids.merge_name = to_merge_name(f.name) AND ids.position = f.position
    WHERE f.position IN {_SKILL_POSITIONS}
    GROUP BY ids.gsis_id, ids.name, ids.position, f.season
)
SELECT
    COALESCE(fp.gsis_id, ffc.gsis_id) AS gsis_id,
    COALESCE(fp.name, ffc.name) AS player_name,
    COALESCE(fp.position, ffc.position) AS position,
    COALESCE(fp.season, ffc.season) AS season,
    fp.fantasypros_adp,
    ffc.ffc_adp_standard,
    ffc.ffc_adp_half_ppr,
    ffc.ffc_adp_ppr,
    CASE
        WHEN fp.fantasypros_adp IS NOT NULL AND ffc.ffc_adp_ppr IS NOT NULL
            THEN (fp.fantasypros_adp + ffc.ffc_adp_ppr) / 2
        ELSE COALESCE(fp.fantasypros_adp, ffc.ffc_adp_ppr)
    END AS consensus_adp,
    (CASE WHEN fp.fantasypros_adp IS NOT NULL THEN 1 ELSE 0 END)
        + (CASE WHEN ffc.ffc_adp_ppr IS NOT NULL THEN 1 ELSE 0 END) AS num_sources
FROM fp_resolved fp
FULL OUTER JOIN ffc_resolved ffc ON fp.gsis_id = ffc.gsis_id AND fp.season = ffc.season
"""


def build_adp_consensus() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("to_merge_name", merge_name, ["VARCHAR"], "VARCHAR")

    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM adp_consensus").fetchone()
    con.close()

    print(f"Built {count} rows into {WAREHOUSE_PATH} (table: adp_consensus)")


if __name__ == "__main__":
    build_adp_consensus()
