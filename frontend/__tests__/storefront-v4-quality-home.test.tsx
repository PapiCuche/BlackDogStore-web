import { render, screen, within } from '@testing-library/react';
import fs from 'fs';
import path from 'path';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { EMPTY_PAGE, NEUTRAL_CONFIG, type StorefrontConfig } from '@/app/lib/storefront';
import type { CatalogModelCard, CatalogModelList } from '@/app/lib/catalog-models';

/**
 * V4-QA — rendimiento móvil y accesibilidad de la portada V4.
 *
 *  · sin foto de hero, la foto del primer modelo es el LCP: se pide pronto y con
 *    prioridad alta también cuando es una imagen subida desde el panel (un `<img>`
 *    normal, donde `priority` sólo quita el `lazy`); las demás esperan;
 *  · los títulos siguen el orden: las campañas son h2 junto a «Modelos», nunca un
 *    h3 colgado de un h1.
 */

let pathname = '/v4';
jest.mock('next/navigation', () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const { HomeV4 } = require('@/app/components/v4/HomeV4');
/* eslint-enable @typescript-eslint/no-require-imports */

const MANAGED = (n: number) => `/api/storefront/images/${String(n).padStart(32, 'a')}`;

function card(slug: string, image_url: string): CatalogModelCard {
  return {
    slug, name: `Equipo ${slug}`, image_url, category: null,
    from_price: '3999.00', has_new: true, has_used: false, sold_out: false,
  } as CatalogModelCard;
}

function list(results: CatalogModelCard[]): CatalogModelList {
  return { lines: { new: { label: 'De caja', count: results.length }, used: { label: 'Reacondicionados', count: 0 } }, results };
}

function renderHome(models: CatalogModelList, cfg: Partial<StorefrontConfig> = {}) {
  pathname = '/v4';
  return render(
    <StorefrontProvider config={{ ...NEUTRAL_CONFIG, ...cfg }}>
      <HomeV4 models={models} />
    </StorefrontProvider>,
  );
}

describe('LCP de la portada sin foto de hero', () => {
  it('la foto del primer modelo va con prioridad alta y sin espera; las demás, diferidas', () => {
    renderHome(list([card('a', MANAGED(1)), card('b', MANAGED(2))]));
    const [first, second] = screen.getAllByRole('listitem').map((item) => item.querySelector('img') as HTMLImageElement);
    expect(first).toHaveAttribute('loading', 'eager');
    expect(first).toHaveAttribute('fetchpriority', 'high');
    expect(second).toHaveAttribute('loading', 'lazy');
    expect(second).not.toHaveAttribute('fetchpriority');
  });

  it('con foto de hero, ninguna tarjeta compite con ella', () => {
    renderHome(list([card('a', MANAGED(1))]), {
      page: { ...EMPTY_PAGE, hero_title: 'Tu próximo equipo', hero_image_url: MANAGED(9) },
    });
    const img = screen.getByRole('listitem').querySelector('img') as HTMLImageElement;
    expect(img).toHaveAttribute('loading', 'lazy');
    expect(img).not.toHaveAttribute('fetchpriority');
  });
});

describe('orden de los títulos', () => {
  const promo = {
    slot: 'home_featured', badge: '', title: 'Promo destacada', subtitle: '', body: 'Detalle.', image_url: '',
    cta_label: 'Ver', cta_url: '/v4', secondary_cta_label: '', secondary_cta_url: '', product: null,
  };

  it('sin hero, «Modelos» es el h1 y cada campaña un h2 (no salta a h3)', () => {
    renderHome(list([card('a', MANAGED(1))]), { campaigns: { home_featured: promo } as StorefrontConfig['campaigns'] });
    expect(screen.getByRole('heading', { level: 1, name: 'Modelos' })).toBeInTheDocument();
    const section = screen.getByRole('region', { name: 'Campañas' });
    expect(within(section).getByRole('heading', { level: 2, name: 'Promo destacada' })).toBeInTheDocument();
  });
});

describe('«Saltar al contenido» en la V4', () => {
  /* eslint-disable @typescript-eslint/no-require-imports */
  const { SkipLink, StorefrontContent } = require('@/app/components/StorefrontChrome');
  const { default: V4Layout } = require('@/app/v4/layout');
  /* eslint-enable @typescript-eslint/no-require-imports */

  it('salta la cabecera V4 y deja el foco en el contenido principal', () => {
    pathname = '/v4/modelo/equipo-x';
    render(
      <StorefrontProvider config={NEUTRAL_CONFIG}>
        <SkipLink />
        <StorefrontContent><V4Layout><p>ficha</p></V4Layout></StorefrontContent>
      </StorefrontProvider>,
    );
    const link = screen.getByRole('link', { name: 'Saltar al contenido' });
    const target = document.querySelector(link.getAttribute('href')!) as HTMLElement;
    expect(target).not.toBeNull();
    expect(target.tagName).toBe('MAIN');
    expect(target).toHaveAttribute('tabindex', '-1');
    expect(target).toHaveTextContent('ficha');
    // La cabecera V4 queda antes del destino: el salto la evita.
    expect(target.querySelector('header')).toBeNull();
    expect(screen.getByRole('main')).toBe(target);
  });

  it('un destino sólo programático no se pinta con el anillo de foco al recibir el salto', () => {
    const css = fs.readFileSync(path.join(process.cwd(), 'app/v4/v4.css'), 'utf8');
    expect(css).toMatch(/\[data-design="v4"\] main\[tabindex="-1"\]:focus-visible \{\s*outline: none;/);
  });
});
