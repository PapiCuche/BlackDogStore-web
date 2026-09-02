# Auditoría RBAC y consola de accesos — 2026-09-02

## Alcance

Auditoría de autoridad de plataforma, roles empresariales, capacidades, aislamiento multiempresa, alcance por sucursal y superficie administrativa web. No se modifica autenticación, esquema de base de datos ni pagos.

## Baseline revisado

- Rama base: `master`.
- HEAD de inicio: `07a4583f75a635e705fe6300337090a3ed65690a`.
- Baseline heredado de la fase anterior: 2698 tests verdes, TypeScript/lint/build verdes. **No se vuelve a declarar como baseline ejecutado en esta rama**: esta fase no dispone de runner conectado y debe validarse antes de merge.

## Estado real

### IMPLEMENTADO

- **Master de plataforma:** `User.is_superuser`. Es la única autoridad transversal entre tenants y recibe todas las capacidades asignables.
- **Administrador de empresa:** autoridad limitada a su `Company`; `Membership.role=superadmin` no convierte al usuario en master de plataforma.
- **Roles configurables por empresa:** `CompanyRole` + catálogo de capacidades.
- **Asignación de múltiples roles:** `MembershipRoleAssignment`, con área opcional.
- **Anti-escalación:** un administrador de empresa solo puede crear/asignar capacidades que él mismo posee; el master de plataforma es la única excepción.
- **Aislamiento multiempresa:** los endpoints de áreas, roles, membresías y asignaciones se filtran por empresas visibles y revalidan ids no confiables.
- **Alcance por sucursal:** se separa correctamente `qué puede hacer` de `dónde puede hacerlo`.
- **Auditoría:** altas/cambios de roles y asignaciones generan `AdminAuditLog`.
- **Presets actuales:** Administrador, Ventas, Inventario y Servicio Técnico. El preset técnico puede diagnosticar/reparar y consumir repuestos aprobados por la reparación, pero no obtiene `inventory.adjust`.

### PARCIAL

- Conviven el RBAC SaaS por capacidades y el sistema heredado `UserProfile.role` / `Membership.role`.
- El frontend histórico de `/admin/users` gestionaba el rol global heredado aunque el backend moderno ya estaba orientado a membresías por empresa.
- Varios módulos conservan puentes legacy deliberados para no bloquear operadores pre-SaaS.

### PENDIENTE — seguridad antes de merge

`resolve_capabilities()` usa el rol heredado cuando no encuentra **asignaciones personalizadas activas**. Como las asignaciones se desactivan para conservar historial, una membresía que ya adoptó RBAC personalizado puede perder su última asignación activa y volver accidentalmente a la autoridad legacy (por ejemplo, un antiguo `admin`).

Regla que debe quedar implementada y cubierta por tests:

> El fallback legacy solo aplica a membresías que **nunca** adoptaron roles personalizados. Si existe historial de asignaciones personalizadas y ninguna está activa, la capacidad efectiva es cero.

Hasta cerrar esa regla, la nueva consola web adopta comportamiento conservador: permite asignar roles y editar capacidades, pero no expone la revocación del último rol ni la desactivación de roles en uso.

### OBSOLETO / deuda de transición

- Usar `UserProfile.role` como fuente primaria de autorización SaaS.
- Presentar “superadmin” de una membresía como si fuera autoridad global.
- Editar el rol global heredado desde la pantalla normal de personal de una empresa.

## Matriz recomendada

| Rol | Estado | Alcance recomendado |
| --- | --- | --- |
| Master de plataforma | IMPLEMENTADO | Todas las empresas; operación explícita por tenant; nunca asignable por empresa |
| Administrador de empresa | IMPLEMENTADO | Todas las capacidades asignables del tenant |
| Ventas | IMPLEMENTADO | Pedidos, POS, notas de venta; sin inventario administrativo ni configuración |
| Inventario | IMPLEMENTADO | Ver/ajustar/reportar inventario; limitado además por sucursal |
| Técnico | IMPLEMENTADO | Órdenes de servicio, diagnóstico, reparación; sin ajuste libre de inventario |
| Recepción | PROPUESTA | Clientes/dispositivos/crear y consultar órdenes; sin diagnóstico, reparación ni inventario |
| Caja | PROPUESTA | POS y cobro; sin analítica sensible, descuentos altos ni administración de roles |
| Supervisor técnico | PROPUESTA | Gestión de órdenes, diagnóstico, reparación y futura calidad; sin administración SaaS |
| Jefe de sucursal | PROPUESTA | Supervisión operativa de una o varias sucursales, sin autoridad de plataforma |
| Control de calidad | PENDIENTE | No conceder hasta que `service.quality.manage` deje de estar reservado |

## Cambios de interfaz en esta rama

- `/admin/users` pasa de “usuarios + rol global” a **Personal y accesos** por empresa.
- Usa `InternalControlGuard`, no el `AdminGuard` basado en nombres de rol legacy.
- Muestra roles empresariales, área, resumen de capacidades y alcance de sucursal.
- El master debe seleccionar empresa explícitamente antes de operar.
- Añade `/admin/roles` con catálogo de capacidades agrupado por módulo, estados Activo/Transición/Reservado y bloqueo visual de capacidades que el administrador no puede delegar.
- La UI explica que el nombre del rol no autoriza; la autorización real la decide el backend por capacidades.

## Archivos modificados

- `frontend/app/admin/users/page.tsx`
- `frontend/app/admin/roles/page.tsx`
- `docs/rbac-access-audit-2026-09-02.md`

## Migraciones

Ninguna.

## Tests

- Suite base conocida antes de esta rama: 2698 tests verdes (fase anterior).
- Tests de esta rama: **PENDIENTE EJECUTAR**.
- TypeScript/lint/build de esta rama: **PENDIENTE EJECUTAR**.
- No fusionar a `master` hasta validar y cerrar el fallback de revocación.

## Decisión técnica

Mantener dos niveles explícitos:

1. **Plataforma:** `User.is_superuser` (master).
2. **Empresa:** `Membership` + `CompanyRole` + capacidades + sucursales.

No crear un “master” como otro rol configurable del tenant, porque permitiría confundir o delegar autoridad transversal.

## Siguiente subfase

1. Endurecer `resolve_capabilities()` para distinguir “nunca migrado” de “RBAC adoptado pero sin rol activo”.
2. Añadir regresiones para revocación, rol inactivo, cross-tenant y master.
3. Ejecutar backend completo + typecheck + lint + build.
4. Solo después, habilitar revocación completa desde la consola y considerar el merge.
