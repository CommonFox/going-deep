import { useState } from 'react'
import { DataTable } from '../components/DataTable/DataTable'
import { DetailPanel, type DetailSection } from '../components/DetailPanel/DetailPanel'
import { StatTile } from '../components/StatTile/StatTile'
import { EmptyState } from '../components/EmptyState/EmptyState'
import { LoadingState } from '../components/LoadingState/LoadingState'
import { ErrorState } from '../components/ErrorState/ErrorState'
import { FreshnessBannerView } from '../components/FreshnessBanner/FreshnessBanner'
import { stalenessWarning } from '../lib/manifest'
import { getRowKey } from '../lib/rowKey'
import { starterColumns as optimalLineupColumns } from '../lib/lineupColumns'
import { waiverColumns } from '../lib/waiverColumns'
import { optimalLineupFixture, waiverRankingsFixture, staleFixtureManifest } from '../lib/fixtures'
import styles from './KitchenSink.module.css'

const TOKENS = ['bg', 'bg-muted', 'ink', 'ink-muted', 'border', 'accent', 'good', 'bad'] as const

const detailSections: DetailSection[] = [
  {
    title: 'Projections',
    fields: [
      { label: 'Sleeper', value: '9.97' },
      { label: 'FantasyPros', value: '11.20' },
      { label: 'ESPN', value: '—', tone: 'unknown' },
    ],
  },
  {
    title: 'Matchup',
    fields: [
      { label: 'Opponent', value: 'vs. DEN' },
      { label: 'Positional rank allowed', value: 'WR22', tone: 'bad' },
      { label: 'Implied team total', value: '24.5', tone: 'good' },
    ],
  },
  {
    title: 'Role trend',
    fields: [
      { label: 'Snap share (L3)', value: '71%' },
      { label: 'Target share (L3)', value: '19%' },
      { label: 'Direction', value: 'rising', tone: 'good' },
    ],
  },
]

const staleMessage = stalenessWarning(new Date(staleFixtureManifest.built_at), new Date())

/** The required gallery: every primitive, every state, for eyeballing changes — #127's own
 * acceptance criteria names this as the verification mechanism, not automated tests. */
export function KitchenSink() {
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    document.documentElement.setAttribute('data-theme', next)
  }

  return (
    <div>
      <div className={styles.toggleRow}>
        <button type="button" className={styles.toggleButton} onClick={toggleTheme}>
          Switch to {theme === 'dark' ? 'light' : 'dark'} mode
        </button>
        <span className="label">currently: {theme}</span>
      </div>

      <section className={styles.section}>
        <h2>Theme tokens</h2>
        <div className={styles.swatchGrid}>
          {TOKENS.map((token) => (
            <div className={styles.swatch} key={token}>
              <div className={styles.swatchColor} style={{ background: `var(--${token})` }} />
              <span className="mono" style={{ fontSize: 'var(--text-xs)' }}>
                --{token}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className={styles.section}>
        <h2>Type scale</h2>
        <div className={styles.typeScale}>
          <div style={{ fontSize: 'var(--text-xl)' }}>Page header — text-xl</div>
          <div style={{ fontSize: 'var(--text-lg)' }}>Section header — text-lg</div>
          <div style={{ fontSize: 'var(--text-md)' }}>Emphasized body — text-md</div>
          <div style={{ fontSize: 'var(--text-base)' }}>Body default — text-base</div>
          <div style={{ fontSize: 'var(--text-sm)' }}>Secondary text — text-sm</div>
          <div className="label">Table header / micro-label — text-xs, wide tracking</div>
          <div className="mono" style={{ fontSize: 'var(--text-md)' }}>
            123.45 — mono, tabular numerals
          </div>
        </div>
      </section>

      <section className={styles.section}>
        <h2>Stat tiles</h2>
        <div className={styles.tiles}>
          <StatTile label="Projected" value="19.42" tone="neutral" />
          <StatTile label="Vs. replacement" value="+42.1" tone="good" />
          <StatTile label="Injury risk" value="Questionable" tone="bad" />
        </div>
      </section>

      <section className={styles.section}>
        <h2>Data table — optimal lineup (normal, close call, no eligible player)</h2>
        <DataTable
          columns={optimalLineupColumns}
          rows={optimalLineupFixture}
          rowKey={(row, index) => getRowKey(row.player_id, row.player_name, row.slot, index)}
        />
      </section>

      <section className={styles.section}>
        <h2>
          Data table — waiver rankings (unidentified player, no weekly projection, ESPN source, no
          replacement-level context)
        </h2>
        <DataTable
          columns={waiverColumns}
          rows={waiverRankingsFixture}
          rowKey={(row, index) => getRowKey(row.player_id, row.player_name, row.position, index)}
        />
      </section>

      <section className={styles.section}>
        <h2>Detail panel</h2>
        <DetailPanel title="Alec Pierce — WR, IND" sections={detailSections} defaultOpen />
      </section>

      <section className={styles.section}>
        <h2>States</h2>
        <div className={styles.stateGrid}>
          <LoadingState />
          <EmptyState message="No optimal_lineup data yet" />
          <ErrorState message="Could not load waiver_rankings." onRetry={() => {}} />
          <FreshnessBannerView message={staleMessage} />
        </div>
      </section>
    </div>
  )
}
