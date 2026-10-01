import { targetHref, sourceLabel } from '@/app/admin/components/NotificationBell';
import {
  ALL_ACTIVE_COMPANIES,
  describeAudience,
  needsGlobalConfirmation,
  type AnnouncementPreview,
} from '@/app/lib/communications';

/**
 * M12C — comunicados, desde el lado del navegador.
 *
 * Lo que se prueba aquí no es que la lista pinte filas. Es lo que un descuido
 * en esta capa haría de verdad: publicar sin que nadie confirmara el alcance,
 * inventar una URL que el servidor no envió, o pintar como HTML un cuerpo que
 * alguien escribió a mano.
 */

jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
}));

const { fetchWithAuth } = jest.requireMock('@/app/lib/auth') as {
  fetchWithAuth: jest.Mock;
};

function reply(body: unknown, ok = true) {
  return Promise.resolve({ ok, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  fetchWithAuth.mockReset();
});

describe('la campana sigue siendo la única bandeja', () => {
  it('lleva un comunicado a su propia ruta, construida de campos estructurados', () => {
    expect(
      targetHref({ target_type: 'announcement', target_id: 42 }),
    ).toBe('/admin/communications/42');
  });

  it('no inventa una ruta cuando no hay destino', () => {
    expect(
      targetHref({ target_type: 'announcement', target_id: null }),
    ).toBeNull();
  });

  it('no confunde un comunicado con una orden de servicio', () => {
    expect(
      targetHref({ target_type: 'repair_order', target_id: 7 }),
    ).toBe('/admin/service/orders/7');
  });

  it('etiqueta lo que escribió una persona, y sólo eso', () => {
    expect(sourceLabel({ source: 'announcement' })).toBe('Comunicado');
    expect(sourceLabel({ source: 'system' })).toBeNull();
    expect(sourceLabel({ source: undefined })).toBeNull();
  });
});

describe('el alcance nunca es implícito', () => {
  it('el literal global se escribe entero', () => {
    // Si esto se acortara alguna vez, el backend dejaría de reconocerlo — que
    // es exactamente la protección que se busca.
    expect(ALL_ACTIVE_COMPANIES).toBe('ALL_ACTIVE_COMPANIES');
  });

  it('pide confirmación extra en cuanto el envío cruza empresas', () => {
    const one: AnnouncementPreview = {
      companies: [{ slug: 'a', name: 'A', recipient_count: 3 }],
      company_count: 1,
      recipient_count: 3,
    };
    const many: AnnouncementPreview = {
      companies: [
        { slug: 'a', name: 'A', recipient_count: 3 },
        { slug: 'b', name: 'B', recipient_count: 9 },
      ],
      company_count: 2,
      recipient_count: 12,
    };
    expect(needsGlobalConfirmation(one)).toBe(false);
    expect(needsGlobalConfirmation(many)).toBe(true);
    expect(needsGlobalConfirmation(null)).toBe(false);
  });
});

describe('el resumen de audiencia', () => {
  it('dice explícitamente cuando no hay destinatarios', () => {
    expect(describeAudience([])).toBe('Sin destinatarios');
    expect(describeAudience(undefined)).toBe('Sin destinatarios');
  });

  it('nombra cada regla con su objetivo', () => {
    expect(
      describeAudience([
        { kind: 'role', role: 'Ventas' },
        { kind: 'branch', branch: 'Miraflores' },
      ]),
    ).toBe('Rol: Ventas · Sucursal: Miraflores');
  });

  it('no inventa un objetivo para toda la empresa', () => {
    expect(describeAudience([{ kind: 'all_company' }])).toBe('Toda la empresa');
  });
});

describe('el detalle del comunicado', () => {
  async function renderDetail(body: string) {
    fetchWithAuth.mockImplementation(() =>
      reply({
        id: 5,
        title: 'Cierre por feriado',
        body,
        priority: 'info',
        status: 'published',
        author: 'Ana',
        created_at: '2026-09-02T10:00:00Z',
        published_at: '2026-09-02T10:00:00Z',
        recipient_count: 4,
      }),
    );
    const { readAnnouncement } = await import('@/app/lib/communications');
    return readAnnouncement('bd', 5);
  }

  it('trae el cuerpo completo tal cual lo escribieron', async () => {
    const a = await renderDetail('Primera línea.\nSegunda línea.');
    expect(a.body).toBe('Primera línea.\nSegunda línea.');
  });

  it('no interpreta el cuerpo: un script llega como texto', async () => {
    const payload = '<script>alert(1)</script>';
    const a = await renderDetail(payload);
    // El backend guarda lo que se escribió; quien lo pinta lo trata como
    // texto. La página usa {body} en JSX, nunca dangerouslySetInnerHTML.
    expect(a.body).toBe(payload);
  });

  it('un fallo del servidor se convierte en un error con mensaje', async () => {
    fetchWithAuth.mockImplementation(() =>
      reply({ detail: 'No encontrado.' }, false),
    );
    const { readAnnouncement } = await import('@/app/lib/communications');
    await expect(readAnnouncement('bd', 9)).rejects.toThrow('No encontrado.');
  });
});

describe('la página no pinta HTML del usuario', () => {
  it('ningún archivo de comunicados usa dangerouslySetInnerHTML', () => {
    const fs = jest.requireActual('fs') as typeof import('fs');
    const path = jest.requireActual('path') as typeof import('path');
    const roots = [
      path.join(process.cwd(), 'app/admin/communications'),
      path.join(process.cwd(), 'app/lib/communications.ts'),
    ];
    const files: string[] = [];
    const walk = (p: string) => {
      const stat = fs.statSync(p);
      if (stat.isDirectory()) {
        for (const entry of fs.readdirSync(p)) walk(path.join(p, entry));
      } else {
        files.push(p);
      }
    };
    roots.forEach(walk);
    expect(files.length).toBeGreaterThan(0);

    // Los COMENTARIOS se quitan antes de mirar. Estos archivos explican por
    // qué NO usan `dangerouslySetInnerHTML`, así que un escaneo de texto casa
    // con su propia explicación y el test acaba vigilando la prosa en vez del
    // código. La primera versión de esta prueba falló exactamente por eso.
    const codeOnly = (source: string) =>
      source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

    for (const file of files) {
      const source = codeOnly(fs.readFileSync(file, 'utf8'));
      expect(source).not.toContain('dangerouslySetInnerHTML');
      expect(source).not.toContain('eval(');
      expect(source).not.toContain('new Function');
    }
  });
});
