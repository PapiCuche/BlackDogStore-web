/**
 * DEVICE-COMMERCE-01 · DC2 — one model as the public API describes it.
 *
 * Brand-neutral on purpose: the labels of the lines are NOT the platform's
 * defaults, so a page that printed its own words instead of the API's would
 * fail to find them.
 */

export const LINE_NEW = 'De caja';
export const LINE_USED = 'Reacondicionados';

const variant = (
  product_id: number, capacity: number, color: number, price: string, available: boolean,
  images: string[] = [], image_url = '',
) => ({
  product_id,
  slug: `equipo-x-${product_id}`,
  name: `Equipo X ${product_id}`,
  options: { '3': capacity, '4': color },
  price,
  image_url,
  images: images.map((url, index) => ({ url, alt_text: '', is_primary: index === 0, width: 800, height: 600 })),
  available,
  availability: available ? 'own_stock' : 'none',
  sells_by_unit: true,
});

export function modelFixture() {
  return {
    slug: 'equipo-x',
    name: 'Equipo X',
    description: 'Un equipo de prueba.',
    image_url: 'http://localhost/modelo.png',
    category: { slug: 'telefonos', name: 'Teléfonos' },
    attributes: [
      // Out of order on purpose: the page sorts by `sort_order`.
      {
        id: 4, name: 'Color', kind: 'color', sort_order: 1,
        options: [
          { id: 21, label: 'Azul', swatch_hex: '#1F3A93', sort_order: 0 },
          { id: 22, label: 'Blanco', swatch_hex: '#FFFFFF', sort_order: 1 },
          { id: 23, label: 'Rojo', swatch_hex: '#B91C1C', sort_order: 2 },
        ],
      },
      {
        id: 3, name: 'Capacidad', kind: 'text', sort_order: 0,
        options: [
          { id: 11, label: '128 GB', swatch_hex: '', sort_order: 0 },
          { id: 12, label: '256 GB', swatch_hex: '', sort_order: 1 },
          { id: 13, label: '512 GB', swatch_hex: '', sort_order: 2 },
        ],
      },
    ],
    lines: { new: { label: LINE_NEW, count: 5 }, used: { label: LINE_USED, count: 2 } },
    variants: [
      variant(41, 11, 21, '3999.00', true, [], 'http://localhost/azul-128.png'),
      variant(42, 11, 22, '3999.00', true, [], 'http://localhost/blanco-128.png'),
      variant(43, 12, 21, '4499.00', true, ['http://localhost/azul-256-a.png', 'http://localhost/azul-256-b.png']),
      // Exists, but nothing to sell: shown disabled, never hidden.
      variant(44, 12, 22, '4499.00', false, [], 'http://localhost/blanco-256.png'),
      // Rojo exists only at 512 GB.
      variant(45, 13, 23, '5499.00', true, [], 'http://localhost/rojo-512.png'),
    ],
    used_units: [
      {
        id: '0b4c7a52-1111-4a6e-9b1c-000000000001', product_id: 52,
        options: { Capacidad: '128 GB', Color: 'Azul' },
        battery_health: null,
        cosmetic_grade: { code: 'A', label: 'Excelente', description: 'Sin marcas visibles.' },
        price: '2599.00', photos: [], notes: 'Incluye caja.', imei_hint: '•••• 1234',
      },
      {
        id: '0b4c7a52-2222-4a6e-9b1c-000000000002', product_id: 53,
        options: { Capacidad: '256 GB', Color: 'Rojo' },
        battery_health: 87,
        cosmetic_grade: null,
        price: '2899.00',
        photos: [
          { url: 'http://localhost/u2-a.png', alt_text: '', width: 800, height: 600 },
          { url: 'http://localhost/u2-b.png', alt_text: '', width: 800, height: 600 },
        ],
        notes: '', imei_hint: '•••• 9876',
      },
    ],
    as_of: '2026-10-14T12:00:00Z',
  };
}

export function reply(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers(),
  } as unknown as Response;
}
