-- Despliegue de R2: 0152 concede `inventory.labels.print` a todo rol con `inventory.view`, también al preset
-- Ventas. Por defecto (decisión conservadora) se retira del preset Ventas hasta que el propietario lo apruebe.
-- Sólo toca el rol con slug `ventas`; no modifica ningún otro rol. Una transacción. NO volver a ejecutar
-- `regrant_r2_capabilities.py` después sin repetir este paso: lo concede otra vez.
BEGIN;
SELECT count(*) AS roles_ventas_con_permiso FROM store_companyrole
 WHERE slug = 'ventas' AND capabilities ? 'inventory.labels.print';
UPDATE store_companyrole r
   SET capabilities = (SELECT coalesce(jsonb_agg(c ORDER BY c), '[]'::jsonb)
                         FROM jsonb_array_elements_text(r.capabilities) AS c
                        WHERE c <> 'inventory.labels.print'),
       updated_at = now()
 WHERE slug = 'ventas' AND capabilities ? 'inventory.labels.print';
COMMIT;
