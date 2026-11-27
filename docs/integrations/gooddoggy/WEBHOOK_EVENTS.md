# GD-001 — Webhooks salientes (PROPUESTA)
Notifican cambios del maestro, no comandos ni autoridad de stock. Sin webhook entrante de mutación.
| Evento | Scope |
|---|---|
| product.created, product.updated, product.deleted | catalog.read |
| inventory.updated | inventory.read |
| price.updated | prices.read |
| promotion.updated | promotions.read |
| order.updated | orders.read (DESHABILITADO) |
| repair.updated | repairs.read (DESHABILITADO) |
Composición adicional: oferta usada/precio bajo pedido que revele disponibilidad exige inventory.read + prices.read; si falta uno, solo invalidación agregada del dominio autorizado sin identidad de oferta ni valores. Tenant y sucursales de suscripción vinculados al principal, revalidar permisos en entrega, reintento y replay; purgar/suspender pendientes revocados. No PII, IMEI ni referencias privadas. Tombstone solo para entidad antes visible.
Envelope v1: event_id UUID único, event_type, event_version=1, occurred_at UTC, tenant_id opaco, entity {type,id}, data_version, correlation_id y branch_id cuando aplica y autorizado. No delta como instrucción. order/repair no emitidos hasta grant de cliente aprobado.
Transactional outbox en la misma transacción del maestro; worker independiente tras commit; no red en request. At-least-once, deduplicar event_id duradero, descartar versiones antiguas; no prometer orden global de entrega. Reconciliación por sync/changes, cursor guardado; 410 resnapshot y reemplazo atómico; no timestamp webhook como watermark. Cambios temporales por worker maestro, nunca por GET. Acuse afecta solo entrega técnica.
HTTPS, destino autorizado, sin redirects, protección SSRF/DNS, límites de cuerpo y timeout. Firma propuesta HMAC-SHA256 sobre timestamp ASCII + punto + bytes originales; headers X-GD-Event-Id, X-GD-Timestamp, X-GD-Key-Id, X-GD-Signature; comparación constante, ventana propuesta 300 s, verificar ID header/cuerpo, nueva firma por intento, dedupe mismo event_id. Claves separadas de autenticación, rotación/revocación.
2xx acuse; 429/5xx/timeouts retry exponencial con jitter y tope; 4xx permanente a DLQ salvo política explícita. Replay por operador autorizado con mismo event_id, revalidar alcance. Retención/SLA/intentos por acordar. Feed 30 días y snapshot 15 min propuestos; no confundir retención de DLQ. Sin fallback público/admin ni cambios de inventario.
Tests futuros: rollback sin evento, commit con evento, crash/retry, duplicado, replay, firma errónea/cuerpo alterado, rotación, SSRF, caída, DLQ, scope revocado, tenant/branch ajeno, eventos deshabilitados, ausencia PII, acuse sin mutación.
