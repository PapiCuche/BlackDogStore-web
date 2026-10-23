# INV-AI-ROADMAP-01 — Inventario asistido, control manual y seguridad

Estado: PROPUESTA para las fases siguientes; INV-TEXT-01 EN RAMA, SIN DESPLIEGUE.
Base: master 6daf9ae68cdd7d6b489c6a148442d107e0b2f1f2, 2026-10-21.
Autoridad: código > migraciones > pruebas > 02_ESTADO_ACTUAL > ADR.

## Decisiones del propietario
- Un solo inventario por empresa, modo rápido y modo avanzado. IA opcional, nunca autoridad de escrituras.
- Listas de proveedor por WhatsApp texto, Excel, posteriormente imágenes. Precio recibido = COSTO del proveedor, nunca precio al cliente.
- Listas completas por proveedor: los ausentes dejan de ser ofertables SOLO cuando un lote completo sea validado y confirmado. Preservar ventas comprometidas, historia y otras empresas.
- Filas comerciales repetidas = duplicados, NO unidades adicionales. Sin cantidad explícita = DESCONOCIDA, jamás 1 ni saldo confirmado.
- Seminuevos externos pueden ser modelos genéricos o unidades descritas con batería, serie, IMEI 1 e IMEI 2; no ingresan como stock propio hasta recepción real.
- Publicación simplificada: Disponible / Agotado / No disponible. Internamente conservar fuente exacta y revalidar la disponibilidad al vender.
- Exportación total o por nuevos, seminuevos, accesorios, sucursal, proveedor, con propio/external separado.
- IMEI Luhn: sugerir dígito 15 solo al digitar exactamente 14; si se escanean 15, validar sin reemplazar. Backend es autoridad; Luhn no certifica identidad física.
- El propietario conectará credenciales IA en el DESPLIEGUE. Nada de tokens reales en código ni fase de preview.

## Código existente que debe respetarse
- backend/store/device_identity.py: Luhn y reglas de identidad.
- backend/store/stock_unit_services.py y unit_import_services.py: validación de unidades, duplicados, Kardex.
- backend/store/supply_services.py y supplier_offer_import.py: ofertas, costos pactados, precio comercial, revalidación, auditoría y lock transaccional. Hoy el importador es PARCIAL.
- backend/store/inventory_exports.py e inventory_export_views.py: exportación catálogo, cantidades, unidades, ZIP reimportable y reportes. El ZIP no incluye ofertas externas ni es un informe integral de ventas.
- frontend/app/admin/components/BulkInventoryCentre.tsx: punto de entrada existente.
- Checkout y fiscal quedan sin cambios hasta ADR separado y aceptación.

## Roadmap de implementación
| Fase | Objetivo | Puerta |
|---|---|---|
| INV-00 | Baseline de tests, migraciones, permisos, producción vs master | Aprobación auditada |
| INV-TEXT-01 | Reconocimiento de texto en navegador, limitado y sin persistencia | Jest, UI, typecheck, build |
| INV-EXP-01 | Exportar líneas new/used/accessories, reporte integral separado de reimportación | tenant/branch/permisos |
| INV-TEXT-02 | Revisión manual editable, mapeos de variantes y perfiles de proveedores | ningún error crítico aplicado |
| INV-TEXT-03 | Snapshot completo, deduplicación, cantidades desconocidas, idempotencia | concurrencia y ADR |
| INV-ID-01 | Autocomplete Luhn en todos los formularios de serie/IMEI | backend+frontend tests |
| INV-SUP-01 | Seminuevos externos genéricos e identificados sin falso stock | ADR+checkout/recepción/fiscal |
| INV-AI-01 | Adaptador IA en Django, desactivado por defecto, proveedor configurable | privacidad+cuotas |
| INV-AI-02 | Claude API en staging con consentimiento y clave privada proporcionada al despliegue | coste real y tests |
| INV-PROD | Backup, migraciones ensayadas, E2E, UAT y autorización explícita | despliegue autorizado |

## Parámetros y controles obligatorios antes de habilitar IA
1. INVENTORY_AI_ENABLED=false por defecto. Nunca llamar a servicios externos en modo deshabilitado.
2. Clave exclusivamente backend/secrets manager, nunca NEXT_PUBLIC, Git, logs, HTML o capturas.
3. Proveedor y modelo configurables por entorno y por empresa si futuro SaaS lo exige. Ninguna clave o marca específica del piloto hardcodeada.
4. API de IA solo vía Django autenticado; permisos y compañía/sucursal determinados por la sesión, no por instrucciones pegadas.
5. Contenido externo = datos no confiables, nunca instrucciones ejecutables. Salida IA = JSON de esquema fijo; sin funciones ni tools que escriban DB. Siempre vista previa y autorización humana.
6. Redactar IMEI, serie, nombres, teléfonos y otros datos personales antes de la llamada. Mantener los identificadores completos y su correspondencia solo dentro de backend autorizado; si no puede anonimizarse, no enviar a API.
7. Topes conservadores propuestos (pendientes de medición): 60 000 caracteres y 300 candidatos por lote; 2 solicitudes IA por minuto por actor, 20/día; timeout 15 s; un reintento seguro como máximo; cuotas por compañía y globales.
8. Presupuesto piloto sugerido US$5/mes con corte real, alertas al 50%, 80%, 100% y sin recarga automática; medir tokens por lote. Créditos Claude Max 5x no equivalen a cupo autorizado ilimitado para el SaaS.
9. No publicar costos sin costs.view ni IMEI completo al público. Toda exportación tenant-scoped, branch-scoped, no-store y auditada.
10. Antidupe por IMEI en ambas posiciones/serie dentro del tenant, verificaciones Luhn backend, bloqueo de stock ficticio y deduplicación que NO suma cantidades.
11. Excel/imagen: validar tamaños, formatos reales, bytes, fórmulas, malware y descompresión. Sin URL externa arbitraria en backend (SSRF).
12. Fallback funcional sin IA: ingreso manual y Excel siguen operativos. No alterar inventario, pagos, precios o CPE por una sugerencia IA.
13. Vista previa obsoleta se rechaza, cambios de precio requieren decisión autorizada. Al actualizar lista completa el cierre de ausentes es una operación auditada y transaccional por proveedor.
14. No enviar datos a proveedores de IA para entrenamiento sin establecer contrato, retención, consentimiento y base legal correspondientes.

## INV-TEXT-01 — alcance efectivamente implementado en la rama
- frontend/app/admin/lib/inventory-text-preview.ts
- frontend/app/admin/components/InventoryTextPreview.tsx
- frontend/app/admin/components/BulkInventoryCentre.tsx
- frontend/__tests__/inventory-text-preview.test.ts

Vista previa local del texto; sin API, sin almacenamiento, sin DB ni rutas nuevas. Clasificación explícita por operador, IMEI validados con Luhn, registros duplicados marcados, costos como campo aparte, cantidad siempre desconocida. Los avisos son orientativos, no validación para insertar. Sigue existiendo el modo avanzado real del panel.

## Tests y baseline pendientes
El repositorio no está clonado en este entorno; no se ejecutaron tests completos, migraciones, lint, typecheck o build. GitHub no devolvió checks para el commit base.
Antes de merge: ejecutar npm ci, Jest focal y bulk-inventory-centre, npx tsc --noEmit, npm run lint, npm run build y los E2E afectados; recopilar baseline backend, tests de aislamiento y producción antes de fases críticas. No fusionar PR borrador sin revisión del propietario.
