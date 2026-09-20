/** Test cases enumerated before the #163 rank/tier column was added to `lineupColumns.tsx`:
 *
 * 1. The starter table's rank/tier column renders `weeklyRankTier(row.context)` — the identical
 *    string `playerDetail.ts`'s detail panel formatter produces for the same `weekly_player_context`
 *    row, so the table and the panel can never drift onto two different values for one player/week.
 * 2. That column renders the unknown dash `'—'` when a starter row carries no `context` at all — an
 *    empty slot, or a player with no matching `weekly_player_context` row.
 * 3. The bench table's rank/tier column behaves identically to the starter column's: same formatter,
 *    same populated output, same unknown fallback.
 */

import { describe, expect, it } from 'vitest'
import { benchColumns, starterColumns, type BenchDisplayRow, type StarterDisplayRow } from './lineupColumns'
import { weeklyRankTier } from './playerDetail'
import type { OptimalLineupRow, WeeklyPlayerContextRow } from './fixtures'
import { weeklyPlayerContextFixture } from './fixtures'

const CONTEXT_ROW: WeeklyPlayerContextRow = weeklyPlayerContextFixture[1] // Alec Pierce, WR22 · tier 2

const STARTER_ROW: OptimalLineupRow = {
  league_key: 'sleeper',
  season: 2026,
  week: 2,
  slot: 'WR1',
  player_id: CONTEXT_ROW.player_id,
  player_name: CONTEXT_ROW.player_name,
  projected_points: 9.97,
  is_close_call: false,
  bench_player_id: null,
  bench_player_name: null,
  bench_projected_points: null,
}

function rankTierColumn<T>(columns: { key: string; render?: (row: T) => unknown }[]) {
  const column = columns.find((c) => c.key === 'rankTier')
  if (!column?.render) throw new Error('rankTier column not found')
  return column.render
}

describe('starterColumns rank/tier', () => {
  it('renders weeklyRankTier(row.context), identical to the detail panel formatter', () => {
    const row: StarterDisplayRow = { ...STARTER_ROW, context: CONTEXT_ROW }
    const render = rankTierColumn<StarterDisplayRow>(starterColumns)
    expect(render(row)).toBe(weeklyRankTier(CONTEXT_ROW))
    expect(render(row)).toBe('WR22 · tier 2')
  })

  it('renders the unknown dash when the row carries no context', () => {
    const row: StarterDisplayRow = { ...STARTER_ROW, context: undefined }
    const render = rankTierColumn<StarterDisplayRow>(starterColumns)
    expect(render(row)).toBe('—')
  })
})

describe('benchColumns rank/tier', () => {
  const BENCH_ROW = {
    player_id: CONTEXT_ROW.player_id,
    player_name: CONTEXT_ROW.player_name,
    position: CONTEXT_ROW.position,
    projected_points: 9.97,
  }

  it('renders weeklyRankTier(row.context), matching the starter column', () => {
    const row: BenchDisplayRow = { ...BENCH_ROW, context: CONTEXT_ROW }
    const render = rankTierColumn<BenchDisplayRow>(benchColumns)
    expect(render(row)).toBe('WR22 · tier 2')
  })

  it('renders the unknown dash when the row carries no context', () => {
    const row: BenchDisplayRow = { ...BENCH_ROW, context: undefined }
    const render = rankTierColumn<BenchDisplayRow>(benchColumns)
    expect(render(row)).toBe('—')
  })
})
