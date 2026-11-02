import { isAppleTouchDevice, pdfInlineSupport } from '@/app/admin/lib/pdf-inline-support';

const MAC_SAFARI = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15';
const MAC_CHROME = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';
const WIN_CHROME = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const IPAD = 'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';
const ANDROID = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36';

test.each([
  ['Safari de Mac', { pdfViewerEnabled: true, userAgent: MAC_SAFARI, maxTouchPoints: 0 }, 'inline'],
  ['Chrome de Mac', { pdfViewerEnabled: true, userAgent: MAC_CHROME, maxTouchPoints: 0 }, 'inline'],
  ['Chrome de Windows con pantalla táctil', { pdfViewerEnabled: true, userAgent: WIN_CHROME, maxTouchPoints: 10 }, 'inline'],
  ['Chrome sin visor (Android)', { pdfViewerEnabled: false, userAgent: ANDROID, maxTouchPoints: 5 }, 'no-viewer'],
  ['navegador que no declara la propiedad', { userAgent: WIN_CHROME, maxTouchPoints: 0 }, 'no-viewer'],
  ['iPhone', { pdfViewerEnabled: true, userAgent: IPHONE, maxTouchPoints: 5 }, 'apple-touch'],
  ['iPad', { pdfViewerEnabled: true, userAgent: IPAD, maxTouchPoints: 5 }, 'apple-touch'],
  ['iPad en modo escritorio (se presenta como Mac)', { pdfViewerEnabled: true, userAgent: MAC_SAFARI, maxTouchPoints: 5 }, 'apple-touch'],
  ['iPhone sin visor', { pdfViewerEnabled: false, userAgent: IPHONE, maxTouchPoints: 5 }, 'no-viewer'],
] as const)('%s → %s', (_name, nav, expected) => {
  expect(pdfInlineSupport(nav as unknown as Navigator)).toBe(expected);
});

test('sin navigator (servidor): sin visor', () => {
  expect(pdfInlineSupport(undefined)).toBe('no-viewer');
});

test('un Mac con una sola entrada táctil no es un iPad', () => {
  expect(isAppleTouchDevice({ userAgent: MAC_SAFARI, maxTouchPoints: 1 })).toBe(false);
});
