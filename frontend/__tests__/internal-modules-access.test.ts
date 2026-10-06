import {
  INTERNAL_MODULES,
  canAccessModule,
  navigableModules,
  type ModuleAccessContext,
} from '@/app/admin/lib/internal-modules';

/**
 * RBAC-F3 — el menú del panel enseña lo que la página va a dejar abrir.
 *
 * Cada página decide con `InternalAccess.can`: con empresa resuelta mandan las
 * capacidades, y el rol antiguo sólo cuenta para el operador sin empresa. El
 * menú seguía otra regla: si las capacidades no alcanzaban, caía al rol
 * antiguo. Un miembro con rol antiguo «admin» y un rol de empresa sin
 * `sales.orders.view` veía «Pedidos» en el menú y, al entrar, «sin acceso».
 *
 * Auditoría tenía el defecto contrario: sólo declaraba rol antiguo, así que
 * quien tenía `memberships.view` —lo que el servidor y la página piden— no la
 * veía en el menú.
 */

const mod = (id: string) => {
  const found = INTERNAL_MODULES.find((m) => m.id === id && m.href);
  if (!found) throw new Error(`módulo ${id} no existe`);
  return found;
};

const member = (capabilities: string[], legacyRole: string | null = 'admin'): ModuleAccessContext => ({
  capabilities, legacyRole, isPlatformAdmin: false, hasCompanyContext: true,
});

const legacyOperator = (legacyRole: string): ModuleAccessContext => ({
  capabilities: [], legacyRole, isPlatformAdmin: false, hasCompanyContext: false,
});

describe('con empresa resuelta mandan las capacidades', () => {
  it.each(['sales.orders', 'admin.areas', 'admin.audit'])(
    'el rol antiguo «admin» no abre %s sin su capacidad',
    (id) => {
      expect(canAccessModule(mod(id), member([]))).toBe(false);
    },
  );

  it('quien tiene la capacidad entra aunque su rol antiguo no sea de administración', () => {
    expect(canAccessModule(mod('sales.orders'), member(['sales.orders.view'], 'technician'))).toBe(true);
    expect(canAccessModule(mod('admin.areas'), member(['areas.manage'], 'technician'))).toBe(true);
  });

  it('Auditoría pide lo mismo que su página y el servidor: memberships.view', () => {
    expect(canAccessModule(mod('admin.audit'), member(['memberships.view'], 'technician'))).toBe(true);
    expect(canAccessModule(mod('admin.audit'), member(['sales.orders.view'], 'admin'))).toBe(false);
  });

  it('el menú de un miembro sin capacidades sólo tiene lo que no pide ninguna', () => {
    const reachable = navigableModules(member([]));
    expect(reachable.every((m) => !m.requiredCapabilities?.length)).toBe(true);
  });
});

describe('sin empresa resuelta queda el rol antiguo', () => {
  it('el operador antiguo conserva los módulos que declaran su rol', () => {
    expect(canAccessModule(mod('sales.orders'), legacyOperator('sales'))).toBe(true);
    expect(canAccessModule(mod('admin.areas'), legacyOperator('admin'))).toBe(true);
  });

  it('y no entra a lo que no declara su rol', () => {
    expect(canAccessModule(mod('admin.areas'), legacyOperator('sales'))).toBe(false);
    expect(canAccessModule(mod('admin.audit'), legacyOperator('admin'))).toBe(false);
  });
});

describe('el master de la plataforma', () => {
  it('entra a todo', () => {
    const master: ModuleAccessContext = {
      capabilities: [], legacyRole: null, isPlatformAdmin: true, hasCompanyContext: false,
    };
    expect(INTERNAL_MODULES.every((m) => canAccessModule(m, master))).toBe(true);
  });
});

describe('el menú no repite nada', () => {
  // Dos entradas al mismo sitio obligan a preguntarse cuál es la buena, y dos
  // entradas con el mismo identificador hacen que una tape a la otra.
  it('cada pantalla tiene una sola entrada', () => {
    const hrefs = INTERNAL_MODULES.filter((m) => m.href).map((m) => m.href);
    const repeated = hrefs.filter((href, i) => hrefs.indexOf(href) !== i);
    expect(repeated).toEqual([]);
  });

  it('cada módulo tiene un identificador único', () => {
    const ids = INTERNAL_MODULES.map((m) => m.id);
    const repeated = ids.filter((id, i) => ids.indexOf(id) !== i);
    expect(repeated).toEqual([]);
  });
});

/**
 * INTEGRATIONS-CONSOLE — un módulo que sólo existe para el master.
 *
 * Las credenciales de los servicios externos no son de ningún rol de empresa:
 * ni el administrador con todas las capacidades, ni el operador antiguo. Un
 * módulo sin capacidades ni roles declarados era, hasta aquí, «para todos»;
 * `platformAdminOnly` se comprueba antes que nada de eso.
 */
describe('un módulo sólo para el master', () => {
  const integrations = () => mod('admin.integrations');
  const everyCapability = Array.from(
    new Set(INTERNAL_MODULES.flatMap((m) => m.requiredCapabilities ?? [])),
  );

  it('está registrado como tal, con una ruta', () => {
    expect(integrations().platformAdminOnly).toBe(true);
    expect(integrations().href).toBe('/admin/settings/integrations');
    expect(integrations().status).toBe('implemented');
  });

  it('no lo abre un administrador con todas las capacidades', () => {
    expect(canAccessModule(integrations(), member(everyCapability, 'admin'))).toBe(false);
    expect(navigableModules(member(everyCapability, 'admin')).map((m) => m.id)).not.toContain('admin.integrations');
  });

  it('ni el operador antiguo, con el rol que sea', () => {
    for (const role of ['admin', 'sales', 'technician', 'inventory']) {
      expect(canAccessModule(integrations(), legacyOperator(role))).toBe(false);
    }
  });

  it('el master lo ve en su menú', () => {
    const master: ModuleAccessContext = {
      capabilities: [], legacyRole: null, isPlatformAdmin: true, hasCompanyContext: true,
    };
    expect(navigableModules(master).map((m) => m.id)).toContain('admin.integrations');
  });

  it('es el único módulo así: nada más deja de estar al alcance de un administrador', () => {
    expect(INTERNAL_MODULES.filter((m) => m.platformAdminOnly).map((m) => m.id)).toEqual(['admin.integrations']);
  });
});
