/**
 * The address an `<img>` shows. A panel image goes through the optimizer
 * (`/_next/image?url=…&w=…`), so the address asked for is its `url` parameter.
 */
export function imageSource(img: Element | null): string | null {
  const src = img?.getAttribute('src') ?? null;
  if (!src?.startsWith('/_next/image')) return src;
  return new URL(src, 'http://local').searchParams.get('url');
}
