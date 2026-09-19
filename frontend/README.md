# frontend

The React SPA for `going-deep` (#111) — reads JSON exported by `src/export/` and is otherwise a
static site. No app backend, no database; the one exception is `api/data.ts` (#130), a small
Vercel Function that proxies reads from the private Blob store production data lives in — see the
root `README.md`'s "Front end" section for the full rebuild → export → publish → deploy loop.

```bash
npm install
npm run dev     # http://localhost:5173, data/export/ served straight off disk (vite.config.ts)
npm run build   # type-checks then builds to dist/
npm run lint    # oxlint
npm run test    # vitest
```

`/kitchen-sink` renders every design-system primitive in every state (loading, empty, error,
stale) against fixture data — the place to eyeball a theme or component change. See #127.

`/lineup` is the real lineup optimizer (#128). `/waiver` is the real waiver board (#129).
