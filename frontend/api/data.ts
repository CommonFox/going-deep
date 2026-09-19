import { get } from '@vercel/blob'

/** Serves one file from the private Vercel Blob store `scripts/publish_export.py` uploads to
 * (#130) — the production counterpart of vite.config.ts's dev-only static server. A private store
 * has no public URL at all (see docs/export-format-spike.md's access-control note): every read
 * goes through a Function, which is what makes gating this route mean gating the data.
 *
 * No auth check here on purpose: this route is reached only through the deployed domain, and
 * Vercel Authentication (enabled on the project, not in code — see README.md) already rejects an
 * unauthenticated request before it reaches this function, the same as it does for every page
 * route. Adding a second check here would check a session that was already verified upstream.
 */
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const pathname = searchParams.get('pathname')
  if (!pathname) {
    return new Response(JSON.stringify({ error: 'missing pathname' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const result = await get(pathname, { access: 'private' })
  if (result?.statusCode !== 200) {
    return new Response('Not found', { status: 404 })
  }

  return new Response(result.stream, {
    headers: {
      'Content-Type': result.blob.contentType,
      'X-Content-Type-Options': 'nosniff',
      // Matches the fetch's own `cache: 'no-store'` (exportFetch.ts) — the manifest's freshness
      // contract requires every request to reach this function for real, never a cached reply.
      'Cache-Control': 'private, no-store',
    },
  })
}
