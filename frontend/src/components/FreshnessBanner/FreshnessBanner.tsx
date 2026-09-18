import { useLeagueWeek } from '../../state/LeagueWeekContext'
import { stalenessWarning } from '../../lib/manifest'
import styles from './FreshnessBanner.module.css'

/** Renders nothing when the export is fresh; a warning banner when it isn't. Lives above the
 * page, not inside it — src/web/app.py's precedent for why — so AppShell mounts it once for
 * every route rather than each page checking for itself. */
export function FreshnessBanner() {
  const { manifest } = useLeagueWeek()
  const message = stalenessWarning(new Date(manifest.built_at), new Date())
  return <FreshnessBannerView message={message} />
}

/** The presentational half, split out so the kitchen sink can demonstrate the stale case against
 * a fixture without needing a second LeagueWeekProvider. */
export function FreshnessBannerView({ message }: { message: string | null }) {
  if (!message) return null

  return (
    <div className={styles.banner} role="status">
      {message}
    </div>
  )
}
