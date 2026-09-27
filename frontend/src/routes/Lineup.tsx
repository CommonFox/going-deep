/** Lineup optimizer — issue #128, under the front-end-rebuild epic (#111). Ported from
 * `src/web/pages/lineup_optimizer.py` at parity: same slot order, same empty-slot and close-call
 * handling, same "no projection this week" distinction on the bench.
 *
 * Reads the static export (`optimal_lineup`, `optimal_lineup_bench`, `weekly_player_context`,
 * `current_week.json`) — this page has no manual week control of its own, same as the Streamlit
 * page it replaces. `leagueKey` is the one thing it takes from the shared platform toggle;
 * everything about "which week" comes from `current_week.json`, resolved at export time from
 * `schedules` (see `src/export/current_week.py`) since the export doesn't carry `schedules`
 * itself.
 *
 * `weekly_player_context` (#161) backs a `DetailPanel` under every starter and bench row, collapsed
 * by default. It's fetched separately from `optimal_lineup`/`optimal_lineup_bench` and only when
 * the manifest says a file exists for the current (league, season, week) — same check `hasLineup`
 * makes — because it's a per-player breakdown, not what decides whether the page has a lineup to
 * show at all; missing it entirely just means every panel renders unknown, same as a player with no
 * row inside a file that does exist.
 *
 * A close call (#162) is the exception to "collapsed by default": `starterPairing`/`benchPairing`
 * open a flagged starter's panel and its `bench_player_id` counterpart's panel together, and pass
 * each as the other's `opponent` so `buildDetailSections` can tag which side's raw value favors it.
 *
 * #163 reuses the same `context` map for the tables themselves: each starter/bench row is enriched
 * with its own `weekly_player_context` row (or `undefined`) right before it reaches `DataTable`, so
 * `lineupColumns.tsx`'s rank/tier column and `buildDetailSections`' panel field read off the same
 * row and can never show two different figures for one player/week.
 *
 * #182: each section's table and its `detailList` sit in a `styles.layout` grid rather than one
 * stacked on top of the other, so above 900px the panels form a rail beside the table — opening one
 * doesn't push the next section's table down the page. Below 900px (and always below the table's
 * own 480px card-stack point) it falls back to the same stacked order as before; the DOM shape is
 * unchanged, only the CSS layout around it.
 *
 * #186: that rail used to hold one `DetailPanel` per row, all rendered at once (collapsed unless
 * #162 flagged a close call), which made the rail's height scale with roster size — exactly the
 * scrolling problem #182's grid was papering over rather than fixing. `detailList` now holds at
 * most two panels: the section's currently *selected* row (clicking a `DataTable` row selects it,
 * via `onRowSelect`/`selectedRowKey`) and, when that selection is one side of a #162 close call,
 * its paired opponent — reusing `starterPairing`/`benchPairing` unchanged, since "does this
 * selection have a paired opponent" is exactly what those already answer. `defaultSelectedStarterId`/
 * `defaultSelectedBenchId` (`playerDetail.ts`) pick a close call's own row as the default selection
 * when one exists, so the flagged comparison is still visible with no click needed, same as #162's
 * auto-open did; otherwise the first real row is selected so the rail never starts empty. Starters
 * and bench are two independent tables and so carry two independent selections. */

import { useEffect, useState } from 'react'
import { useLeague } from '../state/LeagueContext'
import { DataTable } from '../components/DataTable/DataTable'
import { DetailPanel } from '../components/DetailPanel/DetailPanel'
import { EmptyState } from '../components/EmptyState/EmptyState'
import { LoadingState } from '../components/LoadingState/LoadingState'
import { ErrorState } from '../components/ErrorState/ErrorState'
import { getRowKey } from '../lib/rowKey'
import { fetchManifest, isAvailable } from '../lib/manifest'
import { fetchCurrentWeek } from '../lib/currentWeek'
import { fetchExportFile, tableFilePath } from '../lib/exportFetch'
import {
  starterColumns,
  benchColumns,
  type BenchRow,
  type StarterDisplayRow,
  type BenchDisplayRow,
} from '../lib/lineupColumns'
import {
  benchPairing,
  buildDetailSections,
  defaultSelectedBenchId,
  defaultSelectedStarterId,
  starterPairing,
} from '../lib/playerDetail'
import type { OptimalLineupRow, WeeklyPlayerContextRow } from '../lib/fixtures'
import styles from './Lineup.module.css'

// Display order for starting slots: skill positions, then FLEX/SUPERFLEX, then the rest — ported
// from lineup_optimizer.py's `_SLOT_ORDER`/`_slot_sort_key`. A slot label not in this list sorts
// after everything named, same fallback the Python version uses.
const SLOT_ORDER = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'SUPERFLEX', 'K', 'DST', 'P']

function slotSortKey(slot: string): [number, number] {
  const match = slot.match(/^([A-Za-z]+)(\d*)$/)
  const prefix = match?.[1] ?? slot
  const number = match?.[2] ? Number(match[2]) : 0
  const rank = SLOT_ORDER.indexOf(prefix)
  return [rank === -1 ? SLOT_ORDER.length : rank, number]
}

// #163: the one lookup every table row and every detail panel shares — a player with no id (an
// empty slot) or no matching `weekly_player_context` row both fall back to `undefined`, which
// `weeklyRankTier`/`buildDetailSections` already render as the unknown dash.
function contextFor(
  playerId: string | null,
  context: Map<string, WeeklyPlayerContextRow>,
): WeeklyPlayerContextRow | undefined {
  return playerId ? context.get(playerId) : undefined
}

type LineupState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | {
      status: 'ready'
      lineup: OptimalLineupRow[]
      bench: BenchRow[]
      context: Map<string, WeeklyPlayerContextRow>
      season: number
      week: number
    }

export function Lineup() {
  const { leagueKey } = useLeague()
  const [state, setState] = useState<LineupState>({ status: 'loading' })
  const [retryToken, setRetryToken] = useState(0)
  // Explicit clicks override the close-call/first-row default computed below; null means "no click
  // yet, use the default". Reset during render (React's own pattern for "a prop changed, adjust
  // state" — see the `key`-less alternative in the React docs) rather than an effect, so a
  // selection from one league's roster doesn't render even once against another league's data
  // before an effect gets a chance to clear it.
  const [selectedStarterId, setSelectedStarterId] = useState<string | null>(null)
  const [selectedBenchId, setSelectedBenchId] = useState<string | null>(null)
  const [selectionLeagueKey, setSelectionLeagueKey] = useState(leagueKey)
  if (leagueKey !== selectionLeagueKey) {
    setSelectionLeagueKey(leagueKey)
    setSelectedStarterId(null)
    setSelectedBenchId(null)
  }

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const [manifest, currentWeeks] = await Promise.all([fetchManifest(), fetchCurrentWeek()])
        const current = currentWeeks.find((entry) => entry.league_key === leagueKey)
        if (!current) {
          throw new Error(`no current week published for league "${leagueKey}"`)
        }

        // The manifest's `available` list answers "does this file exist" so this never has to
        // probe with a fetch that 404s — the same rule `LeagueContext`'s leagueKeys list follows.
        const hasLineup = isAvailable(manifest, 'optimal_lineup', current)
        if (!hasLineup) {
          if (!cancelled) {
            setState({
              status: 'ready',
              lineup: [],
              bench: [],
              context: new Map(),
              season: current.season,
              week: current.week,
            })
          }
          return
        }

        const hasContext = isAvailable(manifest, 'weekly_player_context', current)

        const [lineup, bench, contextRows] = await Promise.all([
          fetchExportFile<OptimalLineupRow[]>(tableFilePath('optimal_lineup', current)),
          fetchExportFile<BenchRow[]>(tableFilePath('optimal_lineup_bench', current)),
          hasContext
            ? fetchExportFile<WeeklyPlayerContextRow[]>(tableFilePath('weekly_player_context', current))
            : Promise.resolve<WeeklyPlayerContextRow[]>([]),
        ])
        const context = new Map(contextRows.map((row) => [row.player_id, row]))
        if (!cancelled) {
          setState({ status: 'ready', lineup, bench, context, season: current.season, week: current.week })
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
  }, [leagueKey, retryToken])

  if (state.status === 'loading') return <LoadingState label="Loading lineup…" />
  if (state.status === 'error') {
    return <ErrorState message={state.message} onRetry={() => setRetryToken((token) => token + 1)} />
  }

  const { lineup, bench, context, season, week } = state

  if (lineup.length === 0) {
    return (
      <EmptyState
        message={`No lineup data for week ${week} yet — this league's projections may not reach it.`}
      />
    )
  }

  const starters = [...lineup].sort((a, b) => {
    const [rankA, numA] = slotSortKey(a.slot)
    const [rankB, numB] = slotSortKey(b.slot)
    return rankA - rankB || numA - numB
  })

  const missing = bench.filter((row) => row.projected_points == null)
  const available = bench
    .filter((row) => row.projected_points != null)
    .sort((a, b) => (b.projected_points ?? 0) - (a.projected_points ?? 0))

  // A `null` id means "no valid row to select" (e.g. every remaining slot is empty), not "select
  // whichever row happens to have a null player_id" — an empty-slot row's own `player_id` is also
  // `null`, so the lookup below would otherwise match it by accident.
  const effectiveStarterId = selectedStarterId ?? defaultSelectedStarterId(starters)
  const selectedStarter = effectiveStarterId
    ? starters.find((row) => row.player_id === effectiveStarterId)
    : undefined
  const starterOpponentId = selectedStarter ? starterPairing(selectedStarter).opponentPlayerId : null

  const effectiveBenchId = selectedBenchId ?? defaultSelectedBenchId(available, starters)
  const selectedBench = effectiveBenchId
    ? available.find((row) => row.player_id === effectiveBenchId)
    : undefined
  const benchOpponentId = selectedBench ? benchPairing(selectedBench.player_id!, starters).opponentPlayerId : null
  const benchOpponentStarter = benchOpponentId
    ? starters.find((row) => row.player_id === benchOpponentId)
    : undefined

  return (
    <div>
      <h1>Lineup Optimizer</h1>
      <p className="mono caption">
        Week {week} · {season} season
      </p>

      <section className={styles.section}>
        <h2>Starters</h2>
        <div className={styles.layout}>
          <DataTable
            columns={starterColumns}
            rows={starters.map(
              (row): StarterDisplayRow => ({ ...row, context: contextFor(row.player_id, context) }),
            )}
            rowKey={(row, index) => getRowKey(row.player_id, row.player_name, row.slot, index)}
            selectedRowKey={effectiveStarterId}
            onRowSelect={(row) => {
              if (row.player_id) setSelectedStarterId(row.player_id)
            }}
          />
          <div className={styles.detailList}>
            {selectedStarter && (
              <DetailPanel
                title={`${selectedStarter.slot} · ${selectedStarter.player_name}`}
                defaultOpen
                sections={buildDetailSections(
                  contextFor(selectedStarter.player_id, context),
                  contextFor(starterOpponentId, context),
                )}
              />
            )}
            {selectedStarter && starterOpponentId && (
              <DetailPanel
                title={`${selectedStarter.bench_player_name} — bench alternative`}
                defaultOpen
                sections={buildDetailSections(
                  contextFor(starterOpponentId, context),
                  contextFor(selectedStarter.player_id, context),
                )}
              />
            )}
          </div>
        </div>
      </section>

      {missing.length > 0 && (
        <p className={styles.caption}>
          No projection this week, so left out of consideration entirely:{' '}
          {missing.map((row) => `${row.player_name} (${row.position})`).join(', ')}
        </p>
      )}

      <section className={styles.section}>
        <h2>Bench</h2>
        {available.length === 0 ? (
          <p className={styles.caption}>Nothing left on the bench with a projection this week.</p>
        ) : (
          <div className={styles.layout}>
            <DataTable
              columns={benchColumns}
              rows={available.map(
                (row): BenchDisplayRow => ({ ...row, context: contextFor(row.player_id, context) }),
              )}
              rowKey={(row, index) => getRowKey(row.player_id, row.player_name, row.position, index)}
              selectedRowKey={effectiveBenchId}
              onRowSelect={(row) => {
                if (row.player_id) setSelectedBenchId(row.player_id)
              }}
            />
            <div className={styles.detailList}>
              {selectedBench && (
                <DetailPanel
                  title={`${selectedBench.player_name} — ${selectedBench.position}`}
                  defaultOpen
                  sections={buildDetailSections(
                    contextFor(selectedBench.player_id, context),
                    contextFor(benchOpponentId, context),
                  )}
                />
              )}
              {selectedBench && benchOpponentStarter && (
                <DetailPanel
                  title={`${benchOpponentStarter.slot} · ${benchOpponentStarter.player_name} — starter alternative`}
                  defaultOpen
                  sections={buildDetailSections(
                    contextFor(benchOpponentStarter.player_id, context),
                    contextFor(selectedBench.player_id, context),
                  )}
                />
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
