# GD-001 — Auditoría (24-11-2026)
Estado PARCIAL; base inspeccionada bf6a33075e31d43de4defc5d96a3279a13aaa4b6. Sin implementación, migraciones ni despliegue.
## Evidencia
- backend/requirements.txt y frontend/package.json (revisión anterior): Django 5.2.17, DRF 3.18.1, Next 16.3.8, React 19.3, PostgreSQL.
- backend/store/models.py: Company, Branch, Product, ProductModel, ProductVariantOption, ProductImage, BranchStock, StockUnit, StockMovement, Promotion, Order, RepairOrder, IntegrationConfig. Product.price y StockUnit.sale_price son DecimalField; la variante se representa por Product, no crear otra tabla. Confirmar identidad SKU con ProductBarcode y reglas de códigos; no asumir campo sku.
- inventory_services.py: BranchStock.quantity maestro; Product.inventory agregado de compatibilidad; Kardex y filas bloqueadas en transacción. StockUnit AVAILABLE concuerda con stock serializado.
- unit_hold_services.py: price_for usa precio de unidad usada y de variante nueva; reserva vencida puede quedar protegida por pago pendiente. Nunca llamar release_expired desde CRM.
- device_catalog_views.py: PublicModelDetailView.get llama release_expired(company=company) antes de public_model. **P0: GET existente tiene mutación indirecta**. No invocarlo ni reutilizar su ruta como proxy. Extraer/reusar únicamente lectura pura tras verificar transitivamente SQL y efectos. No llamar get_or_create_branch_stock. Una reserva vencida aún no liberada cuenta como no disponible hasta liberación autónoma del backend.
- availability_views.py: AdminAvailabilityView, search_products, _product_rows; stock por sucursal y datos de proveedores. No reutilizar payload administrativo; excluir ofertas/costos y disponibilidad no confirmada. device_catalog.py: public_model, new_for_sale_prices, public_unit, _unit_price candidatos sujetos a auditoría de efectos y exposición.
- tenancy.py: storefront_available_stock, customer_owned_orders, visible_orders, scope_queryset. El principal CRM no es un usuario staff; usar alcance explícito y vínculo de cliente probado.
- tracking_views.py: AccountRepairsView se limita por Customer.user; no convertir ID, teléfono o correo declarado por CRM en prueba de propiedad.
- urls.py: products/, catalog/models/, orders/, admin/availability/, admin/inventory/stock/, admin/inventory/movements/, admin/inventory/units/, admin/sales/promotions/, admin/branches/, admin/integrations/, account/repairs/. Existen reserva/liberación de unidades administrativas: prohibidas para CRM. Prefijo global y detalle de autenticación por confirmar. Payments Izipay/MiCuentaWeb y WhatsApp existentes según revisión previa; no equivalen a outbox genérico.
- integrations/registry.py e IntegrationConfig: registro y configuración active/draft existente; no confundir consola de platform admin con identidad S2S.
## Brechas
Identidad S2S aislada, contrato de lectura puro, proyecciones allowlist, feed incremental consistente, autorizaciones de cliente y outbox Good Doggy: PROPUESTA, no verificados como implementados. No inventar catálogo de servicios a partir de RepairOrder.
## Baseline
Combined status de SHA devolvió lista vacía; no equivale a CI verde. Tests, migraciones completas, prefijo raíz, trazado de efectos de todas las funciones y configuración de producción no verificados. Bloquean implementación, no revisión documental.
