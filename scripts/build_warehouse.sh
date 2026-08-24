#!/usr/bin/env bash
# Fetch and load every data source into data/warehouse.duckdb, in sequence.
#
# Run this after cloning the repo onto a new machine (with .venv activated and
# requirements installed) to rebuild the full warehouse from scratch. ESPN
# requires a .env file with ESPN_S2 and SWID if the league is private — see
# .env.example.
#
# Usage: build_warehouse.sh [-v|--verbose]
#
# The job here is to refresh the data and report where the build got to, so each
# module prints one line per table and nothing else. The gold layer's model
# reports — backtest folds, quantile calibration, archetype edges, the live
# draft board — are suppressed via GOING_DEEP_QUIET; pass --verbose to see them,
# or run the one module you care about directly (python -m src.gold.draft_value),
# which always prints in full.
set -euo pipefail
cd "$(dirname "$0")/.."

export GOING_DEEP_QUIET=1
for arg in "$@"; do
    case "$arg" in
        -v|--verbose) unset GOING_DEEP_QUIET ;;
        *) echo "usage: $0 [-v|--verbose]" >&2; exit 2 ;;
    esac
done

BUILD_START=$SECONDS

# Print a step's name, run it, then report how long it took. Timing is the other
# half of "where we're at": nfl_data takes minutes and the gold layer seconds, so
# a step running long is the first sign something is stuck rather than slow.
run() {
    local module=$1
    local start=$SECONDS
    echo "$module"
    python -m "$module"
    printf '  %s\n\n' "└─ $((SECONDS - start))s"
}

echo "── silver ────────────────────────────────────────────────"
run src.silver.nfl_data
run src.silver.sleeper
run src.silver.espn
run src.silver.fantasypros
run src.silver.fantasyfootballcalculator
run src.silver.fftoday
run src.silver.cbs

echo "── gold ──────────────────────────────────────────────────"
# First tier — pure transforms of a silver table, depending on nothing else in gold:
run src.gold.offensive_line
run src.gold.skill_position_grades
run src.gold.player_baselines
run src.gold.depth_charts
run src.gold.adp_consensus
run src.gold.league_settings

# Second tier — inhouse_projections needs every first-tier table except league_settings
# (player_weighted_baselines, the two grade tables, player_depth_chart, and adp_consensus as its
# backtest benchmark); points_over_replacement needs league_settings for per-league scoring, and
# punters needs it for the punt scoring no other league prices.
run src.gold.inhouse_projections
run src.gold.points_over_replacement
run src.gold.punters
run src.gold.punt_environment

# Third tier — consensus blends every external source plus inhouse_projections; boom_bust and
# breakout_candidates each read a second-tier table alongside adp_consensus.
run src.gold.consensus
run src.gold.boom_bust
run src.gold.breakout_candidates
# draft_strategy simulates drafts off adp_consensus and scores them with points_over_replacement,
# so it needs nothing from the fourth tier — it prices a roster plan, not a player.
run src.gold.draft_strategy

# Fourth tier — draft_value prices points_over_replacement against adp_consensus using
# inhouse_projections for its forward-looking half; player_archetypes then reads boom_bust for the
# elite-finish history and outcome buckets and draft_value for the ADP-adjusted edge, so it has to
# come last.
run src.gold.draft_value
run src.gold.player_archetypes

# Fifth tier — the draft-night tables. draft_board reprices consensus_projections in each league's
# own scoring and slots for the season about to be played; draft_plan then reads that board to work
# out who survives to each seat's picks and what opening plan is worth from there.
run src.gold.draft_board
run src.gold.draft_plan

echo "── warehouse ─────────────────────────────────────────────"
# Brief: every table was named as it was written. `python -m src.summary` lists them all.
python -m src.summary --brief
printf '\nbuilt in %dm%02ds\n' $(((SECONDS - BUILD_START) / 60)) $(((SECONDS - BUILD_START) % 60))
