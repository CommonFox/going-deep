import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from './components/AppShell/AppShell'
import { LeagueWeekProvider } from './state/LeagueWeekContext'
import { fixtureManifest } from './lib/fixtures'
import { Home } from './routes/Home'
import { KitchenSink } from './routes/KitchenSink'

// #128/#129 replace these with real pages; the routes exist now so adding them later is a page,
// not plumbing.
function LineupPlaceholder() {
  return <p>Lineup optimizer lands in #128.</p>
}

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
            <Route path="/lineup" element={<LineupPlaceholder />} />
            <Route path="/waiver" element={<WaiverPlaceholder />} />
            <Route path="/kitchen-sink" element={<KitchenSink />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </LeagueWeekProvider>
  )
}
