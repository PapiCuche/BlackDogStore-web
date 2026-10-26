"""Run with the OLD code (6834bc2): `TAG=x python3 manage.py shell < old_smoke.py`.
What the old version must still do on whichever schema is there (0145, the mixed window at 0155,
or after the rollback): password login with cookies, me, catalogue, counts (create, fill, approve),
a till sale through the API, a device reception, a repair read, a fiscal read, logout.
Prints only status codes and counts."""
import os
import uuid

from django.conf import settings
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
from rest_framework.test import APIClient

from store import stock_unit_services as units
from store.models import Branch, Company, Order, Product, StockUnit
from store.tests import _prod

tag = os.environ['TAG']
company = Company.objects.get(slug=os.environ.get('COMPANY_SLUG', 'black-dog-store'))
branch = Branch.objects.filter(company=company, is_active=True).order_by('pk').first()
c = APIClient()
res = {}
r = c.post('/api/auth/login/', {'username': os.environ.get('SMOKE_USER', 'dev_admin'), 'password': os.environ.get('SMOKE_PASS', 'Demo123!')}, format='json')
res['login'] = r.status_code
res['me'] = c.get('/api/auth/me/').status_code
res['products'] = c.get('/api/products/').status_code
res['admin_products'] = c.get('/api/admin/products/').status_code
r = c.get('/api/admin/inventory/counts/')
res['counts_list'] = (r.status_code, len(r.json().get('results', [])) if r.status_code == 200 else r.content[:120])
for pk in [x['id'] for x in (r.json().get('results', []) if r.status_code == 200 else [])][:5]:
    d = c.get(f'/api/admin/inventory/counts/{pk}/')
    res.setdefault('count_details', []).append(d.status_code)
# A new ordinary count: create, fill, approve (old code: approval needs only inventory.adjust).
item = _prod(company, f'Accesorio smoke {tag}', f'smoke-{tag}', price='15.00', inventory=0)
from store import inventory_services as inv
from django.contrib.auth import get_user_model
inv.apply_initial_stock(branch=branch, product=item, quantity=5, actor=get_user_model().objects.get(username=os.environ.get('SMOKE_USER', 'dev_admin')),
                        reason='smoke')
r = c.post('/api/admin/inventory/counts/', {'branch': branch.pk, 'reason': f'smoke {tag}'}, format='json')
res['count_create'] = r.status_code
if r.status_code == 201:
    pk = r.json()['id']
    res['count_items'] = c.put(f'/api/admin/inventory/counts/{pk}/items/',
                               [{'product': item.pk, 'physical_quantity': 4}], format='json').status_code
    res['count_approve'] = c.post(f'/api/admin/inventory/counts/{pk}/approve/', {}, format='json').status_code
# A till sale through the API, twice with the same key (idempotent replay).
key = str(uuid.uuid4())
body = {'branch': branch.pk, 'items': [{'product': item.pk, 'quantity': 1}], 'payment_method': 'cash',
        'idempotency_key': key, 'terms_confirmed': True, 'amount_received': '15.00'}
r1 = c.post('/api/admin/pos/sales/', body, format='json')
r2 = c.post('/api/admin/pos/sales/', body, format='json')
res['pos_sale'] = (r1.status_code, r2.status_code,
                   Order.objects.filter(company=company, pos_idempotency_key=key).count())
if r1.status_code in (200, 201):
    oid = Order.objects.get(company=company, pos_idempotency_key=key).pk
    if oid:
        res['order_fiscal_read'] = c.get(f'/api/admin/orders/{oid}/fiscal-document/').status_code
# A device received by the old reception (no label_code: the column is NULL).
phone = Product.objects.filter(company=company, is_serialized=True).order_by('pk').first()
if phone:
    n = StockUnit.objects.count() + 1
    body_imei = f'35990{n:09d}'
    digits = [int(x) for x in body_imei]
    s = sum((d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 == 0 else d for i, d in enumerate(reversed(digits)))
    imei = body_imei + str((10 - s % 10) % 10)
    got = units.receive_units(branch=branch, product=phone, units=[{'imei': imei, 'serial_number': f'SMK{tag}{n}'}],
                              reason='smoke')
    res['unit_received'] = len(got)
res['v1_service_orders'] = c.get(f'/api/v1/internal/{company.slug}/service/orders/').status_code
res['v1_pos_context'] = c.get(f'/api/v1/internal/{company.slug}/sales/pos/context/').status_code
res['logout'] = c.post('/api/auth/logout/', {}, format='json').status_code
res['me_after_logout'] = c.get('/api/auth/me/').status_code
print('OLD_SMOKE', tag, res)
