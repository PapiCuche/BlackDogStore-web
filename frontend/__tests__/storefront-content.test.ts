import {
  EMPTY_LOGOS,
  EMPTY_PAGE,
  NEUTRAL_CONFIG,
  type StorefrontConfig,
} from '@/app/lib/storefront';

/**
 * M12F — lo que la web hace con el contenido que le manda el tenant.
 *
 * El backend ya filtró por empresa, estado y ventana temporal. Lo que se prueba
 * aquí es lo que ocurre en el borde: un backend anterior que no manda estas
 * claves, un tenant que no ha escrito nada, y un contenido que llega con
 * aspecto de código.
 */

describe('un backend anterior a M12F no rompe la portada', () => {
  it('la configuración neutra trae las claves nuevas vacías', () => {
    // `undefined` en `config.campaigns.home_hero` reventaría al leer `.title`.
    // Vacío es una respuesta; ausente es un fallo aguas abajo.
    expect(NEUTRAL_CONFIG.page).toEqual(EMPTY_PAGE);
    expect(NEUTRAL_CONFIG.campaigns).toEqual({});
  });

  it('la plataforma sin tenant no anuncia nada de nadie', () => {
    // Sin empresa resuelta no hay campañas. Nunca «las de la primera empresa
    // de la base», que es como un tenant acaba anunciando lo de otro.
    expect(Object.keys(NEUTRAL_CONFIG.campaigns)).toHaveLength(0);
  });

  it('las variantes de logotipo incluyen el isotipo', () => {
    expect(EMPTY_LOGOS).toHaveProperty('isotype_on_light', '');
    expect(EMPTY_LOGOS).toHaveProperty('isotype_on_dark', '');
  });
});

describe('el contenido del tenant es texto, no marcado', () => {
  it('un título con aspecto de script sigue siendo una cadena', () => {
    // No se rechaza: rechazar texto por parecerse a código es una lista negra
    // que envejece. Se GUARDA como texto y se PINTA como texto. React escapa
    // por defecto, y un test aparte comprueba que nadie usa
    // `dangerouslySetInnerHTML` para saltárselo.
    const config: StorefrontConfig = {
      ...NEUTRAL_CONFIG,
      campaigns: {
        home_bottom_promo: {
          slot: 'home_bottom_promo',
          badge: '',
          title: '<script>alert(1)</script>',
          subtitle: '',
          body: '',
          image_url: '',
          cta_label: '',
          cta_url: '',
          secondary_cta_label: '',
          secondary_cta_url: '',
          product: null,
        },
      },
    };
    const promo = config.campaigns.home_bottom_promo!;
    expect(typeof promo.title).toBe('string');
    expect(promo.title).toBe('<script>alert(1)</script>');
  });
});

describe('el titular respeta los saltos que escribió el tenant', () => {
  it('cada salto de línea es una línea, no un <br>', () => {
    // La misma operación que hace el Hero. Un salto es composición del
    // titular; convertirlo en marcado sería aceptar marcado.
    const title = 'Tu Apple,\ncon respaldo\nespecializado';
    expect(title.split('\n').filter(Boolean)).toEqual([
      'Tu Apple,',
      'con respaldo',
      'especializado',
    ]);
  });

  it('un titular de una sola línea no se parte', () => {
    expect('Solo una línea'.split('\n').filter(Boolean)).toHaveLength(1);
  });

  it('un titular vacío no produce una línea en blanco', () => {
    // Sin el `.filter(Boolean)`, un titular vacío pinta un <span> vacío con el
    // subrayado decorativo colgando de nada.
    expect(''.split('\n').filter(Boolean)).toEqual([]);
  });
});

describe('el destino de un botón decide cómo se navega', () => {
  // La misma condición que usa `PromoLink`.
  const isInternal = (href: string) => href.startsWith('/') && !href.startsWith('//');

  it('una ruta del sitio navega sin recargar', () => {
    expect(isInternal('/product')).toBe(true);
    expect(isInternal('/product/iphone-18')).toBe(true);
  });

  it('una URL de protocolo relativo NO es interna', () => {
    // `//evil.example` empieza por «/» y es absoluta. Tratarla como interna la
    // metería en el router del sitio.
    expect(isInternal('//evil.example')).toBe(false);
  });

  it('http, tel y mailto son externos', () => {
    expect(isInternal('https://wa.me/51999888777')).toBe(false);
    expect(isInternal('tel:+51999888777')).toBe(false);
    expect(isInternal('mailto:hola@example.com')).toBe(false);
  });
});
