/** Test cases enumerated before `playerDetail.ts` was written, per #161:
 *
 * 1. A fully populated `weekly_player_context` row: every field across all four sections renders
 *    a formatted value, none carry `tone: 'unknown'`.
 * 2. No row at all (`undefined` — the "player has no weekly_player_context row" case #161's
 *    acceptance names explicitly): every field across all four sections is `'—'`/`tone: 'unknown'`.
 * 3. A row with specific columns nulled (partial coverage — the other acceptance case): only
 *    those fields go unknown; sibling fields, in the same section and others, still render.
 * 4. A zero-valued share (`role_snap_share: 0`) renders as a real `0%`, not unknown — `0` is falsy
 *    in JS, so a truthiness check instead of `== null` would misclassify it.
 * 5. `role_is_starter: false` renders `'No'`, not unknown — same falsy-vs-null trap as above, on a
 *    boolean this time.
 * 6. Signed deltas: a positive delta gets an explicit leading `+`, a negative one shows a single
 *    leading `-` (no double sign from string-concatenating onto an already-negative number).
 * 7. Every `game_gamescript_lean` bucket maps to its humanized label, and a value outside the known
 *    buckets passes through unchanged rather than throwing or going blank.
 */

import { describe, expect, it } from 'vitest'
import { buildDetailSections } from './playerDetail'
import type { WeeklyPlayerContextRow } from './fixtures'

const FULL_ROW: WeeklyPlayerContextRow = {
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
  role_target_share: 0.05,
  role_target_share_delta: -0.01,
  role_air_yards_share: 0.03,
  role_air_yards_share_delta: 0.01,
  role_wopr: 0.15,
  role_wopr_delta: 0.02,
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
}

function allFields(sections: ReturnType<typeof buildDetailSections>) {
  return sections.flatMap((section) => section.fields)
}

describe('buildDetailSections', () => {
  it('renders every field from a fully populated row with no unknown tone', () => {
    const sections = buildDetailSections(FULL_ROW)
    expect(sections).toHaveLength(4)
    expect(sections.map((s) => s.title)).toEqual([
      'Projections',
      'Matchup & environment',
      'Role trend',
      'Outcome rates',
    ])
    for (const field of allFields(sections)) {
      expect(field.tone).not.toBe('unknown')
      expect(field.value).not.toBe('—')
    }
  })

  it('renders every field as unknown when the player has no weekly_player_context row at all', () => {
    const sections = buildDetailSections(undefined)
    for (const field of allFields(sections)) {
      expect(field.tone).toBe('unknown')
      expect(field.value).toBe('—')
    }
  })

  it('marks only the nulled columns unknown, leaving sibling fields untouched', () => {
    const row: WeeklyPlayerContextRow = {
      ...FULL_ROW,
      espn_points: null,
      fantasypros_pos_rank: null,
      dvp_vs_league_avg_zscore: null,
      outcome_ceiling_rate: null,
    }
    const sections = buildDetailSections(row)
    const byLabel = new Map(allFields(sections).map((f) => [f.label, f]))

    expect(byLabel.get('ESPN')?.tone).toBe('unknown')
    expect(byLabel.get('FantasyPros')?.tone).toBe('unknown')
    expect(byLabel.get('Defense vs. position (z-score)')?.tone).toBe('unknown')
    expect(byLabel.get('Ceiling rate')?.tone).toBe('unknown')

    expect(byLabel.get('Sleeper')?.tone).not.toBe('unknown')
    expect(byLabel.get('Sleeper')?.value).toBe('19.42 pts')
    expect(byLabel.get('Median')?.tone).not.toBe('unknown')
  })

  it('renders a zero-valued share as a real 0%, not unknown', () => {
    const row: WeeklyPlayerContextRow = { ...FULL_ROW, role_snap_share: 0, role_snap_share_delta: 0 }
    const sections = buildDetailSections(row)
    const field = allFields(sections).find((f) => f.label === 'Snap share')
    expect(field?.tone).not.toBe('unknown')
    expect(field?.value).toBe('0%')
  })

  it('renders role_is_starter: false as "No", not unknown', () => {
    const row: WeeklyPlayerContextRow = { ...FULL_ROW, role_is_starter: false }
    const sections = buildDetailSections(row)
    const field = allFields(sections).find((f) => f.label === 'Starter')
    expect(field?.tone).not.toBe('unknown')
    expect(field?.value).toBe('No')
  })

  it('signs a positive delta with a leading + and a negative delta with a single leading -', () => {
    const row: WeeklyPlayerContextRow = {
      ...FULL_ROW,
      role_snap_share_delta: 0.032,
      role_carries_share_delta: -0.03,
    }
    const sections = buildDetailSections(row)
    const byLabel = new Map(allFields(sections).map((f) => [f.label, f]))
    expect(byLabel.get('Snap share Δ')?.value).toBe('+3.2pp')
    expect(byLabel.get('Carries share Δ')?.value).toBe('-3.0pp')
  })

  it('humanizes every known gamescript_lean bucket and passes through an unrecognized one', () => {
    const buckets: [string, string][] = [
      ['big_favorite', 'Big favorite'],
      ['favorite', 'Favorite'],
      ['pick_em', "Pick 'em"],
      ['underdog', 'Underdog'],
      ['big_underdog', 'Big underdog'],
    ]
    for (const [raw, label] of buckets) {
      const sections = buildDetailSections({ ...FULL_ROW, game_gamescript_lean: raw })
      const field = allFields(sections).find((f) => f.label === 'Gamescript lean')
      expect(field?.value).toBe(label)
    }

    const unknownBucket = buildDetailSections({ ...FULL_ROW, game_gamescript_lean: 'made_up_bucket' })
    const field = allFields(unknownBucket).find((f) => f.label === 'Gamescript lean')
    expect(field?.value).toBe('made_up_bucket')
  })
})
