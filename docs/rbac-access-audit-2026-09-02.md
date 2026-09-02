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

### RESUELTO — el bloqueo de seguridad (M11)

`resolve_capabilities()` usaba el rol heredado cuando no encontraba **asignaciones personalizadas activas**. Como las asignaciones se desactivan para conservar historial, una membresía que ya había adoptado RBAC podía perder su última asignación activa y volver a la autoridad legacy. En el caso peor —rol heredado `admin`— **quitarle a alguien su único rol lo convertía en administrador de la empresa**.

Regla implementada:

> El fallback legacy solo aplica a membresías que **nunca** adoptaron roles personalizados. Si existe historial de asignaciones y ninguna está activa, la capacidad efectiva es cero.

El discriminador no es «cuántos roles activos tiene» sino «¿esta empresa ha expresado alguna vez la autoridad de esta persona mediante RBAC?». Se responde con `has_custom_role_history(membership)` → `role_assignments.exists()`, que es fiable **porque la revocación es un soft-disable**: nada en el proyecto borra un `MembershipRoleAssignment`. Un test fija esa propiedad, porque un borrado físico futuro borraría la prueba de que la membresía migró y rearmaría el fallback en silencio.

Prueba negativa: neutralizar la guarda hace fallar 5 tests; restaurarla los deja en verde.

Con el backend cerrado, la consola **ya expone** la revocación individual de roles y la activación/desactivación de roles en uso.

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
| Recepción | **ABSORBIDO EN VENTAS** | Decisión de producto: no hay preset propio |
| Caja | **ABSORBIDO EN VENTAS** | Decisión de producto: no hay preset propio |
| Supervisor técnico | **IMPLEMENTADO** (M11) | Órdenes, asignación, diagnóstico y reparación; sin administración SaaS ni `inventory.adjust` |
| Jefe de sucursal | PROPUESTA — **bloqueada por el modelo** | Ver más abajo |
| Control de calidad | DESCARTADO EN ESTA FASE | `service.quality.manage` sigue reservada; no se crea preset |

### Ventas = Ventas + Recepción + Caja (M11)

La granularidad necesaria **ya existía**; no hizo falta inventar ninguna capacidad ni conceder de más. Ningún endpoint de recepción exige `service.manage`:

| Operación de mostrador | Capacidad que la protege |
| --- | --- |
| Buscar al cliente | `service.customers.view` |
| Registrar un cliente nuevo | `service.customers.manage` |
| Anotar el equipo | `service.devices.view` / `.manage` |
| Abrir la orden de recepción | `service.orders.create` |
| Consultarla para atender | `service.orders.view` |

Lo que Ventas **no** recibe, y por qué:

| Excluida | Razón |
| --- | --- |
| `service.manage` | Es el módulo entero; recepción no lo necesita |
| `service.orders.manage` | Mover la orden por el taller es dirigir el banco, no recibir |
| `service.diagnostic.manage` / `service.repair.manage` | Decir qué falla y arreglarlo es el trabajo técnico |
| `inventory.*` | Un mostrador que vende no es un mostrador que corrige la estantería |
| `sales.discounts.apply` | Decidir el precio es de supervisión; cobrarlo no |
| `sales.analytics.view` | Cobrar un cable no exige ver la facturación |

**La matriz legacy NO creció.** Divergen a propósito: la matriz responde por membresías que nunca adoptaron RBAC —operadores pre-SaaS— y añadirle recepción les daría acceso al taller porque el software se publicó, que es exactamente la «autoridad que nadie decidió» que este proyecto rechaza. El preset crece porque una empresa lo elige.

### Por qué *Jefe de sucursal* queda en PROPUESTA

No es falta de capacidades: es que **el alcance por sucursal no vive en el rol**. `branch_access_mode` y `MembershipBranchAccess` son atributos de la **membresía**, deliberadamente independientes del rol (§5). Un `CompanyRole` no puede exigir «este rol solo tiene sentido con alcance restringido»: un administrador podría asignarlo con `branch_access_mode = ALL` y el resultado sería un supervisor de toda la empresa con nombre de jefe de sucursal.

Crear el preset daría una garantía que el modelo no sostiene. Implementarlo de verdad requiere que el alcance obligatorio sea expresable —por ejemplo, un rol que declare `requires_branch_scope`— y eso es infraestructura de negocio que esta fase no tenía por qué inventar.

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

## Hallazgo adicional de M11 — duplicados de asignación

`UniqueConstraint(membership, role, area)` **no cubría el caso normal**. `area` es nullable y en SQL dos NULL nunca son iguales, así que `(membresía, rol, NULL)` jamás colisionaba consigo mismo: la base de datos aceptaba dos asignaciones idénticas. Medido contra una base real antes de escribir nada, no deducido.

Lo único que lo impedía era un `.exists()` previo a la inserción en la vista —una lectura antes de una escritura, que dos peticiones concurrentes atraviesan— exactamente la forma que P0-E ya había declarado insuficiente para las líneas de carrito.

Corregido con un índice único parcial `WHERE area IS NULL`, precedido de una migración que consolida los duplicados que el hueco pudiera haber dejado.

## Fuente única de autorización (§28)

`CompanyContext.can()` responde desde `COMPANY_CAPABILITIES`, la matriz de la Fase 2A basada en el rol heredado. Sería un riesgo real de doble autoridad **si algo la usara**: no lo usa nada. `build_company_context()` y `.can()` no tienen ningún llamador en el runtime; solo los ejercitan sus propios tests.

Por eso M11 **no la reescribe**: cambiar código dormido para arreglar un problema que no está causando es como una fase de seguridad se convierte en una refactorización. En su lugar hay un test estructural que recorre los módulos de runtime y falla si alguien la conecta, de modo que unificarla tenga que ser una decisión consciente.

Clasificación: **OBSOLETO / deuda de transición.**

## Migraciones

| Migración | Propósito |
| --- | --- |
| `0054_sales_reception_and_service_supervisor` | Da recepción a los presets `Ventas` **sin modificar** (igualdad exacta) y ofrece `Supervisor Técnico` a las empresas existentes |
| `0055_consolidate_duplicate_role_assignments` | Desactiva duplicados `(membresía, rol)` sin área conservando el historial |
| `0056_role_assignment_uniqueness` | Índice único parcial `WHERE area IS NULL` |

## Tests

Ejecutados en esta rama; cifras reales en el informe de la fase.

- Baseline al empezar M11: **2698 verdes**.
- Suite completa tras M11: ver informe.
- Prueba negativa del fallback: 5 fallos al retirar la guarda, verde al restaurarla.

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

## Nota sobre capacidades reservadas (tras fusionar `master`)

Esta fase tenía instrucción explícita de **no** convertir capacidades reservadas en asignables y de mantener `service.quality.manage` reservada. M11 la respetó: no la tocó.

Durante el desarrollo, `master` incorporó el PR #12 (control de calidad), que **hizo asignable** `service.quality.manage` y la añadió al preset `Servicio Técnico`. Es una decisión de esa fase, posterior a este encargo, y M11 no la deshace.

Consecuencias que sí corresponden anotar:

- El catálogo se queda **sin ninguna capacidad reservada**: reservadas = 0, asignables = 36 de 36.
- `Supervisor Técnico` la hereda, porque se define como `_TECHNICIAN_CAPS + (…)`. Es coherente con lo que la propia matriz de esta auditoría proponía para el rol («diagnóstico, reparación y futura calidad»).
- **No se crea** ningún preset de Control de Calidad, ni navegación, ni pantalla, ni asignación estándar, como pedía el encargo.
- Un test de M11 asumía que existía al menos una capacidad reservada y se volvió engañoso al vaciarse el conjunto. Reescrito: la invariante «reservada ⇒ no asignable» se comprueba siempre, y el caso concreto se omite con motivo explícito mientras no haya ninguna.

---

## M11.1 — estabilización antes de merge

Revisión independiente del PR #10. Tres bloqueos, los tres reales.

### 1. La migración de consolidación no preparaba nada (crítico)

`0048` desactivaba las filas duplicadas y las conservaba. `0049` añade
`UNIQUE (membership, role) WHERE area IS NULL`, que **no menciona `is_active`**:
dos filas del mismo par colisionan estén activas o no. Contra una base con
duplicados reales, `AddConstraint` fallaba con `IntegrityError` — comprobado
ejecutando la secuencia, no deducido.

El error vale nombrarlo porque es fácil de repetir: el soft-delete es el
instinto correcto para revocar **autoridad** y el incorrecto para eliminar una
**fila** que el esquema prohíbe. Se parecen y no son lo mismo.

**Corregido eliminando de verdad las filas redundantes**, tras comprobar que es
seguro: `MembershipRoleAssignment` no tiene FKs entrantes, y `AdminAuditLog`
registra cada alta, cambio y baja con el id de la asignación, el rol, la empresa
y el actor. La traza no vive en esas filas. Sobrevive la más antigua —conserva
`created_at` y `assigned_by` del otorgamiento original— y queda activa si
**alguna** de las duplicadas lo estaba: consolidar almacenamiento no puede
devolver autoridad revocada ni retirar la vigente.

Cubierto por un test de migración que inserta duplicados por SQL crudo con la
constraint retirada, ejecuta la consolidación y **vuelve a añadir la
constraint** — que es la aserción que faltaba. Prueba negativa: con la versión
anterior de `0048`, 7 de 9 casos fallan.

### 2. La consola llamaba «Legacy» a quien ya había migrado

La tarjeta colapsada decidía por `activeAssignments.length`, así que alguien con
historial custom y cero roles activos aparecía como *Legacy: Administrador* —
insinuando una autoridad que el backend no le concede. Ahora hay tres estados
explícitos, derivados del mismo dato que lee el servidor.

### 3. La reactivación no existía en la UI

El backend ya la soportaba (`PATCH is_active=true`, revalidando delegación) pero
la consola sólo sabía quitar. Además, el selector podía ofrecer un rol con
asignación histórica y terminar en un alta que la base rechaza.

Ahora quitar y reactivar reutilizan **la misma fila**, y el selector propone
reactivar cuando ya existe el hueco lógico `(rol, área)`.

### Hallazgo adicional: la carrera del alta daba 500

`.exists()` antes de `create()` es una lectura antes de una escritura. Con la
constraint puesta, el perdedor de la carrera recibía un `IntegrityError` sin
capturar. Traducido a 400 explicado, re-lanzando cualquier otro. Reconoce las
dos formas del mensaje: **PostgreSQL nombra la constraint, SQLite nombra las
columnas** — buscar sólo el nombre funcionaba en producción y fallaba en la
suite, el sentido contrario al útil.
