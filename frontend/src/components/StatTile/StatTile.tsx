import styles from './StatTile.module.css'

export type Tone = 'good' | 'bad' | 'neutral'

export function StatTile({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: string
  tone?: Tone
}) {
  return (
    <div className={styles.tile} data-tone={tone}>
      <div className={`label ${styles.label}`}>{label}</div>
      <div className={`mono ${styles.value}`}>{value}</div>
    </div>
  )
}
