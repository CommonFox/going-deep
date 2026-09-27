/** Waiver board — issue #129, under the front-end-rebuild epic (#111). Ported from
 * `src/web/pages/waiver_board.py` at parity: same rows, same default filters, weekly/ROS as two
 * independently sortable columns (never blended), on-waivers players shown by default with a
 * badge, ESPN-sourced weekly points tagged, and replacement-level context per row — now via the
 * data-table primitive instead of one bordered container per row (the ticket's presentational
 * change), with `DataTable`'s own click-to-sort headers standing in for Streamlit's explicit
 * "Sort by" radio — `defaultSortKey`/`defaultSortDir` below reproduce its default choice ("This
 * week", descending) so the table opens already sorted rather than in export order.
 *
 * `waiver_rankings` is already scoped to each league's own current week at build time (see the
 * gold table's own docstring), so unlike `/lineup` this page needs no `current_week.json` lookup —
 * the manifest's `available` entries for this table already name the one (season, week) file each
 * league has.
 *
 * #173 gives every row a collapsed-by-default `DetailPanel`, reusing the same four sections and
 * `weekly_player_context` shape #161 built for `/lineup` (`buildDetailSections`) rather than
 * inventing a second layout. `weekly_player_context` is keyed by `player_id`, the same crosswalk
 * `waiver_rankings` itself resolves through, and a row with no resolved `player_id` (or no matching
 * `weekly_player_context` row) falls back to `undefined`, which `buildDetailSections` already
 * renders as every field `tone: 'unknown'` rather than the panel erroring or hiding. No opponent
 * pairing here — that's #162's close-call concept, and there's no waiver-board equivalent. */

import { useEffect, useMemo, useState } from 'react'
import { useLeague } from '../state/LeagueContext'
import { DataTable } from '../components/DataTable/DataTable'
import { DetailPanel } from '../components/DetailPanel/DetailPanel'
import { EmptyState } from '../components/EmptyState/EmptyState'
import { LoadingState } from '../components/LoadingState/LoadingState'
import { ErrorState } from '../components/ErrorState/ErrorState'
import { getRowKey } from '../lib/rowKey'
import { fetchManifest, isAvailable } from '../lib/manifest'
import { fetchExportFile, tableFilePath } from '../lib/exportFetch'
import { waiverColumns } from '../lib/waiverColumns'
import { buildDetailSections } from '../lib/playerDetail'
import type { WaiverRankingRow, WeeklyPlayerContextRow } from '../lib/fixtures'
import styles from './Waiver.module.css'

const POSITIONS = ['QB', 'RB', 'WR', 'TE']

// Same lookup Lineup.tsx uses for its own `context` map: a free agent whose `player_id` never
// resolved (waiver_rankings' own documented gap) falls back to `undefined`, which
// `buildDetailSections` already renders as every field `tone: 'unknown'` rather than erroring.
function contextFor(
  playerId: string | null,
  context: Map<string, WeeklyPlayerContextRow>,
): WeeklyPlayerContextRow | undefined {
  return playerId ? context.get(playerId) : undefined
}

type WaiverState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | {
      status: 'ready'
      rows: WaiverRankingRow[]
      context: Map<string, WeeklyPlayerContextRow>
      season: number
      week: number
    }

export function Waiver() {
  const { leagueKey } = useLeague()
  const [state, setState] = useState<WaiverState>({ status: 'loading' })
  const [retryToken, setRetryToken] = useState(0)
  const [positions, setPositions] = useState<string[]>(POSITIONS)
  const [includeOnWaivers, setIncludeOnWaivers] = useState(true)

  useEffect(() => {
    let cancelled = false

    async function load() {
      setState({ status: 'loading' })
      try {
        const manifest = await fetchManifest()
        // The manifest's `available` list answers "does this file exist" so this never has to
        // probe with a fetch that 404s — the same rule `LeagueContext`'s leagueKeys list follows.
        const entry = manifest.available.find(
          (candidate) => candidate.table === 'waiver_rankings' && candidate.league_key === leagueKey,
        )
        if (!entry) {
          throw new Error(`no waiver_rankings published for league "${leagueKey}"`)
        }

        const hasContext = isAvailable(manifest, 'weekly_player_context', entry)

        const [rows, contextRows] = await Promise.all([
          fetchExportFile<WaiverRankingRow[]>(tableFilePath('waiver_rankings', entry)),
          hasContext
            ? fetchExportFile<WeeklyPlayerContextRow[]>(tableFilePath('weekly_player_context', entry))
            : Promise.resolve<WeeklyPlayerContextRow[]>([]),
        ])
        const context = new Map(contextRows.map((row) => [row.player_id, row]))
        if (!cancelled) {
          setState({ status: 'ready', rows, context, season: entry.season, week: entry.week })
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

  const filtered = useMemo(() => {
    if (state.status !== 'ready') return []
    return state.rows
      .filter((row) => positions.includes(row.position))
      .filter((row) => includeOnWaivers || row.availability !== 'on_waivers')
  }, [state, positions, includeOnWaivers])

  function togglePosition(position: string) {
    setPositions((current) =>
      current.includes(position) ? current.filter((p) => p !== position) : [...current, position],
    )
  }

  if (state.status === 'loading') return <LoadingState label="Loading waiver board…" />
  if (state.status === 'error') {
    return <ErrorState message={state.message} onRetry={() => setRetryToken((token) => token + 1)} />
  }

  const { season, week, context } = state

  return (
    <div>
      <h1>Waiver Board</h1>
      <p className="mono caption">
        Week {week} · {season} season
      </p>

      <div className={styles.filters}>
        <div className={styles.positionFilter}>
          {POSITIONS.map((position) => (
            <label key={position} className={styles.checkboxLabel}>
              <input
                type="checkbox"
                checked={positions.includes(position)}
                onChange={() => togglePosition(position)}
              />
              {position}
            </label>
          ))}
        </div>
        <label className={styles.checkboxLabel}>
          <input
            type="checkbox"
            checked={includeOnWaivers}
            onChange={(event) => setIncludeOnWaivers(event.target.checked)}
          />
          Include on-waivers
        </label>
      </div>

      {filtered.length === 0 ? (
        <EmptyState message="No available players match this filter." />
      ) : (
        <>
          <DataTable
            columns={waiverColumns}
            rows={filtered}
            rowKey={(row, index) => getRowKey(row.player_id, row.player_name, row.position, index)}
            defaultSortKey="weekly"
            defaultSortDir="desc"
          />
          <div className={styles.detailList}>
            {filtered.map((row, index) => (
              <DetailPanel
                key={getRowKey(row.player_id, row.player_name, row.position, index)}
                title={`${row.player_name} — ${row.position}`}
                sections={buildDetailSections(contextFor(row.player_id, context))}
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
