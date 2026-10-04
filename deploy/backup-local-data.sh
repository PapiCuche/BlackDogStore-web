#!/bin/sh
# Respaldo de los datos LOCALES de desarrollo, antes de publicar.
#
#   sh deploy/backup-local-data.sh /ruta/al/repo/backend/db.sqlite3
#
# No modifica la base: la abre en SÓLO LECTURA. Deja en ./backups/local-<fecha>/:
#   db.sqlite3            copia íntegra y consistente de la base local
#   stock.csv             producto, sucursal y existencias, para cargarlas en producción
#   products.csv          catálogo con precios
#   resumen.txt           cuántas filas hay de cada cosa y qué cuentas son de demostración
#
# Esta copia conserva TODO lo que hay hoy —productos, usuarios, pedidos, stock,
# movimientos— tal como está. Qué de ello pasa a producción es otra decisión:
# ver «Datos iniciales» en docs/despliegue-produccion.md.
set -eu

SRC="${1:?Uso: sh deploy/backup-local-data.sh /ruta/a/backend/db.sqlite3}"
[ -f "$SRC" ] || { echo "No existe $SRC" >&2; exit 1; }
command -v sqlite3 >/dev/null || { echo "Hace falta sqlite3." >&2; exit 1; }

DEST="backups/local-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
umask 077
RO="file:$SRC?mode=ro"

# `.backup` copia de forma consistente aunque el servidor de desarrollo esté
# usando la base en ese momento. Un `cp` podría copiarla a medio escribir.
sqlite3 "$RO" ".backup '$DEST/db.sqlite3'"
[ "$(sqlite3 "$DEST/db.sqlite3" 'pragma integrity_check;')" = "ok" ] || { echo "La copia no pasó la comprobación de integridad." >&2; exit 1; }

sqlite3 -header -csv "$DEST/db.sqlite3" "
  select p.slug as producto_slug, p.name as producto, b.name as sucursal, s.quantity as existencias
  from store_branchstock s
  join store_product p on p.id = s.product_id
  join store_branch b on b.id = s.branch_id
  order by b.name, p.name;" > "$DEST/stock.csv"

sqlite3 -header -csv "$DEST/db.sqlite3" "
  select p.slug, p.name, c.name as categoria, p.price as precio, p.is_active as publicado, p.image_url as imagen
  from store_product p left join store_category c on c.id = p.category_id
  order by p.name;" > "$DEST/products.csv"

{
  echo "Respaldo de $SRC"
  echo "Fecha: $(date)"
  echo
  sqlite3 "$DEST/db.sqlite3" "
    select 'empresas: ' || count(*) from store_company;
    select 'sucursales: ' || count(*) from store_branch;
    select 'productos: ' || count(*) from store_product;
    select 'filas de stock: ' || count(*) from store_branchstock;
    select 'movimientos de stock: ' || count(*) from store_stockmovement;
    select 'pedidos: ' || count(*) from store_order;
    select 'clientes: ' || count(*) from store_customer;
    select 'usuarios: ' || count(*) from auth_user;
    select 'usuarios de demostración (@example.invalid): ' || count(*) from auth_user where email like '%@example.invalid';"
  echo
  echo "Usuarios:"
  sqlite3 "$DEST/db.sqlite3" "select '  ' || username || ' <' || email || '>' || case when email like '%@example.invalid' then '  [DEMO: no pasa a producción]' else '' end from auth_user order by username;"
} > "$DEST/resumen.txt"

echo "Respaldo local en $DEST:"
ls -lh "$DEST"
echo
cat "$DEST/resumen.txt"
