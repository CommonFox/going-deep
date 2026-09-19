# frontend

The React SPA for `going-deep` (#111) — reads JSON exported by `src/export/` and is otherwise a
static site. No backend, no database in production.

```bash
npm install
npm run dev     # http://localhost:5173
npm run build   # type-checks then builds to dist/
npm run lint    # oxlint
```

`/kitchen-sink` renders every design-system primitive in every state (loading, empty, error,
stale) against fixture data — the place to eyeball a theme or component change. See #127.

`/lineup` is the real lineup optimizer (#128). `/waiver` is still a placeholder; it lands in #129.
