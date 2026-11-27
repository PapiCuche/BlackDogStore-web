# GD-001 — Preguntas abiertas después de review #424
**Resuelto permanentemente:** BlackDogStore maestro, Good Doggy lector; no fallback ni escrituras indirectas. No reabrir.
| Pendiente | Afecta |
|---|---|
| Aceptar identidad S2S, scopes y composición de ofertas, allowlists y códigos HTTP de OpenAPI 0.2.0 | catálogo |
| Confirmar mapeo de tenant/sucursal, IDs estables, SKU primario, URLs canónicas por modelo/oferta e imágenes autorizadas | catálogo |
| Acordar oferta usada, moneda/impuestos, condición de promoción, vigencia y disponibilidad bajo pedido | catálogo |
| Aceptar TTL snapshot 15 min, retención journal 30 días, mecanismo commit watermark sin huecos, cuotas y SLA | sync |
| Acordar destino, firma, reintentos, retención DLQ y política de replay | webhooks |
| Fuente real publicable de servicios y repuestos | solo services (deshabilitado) |
| Mecanismo de autorización de cliente emitido/validado por Web, expiración, revocación, consentimiento y proyección mínima | solo orders/repairs (deshabilitados también en feed y eventos); NO bloquea catálogo |
| Pruebas de ejecución PostgreSQL, aislamiento, pureza SQL, concurrencia y autorización | antes de habilitar implementación |
La validación documental OpenAPI 3.1 con metaesquema oficial resuelto y Ajv 8.17.1 pasó para el blob consignado en INTEGRATION_AUDIT.md; no equivale a tests de API. Revisión de ambos equipos y GO explícito siguen pendientes. No iniciar GD-002 ni hacer merge de #424.
