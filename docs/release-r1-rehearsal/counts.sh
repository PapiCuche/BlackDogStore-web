#!/bin/sh
# Row counts and invariants on the rehearsal database (works before and after 0143).
DB="${REHEARSAL_DB_URL:?set REHEARSAL_DB_URL}"
q() { psql "$DB" -Atc "$1"; }
printf 'companies=%s users=%s products=%s suppliers=%s offers=%s offers_null_qty=%s orders=%s orderitems=%s jobs=%s audit=%s movements=%s\n' \
  "$(q 'select count(*) from store_company')" "$(q 'select count(*) from auth_user')" "$(q 'select count(*) from store_product')" \
  "$(q 'select count(*) from store_supplier')" "$(q 'select count(*) from store_supplieroffer')" \
  "$(q 'select count(*) from store_supplieroffer where confirmed_quantity is null')" "$(q 'select count(*) from store_order')" \
  "$(q 'select count(*) from store_orderitem')" "$(q 'select count(*) from store_bulkimportjob')" \
  "$(q 'select count(*) from store_adminauditlog')" "$(q 'select count(*) from store_stockmovement')"
if [ "$(q "select count(*) from information_schema.columns where table_name='store_supplieroffer' and column_name='availability_confirmed'")" = 1 ]; then
  printf 'null_qty_confirmed=%s null_qty_unconfirmed=%s\n' \
    "$(q 'select count(*) from store_supplieroffer where confirmed_quantity is null and availability_confirmed')" \
    "$(q 'select count(*) from store_supplieroffer where confirmed_quantity is null and not availability_confirmed')"
else echo 'availability_confirmed: column absent'; fi
printf 'constraint=%s ai_usage_table=%s ai_settings_table=%s last_store_migration=%s\n' \
  "$(q "select count(*) from pg_constraint where conname='supplier_offer_confirmed_has_quantity'")" \
  "$(q "select to_regclass('store_inventoryaiusage') is not null")" "$(q "select to_regclass('store_inventoryaisettings') is not null")" \
  "$(q "select max(name) from django_migrations where app='store'")"
