-- Vuelta atras de R3 SOLO DE CODIGO (imagen R2 sobre esquema 0159), SOLO si el propietario pide editar roles con R2:
-- quita de cada rol TODO codigo finance.* (los 3 que otorga 0157 y cualquier otro asignado a mano despues) para que el
-- editor de roles de R2 vuelva a guardar (H1: 'Capacidad desconocida'). Borra tambien lo otorgado a mano: anotalo antes.
-- Una transaccion; imprime los roles afectados antes y los que siguen con finance.* despues (debe ser 0).
BEGIN;
SELECT count(*) AS roles_afectados FROM store_companyrole WHERE EXISTS (
  SELECT 1 FROM jsonb_array_elements_text(capabilities) AS c WHERE c LIKE 'finance.%');
UPDATE store_companyrole r
   SET capabilities = (SELECT coalesce(jsonb_agg(c ORDER BY c), '[]'::jsonb)
                         FROM jsonb_array_elements_text(r.capabilities) AS c
                        WHERE c NOT LIKE 'finance.%'),
       updated_at = now()
 WHERE EXISTS (SELECT 1 FROM jsonb_array_elements_text(r.capabilities) AS c WHERE c LIKE 'finance.%');
SELECT count(*) AS roles_con_finance_despues FROM store_companyrole WHERE EXISTS (
  SELECT 1 FROM jsonb_array_elements_text(capabilities) AS c WHERE c LIKE 'finance.%');
COMMIT;
