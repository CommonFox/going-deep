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
 *
 * Test cases enumerated before the #162 close-call work was added:
 *
 * Tone comparison (`buildDetailSections(row, opponent)`):
 * 8. A toned field where `row`'s raw value is more favorable than `opponent`'s gets `tone: 'good'`;
 *    the same field on `opponent`'s own sections stays untoned (neutral, never `'bad'`).
 * 9. Reversing which side is more favorable flips which side gets `tone: 'good'`.
 * 10. Equal raw values on a toned field leave both sides untoned — no arbitrary winner on a tie.
 * 11. A null value on either side of a toned field leaves both sides untoned for that field; the
 *     null side's own `tone: 'unknown'` is unaffected by the comparison.
 * 12. `role_depth_rank` is inverted from every other toned field: the *lower* rank number gets
 *     `tone: 'good'`.
 * 13. Every field #162 lists — `game_implied_team_total`, `dvp_vs_league_avg_zscore`, each `role_`
 *     share and its `_delta` (snap/target/air_yards/wopr/carries), and `role_depth_rank` — resolves
 *     a `'good'` tone for whichever side's raw value favors it, checked table-driven over the full
 *     set.
 * 14. `outcome_ceiling_rate`/`outcome_floor_rate` never carry `tone: 'good'`, even when one side is
 *     strictly better on that figure — informational, not part of the compared set.
 * 15. Fields outside #162's list (Sleeper/ESPN points, FantasyPros rank, Defense rank, Outcome
 *     median/floor/ceiling) are untouched by an opponent row.
 * 16. Calling `buildDetailSections(row)` with no `opponent` argument — the #161 call shape — never
 *     tones a field `'good'`, matching the existing no-opponent behavior exactly.
 *
 * Pairing (`starterPairing` / `benchPairing`):
 * 17. A close-call starter row (`is_close_call: true`) returns `defaultOpen: true` and
 *     `opponentPlayerId` equal to its `bench_player_id`.
 * 18. A non-close-call starter row returns `defaultOpen: false` and `opponentPlayerId: null`.
 * 19. A bench player whose id matches a close-call starter's `bench_player_id` returns
 *     `defaultOpen: true` and `opponentPlayerId` equal to that starter's `player_id`.
 * 20. A bench player named by no starter's close call returns `defaultOpen: false` and
 *     `opponentPlayerId: null`.
 * 21. With two independent close calls in the same lineup, `benchPairing` resolves each bench
 *     player against the specific starter that named them, not any close call in the list.
 * 22. When two close-call starters share the same `bench_player_id` (seen in real export data —
 *     one bench tight end backing up both a FLEX and a TE close call), `benchPairing` still opens
 *     and pairs with the first matching starter in array order, rather than throwing or picking
 *     arbitrarily on each call.
 */

import { describe, expect, it } from 'vitest'
import { benchPairing, buildDetailSections, starterPairing } from './playerDetail'
import type { OptimalLineupRow, WeeklyPlayerContextRow } from './fixtures'

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

// Strictly better than FULL_ROW on every #162-toned field except `role_depth_rank`, where lower
// wins — FULL_ROW's rank of 1 already beats this row's 3, so FULL_ROW is the favored side on every
// toned field below.
const WORSE_ROW: WeeklyPlayerContextRow = {
  ...FULL_ROW,
  player_id: '00-0038606',
  player_name: 'Parker Washington',
  game_implied_team_total: 18.0,
  dvp_vs_league_avg_zscore: -0.1,
  role_snap_share: 0.5,
  role_snap_share_delta: -0.05,
  role_target_share: 0.02,
  role_target_share_delta: -0.03,
  role_air_yards_share: 0.01,
  role_air_yards_share_delta: -0.02,
  role_wopr: 0.05,
  role_wopr_delta: -0.01,
  role_carries_share: 0.03,
  role_carries_share_delta: -0.08,
  role_depth_rank: 3,
  outcome_ceiling_rate: 0.05,
}

// The full #162-toned field set, paired with the direction FULL_ROW is favored on each — FULL_ROW
// beats WORSE_ROW on all of them (higher on every share/z-score/total, lower on depth rank).
const TONED_LABELS = [
  'Implied team total',
  'Defense vs. position (z-score)',
  'Snap share',
  'Snap share Δ',
  'Target share',
  'Target share Δ',
  'Air yards share',
  'Air yards share Δ',
  'WOPR',
  'WOPR Δ',
  'Carries share',
  'Carries share Δ',
  'Depth chart rank',
]

describe('buildDetailSections close-call tone comparison', () => {
  it('tags the favored side tone: good and leaves the other side untoned', () => {
    const favored = allFields(buildDetailSections(FULL_ROW, WORSE_ROW))
    const disfavored = allFields(buildDetailSections(WORSE_ROW, FULL_ROW))
    const favoredField = favored.find((f) => f.label === 'Implied team total')
    const disfavoredField = disfavored.find((f) => f.label === 'Implied team total')
    expect(favoredField?.tone).toBe('good')
    expect(disfavoredField?.tone).not.toBe('good')
  })

  it('flips which side is tagged good when which row is more favorable reverses', () => {
    // WORSE_ROW as the primary row, but now compared against something worse than itself.
    const evenWorse: WeeklyPlayerContextRow = { ...WORSE_ROW, game_implied_team_total: 10.0 }
    const sections = buildDetailSections(WORSE_ROW, evenWorse)
    const field = allFields(sections).find((f) => f.label === 'Implied team total')
    expect(field?.tone).toBe('good')
  })

  it('leaves both sides untoned on a tie', () => {
    const tiedOpponent: WeeklyPlayerContextRow = { ...WORSE_ROW, dvp_vs_league_avg_zscore: FULL_ROW.dvp_vs_league_avg_zscore }
    const a = allFields(buildDetailSections(FULL_ROW, tiedOpponent)).find(
      (f) => f.label === 'Defense vs. position (z-score)',
    )
    const b = allFields(buildDetailSections(tiedOpponent, FULL_ROW)).find(
      (f) => f.label === 'Defense vs. position (z-score)',
    )
    expect(a?.tone).not.toBe('good')
    expect(b?.tone).not.toBe('good')
  })

  it('leaves a toned field untoned on both sides when either side is null, without disturbing the null side\'s own unknown tone', () => {
    const opponentMissingWopr: WeeklyPlayerContextRow = { ...WORSE_ROW, role_wopr: null }
    const rowField = allFields(buildDetailSections(FULL_ROW, opponentMissingWopr)).find((f) => f.label === 'WOPR')
    const opponentField = allFields(buildDetailSections(opponentMissingWopr, FULL_ROW)).find(
      (f) => f.label === 'WOPR',
    )
    expect(rowField?.tone).not.toBe('good')
    expect(opponentField?.tone).toBe('unknown')
  })

  it('inverts role_depth_rank: the lower rank number is favored even when otherwise behind', () => {
    const lowerRankButOtherwiseWorse: WeeklyPlayerContextRow = { ...WORSE_ROW, role_depth_rank: 1 }
    const higherRankButOtherwiseBetter: WeeklyPlayerContextRow = { ...FULL_ROW, role_depth_rank: 5 }
    const sections = buildDetailSections(lowerRankButOtherwiseWorse, higherRankButOtherwiseBetter)
    const field = allFields(sections).find((f) => f.label === 'Depth chart rank')
    expect(field?.tone).toBe('good')
  })

  it('resolves a good tone for the favored side across every #162-listed field', () => {
    const sections = allFields(buildDetailSections(FULL_ROW, WORSE_ROW))
    for (const label of TONED_LABELS) {
      const field = sections.find((f) => f.label === label)
      expect(field?.tone, `${label} should be toned good`).toBe('good')
    }
  })

  it('never tones outcome_ceiling_rate/outcome_floor_rate even when one side is strictly better', () => {
    const sections = allFields(buildDetailSections(FULL_ROW, WORSE_ROW))
    expect(sections.find((f) => f.label === 'Ceiling rate')?.tone).not.toBe('good')
    expect(sections.find((f) => f.label === 'Floor rate')?.tone).not.toBe('good')
  })

  it('leaves fields outside the #162 list untouched by an opponent row', () => {
    const sections = allFields(buildDetailSections(FULL_ROW, WORSE_ROW))
    for (const label of ['Sleeper', 'ESPN', 'FantasyPros', 'Defense rank', 'Median', 'Floor', 'Ceiling']) {
      expect(sections.find((f) => f.label === label)?.tone).not.toBe('good')
    }
  })

  it('never tones a field good when called with no opponent, matching the #161 shape', () => {
    const sections = allFields(buildDetailSections(FULL_ROW))
    expect(sections.every((f) => f.tone !== 'good')).toBe(true)
  })
})

const CLOSE_CALL_STARTER: OptimalLineupRow = {
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
}

const NON_CLOSE_CALL_STARTER: OptimalLineupRow = {
  ...CLOSE_CALL_STARTER,
  slot: 'QB1',
  player_id: '00-0038122',
  player_name: 'C.J. Stroud',
  is_close_call: false,
  bench_player_id: null,
  bench_player_name: null,
  bench_projected_points: null,
}

describe('starterPairing / benchPairing', () => {
  it('opens a close-call starter and pairs it with its bench_player_id', () => {
    expect(starterPairing(CLOSE_CALL_STARTER)).toEqual({
      defaultOpen: true,
      opponentPlayerId: '00-0038606',
    })
  })

  it('leaves a non-close-call starter collapsed with no opponent', () => {
    expect(starterPairing(NON_CLOSE_CALL_STARTER)).toEqual({ defaultOpen: false, opponentPlayerId: null })
  })

  it('opens the bench player named by a close-call starter and pairs it back with that starter', () => {
    const starters = [NON_CLOSE_CALL_STARTER, CLOSE_CALL_STARTER]
    expect(benchPairing('00-0038606', starters)).toEqual({
      defaultOpen: true,
      opponentPlayerId: '00-0037664',
    })
  })

  it('leaves a bench player named by no close call collapsed with no opponent', () => {
    const starters = [NON_CLOSE_CALL_STARTER, CLOSE_CALL_STARTER]
    expect(benchPairing('00-0099999', starters)).toEqual({ defaultOpen: false, opponentPlayerId: null })
  })

  it('pairs each bench player with the specific close-call starter that named them, not any close call', () => {
    const secondCloseCall: OptimalLineupRow = {
      ...CLOSE_CALL_STARTER,
      slot: 'WR1',
      player_id: '00-0011111',
      player_name: 'Second Starter',
      bench_player_id: '00-0022222',
      bench_player_name: 'Second Bench',
    }
    const starters = [CLOSE_CALL_STARTER, secondCloseCall]
    expect(benchPairing('00-0038606', starters).opponentPlayerId).toBe('00-0037664')
    expect(benchPairing('00-0022222', starters).opponentPlayerId).toBe('00-0011111')
  })

  it('pairs a bench player shared by two close-call starters with the first one in array order', () => {
    const secondStarterSameBench: OptimalLineupRow = {
      ...CLOSE_CALL_STARTER,
      slot: 'TE1',
      player_id: '00-0033333',
      player_name: 'Third Starter',
      bench_player_id: CLOSE_CALL_STARTER.bench_player_id,
    }
    const starters = [CLOSE_CALL_STARTER, secondStarterSameBench]
    const pairing = benchPairing(CLOSE_CALL_STARTER.bench_player_id!, starters)
    expect(pairing).toEqual({ defaultOpen: true, opponentPlayerId: CLOSE_CALL_STARTER.player_id })
  })
})
