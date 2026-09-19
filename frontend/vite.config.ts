import path from 'node:path'
import fs from 'node:fs'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react(), command === 'serve' && serveLocalExport()],
}))

/** `npm run dev` has no Vercel Function and no Blob store, so it serves `data/export/` straight
 * off disk at the same paths `exportFetch.ts` requests in dev — the export's own layout
 * (`<table>/<league_key>/<season>-<week>.json`, `manifest.json`, `current_week.json`) becomes the
 * URL path with no prefix. A production build instead goes through `api/data.ts` (#130); this
 * plugin only has to reproduce serving files that already exist on disk, not that function's
 * auth or Blob lookup. Dev-only — `command === 'serve'` excludes it from `npm run build`. */
function serveLocalExport(): Plugin {
  const exportDir = path.resolve(import.meta.dirname, '../data/export')
  return {
    name: 'serve-local-export',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url) return next()
        const urlPath = decodeURIComponent(req.url.split('?')[0])
        const filePath = path.join(exportDir, urlPath)
        // path.join already collapses `..` segments; this rejects anything that still resolves
        // outside exportDir (e.g. an absolute-looking request) rather than trusting the client.
        if (!filePath.startsWith(exportDir) || !filePath.endsWith('.json')) return next()
        fs.readFile(filePath, (err, data) => {
          if (err) return next()
          res.setHeader('Content-Type', 'application/json')
          res.end(data)
        })
      })
    },
  }
}
