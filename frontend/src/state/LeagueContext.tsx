/** Which league (platform) is global state in this app, not per-page — every route reads its
 * selection from here rather than holding its own. Seeded from a manifest's `available` list, so
 * the platform toggle only ever offers a league that actually has a file — never a dead end.
 *
 * Used to also carry season/week (`LeagueWeekContext`, #127) behind a combined dropdown, but no
 * route ever read `selection.season`/`selection.week` — every page resolves "which week" from
 * `current_week.json` or a table already scoped to one week at build time (see `Lineup.tsx`,
 * `ViewingGuide.tsx`, `Waiver.tsx`). #180 drops both from the switcher entirely rather than keep
 * threading state nothing consumes; if a page ever needs to browse a week other than the current
 * one, that's new scope for whichever page wants it, not a reason to bring this back. */

import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'
import { distinctLeagueKeys, type Manifest } from '../lib/manifest'

interface LeagueContextValue {
  manifest: Manifest
  leagueKeys: string[]
  leagueKey: string
  setLeagueKey: (next: string) => void
}

const LeagueContext = createContext<LeagueContextValue | undefined>(undefined)

export function LeagueProvider({ manifest, children }: { manifest: Manifest; children: ReactNode }) {
  const leagueKeys = useMemo(() => distinctLeagueKeys(manifest), [manifest])
  const [leagueKey, setLeagueKey] = useState<string>(leagueKeys[0] ?? '')

  const value = useMemo(
    () => ({ manifest, leagueKeys, leagueKey, setLeagueKey }),
    [manifest, leagueKeys, leagueKey],
  )

  return <LeagueContext.Provider value={value}>{children}</LeagueContext.Provider>
}

export function useLeague(): LeagueContextValue {
  const context = useContext(LeagueContext)
  if (!context) {
    throw new Error('useLeague must be used within a LeagueProvider')
  }
  return context
}
