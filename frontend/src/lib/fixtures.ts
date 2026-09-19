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

export const fixtureManifest: Manifest = {
  built_at: new Date().toISOString(),
  schema_version: 1,
  available: [
    { table: 'optimal_lineup', league_key: 'sleeper', season: 2026, week: 2 },
    { table: 'optimal_lineup', league_key: 'espn', season: 2026, week: 2 },
    { table: 'waiver_rankings', league_key: 'sleeper', season: 2026, week: 2 },
    { table: 'waiver_rankings', league_key: 'espn', season: 2026, week: 2 },
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
