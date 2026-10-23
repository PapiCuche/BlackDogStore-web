"""Run with the OLD code (6daf9ae): `TAG=x python3 manage.py shell < old_writes.py`.
Writes through the OLD services and API: supplier, offers (with and without quantity), the weekly import
(a row with an empty quantity), a used variant, and an on-order order paid by the real `_confirm`."""
import os
from django.conf import settings
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
from store import device_catalog, supply_services as supply
from store.models import CartItem, Product, SupplierOffer
from store.test_supplier_weekly_import import WeeklyFile
from store.tests import _prod

tag = os.environ['TAG']
w = WeeklyFile()
w.build_week(f'rel-{tag}')                       # company, supplier, offer «Cargador 20W» qty 5, product «Cable USB-C»
# 1. A direct offer WITHOUT quantity (old create_offer, as the panel form does).
phone = _prod(w.company, 'Equipo Ensayo 128 GB', f'rel-{tag}-eq', price='999.00', inventory=0)
Product.objects.filter(pk=phone.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
no_qty = w.make_offer(product=phone, confirmed_quantity=None)
# 2. The weekly import: «Cable USB-C» with an EMPTY quantity (new offer) and the running offer re-stated (7).
body = w.preview([w.same(**{'Cantidad confirmada': 7}),
                  w.row(**{'Código/SKU del producto': w.cable.slug, 'Cantidad confirmada': None})]).json()
print('preview', {k: body.get(k) for k in ('rows_create', 'rows_update', 'rows_error', 'rows_skip')},
      [r.get('errors') for r in body.get('rows', []) if r.get('errors')][:3])
applied = w.apply(body['id'])
print('apply', applied.status_code, str(applied.content[:160]))
# 3. A used variant (line «used»).
cap = device_catalog.create_attribute(company=w.company, name='Capacidad')
gb = device_catalog.create_option(company=w.company, attribute_id=cap.pk, label='128 GB')
model = device_catalog.create_model(company=w.company, name=f'Modelo Ensayo {tag}')
used = _prod(w.company, 'Modelo Ensayo 128 GB usado', f'rel-{tag}-used', price='500.00', inventory=0)
device_catalog.set_variant(used, model_id=model.pk, condition_line='used', option_ids=[gb.pk])
# 4. An on-order order of the accessory, paid through the real confirmation.
w.add_on_order(quantity=1, session=f'cart-{tag}')
order = w.checkout(session=f'cart-{tag}')
w.pay(order)
order.refresh_from_db()
print('order', order.pk, order.status, list(order.items.values_list('supply_status', flat=True)))
print('offers', list(SupplierOffer.objects.filter(company=w.company).values_list('product__name', 'confirmed_quantity')))
# 5. An EXISTING offer of an earlier run updated to an unknown quantity (old update_offer), and the old API reads.
earlier = SupplierOffer.objects.exclude(company=w.company).filter(confirmed_quantity__isnull=False, committed_quantity=0).order_by('pk').first()
if earlier is not None:
    upd = supply.update_offer(earlier, actor=w.staff, may_view_costs=True, confirmed_quantity=None)
    print('updated_to_null', upd.pk, upd.confirmed_quantity)
listed = w.client_.get('/api/admin/supplier-offers/')
print('old_api_list', listed.status_code, len(listed.json().get('results', listed.json())) if listed.status_code == 200 else listed.content[:120])
exported = w.client_.get('/api/admin/supplier-offers/export/')
print('old_api_export', exported.status_code)
print('OK', tag)
