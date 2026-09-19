import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from './components/AppShell/AppShell'
import { LeagueWeekProvider } from './state/LeagueWeekContext'
import { fixtureManifest } from './lib/fixtures'
import { Home } from './routes/Home'
import { Lineup } from './routes/Lineup'
import { KitchenSink } from './routes/KitchenSink'

// #129 replaces this with the real waiver board; the route exists now so adding it later is a
// page, not plumbing.
function WaiverPlaceholder() {
  return <p>Waiver board lands in #129.</p>
}

export function App() {
  return (
    <LeagueWeekProvider manifest={fixtureManifest}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Home />} />
            <Route path="/lineup" element={<Lineup />} />
            <Route path="/waiver" element={<WaiverPlaceholder />} />
            <Route path="/kitchen-sink" element={<KitchenSink />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </LeagueWeekProvider>
  )
}
