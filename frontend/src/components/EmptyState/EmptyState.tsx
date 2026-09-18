import styles from './EmptyState.module.css'

/** "No data yet" for one table/section — e.g. Streamlit's `st.info("No optimal_lineup data yet")`.
 * Distinct from FreshnessBanner: this says one table has nothing, not that the whole export is
 * stale. */
export function EmptyState({ message }: { message: string }) {
  return <div className={styles.empty}>{message}</div>
}
