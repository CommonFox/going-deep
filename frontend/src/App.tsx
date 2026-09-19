import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from './components/AppShell/AppShell'
import { LeagueWeekProvider } from './state/LeagueWeekContext'
import { fixtureManifest } from './lib/fixtures'
import { Home } from './routes/Home'
import { Lineup } from './routes/Lineup'
import { Waiver } from './routes/Waiver'
import { KitchenSink } from './routes/KitchenSink'

export function App() {
  // Still the fixture, not fetchManifest() — serving data/export/ to a dev server or a deploy is
  // #130's question (#127's own reasoning for the fixture), so the app shell's switcher and
  // freshness banner stay fixture-fed until #130 wires that up. /lineup (#128) and /waiver (#129)
  // both fetch the real manifest for their own data independently of this provider, which is why
  // their rendered week can legitimately differ from what the switcher above them shows — a
  // known, temporary split that #130 resolves by making this fetch real too.
  return (
    <LeagueWeekProvider manifest={fixtureManifest}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Home />} />
            <Route path="/lineup" element={<Lineup />} />
            <Route path="/waiver" element={<Waiver />} />
            <Route path="/kitchen-sink" element={<KitchenSink />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </LeagueWeekProvider>
  )
}
