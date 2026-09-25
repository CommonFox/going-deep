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
run src.gold.game_environment
# free_agents and my_roster each need only silver tables (sleeper_users/sleeper_rosters/
# sleeper_players, espn_teams/espn_player_ownership) — neither has a gold dependency, but both
# live in gold as derived views rather than a raw load.
run src.gold.free_agents
run src.gold.my_roster

# Second tier — inhouse_projections needs every first-tier table except league_settings
# (player_weighted_baselines, the two grade tables, player_depth_chart, and adp_consensus as its
# backtest benchmark); points_over_replacement needs league_settings for per-league scoring, and
# punters needs it for the punt scoring no other league prices. defense_vs_position needs only
# weekly_stats and league_settings, same shape as points_over_replacement. player_role_trend needs
# weekly_stats/snap_counts/ids (silver) plus player_depth_chart, so it has to run after depth_charts
# but is otherwise independent of every other second-tier table. weekly_outcome_rates needs only
# weekly_stats and league_settings, the same two inputs as defense_vs_position.
run src.gold.inhouse_projections
run src.gold.points_over_replacement
run src.gold.defense_vs_position
run src.gold.player_role_trend
run src.gold.weekly_outcome_rates
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
# out who survives to each seat's picks and what opening plan is worth from there. weekly_projections
# needs draft_board's sleeper_id map to resolve identity, so it runs right after. ros_points needs
# draft_board's projected_points_adjusted and sleeper_nfl_state's current week to net out points
# already scored. waiver_rankings joins free_agents onto weekly_projections/ros_points through
# draft_board's identity crosswalk; drop_candidates (#171) is the other half of that same board,
# joining my_roster through the identical crosswalk onto ros_points, draft_board's replacement
# level, player_role_trend (second tier) and injuries (silver) instead. optimal_lineup needs
# my_roster, weekly_projections and that same crosswalk, so it runs after both exist.
run src.gold.draft_board
run src.gold.draft_plan
run src.gold.weekly_projections
run src.gold.ros_points
run src.gold.waiver_rankings
run src.gold.drop_candidates
run src.gold.optimal_lineup

# Sixth tier — weekly_player_context (#124) joins together every table the in-season epic (#108)
# built: game_environment, defense_vs_position, player_role_trend and weekly_outcome_rates from
# the second tier, league_settings from the first, plus draft_board and weekly_projections from
# the fifth.
run src.gold.weekly_player_context

# Seventh tier — viewing_guide (#168) needs optimal_lineup (fifth tier), weekly_player_context
# (sixth tier, for a starter's current-week team) and game_environment (first tier), so it runs
# last among the gold tables.
run src.gold.viewing_guide

echo "── export ────────────────────────────────────────────────"
# Reads the warehouse this build just finished writing and writes the JSON the SPA (#111) fetches
# statically. Runs last among the data-producing steps so the export can never drift from a
# gold table that hadn't landed yet.
run src.export.build

echo "── warehouse ─────────────────────────────────────────────"
# Brief: every table was named as it was written. `python -m src.summary` lists them all.
python -m src.summary --brief
printf '\nbuilt in %dm%02ds\n' $(((SECONDS - BUILD_START) / 60)) $(((SECONDS - BUILD_START) % 60))
