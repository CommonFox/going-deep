/** League and week are global state in this app, not per-page — the Streamlit app re-derives them
 * on every page independently, and #127 exists partly to stop that. Every route reads its
 * selection from here rather than holding its own. Seeded from a manifest's `available` list, so
 * the switcher only ever offers combinations that actually have a file — never a dead end. */

import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'
import type { Manifest } from '../lib/manifest'

export interface LeagueWeekSelection {
  leagueKey: string
  season: number
  week: number
}

interface LeagueWeekContextValue {
  manifest: Manifest
  combos: LeagueWeekSelection[]
  selection: LeagueWeekSelection
  setSelection: (next: LeagueWeekSelection) => void
}

const LeagueWeekContext = createContext<LeagueWeekContextValue | undefined>(undefined)

function dedupeCombos(manifest: Manifest): LeagueWeekSelection[] {
  const seen = new Map<string, LeagueWeekSelection>()
  for (const entry of manifest.available) {
    const key = `${entry.league_key}-${entry.season}-${entry.week}`
    if (!seen.has(key)) {
      seen.set(key, { leagueKey: entry.league_key, season: entry.season, week: entry.week })
    }
  }
  return [...seen.values()]
}

export function LeagueWeekProvider({
  manifest,
  children,
}: {
  manifest: Manifest
  children: ReactNode
}) {
  const combos = useMemo(() => dedupeCombos(manifest), [manifest])
  const [selection, setSelection] = useState<LeagueWeekSelection>(
    combos[0] ?? { leagueKey: '', season: 0, week: 0 },
  )

  const value = useMemo(
    () => ({ manifest, combos, selection, setSelection }),
    [manifest, combos, selection],
  )

  return <LeagueWeekContext.Provider value={value}>{children}</LeagueWeekContext.Provider>
}

export function useLeagueWeek(): LeagueWeekContextValue {
  const context = useContext(LeagueWeekContext)
  if (!context) {
    throw new Error('useLeagueWeek must be used within a LeagueWeekProvider')
  }
  return context
}
