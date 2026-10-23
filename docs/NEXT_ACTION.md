# NEXT ACTION — coordinación de etiquetas

## Checkpoint para ChatGPT · 2026-10-23 · PARCIAL / EN CURSO

Arquitecto/supervisor: ChatGPT. Coordinador actual: Codex. Continuidad posterior: Google Antigravity.
ADR de etiquetas integrado: `f71ccda09eea70c116bdbf2b04a4f71febf3a9e7` (#165), base de las capturas.
Último fetch: `origin/master` avanzó a `ac3fbd7` con #166, solo `docs/adr-migration-count.md` (MIGRATION-A,
PROPUESTA). No cambia el código de las capturas ni autoriza implementar recuento u otras fases nuevas.
Rama de esta documentación: `codex/labels-coordination`. No integra código de las fases.
**Handoff de implementación: NO LISTO.** Corte WIP inicial 13:12; actualización de GitHub 13:20
(America/Lima, 2026-10-23). LABEL-01 publicó #167 en `f29ba3a`, limpio localmente, sin integrar.
MIG tiene dos commits y WIP posterior; LABEL-03 sigue en desarrollo. No hay PR de estas dos en el corte.
La documentación R1 registra producción en `6834bc2`, hasta `0145`; no se consultó ni modificó producción.
La aceptación del propietario sigue en `docs/acceptance-r1.md`; este trabajo no cambia ese estado.

Esta orden del propietario prevalece sobre la autonomía general de AGENTS.md: **ningún merge** sin
nueva autorización, aunque CI quede verde. No cambiar ControlDePersonal-Web, R1 ni los árboles ajenos.

## Fases observadas y diferencias entre rama y snapshot

| Fase | Rama / HEAD observado | Estado verificable |
|---|---|---|
| LABEL-01 | `feat/label-01-product-codes` / `f29ba3a` | PARCIAL / PR #167 abierto en borrador: backend focal verificado, frontend CI verde; backend CI y guardas transversales pendientes |
| LABEL-MIGRATION-01 | `feat/label-migration-01` / `7d97a9f` | PARCIAL: commits `0cb5c12` (tests) y `7d97a9f` (backend); WIP adicional y sin cierre definitivo |
| LABEL-03 | `feat/label-03-layouts` / `f71ccda` | PARCIAL: render puro con 31 pruebas aprobadas en copia; endpoints privados nuevos en desarrollo, sin modelos persistentes ni hardware certificado |

Los worktrees están en `~/Library/Caches/blackdog-worktrees/label-01`, `label-mig` y `label-03`.
La coordinación no modificó, confirmó ni publicó sus archivos. Las pruebas del supervisor usan **copias
congeladas**, no los árboles que otros agentes siguen cambiando. Un HEAD común no identifica sus cambios
locales: hay que comparar también las huellas del árbol antes de reutilizar resultados.
LABEL-01 ahora tiene rama remota y PR #167, HEAD completo
`f29ba3af8c5d5a754310a988e4bee723fa5ee46d`; sus archivos clave de backend coinciden byte a byte con la
captura 49/49. Para MIG/LABEL-03 aún no hay PR/CI en el corte, y el upstream observado era `origin/master`.
No confundir 0/0 respecto de master con publicación de los cambios sin commit.
Las ejecuciones que otros agentes pudieran estar realizando no se atribuyen como evidencia propia.

## Evidencia focal del supervisor (PostgreSQL local aislado)

| Captura / módulo | Ejecutadas | Aprobadas | Fallos | Errores | Omitidas |
|---|---:|---:|---:|---:|---:|
| LABEL-01, segunda captura `38758a246bb2…`, `store.test_product_codes` | 49 | 49 | 0 | 0 | 0 |
| Diagnóstico de recepción, primera captura, `store.test_coordination_checks` | 2 | 0 | 2 | 0 | 0 |
| LABEL-MIGRATION-01, segunda captura `6dc9f03506f8…`, `store.test_label_migration_import` | 29 | 29 | 0 | 0 | 0 |
| LABEL-03, segunda captura `a0d75b29a814…`, `store.test_labels_render` | 31 | 31 | 0 | 0 | 0 |
| LABEL-03 API, captura `010f21569cf4…`, `store.test_label_views` | 11 | 11 | 0 | 0 | 0 |

Los 49/29/31/11 aprobados identifican esas capturas, no los WIP posteriores ni commits finales.
No se suman capturas sucesivas como pruebas distintas ni se presenta este cuadro como suite final verde.
La primera captura de LABEL-01 tuvo 48/49: `_generate` contenía temporalmente `if False` en vez del
control de ocupación; el árbol vivo restauró el control y la segunda captura pasó 49/49. La primera de
migración tuvo 27/29: clasificación y activación de modo histórico; el agente ajustó fixtures/casos,
por eso se volvió a validar una captura nueva. No se modificaron esas pruebas desde coordinación.

Los dos diagnósticos adicionales de recepción reproducen que `receive_units` acepta un IMEI o serie
que ya es `ProductBarcode` de la misma empresa. No se publica el dato sintético ni se corrige en una rama
ajena. Es una **compuerta pendiente** antes de declarar seguro el espacio común producto/unidad:
coordinar el responsable de esa guarda y probar ambos órdenes y la carrera, sin iniciar LABEL-02.
El escritor `stock_unit_services.py` no aparece modificado en las capturas revisadas.

La primera captura LABEL-03 (`ff8436bc224a…`) tuvo 5/31 y 26 `AttributeError` porque el paquete no
exportaba todavía composición/render. Una segunda captura, ya con esas funciones, pasó 31/31.
Además se inspeccionaron dos PDF sintéticos: página accesorio 30×20 mm (85.03937×56.69291 pt),
equipo 50×25 mm (141.7323×70.86614 pt); 25 rectángulos vectoriales de barras por página, sin imagen
raster en esos ejemplos, y espacio libre a ambos lados mayor que diez módulos. Se comprobaron los
objetos PDF con pypdf y se revisaron visualmente los renders Quartz/sips sobre blanco; Poppler no
completó la rasterización y se detuvo únicamente ese proceso propio. Los tests cubren rechazo por
ancho y permisos del contrato puro. **No acreditan escaneo, escala impresa ni legibilidad física.**
203 dpi sigue siendo una hipótesis configurable, no evidencia de la ADV-9010N. No se conectó ni se
simuló la impresora, ni se marcó una etiqueta como impresa. Una tercera captura privada (`010f21569cf4…`) pasó las 11 pruebas `test_label_views` en PostgreSQL:
empresa/sucursal, autenticación y capacidad, no exposición de IMEI/series y descarga sin escrituras
ni trabajos de impresión. Su cierre de rama, frontend, regresión conjunta y CI siguen pendientes.
No hubo modelos nuevos ni migraciones de LABEL-03 en las capturas.

LABEL-01: `makemigrations --check --dry-run` pasó; `sqlmigrate store
0146_internal_sequence_product_code` produce un `AlterField` de estado con SQL `(no-op)`.
No se aplicó ninguna migración a una base comercial. Las bases de pruebas contienen solo datos sintéticos.
Regresión completa de backend/frontend y CI sobre commits finales: **PENDIENTE**, no inferida del focal.

### Comandos realmente ejecutados

Desde el `backend/` de cada copia congelada, con `DEBUG=1` exportado y sin copiar .env ni bases reales:

```bash
DATABASE_URL=postgresql:///blackdog_labels_coord01_125641 python3 manage.py test store.test_product_codes --noinput --verbosity=1
DATABASE_URL=postgresql:///blackdog_labels_coordmig_125641 python3 manage.py test store.test_label_migration_import --noinput --verbosity=1
DATABASE_URL=postgresql:///blackdog_labels_coordpdf_125641 python3 manage.py test store.test_labels_render --noinput --verbosity=1
DATABASE_URL=postgresql:///blackdog_labels_coordapi_125641 python3 manage.py test store.test_label_views --noinput --verbosity=1
DATABASE_URL=postgresql:///blackdog_labels_coord01_125641 python3 manage.py makemigrations --check --dry-run
DATABASE_URL=postgresql:///blackdog_labels_coord01_125641 python3 manage.py sqlmigrate store 0146_internal_sequence_product_code
```

No ejecutar estos comandos en los worktrees activos ni reutilizar bases de otros agentes. Las copias,
huellas y logs locales están en `~/Library/Caches/blackdog-worktrees/labels-coordination-validation-20261023T125641/`.
El módulo `store.test_coordination_checks` existe **solo** en su copia local `label-01/backend/`, no en
master ni en las ramas activas. Su ejecución también fue PostgreSQL y secuencial, 2/2 fallos esperados
por las guardas de recepción ausentes. No usar SQLite como sustituto de concurrencia PostgreSQL.

## Revisión cruzada y orden seguro (ninguna integración autorizada)

1. Los autores cierran sus fases, confirman y publican ramas y PR propios. No publicar sus WIP desde esta
   coordinación. Releer diffs sobre el HEAD definitivo y volver a validar lo que cambió desde la captura.
2. LABEL-01 debe conservar códigos, ceros, mayúsculas, EAN y los desactivados; generar solo cuando falten;
   rechazar conflicto explícito sin inventar otro código; proteger IMEI/series en ambos órdenes de alta;
   validar carrera PostgreSQL y caja; revisar su frontend y CI exactos.
3. LABEL-MIGRATION-01 puede revisarse en paralelo. El orden recomendado, una vez autorizado, es LABEL-01
   y después migración, comprobando el punto de unión: `apply_products` todavía deja un TODO para llamar
   a `product_codes.ensure_code` en nuevas altas sin código. No declarar todos los caminos unificados.
   Importar el histórico **antes** de cualquier relleno masivo; nada con datos reales está autorizado.
4. La vista previa no altera el catálogo comprobado (`Product`, `ProductBarcode`, `AdminAuditLog` en su
   prueba), pero sí persiste staging `BulkImportJob`/filas. No llamarla «cero escrituras en toda la BD».
   Conservar revisión por nombres/conflictos, consentimiento explícito e idempotencia; no reconstruir
   códigos que Excel ya dañó. El archivo real sigue bloqueado y no se abrió.
5. LABEL-03 puro (sin modelos/rutas) se puede revisar aparte. Sus modelos/lotes esperan las migraciones
   de las fases anteriores; no adelantar `LabelTemplate`/`LabelBatch` ni simular permisos HTTP con flags.
   Un `sensitive_allowed` del contrato puro no sustituye capacidad por empresa y auditoría del endpoint.
6. **LABEL-02 NO COMENZAR** hasta LABEL-01 integrada y verificada, y nueva comprobación del contexto.
   Después de LABEL-02 podrán revisarse los modelos de LABEL-03, en el orden del ADR.
7. LABEL-04: BLOCKED/HARDWARE. LABEL-05: NO AUTORIZADA. No iniciar ninguna de las dos.

### Archivos y conflictos

- LABEL-01: `store/product_codes.py`, `models.py` (InternalSequence), `pos_views.py`, `admin_views.py`,
  `registration_services.py`, `urls.py`, `management/commands/assign_internal_codes.py`, migración
  `0146_internal_sequence_product_code.py`, `test_product_codes.py`, `backend/scripts/ci_shard_weights.json`.
  Frontend observado después de la captura backend: `ProductCodesPanel.tsx`, `ProductForm.tsx`,
  `admin/lib/product-codes-api.ts`, `admin/products/[id]/page.tsx`, `app/lib/admin.ts` y su prueba de panel.
  No hay pruebas frontend del supervisor sobre ese WIP.
- MIG: `store/import_formats.py`, `import_services.py`, `import_exports.py`, `import_views.py`,
  `test_label_migration_import.py`. No trae migración en el corte.
- LABEL-03: `store/labels/{__init__,data,fields,layout,code128,symbols,compose,pdf}.py`,
  `test_labels_render.py`; después de la captura render aparecieron `label_views.py`, `urls.py` y
  `test_label_views.py`; una captura posterior pasó 11 pruebas API, sin certificar WIP adicional o CI.
  No hay modelos ni migraciones en el corte.
- **Intersección actual LABEL-01/LABEL-03: `store/urls.py`**. No se resolvió ni integró.
  MIG no comparte archivos de implementación en el corte, pero hay acoplamiento semántico en
  `import_services` ↔ `product_codes`, y futuros puntos comunes `models.py`, pesos CI, `02_`,
  `07_`, `NEXT_ACTION.md` y mapa. Esta rama documental también tocará esos docs: integrar uno por uno,
  conservando todas las entradas; no resolver conflictos quitando funciones, tests o guardas.
- `0146_internal_sequence_product_code` colisiona en numeración/hoja con
  `0146_external_used_card_and_incident` del PR #162. El segundo que se autorice deberá resolver el
  grafo **solo con migraciones no aplicadas**, sin editar las históricas de R1.
- No confundir CI verde de #162 o de otros PR con CI de etiquetas. En #167, workflow frontend
  `40001937765` está SUCCESS sobre `f29ba3a`: 168 suites, 1.887 tests aprobados, verificados en su log.
  Backend `40001937829`: seis shards IN_PROGRESS en la consulta; no se acredita resultado final.
  Los 188 tests vecinos y otros comandos que declara el cuerpo del PR no se presentan como ejecutados
  por coordinación sin contrastar su evidencia. MIG/LABEL-03 no tienen CI de fase disponible en el corte.

## Decisiones y bloqueos

**APROBADO:** conservar códigos originales; no usar IMEI como código de producto; no regenerar al editar
ni imprimir; fuente de verdad por empresa; recibos/consultas no exponen IMEI en público; sin producción.

**PROPUESTA, no autorización para datos reales:** prefijo `C` + seis dígitos; `U` para unidades; no generar
otro código cuando ya hay EAN; mantener reglas actuales de escaneo manual; permisos separados para
etiquetas sensibles. El WIP de LABEL-01 siembra `P` + seis por defecto; no se aprobó ese prefijo ni se
cambia silenciosamente a C. Resolver con el propietario antes de configurar/aplicar sobre datos reales.

**BLOCKED:** compatibilidad física ADV-9010N; dpi/lenguaje/transporte/controlador confirmados; dimensiones
reales, separaciones y etiquetas por fila del rollo; archivo histórico real; decisiones comerciales,
permisos y prefijos pendientes; cierre de los WIP/PR/CI y compuertas aún no aprobadas.

## Prompt exacto preparado para Antigravity (transferencia CONDICIONADA)

```text
Trabaja en PapiCuche/BlackDogStore-web. Lee AGENTS.md, docs/NEXT_ACTION.md de la rama
codex/labels-coordination, docs/adr-inventory-labels.md y docs/CODEBASE_MAP.md.
La base auditada fue f71ccda; comprueba GitHub y los HEAD actuales antes de reutilizar pruebas.
No asumas que los WIP descritos ya están integrados o terminados. Identifica las tres ramas
feat/label-01-product-codes, feat/label-migration-01 y feat/label-03-layouts y sus PR definitivos.
No interrumpas sesiones ni cambies sus worktrees. Crea tu rama/worktree aislado, fuera de /tmp,
solo cuando corresponda retomar una fase sin otro escritor; nunca robes una rama activa.
Revalida conservación de códigos, PostgreSQL concurrente, guardas en ambos órdenes producto/unidad,
caja e importación idempotente con datos sintéticos. Para PDF, verifica página exacta, Code 128
vectorial, quiet zones y rechazo si no cabe; eso no acredita una impresora real.
Mantén C+6, U, política EAN, escaneo manual y permisos sensibles como propuestas pendientes.
No modifiques R1, producción, ControlDePersonal-Web, datos reales ni migraciones ya aplicadas.
No fusiones PR ni publiques datos sensibles. LABEL-02 no empieza hasta integrar y verificar LABEL-01
con autorización. Los modelos LABEL-03 esperan sus dependencias. LABEL-04 está bloqueada por hardware;
LABEL-05 no está autorizada. La siguiente acción aprobada es cerrar y validar las fases existentes,
no comenzar otra. Si siguen activas, limita tu trabajo a coordinación en rama propia y reporta el bloqueo.
Actualiza los documentos existentes y reporta FASE/ESTADO/RAMA/COMMIT/PR/ARCHIVOS/PRUEBAS/CI/RIESGOS/
CONFLICTOS/DECISIONES/BLOQUEOS/SIGUIENTE ACCIÓN con evidencia del HEAD exacto.
```
