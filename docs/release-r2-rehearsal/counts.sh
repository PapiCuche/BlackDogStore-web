#!/bin/sh
# Row counts and R2 invariants (works at 0145 and at 0155). Prints numbers only, never row data.
DB="${REHEARSAL_DB_URL:?set REHEARSAL_DB_URL}"
q() { psql "$DB" -Atc "$1" 2>/dev/null || echo '-'; }
has() { [ "$(q "select to_regclass('$1') is not null")" = t ]; }
col() { [ "$(q "select count(*) from information_schema.columns where table_name='$1' and column_name='$2'")" = 1 ]; }
for t in store_company auth_user store_companyrole store_membership store_product store_productbarcode store_stockunit \
         store_branchstock store_stockmovement store_order store_orderitem store_paymenttransaction store_repairorder \
         store_repairpayment store_inventorycount store_inventorycountitem store_fiscaldocument store_fiscaldailysummary \
         store_fiscalvoidcommunication store_internalsequence store_supplieroffer store_bulkimportjob store_adminauditlog \
         token_blacklist_outstandingtoken; do
  printf '%s=%s ' "${t#store_}" "$(q "select count(*) from $t")"
done; echo
printf 'last_store_migration=%s\n' "$(q "select max(name) from django_migrations where app='store'")"
if col store_inventorycount kind; then
  printf 'counts_by_kind_status=%s\n' "$(q "select string_agg(kind||':'||status||'='||n, ' ' order by kind,status) from (select kind,status,count(*) n from store_inventorycount group by 1,2) s")"
  printf 'count_items_null_theoretical=%s\n' "$(q 'select count(*) from store_inventorycountitem where theoretical_at_start is null')"
else
  printf 'counts_by_status=%s (kind absent)\n' "$(q "select string_agg(status||'='||n, ' ' order by status) from (select status,count(*) n from store_inventorycount group by 1) s")"
fi
printf 'roles_with: adjust=%s counts.approve=%s company.manage=%s accounting.view=%s accounting.export=%s inventory.view=%s labels.print=%s labels.sensitive=%s\n' \
  "$(q "select count(*) from store_companyrole where capabilities ? 'inventory.adjust'")" \
  "$(q "select count(*) from store_companyrole where capabilities ? 'inventory.counts.approve'")" \
  "$(q "select count(*) from store_companyrole where capabilities ? 'company.manage'")" \
  "$(q "select count(*) from store_companyrole where capabilities ? 'accounting.view'")" \
  "$(q "select count(*) from store_companyrole where capabilities ? 'accounting.export'")" \
  "$(q "select count(*) from store_companyrole where capabilities ? 'inventory.view'")" \
  "$(q "select count(*) from store_companyrole where capabilities ? 'inventory.labels.print'")" \
  "$(q "select count(*) from store_companyrole where capabilities ? 'inventory.labels.sensitive'")"
col store_stockunit label_code && printf 'units_with_label_code=%s\n' "$(q 'select count(*) from store_stockunit where label_code is not null')"
for t in store_authsession store_possalekeyreservation store_companylabelsettings; do
  has $t && printf '%s=%s ' "${t#store_}" "$(q "select count(*) from $t")" || printf '%s=absent ' "${t#store_}"
done; echo
col store_fiscaldocument cdr_raw && printf 'fiscal_docs_with_cdr_raw=%s\n' "$(q 'select count(*) from store_fiscaldocument where cdr_raw is not null')"
printf 'constraints: one_open_migration=%s label_code_unique=%s label_code_not_blank=%s pos_key=%s\n' \
  "$(q "select count(*) from pg_indexes where indexname='one_open_migration_count_per_branch'")" \
  "$(q "select count(*) from pg_indexes where indexname='unique_stock_unit_label_code_per_company'")" \
  "$(q "select count(*) from pg_constraint where conname='stock_unit_label_code_null_not_blank'")" \
  "$(q "select count(*) from pg_constraint where conname='unique_pos_key_reservation'")"
