/** Viewing guide — issue #169, under the viewing-guide epic (#112). Groups a week's games by
 * broadcast window so "what's on and when do I care" doesn't have to be worked out from memory.
 * #181 extended it to show every platform's starters together per real-world game rather than
 * switching the platform toggle to check one league at a time.
 *
 * Reads the static export (`viewing_guide`, `viewing_guide_starters`, `current_week.json`) —
 * `viewing_guide` spans every week `optimal_lineup` does, so this page resolves "this week" from
 * `current_week.json`, exactly as `Lineup.tsx`'s own docstring explains for the identical reason.
 * Unlike `Lineup.tsx`/`Waiver.tsx`, this page fetches every league in `leagueKeys` — not just the
 * selected `leagueKey` — and merges them client-side with `combineViewingGuides` (see
 * `lib/viewingGuide.ts` for why that merge is safe: game-level facts are the same across leagues,
 * only the starters differ). The platform toggle in `AppShell` stays visible on this route but has
 * no effect on it — showing every platform at once is the whole point.
 *
 * No file for the current week (a bye-heavy week can legitimately empty every window out) is
 * treated the same as a file with zero rows — both fall through to `groupByWindow([])`, which
 * still renders all five standard sections empty, never an error or a blank page. The same holds
 * per league now: a league missing its own `current_week.json` entry or `viewing_guide` file
 * contributes zero games rather than failing the whole page — the other league's guide still
 * renders. Only if *no* league resolves at all does this surface as the error state, the same
 * "nothing to show at all" case the single-league version used to throw on.
 *
 * **No opponent-side information of any kind** — not the opposing roster, not either team's
 * score, not the margin. This is #112's explicit, permanent product constraint: the whole point
 * of this page is to stop tracking an opponent's score. `viewing_guide` itself carries no score
 * column to begin with, so there's nothing here to accidentally render. */

import { useEffect, useState } from 'react'
import { useLeague } from '../state/LeagueContext'
import { LoadingState } from '../components/LoadingState/LoadingState'
import { ErrorState } from '../components/ErrorState/ErrorState'
import { getRowKey } from '../lib/rowKey'
import { fetchManifest, isAvailable } from '../lib/manifest'
import { fetchCurrentWeek } from '../lib/currentWeek'
import { fetchExportFile, tableFilePath } from '../lib/exportFetch'
import { combineViewingGuides, groupByWindow, type CombinedGame, type LeagueViewingGuide } from '../lib/viewingGuide'
import { platformLabel } from '../lib/platform'
import type { ViewingGuideRow, ViewingGuideStarterRow } from '../lib/fixtures'
import styles from './ViewingGuide.module.css'

type ViewingGuideState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | {
      status: 'ready'
      games: CombinedGame[]
      season: number
      week: number
    }

interface LeagueLoad extends LeagueViewingGuide {
  season: number | null
  week: number | null
}

export function ViewingGuide() {
  const { leagueKeys } = useLeague()
  const [state, setState] = useState<ViewingGuideState>({ status: 'loading' })
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const [manifest, currentWeeks] = await Promise.all([fetchManifest(), fetchCurrentWeek()])

        const perLeague: LeagueLoad[] = await Promise.all(
          leagueKeys.map(async (league_key) => {
            const current = currentWeeks.find((entry) => entry.league_key === league_key)
            if (!current || !isAvailable(manifest, 'viewing_guide', current)) {
              return { league_key, season: null, week: null, games: [], starters: [] }
            }

            const hasStarters = isAvailable(manifest, 'viewing_guide_starters', current)
            const [games, starters] = await Promise.all([
              fetchExportFile<ViewingGuideRow[]>(tableFilePath('viewing_guide', current)),
              hasStarters
                ? fetchExportFile<ViewingGuideStarterRow[]>(tableFilePath('viewing_guide_starters', current))
                : Promise.resolve<ViewingGuideStarterRow[]>([]),
            ])
            return { league_key, season: current.season, week: current.week, games, starters }
          }),
        )

        const resolved = perLeague.find((entry) => entry.season !== null && entry.week !== null)
        if (!resolved) {
          throw new Error(`no current week published for any league (${leagueKeys.join(', ')})`)
        }

        const games = combineViewingGuides(perLeague)

        if (!cancelled) {
          setState({ status: 'ready', games, season: resolved.season!, week: resolved.week! })
        }
      } catch (error) {
        if (!cancelled) {
          setState({ status: 'error', message: error instanceof Error ? error.message : String(error) })
        }
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [leagueKeys, retryToken])

  if (state.status === 'loading') return <LoadingState label="Loading viewing guide…" />
  if (state.status === 'error') {
    return <ErrorState message={state.message} onRetry={() => setRetryToken((token) => token + 1)} />
  }

  const { games, season, week } = state
  const sections = groupByWindow(games)

  return (
    <div>
      <h1>Viewing Guide</h1>
      <p className="mono caption">
        Week {week} · {season} season
      </p>

      {sections.map((section) => (
        <section key={section.window} className={styles.section}>
          <h2>{section.label}</h2>
          {section.games.length === 0 ? (
            <p className={styles.emptyWindow}>Nothing of yours in this window.</p>
          ) : (
            <div className={styles.cardGrid}>
              {section.games.map((game) => (
                <div key={game.game_id} className={styles.card}>
                  <p className={`mono ${styles.gameTime}`}>{game.gametime}</p>
                  <p className={styles.matchup}>
                    {game.away_team} @ {game.home_team}
                  </p>
                  {game.byLeague.map((league) => (
                    <div key={league.league_key} className={styles.leagueSection}>
                      <p className={`mono ${styles.leagueLabel}`}>{platformLabel(league.league_key)}</p>
                      <ul className={styles.starterList}>
                        {league.starters.map((starter, index) => (
                          <li key={getRowKey(starter.player_id, starter.player_name, starter.slot, index)}>
                            <span className={styles.slot}>{starter.slot}</span> {starter.player_name} ·{' '}
                            {starter.team} —{' '}
                            <span className="mono">
                              {starter.projected_points != null
                                ? `${starter.projected_points.toFixed(2)} pts`
                                : '—'}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  )
}
