/** Viewing guide — issue #169, under the viewing-guide epic (#112). Groups a week's games by
 * broadcast window so "what's on and when do I care" doesn't have to be worked out from memory.
 *
 * Reads the static export (`viewing_guide`, `viewing_guide_starters`, `current_week.json`) —
 * `viewing_guide` spans every week `optimal_lineup` does, so this page resolves "this week" from
 * `current_week.json` rather than `selection.week`, exactly as `Lineup.tsx`'s own docstring
 * explains for the identical reason. `selection.leagueKey` is still the one thing it takes from
 * the shared switcher.
 *
 * No file for the current week (a bye-heavy week can legitimately empty every window out) is
 * treated the same as a file with zero rows — both fall through to `groupByWindow([])`, which
 * still renders all five standard sections empty, never an error or a blank page.
 *
 * **No opponent-side information of any kind** — not the opposing roster, not either team's
 * score, not the margin. This is #112's explicit, permanent product constraint: the whole point
 * of this page is to stop tracking an opponent's score. `viewing_guide` itself carries no score
 * column to begin with, so there's nothing here to accidentally render. */

import { useEffect, useState } from 'react'
import { useLeagueWeek } from '../state/LeagueWeekContext'
import { LoadingState } from '../components/LoadingState/LoadingState'
import { ErrorState } from '../components/ErrorState/ErrorState'
import { getRowKey } from '../lib/rowKey'
import { fetchManifest, isAvailable } from '../lib/manifest'
import { fetchCurrentWeek } from '../lib/currentWeek'
import { fetchExportFile, tableFilePath } from '../lib/exportFetch'
import { groupByWindow } from '../lib/viewingGuide'
import type { ViewingGuideRow, ViewingGuideStarterRow } from '../lib/fixtures'
import styles from './ViewingGuide.module.css'

type ViewingGuideState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | {
      status: 'ready'
      games: ViewingGuideRow[]
      startersByGame: Map<string, ViewingGuideStarterRow[]>
      season: number
      week: number
    }

export function ViewingGuide() {
  const { selection } = useLeagueWeek()
  const [state, setState] = useState<ViewingGuideState>({ status: 'loading' })
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const [manifest, currentWeeks] = await Promise.all([fetchManifest(), fetchCurrentWeek()])
        const current = currentWeeks.find((entry) => entry.league_key === selection.leagueKey)
        if (!current) {
          throw new Error(`no current week published for league "${selection.leagueKey}"`)
        }

        const hasGuide = isAvailable(manifest, 'viewing_guide', current)
        if (!hasGuide) {
          if (!cancelled) {
            setState({
              status: 'ready',
              games: [],
              startersByGame: new Map(),
              season: current.season,
              week: current.week,
            })
          }
          return
        }

        const hasStarters = isAvailable(manifest, 'viewing_guide_starters', current)
        const [games, starters] = await Promise.all([
          fetchExportFile<ViewingGuideRow[]>(tableFilePath('viewing_guide', current)),
          hasStarters
            ? fetchExportFile<ViewingGuideStarterRow[]>(tableFilePath('viewing_guide_starters', current))
            : Promise.resolve<ViewingGuideStarterRow[]>([]),
        ])

        const startersByGame = new Map<string, ViewingGuideStarterRow[]>()
        for (const starter of starters) {
          const bucket = startersByGame.get(starter.game_id)
          if (bucket) bucket.push(starter)
          else startersByGame.set(starter.game_id, [starter])
        }

        if (!cancelled) {
          setState({ status: 'ready', games, startersByGame, season: current.season, week: current.week })
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
  }, [selection.leagueKey, retryToken])

  if (state.status === 'loading') return <LoadingState label="Loading viewing guide…" />
  if (state.status === 'error') {
    return <ErrorState message={state.message} onRetry={() => setRetryToken((token) => token + 1)} />
  }

  const { games, startersByGame, season, week } = state
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
                  <ul className={styles.starterList}>
                    {(startersByGame.get(game.game_id) ?? []).map((starter, index) => (
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
          )}
        </section>
      ))}
    </div>
  )
}
