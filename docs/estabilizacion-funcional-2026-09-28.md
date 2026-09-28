# Estabilización funcional — auditoría y entrega

Fecha: 2026-09-28. Repositorio: `PapiCuche/BlackDogStore-web`.
Rama auditada: `erp/sales-fiscal-ui`, HEAD inicial `0f39c6be01661a348eefa4e53809eaf28aef6107`.
Árbol inicial limpio. No se cambió de rama ni se hizo commit, merge o push.

## Fuentes y baseline

No existían los ocho archivos numerados `00_CONTEXTO_MAESTRO.md` a
`07_CHANGELOG.md` solicitados. Se inspeccionaron código, modelos, migraciones,
tests, README y la documentación real: estado-actual-y-auditoria-tecnica,
saas-multiempresa, inventario-y-notas-de-venta, h41-servicio-tecnico-401,
adr-auth-v1-internal y las entregas fiscales recientes. Los documentos antiguos
que describen servicio técnico como pendiente o v1 como exclusivamente Bearer
son OBSOLETOS para esta rama. Las decisiones fiscales no implementadas de
`entrega-sales-fiscal-ui-gap.md` continúan siendo PROPUESTA/PENDIENTE.

Se crean `02_ESTADO_ACTUAL.md` y `07_CHANGELOG.md` como entradas resumidas a las
fuentes existentes; no se fabrican documentos históricos ni decisiones previas.

| Comprobación inicial | Resultado observado |
|---|---|
| Backend | Django 5.2.17, DRF 3.17.1; base configurada local SQLite |
| Suite backend | 4388 tests; OK, 25 omitidos; 1224,591 segundos |
| Configuración | Primer arranque falló por CORS de producción. Suite ejecutada con `DEBUG=1`, sin editar `.env` |
| Migraciones locales | 8 pendientes: 0085–0092; `makemigrations --check --dry-run`: sin cambios |
| Datos de reparación locales | 0 órdenes; existen los cinco presets y 5 membresías |
| Frontend | Next.js 16.3.4 / React 19.2.4 |
| Jest | 341 tests, 29 suites, aprobados |
| TypeScript | Inicialmente correcto; tras generar `.next/types`, aparecen dos errores preexistentes de exports de páginas |
| ESLint | 0 errores, 33 advertencias |
| Build normal | Turbopack: descarga de fuentes y luego restricción de apertura de puerto |
| Build con Webpack | Compila; falla en typecheck: `CountStatusBadge` y `TransferStatusBadge` exportados desde `page.tsx` |

## POS — IMPLEMENTADO con alcance fiscal BETA

El defecto estaba en todo el contrato: la pantalla no preguntaba y las dos
vistas ignoraban `receipt_type`. Se admitía incluso un tipo inventado y el hash
de idempotencia no distinguía documentos distintos.

- Opciones procedentes del backend, filtradas por capacidades, sucursales
  visibles, habilitación fiscal y series de la empresa/sucursal.
- **Nota de venta interna:** exige `sales.notes.manage`; se crea el `SalesNote`
  existente en la misma transacción que la venta. No tiene validez tributaria.
- **Boleta/factura:** exigen `sales.fiscal.issue`, `FISCAL_ENABLED` y serie
  resoluble BETA. Se conserva `Order.receipt_type` y se prepara el
  `FiscalDocument` existente en estado `GENERATED`. No se envía a SUNAT al cobrar.
- Los requisitos de receptor, RUC, límites de boleta, gravado y descuentos son
  los del validador fiscal existente. Una negativa revierte venta, documento,
  número y stock. No se agregaron reglas tributarias ni se habilitó producción.
- La nota se identifica persistentemente por `SalesNote` y su relación uno a
  uno con `Order`; el campo fiscal de la venta queda vacío. No se añadieron
  elecciones fiscales artificiales al checkout ni columnas duplicadas.
- El resultado del POS devuelve tipo, número y estado. El histórico fiscal se
  conserva en `Order`/`FiscalDocument`; el histórico interno, en `SalesNote`.
  Reimpresión y consulta utilizan esos registros, no la configuración actual.
- Se reutilizan `InternalSequence` y `FiscalSeries`, incluyendo sus bloqueos y
  restricciones existentes. La lectura del contexto no aprovisiona series.
- La selección forma parte de la huella de idempotencia. Omitirla conserva el
  contrato y las huellas de clientes anteriores; estos no emiten automáticamente.
- La interfaz distingue firma, envío y aceptación. Boletas y facturas BETA se
  consultan en el panel fiscal; los botones de emisión requieren capacidad.
- La boleta se informa por el Resumen Diario existente, no por envío individual.
  El panel permite prepararla/firmarla/consultarla y explica que la gestión web
  del resumen sigue pendiente; no ofrece un envío que el backend rechazaría.
- Tras una nota de crédito/débito, el POS histórico, la consulta del pedido y
  la emisión idempotente siguen resolviendo el comprobante original. Las notas
  conservan sus rutas propias; no sustituyen la identidad de la venta.

El preset Ventas mantiene **solo consulta fiscal**; no se le concede emisión
fiscal por corregir POS. Un administrador puede configurar esa capacidad con el
RBAC existente. Sin habilitación o serie no se ofrece un documento fiscal.

## Servicio técnico y matriz de autoridad

El 401 histórico por cookies/Bearer ya estaba corregido en HEAD. Las peticiones
actuales de detalle y sus dependencias responden correctamente con los presets
Ventas, Servicio Técnico y Administrador. El bloqueo de apertura por 403/404
reportado NO SE REPRODUJO en esta versión; no se retiraron permisos para intentar
resolverlo. Se probaron login real de API y renderizado de la página con contratos
HTTP representativos; no se afirma haber reproducido la sesión del usuario en
producción.

Defectos confirmados: faltaba recepción en web; Ventas abría una cola de órdenes
asignadas a técnicos; el timeline leía `status_label` cuando el servidor devuelve
`to_status_label`.

| Perfil/preset | Resultado |
|---|---|
| Técnico | Lista/detalle/recepción; diagnóstico, reparación, calidad y entrega según capacidades existentes; sin nuevos permisos financieros |
| Ventas | Lista/detalle/recepción, cobro ya existente y entrega operativa; no diagnóstico, reparación, asignación ni transiciones técnicas genéricas |
| Administrador | Conserva capacidades existentes |
| Superusuario | Conserva autoridad de plataforma con empresa explícita |
| Inventario | Sin cambio; no accede a órdenes técnicas sin capacidad |
| Cliente sin membresía | Sin acceso a la superficie interna |
| Roles personalizados | No se modifican sus capacidades |

La recepción permite buscar cliente, elegir equipo o registrar uno, describir
falla/condición/accesorios y elegir una sucursal permitida. La creación de clientes
continúa en Clientes, visible únicamente a quien puede administrarlos.

La máquina de estados sigue en `service_services`. Ventas recibe el dispositivo
al crear la orden y puede pasar de listo para recoger a entregado mediante la
operación de entrega: no obtiene `service.orders.manage`. Se conservan la puerta
de calidad, la política de saldo y el historial/auditoría. Los estados terminales
no se reabren arbitrariamente.

## Avisos y privacidad

| Canal | Clasificación | Alcance real |
|---|---|---|
| Bandeja/portal | IMPLEMENTADO | Eventos y notificaciones persistentes; aislamiento por cliente y empresa |
| Correo | IMPLEMENTADO, condicionado a configuración | Solo eventos seleccionados; intento posterior al commit, estados reales de entrega |
| WhatsApp | PENDIENTE | No hay integración ni envío manual añadido en esta fase |
| SMS | PENDIENTE | Sin integración |

La orden muestra los avisos existentes de su cliente y el resultado del correo.
`pending`, `sent`, `failed`, `skipped` y canal no previsto son distintos. No se
presenta una notificación en bandeja como prueba de correo enviado. No se añadió
un disparador manual duplicado: se usa la notificación automática existente.

Se corrigió el envío a clientes de estados ocultos por configuración, conservando
el aviso interno para el personal de entrega. El resumen del portal tampoco
expone ese estado: utiliza el último estado visible. El historial existente no
se reescribe. Notas internas, costos, técnicos y otros clientes permanecen fuera
de los avisos. Se conserva la idempotencia por evento/orden/estado; volver al
mismo estado no genera avisos repetidos.

## Auditoría dirigida de hallazgos

| ID | Módulo | Bug/hallazgo | Severidad | Reproducible | Causa | Estado |
|---|---|---|---|---|---|---|
| POS-01 | POS | Tipo ignorado y no validado | HIGH | Sí, tests rojos | Contrato sin `receipt_type` | CORREGIDO |
| POS-02 | POS | Cambiar documento en reintento devuelve venta anterior | HIGH | Sí | Huella no incluía selección | CORREGIDO |
| POS-03 | POS | Numeración paralela/stock/doble cobro | HIGH | No | Bloqueos, restricciones e idempotencia ya existentes | DISEÑO ESPERADO; pruebas de regresión/concurrencia |
| SRV-01 | Servicio | Recepción no disponible en web | MEDIUM | Sí | UI no conectaba endpoints existentes | CORREGIDO |
| SRV-02 | Servicio | Ventas ve cola de asignaciones vacía | MEDIUM | Sí | Filtro `mine=true` universal | CORREGIDO |
| SRV-03 | Estados | Timeline sin etiqueta del estado | MEDIUM | Sí | Campo de contrato incorrecto | CORREGIDO |
| RBAC-01 | Servicio | 401/403 de apertura de orden autorizado | HIGH | No en HEAD | 401 histórico ya corregido; APIs actuales correctas | NO REPRODUCIBLE |
| RBAC-02 | Recepción | Ventas no podía registrar entrega | MEDIUM | Sí | Preset carecía de capacidad específica | CORREGIDO con migración selectiva |
| NTF-01 | Avisos | Estado oculto enviado al cliente | HIGH | Sí, test rojo | Emisor omitía visibilidad | CORREGIDO |
| NTF-02 | Portal | Estado oculto en resumen del cliente | HIGH | Sí, test rojo | Serialización directa del estado interno | CORREGIDO |
| FIS-01 | Fiscal UI | Boleta oculta / envío antes de firma / acciones de solo consulta | MEDIUM | Sí | UI anterior al soporte backend | CORREGIDO |
| FIS-02 | Fiscal | Producción y descuentos fiscales no soportados | HIGH | Sí | Alcance fiscal existente | DISEÑO ESPERADO; POS rechaza antes de persistir |
| FIS-03 | Histórico | Una nota correctiva sustituye al original en la consulta de la venta | HIGH | Sí, tres aserciones rojas | Se elegía el último documento sin filtrar su tipo | CORREGIDO en POS, consulta y emisión idempotente |
| FIS-04 | Fiscal UI/API | Boleta ofrecía envío individual incompatible con su canal | MEDIUM | Sí, tests rojos | Pistas de acción sin distinguir el canal | CORREGIDO; Resumen Diario web PENDIENTE |
| ENV-01 | Datos locales | Migraciones 0085–0092 pendientes | HIGH | Sí | Base local atrasada | CORREGIDO localmente con respaldo; aplicadas 0085–0093 |
| BUILD-01 | Inventario/Next | Exports de dos páginas invalidan build | MEDIUM | Sí, antes de editar | Componentes exportados desde archivos `page.tsx` | PENDIENTE, preexistente fuera de esta fase |

No se modificaron pasarela Izipay, checkout público, inventario, promociones ni
los servicios de stock. La regresión e-commerce corresponde a la suite backend
existente y a Jest; no se enviaron pagos ni mensajes reales a terceros.

## Migración y despliegue

`0093_sales_service_delivery`: solo datos. Compara slug `ventas` y conjunto
**exacto** de capacidades anteriores; añade únicamente `service.delivery.manage`.
Un rol modificado o de otro slug queda intacto. Idempotente; sin nuevas tablas ni
columnas y sin edición de migraciones aplicadas.

Rollback de código: compatible con los datos. El reverso de la migración es
`noop` para no revocar permisos que el tenant pueda haber concedido después;
si se necesita revocación, revisar los roles concretos en el administrador o
restaurar el respaldo antes de retomar escrituras. No revertir otros roles por
nombre ni borrar historial. El deploy debe aplicar todas las migraciones
pendientes sobre una base respaldada.

En esta sesión se respaldó SQLite en
`/tmp/blackdog-before-stabilization-20260928.sqlite3` y se aplicaron 0085–0093.
`check`, `migrate --check` y `makemigrations --check --dry-run` son correctos.
Se conservaron 32 pedidos, 0 reparaciones y 5 membresías. El único preset Ventas
local recibió la capacidad de entrega. No se migró ninguna base de producción.

## Validación final

Suite backend completa: **4412 pruebas; OK, 3 omitidas**, en **356,627 segundos**
sobre PostgreSQL, con bases temporales exclusivas y cuatro procesos. Los datos
de prueba no se escribieron en la base de la aplicación. Incremento respecto al
baseline: 24 pruebas backend y 9 frontend.

Comando ejecutado desde `backend/`:

```sh
DEBUG=1 DATABASE_URL=postgres:///blackdog PYTHONPATH=/tmp:/tmp/blackdog-testdeps python3 manage.py test store --settings=blackdog_stabilization_settings --parallel=4 --keepdb --verbosity=1
```

La configuración temporal importa `backend.settings` y fija exclusivamente
`DATABASES['default']['TEST']['NAME'] = 'test_blackdog_stabilization_e8d913'`.
Tras el resultado correcto se eliminaron esa base y sus cuatro clones de
prueba. Se conservaron el respaldo SQLite y los logs locales de evidencia.

Resultados observados sobre las correcciones:

- PostgreSQL: 31 pruebas dirigidas de RBAC, destinatarios y concurrencia, todas
  correctas; incluye ventas simultáneas y correlativos distintos.
- SQLite: 21 pruebas de POS y notas fiscales, correctas. La regresión de
  comprobante original se observó fallar en las tres superficies antes del fix.
- SQLite: 11 pruebas de eventos de servicio, correctas, incluidos estados
  ocultos, idempotencia y estados de entrega del correo.
- Frontend: 350 tests, 33 suites, correctos (`npm run test:ci`).
- Typecheck: falla únicamente en los dos exports previos de inventario.
- Lint: 0 errores, 33 advertencias (`npm run lint`).
- Build Webpack: compilación correcta; falla en esos mismos dos errores de
  TypeScript. El build normal con Turbopack ya fallaba en el baseline por las
  restricciones del entorno. No se etiqueta ninguno como PASS.
- `check`, `migrate --check`, `makemigrations --check --dry-run` y
  `git diff --check`: correctos.

### Cobertura y límites

`StabilizationPosReceiptTest` prueba selección/persistencia, validaciones,
permisos, rollback, huellas de reintento, compatibilidad y series del tenant.
`C1PosConcurrencyTest` prueba ventas simultáneas en PostgreSQL.
`StabilizationServiceAccessTest` hace login de API y consulta todas las
dependencias de apertura con los tres presets, recepción, entrega, transiciones,
historial y negativas con IDs de otra empresa/sucursal. Comprueba además
Inventario, cliente, superusuario y preservación de roles personalizados.
Las suites existentes cubren IDOR de presupuestos, evidencias, notificaciones,
ventas y documentos; y regresión de catálogo, carrito, checkout, stock,
promociones, pagos y autenticación.

Jest prueba recepción, selector, página de detalle para Ventas/Técnico/Admin y
acciones fiscales. Simula la frontera HTTP: no es una prueba en un navegador
contra un backend vivo. Se actualizó `e2e/pos-ticket.spec.ts` al documento creado
al cobrar; **Playwright no se ejecutó**. Tampoco se hicieron pagos, entregas SMTP
externas ni envíos reales a SUNAT. El alcance fiscal sigue BETA; el Resumen
Diario existe en backend, pero su operación web continúa pendiente.

Evidencia temporal local: `/tmp/blackdog-baseline-backend.log`,
`/tmp/blackdog-final-postgres.log`, `/tmp/blackdog-final-frontend.log`,
`/tmp/blackdog-final-typecheck.log`, `/tmp/blackdog-final-lint.log`,
`/tmp/blackdog-final-build.log` y `/tmp/blackdog-local-migrate.log`.

La ejecución paralela inicialmente no podía reportar todos los fallos porque
faltaba `tblib`. Se instaló solo en `/tmp/blackdog-testdeps`; no se cambiaron
dependencias del repositorio. Las expectativas anteriores del preset Ventas se
actualizaron para incluir entrega y seguir excluyendo gestión técnica y fiscal.
