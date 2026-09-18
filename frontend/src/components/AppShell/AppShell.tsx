import type { ChangeEvent } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { FreshnessBanner } from '../FreshnessBanner/FreshnessBanner'
import { useLeagueWeek } from '../../state/LeagueWeekContext'
import styles from './AppShell.module.css'

/** Nav, freshness banner, and the league/week switcher — wraps every route so none of them
 * re-derive this state per page the way the Streamlit app did. */
export function AppShell() {
  const { combos, selection, setSelection } = useLeagueWeek()

  const selectedIndex = combos.findIndex(
    (combo) =>
      combo.leagueKey === selection.leagueKey &&
      combo.season === selection.season &&
      combo.week === selection.week,
  )

  function handleComboChange(event: ChangeEvent<HTMLSelectElement>) {
    const combo = combos[Number(event.target.value)]
    if (combo) setSelection(combo)
  }

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
            to="/kitchen-sink"
            className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ''}`}
          >
            Kitchen Sink
          </NavLink>
        </nav>
        <select
          className={`mono ${styles.switcher}`}
          value={selectedIndex}
          onChange={handleComboChange}
          aria-label="League and week"
        >
          {combos.map((combo, index) => (
            <option key={`${combo.leagueKey}-${combo.season}-${combo.week}`} value={index}>
              {combo.leagueKey} · {combo.season} wk{combo.week}
            </option>
          ))}
        </select>
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
