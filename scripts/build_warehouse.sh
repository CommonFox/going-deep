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
python -m src.silver.fftoday
python -m src.silver.cbs

# Gold layer, in dependency order: the team-context grades and player baseline are pure
# transforms of nfl_data.py's tables; inhouse_projections depends on all three of those; consensus
# depends on every source above (external sites joined through the nflverse `ids` crosswalk,
# inhouse_projections joined directly since it's already keyed on that same ID space).
python -m src.gold.offensive_line
python -m src.gold.skill_position_grades
python -m src.gold.player_baselines
python -m src.gold.inhouse_projections
python -m src.gold.consensus
