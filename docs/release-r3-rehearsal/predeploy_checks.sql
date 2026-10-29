-- SOLO LECTURA. Para correr (el propietario / quien tenga acceso) en produccion ANTES del migrate de R3. No se ejecuto aqui.
-- 1. Productos activos con precio <= 0 (R3: la caja los rechaza con 400 «Este producto no tiene precio»)
SELECT p.id, p.name, p.price, p.is_published_online,
       (SELECT coalesce(sum(bs.quantity),0) FROM store_branchstock bs WHERE bs.product_id = p.id) AS stock
  FROM store_product p WHERE p.is_active AND p.price <= 0 ORDER BY p.id;
-- 1b. ¿Se vendieron alguna vez a 0? (lineas de pedido con precio unitario 0 en ventas de caja)
SELECT count(*) FROM store_orderitem oi JOIN store_order o ON o.id = oi.order_id
 WHERE o.pos_idempotency_key <> '' AND oi.price <= 0;
-- 2. Roles con company.manage (ganaran finance.view/manage/payments.record) y roles que ya tengan finance.*
SELECT r.id, r.company_id, r.slug FROM store_companyrole r WHERE r.capabilities ? 'company.manage' ORDER BY 1;
SELECT count(*) AS roles_con_finance_ya FROM store_companyrole WHERE capabilities::text LIKE '%finance.%';  -- debe ser 0
-- 3. Recuentos de migracion abiertos (cancel_migration_counts es obligatorio antes de cualquier vuelta atras de esquema 0147)
SELECT kind, status, count(*) FROM store_inventorycount GROUP BY 1,2 ORDER BY 1,2;
-- 4. Claves POS en vuelo: decisiones «no se cobro» y ventas recientes (una venta movil en cola con precio 0 recibira 400)
SELECT count(*) AS reservas_pos, min(decided_at), max(decided_at) FROM store_possalekeyreservation;
SELECT count(*) AS ventas_caja_24h FROM store_order WHERE pos_idempotency_key <> '' AND created_at > now() - interval '24 hours';
-- 5. Reparaciones aprobadas sin cuenta por cobrar (las creara backfill_repair_receivables tras el despliegue)
SELECT count(DISTINCT q.repair_order_id) FROM store_repairquote q WHERE q.status = 'approved';
