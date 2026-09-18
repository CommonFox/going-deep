import styles from './LoadingState.module.css'

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className={styles.loading} role="status" aria-live="polite">
      <span className={styles.spinner} aria-hidden="true" />
      {label}
    </div>
  )
}
