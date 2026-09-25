/** Groups `viewing_guide` rows into the five broadcast windows for `/viewing-guide` — #169, under
 * the viewing-guide epic (#112). `game_environment`/`viewing_guide.py` already flag a game's
 * `broadcast_window` as one of the five standard names or, for a game that isn't
 * Thursday/Sunday/Monday at all (a Saturday playoff slate, a Friday international game), its own
 * raw weekday — this module is what turns that column into the fixed section order the page
 * renders, entirely client-side. The export file's own `["kickoff", "game_id"]` sort is a
 * convenient base order, not something this module leans on: it re-sorts within each window
 * itself so the grouping stays correct even if that changed. */

import type { ViewingGuideRow } from './fixtures'

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

export interface WindowSection {
  window: string
  label: string
  games: ViewingGuideRow[]
}

function windowLabel(window: string): string {
  return BROADCAST_WINDOW_LABELS[window] ?? window
}

function byKickoffThenGameId(a: ViewingGuideRow, b: ViewingGuideRow): number {
  return a.kickoff.localeCompare(b.kickoff) || a.game_id.localeCompare(b.game_id)
}

export function groupByWindow(games: ViewingGuideRow[]): WindowSection[] {
  const byWindow = new Map<string, ViewingGuideRow[]>()
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
