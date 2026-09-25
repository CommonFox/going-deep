/** Fixture data for the kitchen sink and for the app shell's league/week switcher in #127.
 * Shaped exactly like the real files in data/export/ (see src/export/build.py) so #128/#129 swap
 * these for a real fetchManifest()/fetch(<table>/<league>/<season>-<week>.json) without a shape
 * change. Deliberately includes the edge cases the ticket calls out by name: a close call, a slot
 * with no eligible player at all, an unidentified waiver-wire player, and a stale manifest. */

import type { Manifest } from './manifest'

export interface OptimalLineupRow {
  league_key: string
  season: number
  week: number
  slot: string
  player_id: string | null
  player_name: string | null
  projected_points: number | null
  is_close_call: boolean
  bench_player_id: string | null
  bench_player_name: string | null
  bench_projected_points: number | null
}

export interface WaiverRankingRow {
  league_key: string
  season: number
  week: number
  player_id: string | null
  player_name: string
  position: string
  availability: 'free_agent' | 'on_waivers'
  weekly_points: number | null
  weekly_points_source: string | null
  // Both nullable per waiver_rankings.py: ros_points is a LEFT JOIN (no consensus_projections row
  // at all is possible, not just no weekly_stats), and replacement_level_points/
  // starters_at_position come from the same LEFT JOIN'd (league_key, position) row together.
  ros_points: number | null
  replacement_level_points: number | null
  starters_at_position: number | null
}

// Mirrors `weekly_player_context` (src/gold/weekly_player_context.py) column for column, exported
// by #161 with every column intact (src/export/build.py's `_TABLE_SORT_BY`) — same "shaped exactly
// like the real file" reasoning as the rest of this module. Every column but the identity/key ones
// is nullable: every join that builds the table is a LEFT JOIN, on purpose, so a miss stays visible
// rather than being coerced to a default.
export interface WeeklyPlayerContextRow {
  league_key: string
  season: number
  week: number
  player_id: string
  player_name: string
  position: string
  scoring: string
  team: string | null
  sleeper_points: number | null
  espn_points: number | null
  fantasypros_rank_ecr: number | null
  fantasypros_pos_rank: number | null
  num_sources: number
  points_gap: number | null
  points_gap_pct: number | null
  game_opponent: string | null
  game_kickoff: string | null
  game_implied_team_total: number | null
  game_gamescript_lean: string | null
  game_roof: string | null
  game_temp: number | null
  game_wind: number | null
  dvp_points_allowed_per_game_season_to_date: number | null
  dvp_points_allowed_per_game_last3: number | null
  dvp_points_allowed_per_game_last5: number | null
  dvp_games_observed: number | null
  dvp_league_avg_points_allowed: number | null
  dvp_vs_league_avg_ratio: number | null
  dvp_vs_league_avg_zscore: number | null
  dvp_rank: number | null
  role_games_observed: number | null
  role_snap_share: number | null
  role_snap_share_delta: number | null
  role_target_share: number | null
  role_target_share_delta: number | null
  role_air_yards_share: number | null
  role_air_yards_share_delta: number | null
  role_wopr: number | null
  role_wopr_delta: number | null
  role_carries_share: number | null
  role_carries_share_delta: number | null
  role_depth_rank: number | null
  role_depth_rank_delta: number | null
  role_is_starter: boolean | null
  role_is_starter_delta: number | null
  outcome_games_observed: number | null
  outcome_median: number | null
  outcome_floor: number | null
  outcome_ceiling: number | null
  outcome_ceiling_rate: number | null
  outcome_floor_rate: number | null
  outcome_position_ceiling_threshold: number | null
  outcome_position_floor_threshold: number | null
  weekly_position_rank: number | null
  weekly_position_tier: number | null
}

// Mirrors `viewing_guide`/`viewing_guide_starters` (src/gold/viewing_guide.py) — exported by
// #169. `broadcast_window` is one of the five standard windows the gold table names in its own
// docstring, or a raw weekday for the fallback case (a Saturday playoff slate, a Friday
// international game).
export interface ViewingGuideRow {
  league_key: string
  season: number
  week: number
  game_id: string
  home_team: string
  away_team: string
  kickoff: string
  weekday: string
  gametime: string
  broadcast_window: string
  starter_count: number
  total_projected_points: number | null
}

export interface ViewingGuideStarterRow {
  league_key: string
  season: number
  week: number
  game_id: string
  slot: string
  player_id: string
  player_name: string
  team: string
  projected_points: number | null
}

export const fixtureManifest: Manifest = {
  built_at: new Date().toISOString(),
  schema_version: 1,
  available: [
    { table: 'optimal_lineup', league_key: 'sleeper', season: 2026, week: 2 },
    { table: 'optimal_lineup', league_key: 'espn', season: 2026, week: 2 },
    { table: 'waiver_rankings', league_key: 'sleeper', season: 2026, week: 2 },
    { table: 'waiver_rankings', league_key: 'espn', season: 2026, week: 2 },
    { table: 'weekly_player_context', league_key: 'sleeper', season: 2026, week: 2 },
    { table: 'weekly_player_context', league_key: 'espn', season: 2026, week: 2 },
  ],
}

export const staleFixtureManifest: Manifest = {
  ...fixtureManifest,
  built_at: new Date(Date.now() - 30 * 60 * 60 * 1000).toISOString(), // 30h ago, past the 24h rule
}

export const optimalLineupFixture: OptimalLineupRow[] = [
  {
    league_key: 'sleeper',
    season: 2026,
    week: 2,
    slot: 'QB1',
    player_id: '00-0038122',
    player_name: 'C.J. Stroud',
    projected_points: 19.42,
    is_close_call: false,
    bench_player_id: null,
    bench_player_name: null,
    bench_projected_points: null,
  },
  {
    league_key: 'sleeper',
    season: 2026,
    week: 2,
    slot: 'FLEX1',
    player_id: '00-0037664',
    player_name: 'Alec Pierce',
    projected_points: 9.97,
    is_close_call: true,
    bench_player_id: '00-0038606',
    bench_player_name: 'Parker Washington',
    bench_projected_points: 9.04,
  },
  {
    league_key: 'sleeper',
    season: 2026,
    week: 2,
    slot: 'DST1',
    player_id: null,
    player_name: null,
    projected_points: null,
    is_close_call: false,
    bench_player_id: null,
    bench_player_name: null,
    bench_projected_points: null,
  },
]

export const waiverRankingsFixture: WaiverRankingRow[] = [
  {
    league_key: 'sleeper',
    season: 2026,
    week: 2,
    player_id: '00-0023459',
    player_name: 'Aaron Rodgers',
    position: 'QB',
    availability: 'free_agent',
    weekly_points: 16.24,
    weekly_points_source: 'sleeper',
    ros_points: 200.87,
    replacement_level_points: 274.45,
    starters_at_position: 10,
  },
  {
    league_key: 'sleeper',
    season: 2026,
    week: 2,
    player_id: null,
    player_name: 'Unresolved Player',
    position: 'WR',
    availability: 'on_waivers',
    weekly_points: null,
    weekly_points_source: null,
    ros_points: 4.1,
    replacement_level_points: 62.8,
    starters_at_position: 20,
  },
  {
    league_key: 'espn',
    season: 2026,
    week: 2,
    player_id: '00-0024243',
    player_name: 'Marcedes Lewis',
    position: 'TE',
    availability: 'free_agent',
    weekly_points: null,
    weekly_points_source: null,
    ros_points: 8.3,
    replacement_level_points: 149.5,
    starters_at_position: 10,
  },
  {
    league_key: 'espn',
    season: 2026,
    week: 2,
    player_id: null,
    player_name: 'Deep Camp Body',
    position: 'QB',
    availability: 'free_agent',
    weekly_points: null,
    weekly_points_source: null,
    ros_points: null,
    replacement_level_points: null,
    starters_at_position: null,
  },
]

// Same two players as `optimalLineupFixture`'s QB1 and close-call FLEX1, so a local dev looking at
// both fixtures together sees one consistent story. Pierce's row leaves several columns null
// (ESPN, FantasyPros, DvP, role, outcome-rate coverage gaps all happen independently in the real
// warehouse) to exercise the panel's unknown-tone handling without a second, contrived fixture.
export const weeklyPlayerContextFixture: WeeklyPlayerContextRow[] = [
  {
    league_key: 'sleeper',
    season: 2026,
    week: 2,
    player_id: '00-0038122',
    player_name: 'C.J. Stroud',
    position: 'QB',
    scoring: 'half_ppr',
    team: 'HOU',
    sleeper_points: 19.42,
    espn_points: 18.1,
    fantasypros_rank_ecr: 8,
    fantasypros_pos_rank: 3,
    num_sources: 2,
    points_gap: 1.32,
    points_gap_pct: 0.071,
    game_opponent: 'DEN',
    game_kickoff: '2026-09-14T17:00:00Z',
    game_implied_team_total: 24.5,
    game_gamescript_lean: 'favorite',
    game_roof: 'dome',
    game_temp: null,
    game_wind: null,
    dvp_points_allowed_per_game_season_to_date: 21.4,
    dvp_points_allowed_per_game_last3: 23.1,
    dvp_points_allowed_per_game_last5: 22.0,
    dvp_games_observed: 3,
    dvp_league_avg_points_allowed: 19.8,
    dvp_vs_league_avg_ratio: 1.08,
    dvp_vs_league_avg_zscore: 0.62,
    dvp_rank: 9,
    role_games_observed: 3,
    role_snap_share: 0.98,
    role_snap_share_delta: 0.02,
    role_target_share: null,
    role_target_share_delta: null,
    role_air_yards_share: null,
    role_air_yards_share_delta: null,
    role_wopr: null,
    role_wopr_delta: null,
    role_carries_share: 0.12,
    role_carries_share_delta: -0.03,
    role_depth_rank: 1,
    role_depth_rank_delta: 0,
    role_is_starter: true,
    role_is_starter_delta: 0,
    outcome_games_observed: 3,
    outcome_median: 18.9,
    outcome_floor: 12.1,
    outcome_ceiling: 27.3,
    outcome_ceiling_rate: 0.33,
    outcome_floor_rate: 0.1,
    outcome_position_ceiling_threshold: 25.0,
    outcome_position_floor_threshold: 13.0,
    weekly_position_rank: 3,
    weekly_position_tier: 1,
  },
  {
    league_key: 'sleeper',
    season: 2026,
    week: 2,
    player_id: '00-0037664',
    player_name: 'Alec Pierce',
    position: 'WR',
    scoring: 'half_ppr',
    team: 'IND',
    sleeper_points: 9.97,
    espn_points: null,
    fantasypros_rank_ecr: null,
    fantasypros_pos_rank: null,
    num_sources: 1,
    points_gap: null,
    points_gap_pct: null,
    game_opponent: 'JAX',
    game_kickoff: '2026-09-14T17:00:00Z',
    game_implied_team_total: 21.2,
    game_gamescript_lean: 'pick_em',
    game_roof: 'outdoors',
    game_temp: 71,
    game_wind: 6,
    dvp_points_allowed_per_game_season_to_date: null,
    dvp_points_allowed_per_game_last3: null,
    dvp_points_allowed_per_game_last5: null,
    dvp_games_observed: 0,
    dvp_league_avg_points_allowed: null,
    dvp_vs_league_avg_ratio: null,
    dvp_vs_league_avg_zscore: null,
    dvp_rank: null,
    role_games_observed: 3,
    role_snap_share: 0.71,
    role_snap_share_delta: 0.04,
    role_target_share: 0.19,
    role_target_share_delta: -0.02,
    role_air_yards_share: 0.24,
    role_air_yards_share_delta: 0.05,
    role_wopr: null,
    role_wopr_delta: null,
    role_carries_share: 0,
    role_carries_share_delta: 0,
    role_depth_rank: 2,
    role_depth_rank_delta: -1,
    role_is_starter: true,
    role_is_starter_delta: 0,
    outcome_games_observed: 3,
    outcome_median: 8.9,
    outcome_floor: 3.1,
    outcome_ceiling: 15.4,
    outcome_ceiling_rate: null,
    outcome_floor_rate: 0.2,
    outcome_position_ceiling_threshold: 19.0,
    outcome_position_floor_threshold: 5.0,
    weekly_position_rank: 22,
    weekly_position_tier: 2,
  },
]
