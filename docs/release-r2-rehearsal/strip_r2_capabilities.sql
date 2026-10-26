-- Vuelta atrás de R2, SÓLO si el propietario la pide: quita de cada rol los cinco códigos que R2 añadió
-- (0148, 0149, 0152) para que el editor de roles de 6834bc2 vuelva a guardar. Borra también lo que una
-- empresa haya otorgado a mano después del despliegue: decídelo antes. Una transacción.
BEGIN;
SELECT count(*) AS roles_afectados FROM store_companyrole
 WHERE capabilities ?| array['inventory.counts.approve','accounting.view','accounting.export',
                             'inventory.labels.print','inventory.labels.sensitive'];
UPDATE store_companyrole r
   SET capabilities = (SELECT coalesce(jsonb_agg(c ORDER BY c), '[]'::jsonb)
                         FROM jsonb_array_elements_text(r.capabilities) AS c
                        WHERE c NOT IN ('inventory.counts.approve','accounting.view','accounting.export',
                                        'inventory.labels.print','inventory.labels.sensitive')),
       updated_at = now()
 WHERE capabilities ?| array['inventory.counts.approve','accounting.view','accounting.export',
                             'inventory.labels.print','inventory.labels.sensitive'];
COMMIT;
