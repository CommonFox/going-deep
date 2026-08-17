#!/usr/bin/env bash
# Fetch and load every data source into data/warehouse.duckdb, in sequence.
#
# Run this after cloning the repo onto a new machine (with .venv activated and
# requirements installed) to rebuild the full warehouse from scratch. ESPN
# requires a .env file with ESPN_S2 and SWID if the league is private — see
# .env.example.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m src.silver.nfl_data
python -m src.silver.sleeper
python -m src.silver.espn
python -m src.silver.fantasypros
python -m src.silver.fantasyfootballcalculator
python -m src.silver.fftoday
python -m src.silver.cbs

# Gold layer, in dependency order.
#
# First tier — pure transforms of a silver table, depending on nothing else in gold:
python -m src.gold.offensive_line
python -m src.gold.skill_position_grades
python -m src.gold.player_baselines
python -m src.gold.depth_charts
python -m src.gold.adp_consensus
python -m src.gold.league_settings

# Second tier — inhouse_projections needs every first-tier table except league_settings
# (player_weighted_baselines, the two grade tables, player_depth_chart, and adp_consensus as its
# backtest benchmark); points_over_replacement needs league_settings for per-league scoring.
python -m src.gold.inhouse_projections
python -m src.gold.points_over_replacement

# Third tier — consensus blends every external source plus inhouse_projections; boom_bust and
# breakout_candidates each read a second-tier table alongside adp_consensus.
python -m src.gold.consensus
python -m src.gold.boom_bust
python -m src.gold.breakout_candidates

# Fourth tier — draft_value prices points_over_replacement against adp_consensus using
# inhouse_projections for its forward-looking half; player_archetypes then reads boom_bust for the
# elite-finish history and outcome buckets and draft_value for the ADP-adjusted edge, so it has to
# come last.
python -m src.gold.draft_value
python -m src.gold.player_archetypes
