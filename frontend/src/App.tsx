import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from './components/AppShell/AppShell'
import { LeagueWeekProvider } from './state/LeagueWeekContext'
import { fetchManifest, type Manifest } from './lib/manifest'
import { LoadingState } from './components/LoadingState/LoadingState'
import { ErrorState } from './components/ErrorState/ErrorState'
import { Home } from './routes/Home'
import { Lineup } from './routes/Lineup'
import { Waiver } from './routes/Waiver'
import { ViewingGuide } from './routes/ViewingGuide'
import { KitchenSink } from './routes/KitchenSink'

type ManifestState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; manifest: Manifest }

export function App() {
  // The real manifest now (#130) — /lineup (#128) and /waiver (#129) already fetched it
  // independently of this provider, which is why their rendered week could legitimately differ
  // from what the switcher above them showed while this was still fixture-fed. Loading/error
  // states mirror Lineup.tsx/Waiver.tsx's own pattern for the same fetch.
  const [state, setState] = useState<ManifestState>({ status: 'loading' })
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    let cancelled = false
    fetchManifest()
      .then((manifest) => {
        if (!cancelled) setState({ status: 'ready', manifest })
      })
      .catch((error) => {
        if (!cancelled) {
          setState({ status: 'error', message: error instanceof Error ? error.message : String(error) })
        }
      })
    return () => {
      cancelled = true
    }
  }, [retryToken])

  if (state.status === 'loading') return <LoadingState label="Loading…" />
  if (state.status === 'error') {
    return <ErrorState message={state.message} onRetry={() => setRetryToken((token) => token + 1)} />
  }

  return (
    <LeagueWeekProvider manifest={state.manifest}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Home />} />
            <Route path="/lineup" element={<Lineup />} />
            <Route path="/waiver" element={<Waiver />} />
            <Route path="/viewing-guide" element={<ViewingGuide />} />
            <Route path="/kitchen-sink" element={<KitchenSink />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </LeagueWeekProvider>
  )
}
