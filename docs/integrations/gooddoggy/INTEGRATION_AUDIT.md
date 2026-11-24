# GD-001 — Auditoría y revisión del PR #424
Fecha: 2026-11-24. Estado: PARCIAL (auditoría de código focalizada; contrato documental PROPUESTA, sin implementación).
Base de código: bf6a33075e31d43de4defc5d96a3279a13aaa4b6. HEAD revisado por el propietario: ee02f088e53833ebddbacdd56369294fc4756b8d. Revisión sobre la misma rama, sin cambios de producción.
## Evidencia del repositorio
| Dominio | Fuente inspeccionada | Resultado |
|---|---|---|
| Stack | backend/requirements.txt, frontend/package.json (auditoría inicial) | Django 5.2.17, DRF 3.18.1, Next.js 16.3.8, React 19.3, PostgreSQL |
| Entidades | backend/store/models.py | Company, Branch, Product, ProductModel, ProductVariantOption, ProductImage, ProductBarcode, BranchStock, StockUnit, StockMovement, Promotion, Order, RepairOrder, IntegrationConfig |
| Inventario | inventory_services.py, models.py | BranchStock.quantity autoritativo; Product.inventory agregado de compatibilidad, NO maestro. Kardex y bloqueos transaccionales. StockUnit AVAILABLE concuerda con stock serializado |
| Tenancy | tenancy.py | storefront_available_stock, customer_owned_orders, visible_orders, scope_queryset: candidatos para inspección de reglas, no para reutilizar identidad staff |
| Integraciones | integrations/registry.py, IntegrationConfig | configuración active/draft y secretos existentes; no equivale a principal S2S Good Doggy |
| Rutas | urls.py, v1_urls.py:1–8, 138 y siguientes | /api/ legacy y /api/v1/ separados; catálogo, pedidos, administración, móvil, servicio y WhatsApp. Reutilizar lógica pura, nunca las rutas como fallback |
| Cliente | tracking_views.py, tenancy.py | cuenta propia y vinculación Customer.user existentes; no prueban identidad de una persona declarada por CRM |
| Pagos y eventos | urls.py, v1_urls.py, unit_hold_services.py | Izipay/MiCuentaWeb y WhatsApp observados en auditoría inicial; no equivalen a outbox de integración |
## OBS-GD-001-06: proyección comercial
| Campo | Evidencia | Regla |
|---|---|---|
| Identidad | models.py Product, ProductModel, ProductVariantOption | Product es la variante; no crear tabla duplicada. model_id agrupador opcional |
| SKU | ProductBarcode.code/is_primary/is_active, constraints 4934/4941 | candidato a SKU si código primario activo inequívoco autorizado; null si falta o ambiguo. Nunca generar en GET |
| Capacidad/color | ProductVariantOption y roles de atributos | leer atributos reales, null si no identificados; no adivinar del nombre |
| Precio | Product.price, StockUnit.sale_price; device_catalog.py:1319–1411; unit_hold_services.price_for | nuevos por variante; usados por unidad, precio positivo, sin fallback a variante; Decimal string. new_for_sale_prices agrega por modelo, NO es precio de una variante |
| Oferta bajo pedido | supply_services.py:570–608 | valid_offer_q exige confirmación, cantidad restante, proveedor activo y fechas. No es existencia física. best_offer puede desempatar por costo: nunca exponerlo ni identificar proveedor |
| Estado y batería | StockUnit.cosmetic_grade, battery_health; device_catalog.py:1414–1444 | solo datos registrados, no inferir para nuevos |
| Garantía | StockUnit.warranty_days, CompanySettings.used_warranty_days y warranty_policy_text/url (models.py:4063–4067) | unidad, empresa o política; cero explícito no garantía; null sin duración. No se verificó plazo numérico de nuevos |
| Promoción | Promotion.BUNDLE_FIXED_PRICE/BUNDLE_PERCENT (5174–5178) | solo vigentes y autorizadas; beneficio requiere prices.read adicional; no afirmar aplicación sin contexto |
| Disponibilidad | device_catalog.offered_units, used_unit_services.py:79–112, availability_views.py:153–245 | AVAILABLE y sucursal autorizada; seminuevo además precio positivo, inspección aprobada, publicación y foto pública real. Admin incluye reservados, vendidos, ofertas proveedor: NO exponer payload |
| Imagen | device_catalog.variant_galleries, unit_media.public_photos | verificar procedencia/licencia, foto propia confirmada de unidad exacta. Si falta autorización, null |
| URL | frontend/app/components/ProductCard.tsx:78 | /product/{slug}; resolver dominio y ruta comercial por tenant; URL de modelo/oferta pendiente de verificar, no fabricar ni incluir tokens |
**Privacidad P0:** public_unit devuelve imei_hint y notas; no reutilizar payload. Prohibidos IMEI incluso enmascarado, seriales, costos, proveedor, datos privados y HTML.
## P0 de efectos indirectos (confirmado independientemente)
PublicModelDetailView.get (backend/store/device_catalog_views.py) llama unit_hold_services.release_expired(company=company) antes de public_model. Una lectura puede liberar reservas y generar movimientos. Good Doggy no puede llamarlo, ni recurrir a GET públicos/administrativos, get_or_create_branch_stock, reservas, liberaciones, checkout o ajustes. Se auditará transitivamente toda función candidata (incluidos middleware, serializer y señales) y SQL; solo reutilizar lecturas puras. Reservas vencidas aún no liberadas siguen no disponibles; los procesos autónomos del maestro se encargan. Si BranchStock y StockUnit discrepan, unknown, no corregir desde GET.
## Brechas y estado
S2S dedicado, scopes, proyecciones, snapshot/journal, outbox y pruebas de ejecución: PROPUESTA. Catálogo de servicios publicable no acreditado. Pedidos/reparaciones deshabilitados hasta grant por cliente aprobado, sin bloquear catálogo. No migraciones ni endpoints nuevos.
## OBS-GD-001-07 — Validación del contrato
OpenAPI 3.1.0, versión 0.2.0, blob Git 969bf78b378191c1597c29e4c695c40df84fc095 (commit bae9b8a9e23ee0276a1b217dcbb5741e5b5f7179). JSON serializado, válido como YAML 1.2.
- Herramienta: Ajv 8.17.1 Draft 2020-12, bundle ajv-validator/ajv-dist v8.17.1 dist/ajv2020.bundle.js, blob d6814eaf1b5d5196459a79ca6c71d7deb4f8fde9.
- Metaesquema oficial https://spec.openapis.org/oas/3.1/schema/2022-10-07, copia fijada de python-openapi/openapi-spec-validator 0.7.2 openapi_spec_validator/resources/schemas/v3.1/schema.json, blob b57b3bae9acd040fdd15ddb3dc45129000343b0c.
- Método reproducible: parsear JSON; Ajv2020 strict:false, allErrors:true; el primer intento con las referencias dinámicas originales produjo errores espurios del resolvedor Ajv. En el metaesquema aislado, sin override de dialecto, resolver las cuatro referencias dinámicas #meta a #/$defs/schema (única ancla meta; sin cambiar el contrato). Compilar y validar documento, luego compilar los esquemas de componentes y los de parámetros/respuestas como JSON Schema 2020-12 con referencias absolutas al mismo documento. Formatos básicos fecha-hora/UUID/URI; no se verifica que URLs estén vivas. Esta es validación estructural formal contra el metaesquema oficial con ancla resuelta, no una ejecución del CLI openapi-spec-validator ni garantía de todas las reglas semánticas OAS.
- Resultado: PASS, 0 errores estructurales; 22 componentes, 93 esquemas de respuesta y 66 de parámetros compilados sin error. 12 operaciones, solo GET, operationId únicos, 0 referencias internas sin resolver. services/orders/repairs no declaran 200.
- 9 ejemplos positivos PASS: disponibilidad confirmada, agotada, desconocida, bajo pedido, seminueva; búsqueda usada y catálogo sin ofertas; precio usado; promoción condicional. Todos sintéticos.
- 16 ejemplos negativos rechazados: unknown=0, available=0, agotado>0, bajo pedido>0, usado=2, IMEI extra, tipo bajo pedido incorrecto, unidad usada no disponible, oferta sin precio, catálogo con precio, catálogo con sucursal, imei_hint anidado, ID ausente, precio numérico, costo extra, garantía de política con días inventados.
- Limitaciones: el entorno local no pudo instalar el CLI por falta de acceso a paquetes; validación realizada con bundle fijado y metaesquema obtenido por conector. Igualdad de IDs/versiones entre proyecciones, auth, cursor y pureza SQL requieren pruebas de backend. No se ejecutó suite PostgreSQL ni se afirma baseline verde. AGENTS.md §5–6: PR exclusivamente docs, revisar diff; suite backend depende de cambios backend/**. Checks del HEAD final deben consultarse y registrarse en handoff; sin merge.
