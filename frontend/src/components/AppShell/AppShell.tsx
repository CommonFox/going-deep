import { NavLink, Outlet } from 'react-router-dom'
import { FreshnessBanner } from '../FreshnessBanner/FreshnessBanner'
import { useLeague } from '../../state/LeagueContext'
import { platformLabel } from '../../lib/platform'
import styles from './AppShell.module.css'

/** Nav, freshness banner, and the platform toggle — wraps every route so none of them re-derive
 * this state per page the way the Streamlit app did. #180 replaced the old combined league/week
 * dropdown with this toggle once it turned out no route read the week axis at all. */
export function AppShell() {
  const { leagueKeys, leagueKey, setLeagueKey } = useLeague()

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <nav className={styles.nav}>
          <NavLink to="/" className={styles.brand}>
            going deep
          </NavLink>
          <NavLink
            to="/lineup"
            className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ''}`}
          >
            Lineup
          </NavLink>
          <NavLink
            to="/waiver"
            className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ''}`}
          >
            Waiver
          </NavLink>
          <NavLink
            to="/viewing-guide"
            className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ''}`}
          >
            Viewing Guide
          </NavLink>
          <NavLink
            to="/kitchen-sink"
            className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ''}`}
          >
            Kitchen Sink
          </NavLink>
        </nav>
        <div className={`mono ${styles.toggle}`} role="group" aria-label="Platform">
          {leagueKeys.map((key) => (
            <button
              key={key}
              type="button"
              className={styles.toggleButton}
              aria-pressed={key === leagueKey}
              onClick={() => setLeagueKey(key)}
            >
              {platformLabel(key)}
            </button>
          ))}
        </div>
      </header>
      <div className={styles.bannerSlot}>
        <FreshnessBanner />
      </div>
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  )
}
