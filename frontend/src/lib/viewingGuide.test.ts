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
 *
 * Test cases for `combineViewingGuides`, enumerated before it was written, per #181 (show every
 * platform together per real-world game instead of switching the platform toggle):
 *
 * 7. A `game_id` only one league has starters in appears once, with `byLeague` holding only that
 *    league — never a fabricated empty entry for a league that has nothing to watch there.
 * 8. A `game_id` both leagues have starters in merges into one entry, with `byLeague` holding each
 *    league's own `starter_count`/`total_projected_points`/starters side by side — never summed or
 *    otherwise merged into one number, per the issue's own callout that these are aggregated
 *    per-league in the warehouse.
 * 9. The merged entry's game-level fields (`home_team`/`away_team`/`kickoff`/`weekday`/`gametime`/
 *    `broadcast_window`) come through unchanged — these are the same across leagues for a given
 *    season/week (`src/gold/viewing_guide.py`), so whichever league's row is seen first is fine.
 * 10. Distinct `game_id`s across leagues each produce their own entry, never collapsed together.
 * 11. No leagues, or leagues with no games at all, produces an empty array.
 * 12. Each league's starters in `byLeague` are only that league's own — a starter belonging to
 *     another league's roster never leaks into it, even when both leagues share the same game.
 */

import { describe, expect, it } from 'vitest'
import { combineViewingGuides, groupByWindow } from './viewingGuide'
import type { ViewingGuideRow, ViewingGuideStarterRow } from './fixtures'

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

function starter(overrides: Partial<ViewingGuideStarterRow>): ViewingGuideStarterRow {
  return {
    league_key: 'sleeper',
    season: 2026,
    week: 3,
    game_id: '2026_03_XXX_YYY',
    slot: 'WR',
    player_id: 'p1',
    player_name: 'Player One',
    team: 'YYY',
    projected_points: 10,
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

describe('combineViewingGuides', () => {
  it('gives a game only one league has starters in a single entry naming only that league', () => {
    const espnGame = game({ league_key: 'espn', game_id: 'g-espn-only' })
    const espnStarter = starter({ league_key: 'espn', game_id: 'g-espn-only' })

    const combined = combineViewingGuides([
      { league_key: 'espn', games: [espnGame], starters: [espnStarter] },
      { league_key: 'sleeper', games: [], starters: [] },
    ])

    expect(combined).toHaveLength(1)
    expect(combined[0].game_id).toBe('g-espn-only')
    expect(combined[0].byLeague.map((l) => l.league_key)).toEqual(['espn'])
  })

  it('merges a game both leagues have starters in into one entry with each league kept separate', () => {
    const shared = 'g-shared'
    const espnGame = game({ league_key: 'espn', game_id: shared, starter_count: 2, total_projected_points: 30 })
    const sleeperGame = game({ league_key: 'sleeper', game_id: shared, starter_count: 1, total_projected_points: 12 })
    const espnStarters = [
      starter({ league_key: 'espn', game_id: shared, player_id: 'e1' }),
      starter({ league_key: 'espn', game_id: shared, player_id: 'e2' }),
    ]
    const sleeperStarters = [starter({ league_key: 'sleeper', game_id: shared, player_id: 's1' })]

    const combined = combineViewingGuides([
      { league_key: 'espn', games: [espnGame], starters: espnStarters },
      { league_key: 'sleeper', games: [sleeperGame], starters: sleeperStarters },
    ])

    expect(combined).toHaveLength(1)
    const [game_] = combined
    expect(game_.byLeague).toHaveLength(2)

    const espnSummary = game_.byLeague.find((l) => l.league_key === 'espn')
    const sleeperSummary = game_.byLeague.find((l) => l.league_key === 'sleeper')
    expect(espnSummary).toMatchObject({ starter_count: 2, total_projected_points: 30 })
    expect(sleeperSummary).toMatchObject({ starter_count: 1, total_projected_points: 12 })
  })

  it('takes game-level fields unchanged, the same across leagues for a season/week', () => {
    const shared = 'g-shared'
    const espnGame = game({
      league_key: 'espn',
      game_id: shared,
      home_team: 'YYY',
      away_team: 'XXX',
      kickoff: '2026-09-21T17:00:00Z',
      weekday: 'Sunday',
      gametime: '17:00',
      broadcast_window: 'sunday_late',
    })

    const combined = combineViewingGuides([
      { league_key: 'espn', games: [espnGame], starters: [starter({ league_key: 'espn', game_id: shared })] },
    ])

    expect(combined[0]).toMatchObject({
      home_team: 'YYY',
      away_team: 'XXX',
      kickoff: '2026-09-21T17:00:00Z',
      weekday: 'Sunday',
      gametime: '17:00',
      broadcast_window: 'sunday_late',
    })
  })

  it('keeps distinct game_ids across leagues as separate entries', () => {
    const espnGame = game({ league_key: 'espn', game_id: 'g-espn' })
    const sleeperGame = game({ league_key: 'sleeper', game_id: 'g-sleeper' })

    const combined = combineViewingGuides([
      { league_key: 'espn', games: [espnGame], starters: [] },
      { league_key: 'sleeper', games: [sleeperGame], starters: [] },
    ])

    expect(combined.map((g) => g.game_id).sort()).toEqual(['g-espn', 'g-sleeper'])
  })

  it('returns an empty array for no leagues, or leagues with no games at all', () => {
    expect(combineViewingGuides([])).toEqual([])
    expect(
      combineViewingGuides([
        { league_key: 'espn', games: [], starters: [] },
        { league_key: 'sleeper', games: [], starters: [] },
      ]),
    ).toEqual([])
  })

  it('never leaks a starter from one league into another league\'s entry for the same game', () => {
    const shared = 'g-shared'
    const espnGame = game({ league_key: 'espn', game_id: shared })
    const sleeperGame = game({ league_key: 'sleeper', game_id: shared })
    const espnStarter = starter({ league_key: 'espn', game_id: shared, player_id: 'e1' })
    const sleeperStarter = starter({ league_key: 'sleeper', game_id: shared, player_id: 's1' })

    const combined = combineViewingGuides([
      { league_key: 'espn', games: [espnGame], starters: [espnStarter] },
      { league_key: 'sleeper', games: [sleeperGame], starters: [sleeperStarter] },
    ])

    const espnSummary = combined[0].byLeague.find((l) => l.league_key === 'espn')
    const sleeperSummary = combined[0].byLeague.find((l) => l.league_key === 'sleeper')
    expect(espnSummary?.starters.map((s) => s.player_id)).toEqual(['e1'])
    expect(sleeperSummary?.starters.map((s) => s.player_id)).toEqual(['s1'])
  })
})
