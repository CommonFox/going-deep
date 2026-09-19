/** Lineup optimizer — issue #128, under the front-end-rebuild epic (#111). Ported from
 * `src/web/pages/lineup_optimizer.py` at parity: same slot order, same empty-slot and close-call
 * handling, same "no projection this week" distinction on the bench.
 *
 * Reads only the static export (`optimal_lineup`, `optimal_lineup_bench`, `current_week.json`) —
 * never `selection.week`/`selection.season` from `useLeagueWeek()`, because this page has no
 * manual week control of its own, same as the Streamlit page it replaces. `selection.leagueKey`
 * is the one thing it takes from the shared switcher; everything else about "which week" comes
 * from `current_week.json`, resolved at export time from `schedules` (see
 * `src/export/current_week.py`) since the export doesn't carry `schedules` itself. */

import { useEffect, useState } from 'react'
import { useLeagueWeek } from '../state/LeagueWeekContext'
import { DataTable, type Column } from '../components/DataTable/DataTable'
import { EmptyState } from '../components/EmptyState/EmptyState'
import { LoadingState } from '../components/LoadingState/LoadingState'
import { ErrorState } from '../components/ErrorState/ErrorState'
import { getRowKey } from '../lib/rowKey'
import { fetchManifest } from '../lib/manifest'
import { fetchCurrentWeek } from '../lib/currentWeek'
import { fetchExportFile, tableFilePath } from '../lib/exportFetch'
import type { OptimalLineupRow } from '../lib/fixtures'
import styles from './Lineup.module.css'

interface BenchRow {
  player_id: string | null
  player_name: string
  position: string
  projected_points: number | null
}

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

const starterColumns: Column<OptimalLineupRow>[] = [
  { key: 'slot', header: 'Slot', accessor: (r) => r.slot },
  {
    key: 'player',
    header: 'Player',
    render: (r) =>
      r.player_id ? r.player_name : <span className="unknown">not enough eligible players</span>,
  },
  {
    key: 'projected',
    header: 'Projected',
    render: (r) => (r.projected_points != null ? `${r.projected_points.toFixed(2)} pts` : '—'),
  },
  {
    key: 'closeCall',
    header: 'Close call',
    render: (r) =>
      r.is_close_call
        ? `vs. ${r.bench_player_name} (${(r.bench_projected_points ?? 0).toFixed(2)} pts, +${(
            (r.projected_points ?? 0) - (r.bench_projected_points ?? 0)
          ).toFixed(2)})`
        : '—',
  },
]

const benchColumns: Column<BenchRow>[] = [
  { key: 'player', header: 'Player', accessor: (r) => r.player_name },
  { key: 'position', header: 'Pos', accessor: (r) => r.position },
  {
    key: 'projected',
    header: 'Projected points',
    render: (r) => (r.projected_points != null ? r.projected_points.toFixed(2) : '—'),
  },
]

type LineupState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; lineup: OptimalLineupRow[]; bench: BenchRow[]; season: number; week: number }

export function Lineup() {
  const { selection } = useLeagueWeek()
  const [state, setState] = useState<LineupState>({ status: 'loading' })
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

        // The manifest's `available` list answers "does this file exist" so this never has to
        // probe with a fetch that 404s — the same rule `LeagueWeekContext`'s combo list follows.
        const hasLineup = manifest.available.some(
          (entry) =>
            entry.table === 'optimal_lineup' &&
            entry.league_key === current.league_key &&
            entry.season === current.season &&
            entry.week === current.week,
        )
        if (!hasLineup) {
          if (!cancelled) {
            setState({ status: 'ready', lineup: [], bench: [], season: current.season, week: current.week })
          }
          return
        }

        const [lineup, bench] = await Promise.all([
          fetchExportFile<OptimalLineupRow[]>(
            tableFilePath('optimal_lineup', current.league_key, current.season, current.week),
          ),
          fetchExportFile<BenchRow[]>(
            tableFilePath('optimal_lineup_bench', current.league_key, current.season, current.week),
          ),
        ])
        if (!cancelled) {
          setState({ status: 'ready', lineup, bench, season: current.season, week: current.week })
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

  if (state.status === 'loading') return <LoadingState label="Loading lineup…" />
  if (state.status === 'error') {
    return <ErrorState message={state.message} onRetry={() => setRetryToken((token) => token + 1)} />
  }

  const { lineup, bench, season, week } = state

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

  return (
    <div>
      <h1>Lineup Optimizer</h1>
      <p className={`mono ${styles.caption}`}>
        Week {week} · {season} season
      </p>

      <section className={styles.section}>
        <h2>Starters</h2>
        <DataTable
          columns={starterColumns}
          rows={starters}
          rowKey={(row, index) => getRowKey(row.player_id, row.player_name, row.slot, index)}
        />
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
          <DataTable
            columns={benchColumns}
            rows={available}
            rowKey={(row, index) => getRowKey(row.player_id, row.player_name, row.position, index)}
          />
        )}
      </section>
    </div>
  )
}
