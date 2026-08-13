#!/usr/bin/env bash
# Fetch and load every data source into data/warehouse.duckdb, in sequence.
#
# Run this after cloning the repo onto a new machine (with .venv activated and
# requirements installed) to rebuild the full warehouse from scratch. ESPN
# requires a .env file with ESPN_S2 and SWID if the league is private — see
# .env.example.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m src.ffb.nfl_data
python -m src.ffb.sleeper
python -m src.ffb.espn
python -m src.ffb.fantasypros
