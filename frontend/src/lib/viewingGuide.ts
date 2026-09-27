/** Groups `viewing_guide` rows into the five broadcast windows for `/viewing-guide` — #169, under
 * the viewing-guide epic (#112). `game_environment`/`viewing_guide.py` already flag a game's
 * `broadcast_window` as one of the five standard names or, for a game that isn't
 * Thursday/Sunday/Monday at all (a Saturday playoff slate, a Friday international game), its own
 * raw weekday — this module is what turns that column into the fixed section order the page
 * renders, entirely client-side. The export file's own `["kickoff", "game_id"]` sort is a
 * convenient base order, not something this module leans on: it re-sorts within each window
 * itself so the grouping stays correct even if that changed.
 *
 * `groupByWindow` is generic over its game type so it works for both a single league's
 * `ViewingGuideRow[]` and `combineViewingGuides`'s merged `CombinedGame[]` below — grouping by
 * broadcast window is the same operation either way.
 *
 * `combineViewingGuides` is #181's addition: merges each league's `viewing_guide` rows by
 * `game_id` so the page can show every platform together for a real-world game instead of
 * switching the platform toggle. Per `src/gold/viewing_guide.py`, a game's own facts
 * (`home_team`/`away_team`/`kickoff`/`broadcast_window`/…) are identical across leagues for a
 * given season/week, but `viewing_guide_starters` — and so `starter_count`/
 * `total_projected_points` — genuinely differs by league, and a league only has a row for a game
 * at all if it has starters in it. So the merge keeps one entry per `game_id`, carrying the
 * game-level facts once and each league's own starters/aggregates alongside, in `byLeague`
 * — never summed together, and never a fabricated entry for a league with nothing to watch there. */

import type { ViewingGuideRow, ViewingGuideStarterRow } from './fixtures'

export const BROADCAST_WINDOW_ORDER = [
  'thursday',
  'sunday_early',
  'sunday_late',
  'sunday_night',
  'monday',
] as const

const BROADCAST_WINDOW_LABELS: Record<string, string> = {
  thursday: 'Thursday',
  sunday_early: 'Sunday Early',
  sunday_late: 'Sunday Late',
  sunday_night: 'Sunday Night',
  monday: 'Monday',
}

export interface WindowSection<T> {
  window: string
  label: string
  games: T[]
}

function windowLabel(window: string): string {
  return BROADCAST_WINDOW_LABELS[window] ?? window
}

type WindowedGame = { broadcast_window: string; kickoff: string; game_id: string }

function byKickoffThenGameId<T extends WindowedGame>(a: T, b: T): number {
  return a.kickoff.localeCompare(b.kickoff) || a.game_id.localeCompare(b.game_id)
}

export function groupByWindow<T extends WindowedGame>(games: T[]): WindowSection<T>[] {
  const byWindow = new Map<string, T[]>()
  for (const game of games) {
    const bucket = byWindow.get(game.broadcast_window)
    if (bucket) bucket.push(game)
    else byWindow.set(game.broadcast_window, [game])
  }

  const standard = BROADCAST_WINDOW_ORDER.map((window) => ({
    window,
    label: windowLabel(window),
    games: (byWindow.get(window) ?? []).slice().sort(byKickoffThenGameId),
  }))

  const standardSet: readonly string[] = BROADCAST_WINDOW_ORDER
  const extra = [...byWindow.entries()]
    .filter(([window]) => !standardSet.includes(window))
    .sort(([, gamesA], [, gamesB]) => byKickoffThenGameId(gamesA[0], gamesB[0]))
    .map(([window, windowGames]) => ({
      window,
      label: windowLabel(window),
      games: windowGames.slice().sort(byKickoffThenGameId),
    }))

  return [...standard, ...extra]
}

/** One league's `viewing_guide`/`viewing_guide_starters` rows, as `ViewingGuide.tsx` fetches them
 * per platform — the input `combineViewingGuides` merges. */
export interface LeagueViewingGuide {
  league_key: string
  games: ViewingGuideRow[]
  starters: ViewingGuideStarterRow[]
}

/** One league's slice of a merged game: its own aggregate (straight off that league's
 * `viewing_guide` row) and its own starters — never another league's. */
export interface LeagueGameSummary {
  league_key: string
  starter_count: number
  total_projected_points: number | null
  starters: ViewingGuideStarterRow[]
}

export interface CombinedGame {
  game_id: string
  home_team: string
  away_team: string
  kickoff: string
  weekday: string
  gametime: string
  broadcast_window: string
  byLeague: LeagueGameSummary[]
}

export function combineViewingGuides(perLeague: LeagueViewingGuide[]): CombinedGame[] {
  const byGameId = new Map<string, CombinedGame>()

  for (const { league_key, games, starters } of perLeague) {
    const startersByGameId = new Map<string, ViewingGuideStarterRow[]>()
    for (const starter of starters) {
      const bucket = startersByGameId.get(starter.game_id)
      if (bucket) bucket.push(starter)
      else startersByGameId.set(starter.game_id, [starter])
    }

    for (const game of games) {
      let combined = byGameId.get(game.game_id)
      if (!combined) {
        combined = {
          game_id: game.game_id,
          home_team: game.home_team,
          away_team: game.away_team,
          kickoff: game.kickoff,
          weekday: game.weekday,
          gametime: game.gametime,
          broadcast_window: game.broadcast_window,
          byLeague: [],
        }
        byGameId.set(game.game_id, combined)
      }

      combined.byLeague.push({
        league_key,
        starter_count: game.starter_count,
        total_projected_points: game.total_projected_points,
        starters: startersByGameId.get(game.game_id) ?? [],
      })
    }
  }

  return [...byGameId.values()]
}
