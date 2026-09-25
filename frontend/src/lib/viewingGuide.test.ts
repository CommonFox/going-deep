/** Test cases enumerated before `viewingGuide.ts` was written, per #169:
 *
 * 1. The five standard windows always render, in the fixed order Thursday, Sunday early, Sunday
 *    late, Sunday night, Monday — regardless of the order games arrive in.
 * 2. A window with no games for the week still gets a section, with an empty `games` array — the
 *    "bye-heavy week" case #169's acceptance criteria names explicitly.
 * 3. Games within a window are sorted by kickoff (then `game_id` to break a tie), not left in
 *    whatever order they arrived in — #169 calls this out as a client-side responsibility, not an
 *    assumption that the file is already in the right order.
 * 4. A `broadcast_window` outside the five named ones (a Saturday playoff slate, a Friday
 *    international game) gets its own section, headed by that raw weekday value.
 * 5. Multiple non-standard windows are ordered deterministically (by earliest kickoff), not left
 *    in Map/object insertion order.
 * 6. Zero games at all (no viewing_guide rows for the week) still produces the five standard
 *    sections, all empty, and no extra sections.
 */

import { describe, expect, it } from 'vitest'
import { groupByWindow } from './viewingGuide'
import type { ViewingGuideRow } from './fixtures'

function game(overrides: Partial<ViewingGuideRow>): ViewingGuideRow {
  return {
    league_key: 'sleeper',
    season: 2026,
    week: 3,
    game_id: '2026_03_XXX_YYY',
    home_team: 'YYY',
    away_team: 'XXX',
    kickoff: '2026-09-21T17:00:00Z',
    weekday: 'Sunday',
    gametime: '17:00',
    broadcast_window: 'sunday_early',
    starter_count: 1,
    total_projected_points: 10,
    ...overrides,
  }
}

describe('groupByWindow', () => {
  it('always renders the five standard windows, in fixed order, regardless of input order', () => {
    const games = [
      game({ game_id: 'g-monday', broadcast_window: 'monday' }),
      game({ game_id: 'g-thursday', broadcast_window: 'thursday' }),
      game({ game_id: 'g-sunday-night', broadcast_window: 'sunday_night' }),
    ]

    const sections = groupByWindow(games)

    expect(sections.map((s) => s.window)).toEqual([
      'thursday',
      'sunday_early',
      'sunday_late',
      'sunday_night',
      'monday',
    ])
  })

  it('gives an empty-but-present section to a window with no games this week', () => {
    const games = [game({ game_id: 'g-thursday', broadcast_window: 'thursday' })]

    const sections = groupByWindow(games)

    const sundayEarly = sections.find((s) => s.window === 'sunday_early')
    expect(sundayEarly?.games).toEqual([])
  })

  it('sorts games within a window by kickoff, then game_id, not input order', () => {
    const early = game({ game_id: 'g-early', kickoff: '2026-09-21T13:00:00Z', broadcast_window: 'sunday_early' })
    const late = game({ game_id: 'g-late', kickoff: '2026-09-21T16:25:00Z', broadcast_window: 'sunday_early' })
    const tieA = game({ game_id: 'g-tie-a', kickoff: '2026-09-21T13:00:00Z', broadcast_window: 'sunday_early' })

    const sections = groupByWindow([late, tieA, early])

    const sundayEarly = sections.find((s) => s.window === 'sunday_early')
    expect(sundayEarly?.games.map((g) => g.game_id)).toEqual(['g-early', 'g-tie-a', 'g-late'])
  })

  it('gives a non-standard broadcast_window its own section, headed by its raw weekday', () => {
    const games = [
      game({ game_id: 'g-saturday', broadcast_window: 'Saturday', weekday: 'Saturday' }),
    ]

    const sections = groupByWindow(games)

    const extra = sections.find((s) => s.window === 'Saturday')
    expect(extra).toBeDefined()
    expect(extra?.label).toBe('Saturday')
    expect(extra?.games.map((g) => g.game_id)).toEqual(['g-saturday'])
  })

  it('orders multiple non-standard windows by earliest kickoff', () => {
    const friday = game({
      game_id: 'g-friday',
      broadcast_window: 'Friday',
      weekday: 'Friday',
      kickoff: '2026-11-27T20:00:00Z',
    })
    const wednesday = game({
      game_id: 'g-wednesday',
      broadcast_window: 'Wednesday',
      weekday: 'Wednesday',
      kickoff: '2026-11-25T01:00:00Z',
    })

    const sections = groupByWindow([friday, wednesday])

    const extraWindows = sections.slice(5).map((s) => s.window)
    expect(extraWindows).toEqual(['Wednesday', 'Friday'])
  })

  it('renders five empty standard sections and nothing else for a week with no games at all', () => {
    const sections = groupByWindow([])

    expect(sections).toHaveLength(5)
    expect(sections.every((s) => s.games.length === 0)).toBe(true)
  })
})
