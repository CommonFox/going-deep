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

# Depends on every source above already being loaded (joins their projections through the
# nflverse `ids` crosswalk from nfl_data.py).
python -m src.gold.consensus
