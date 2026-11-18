/**
 * E2E only — models that exist only in the browser's mocks.
 *
 * `e2e/device-commerce.spec.ts` checks the model page against a made-up model it serves to the BROWSER with
 * `page.route` (so the page is tested without depending on what the local backend holds). The server cannot see
 * those mocks: its own read says «no such model», and since SEO-AUDIT #4 that is a 404 (`proxy.ts`) and a
 * `notFound()` (`app/modelo/[slug]/page.tsx`) before the page ever mounts.
 *
 * For a slug that starts with `E2E_MOCKED_MODEL_PREFIX` (set on the `next dev` that Playwright drives, e.g.
 * `E2E_MOCKED_MODEL_PREFIX=equipo-e2e-`), «missing» is treated as «not read»: no 404, no data from the server, and
 * the browser reads the model itself — and gets the mock — exactly as before the server read existed.
 *
 * It cannot be switched on in production: `next build` and `next start` always run with `NODE_ENV=production`
 * (and Next inlines that value into the build), so this is `false` there whatever the environment says. It also
 * needs a prefix that names «e2e», so a stray value can never cover every model.
 */
export function isE2eMockedModel(slug: string): boolean {
  if (process.env.NODE_ENV === "production") return false;
  const prefix = process.env.E2E_MOCKED_MODEL_PREFIX ?? "";
  return /e2e/i.test(prefix) && slug.startsWith(prefix);
}
