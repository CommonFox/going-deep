"""Build a consensus ADP table blending FantasyPros' and FantasyFootballCalculator's historical
average draft position onto one shared `gsis_id`.

Pure warehouse-to-warehouse SQL/Python — no fetch step, no network. Built from `fantasypros_adp`
and `ffc_adp` (already loaded by fantasypros.py's load_adp_manual / fantasyfootballcalculator.py),
resolved onto the nflverse player crosswalk (`ids`, loaded by nfl_data.py) via the `merge_name`
normalization in players.py, since neither ADP source carries a platform ID `ids` already knows
the way ESPN/Sleeper/CBS projections do in consensus.py.

Scoped to draftable player positions (QB/RB/WR/TE/K) — team defenses aren't in the player
crosswalk at all (see consensus.py's DST split) and are resolved on team abbreviation by whatever
model needs them.

## `format` is a roster format, not a scoring one

Every row exists twice, once per **format**, and the format decides what `consensus_adp` means:

- `'1qb'` — one quarterback starts. FantasyPros' blended ADP averaged with FFC's PPR ADP.
- `'superflex'` — a second quarterback can start. FFC's `2qb` board alone, since FantasyPros
  publishes no superflex ADP.

This is the axis that matters most and the one most easily missed, because it is not about
points: a superflex board prices Josh Allen at 1.7 overall where the 1QB boards have him outside
the top 60. Nothing about Allen changed — the number of starting quarterback jobs did. A model
that reads the wrong format's `consensus_adp` is not slightly off, it is reading another league's
price sheet, so `draft_strategy.variant` joins against this column by name.

Note that silver stores `2qb` under `scoring_format`, mirroring FFC's endpoint. That is the wrong
axis and the translation to `format` happens here, on purpose: silver mirrors the source, gold
says what things are.

## One vote per source

Each source counts once regardless of how many formats it offers: FantasyPros contributes its
single blended (format-unlabeled) ADP, and FFC contributes the format-appropriate board. FFC's
other formats stay exposed as their own columns for reference but are excluded from
`consensus_adp`, so a single site's several formats can't outvote the other site's one number.

`adp_stdev`, `adp_high` and `adp_low` come from whichever FFC board feeds that row's format —
they describe how tightly the market agrees, which is what makes "who survives to my next pick"
answerable rather than guesswork.
"""

from pathlib import Path

import duckdb

from src import console
from src.silver.players import merge_name

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# Kickers included, unlike the DST split above: `ids` carries them (labelled "PK", normalized to
# "K" below) and a fifteen-round draft has to spend a pick on one.
_SKILL_POSITIONS = ("QB", "RB", "WR", "TE", "K")

# gold's `format` <- the FFC board that prices it. FantasyPros has no superflex board, so the
# superflex rows are single-source by necessity rather than by choice.
_FORMATS = {"1qb": "ppr", "superflex": "2qb"}

# FFC labels kickers "PK" (so does `ids`, which is normalized to "K" above), so both ADP sources
# get the same normalization before the join or every kicker silently fails to resolve.
_POSITION_SQL = "CASE WHEN {col} = 'PK' THEN 'K' ELSE {col} END"


def _format_row(format_name: str, board: str) -> str:
    """One format's projection of the joined sources: which board becomes `consensus_adp`."""
    if format_name == "1qb":
        consensus = """CASE
            WHEN fantasypros_adp IS NOT NULL AND ffc_adp_ppr IS NOT NULL
                THEN (fantasypros_adp + ffc_adp_ppr) / 2
            ELSE COALESCE(fantasypros_adp, ffc_adp_ppr)
        END"""
        num_sources = """(CASE WHEN fantasypros_adp IS NOT NULL THEN 1 ELSE 0 END)
            + (CASE WHEN ffc_adp_ppr IS NOT NULL THEN 1 ELSE 0 END)"""
    else:
        consensus = "ffc_adp_2qb"
        num_sources = "CASE WHEN ffc_adp_2qb IS NOT NULL THEN 1 ELSE 0 END"

    return f"""
SELECT
    gsis_id, player_name, position, season,
    '{format_name}' AS format,
    fantasypros_adp, ffc_adp_standard, ffc_adp_half_ppr, ffc_adp_ppr, ffc_adp_2qb,
    {consensus} AS consensus_adp,
    {num_sources} AS num_sources,
    ffc_stdev_{board} AS adp_stdev,
    ffc_high_{board} AS adp_high,
    ffc_low_{board} AS adp_low
FROM combined
"""


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
        ON ids.merge_name = to_merge_name(fp.name)
        AND ids.position = {_POSITION_SQL.format(col="fp.position")}
    WHERE {_POSITION_SQL.format(col="fp.position")} IN {_SKILL_POSITIONS}
),
ffc_resolved AS (
    SELECT
        ids.gsis_id, ids.name, ids.position, f.season,
        MAX(f.adp) FILTER (WHERE f.scoring_format = 'standard') AS ffc_adp_standard,
        MAX(f.adp) FILTER (WHERE f.scoring_format = 'half-ppr') AS ffc_adp_half_ppr,
        MAX(f.adp) FILTER (WHERE f.scoring_format = 'ppr') AS ffc_adp_ppr,
        MAX(f.adp) FILTER (WHERE f.scoring_format = '2qb') AS ffc_adp_2qb,
        MAX(f.stdev) FILTER (WHERE f.scoring_format = 'ppr') AS ffc_stdev_ppr,
        MAX(f.high) FILTER (WHERE f.scoring_format = 'ppr') AS ffc_high_ppr,
        MAX(f.low) FILTER (WHERE f.scoring_format = 'ppr') AS ffc_low_ppr,
        MAX(f.stdev) FILTER (WHERE f.scoring_format = '2qb') AS ffc_stdev_2qb,
        MAX(f.high) FILTER (WHERE f.scoring_format = '2qb') AS ffc_high_2qb,
        MAX(f.low) FILTER (WHERE f.scoring_format = '2qb') AS ffc_low_2qb
    FROM ffc_adp f
    JOIN ids_normalized ids
        ON ids.merge_name = to_merge_name(f.name)
        AND ids.position = {_POSITION_SQL.format(col="f.position")}
    WHERE {_POSITION_SQL.format(col="f.position")} IN {_SKILL_POSITIONS}
    GROUP BY ids.gsis_id, ids.name, ids.position, f.season
),
combined AS (
    SELECT
        COALESCE(fp.gsis_id, ffc.gsis_id) AS gsis_id,
        COALESCE(fp.name, ffc.name) AS player_name,
        COALESCE(fp.position, ffc.position) AS position,
        COALESCE(fp.season, ffc.season) AS season,
        fp.fantasypros_adp,
        ffc.* EXCLUDE (gsis_id, name, position, season)
    FROM fp_resolved fp
    FULL OUTER JOIN ffc_resolved ffc ON fp.gsis_id = ffc.gsis_id AND fp.season = ffc.season
)
SELECT * FROM (
{"    UNION ALL".join(_format_row(name, board) for name, board in _FORMATS.items())}
)
-- A player with no board for a format gets no row for it, rather than a row whose price is NULL.
WHERE consensus_adp IS NOT NULL
"""


def build_adp_consensus() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("to_merge_name", merge_name, ["VARCHAR"], "VARCHAR")

    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM adp_consensus").fetchone()
    con.close()

    console.table("adp_consensus", count)


if __name__ == "__main__":
    build_adp_consensus()
