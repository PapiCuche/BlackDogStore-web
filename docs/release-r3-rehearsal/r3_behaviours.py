"""R3 code on the upgraded database. Status codes and counts only."""
import os, uuid
from decimal import Decimal
from django.conf import settings
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient
from store import service_services as service
from store.models import (Branch, Category, Company, LabelBatch, LabelBatchItem, Membership, Obligation, Order, Product,
                          RepairOrder, StockMovement, StockUnit, UserProfile)
User = get_user_model()
company = Company.objects.get(slug='black-dog-store')
branch = Branch.objects.filter(company=company, is_active=True).order_by('pk').first()
res = {}

def cl(username, password='Demo123!'):
    c = APIClient(raise_request_exception=False)
    c.force_authenticate(user=User.objects.get(username=username))
    return c

admin = cl('dev_admin')
# --- A. new repair approved with R3 code: receivable derived in the same transaction
o3 = RepairOrder.objects.get(company=company, number='SRV-000003')
out = service.service_payment_summary(o3)['outstanding']
if out > 0: service.record_service_payment(repair_order=o3, amount=out, method='cash', actor=User.objects.get(username='dev_admin'), idempotency_key='r3b-pay-3')
n0 = Obligation.objects.count()
call_command('seed_demo_users', '--company-slug', 'black-dog-store', '--e2e-fixtures', verbosity=0)
o4 = RepairOrder.objects.filter(company=company).order_by('-pk').first()
ob = Obligation.objects.filter(company=company).order_by('-pk').first()
res['A_new_repair'] = (o4.number, 'obligations', n0, '->', Obligation.objects.count(),
                       'origin', ob.origin_type, ob.direction, str(ob.amount_original), ob.lifecycle)
res['A_backfill_dry_after'] = None

# --- B. POS: zero price refused, normal sells, replay
zero = Product.objects.get(company=company, slug='r3-zero-price')
acc = Product.objects.get(company=company, slug='r3-acc-stock')
def sale(prod, key, qty=1, amt='25.00'):
    return admin.post('/api/admin/pos/sales/', {'branch': branch.pk, 'items': [{'product': prod.pk, 'quantity': qty}], 'payment_method': 'cash',
        'idempotency_key': key, 'terms_confirmed': True, 'amount_received': amt}, format='json')
kz = str(uuid.uuid4())
r = sale(zero, kz, amt='0.00'); res['B_zero_price'] = (r.status_code, r.json() if r.status_code != 201 else '')
stock_zero = zero.branch_stocks.first().quantity if hasattr(zero, 'branch_stocks') else None
kn = str(uuid.uuid4())
r1 = sale(acc, kn); r2 = sale(acc, kn)
res['B_normal_and_replay'] = (r1.status_code, r2.status_code, Order.objects.filter(company=company, pos_idempotency_key=kn).count())
r3 = sale(zero, kz, amt='0.00'); res['B_zero_again_same_key'] = r3.status_code
# --- C. labels
b0 = LabelBatch.objects.count()
r = admin.post('/api/admin/inventory/movements/', {'product_id': acc.pk, 'movement_type': 'purchase_entry', 'quantity': 7, 'reason': 'Compra R3', 'branch': branch.pk}, format='json')
lb = r.json().get('label_batch') if r.status_code == 201 else r.content[:150]
res['C_purchase_entry_7'] = (r.status_code, lb and {k: lb.get(k) for k in ('status', 'items', 'labels_total')} if isinstance(lb, dict) else lb, LabelBatch.objects.count() - b0)
b1 = LabelBatch.objects.count()
r = admin.post('/api/admin/inventory/movements/', {'product_id': acc.pk, 'movement_type': 'manual_entry', 'quantity': 4, 'reason': 'Hallazgo R3', 'branch': branch.pk}, format='json')
res['C_manual_entry'] = (r.status_code, r.json().get('label_batch') if r.status_code == 201 else r.content[:150], LabelBatch.objects.count() - b1)
phone = Product.objects.filter(company=company, is_serialized=True).order_by('pk').first()
def luhn(body):
    d = [int(x) for x in body]; s = sum((x * 2 - 9 if x * 2 > 9 else x * 2) if i % 2 == 0 else x for i, x in enumerate(reversed(d)))
    return body + str((10 - s % 10) % 10)
n = StockUnit.objects.count() + 100
imeis = [luhn(f'35880{n + i:09d}') for i in range(2)]
b2 = LabelBatch.objects.count()
r = admin.post('/api/admin/inventory/units/', {'branch': branch.pk, 'product_id': phone.pk, 'reason': 'Compra R3',
        'units': [{'serial_number': f'R3U{n}{i}', 'imei': imeis[i]} for i in range(2)]}, format='json')
lbu = r.json().get('label_batch') if r.status_code == 201 else r.content[:200]
res['C_units_receive_2'] = (r.status_code, {k: lbu.get(k) for k in ('status', 'items', 'labels_total')} if isinstance(lbu, dict) else lbu, LabelBatch.objects.count() - b2)
kreg = str(uuid.uuid4())
cat = Category.objects.filter(company=company).order_by('pk').first() if hasattr(Category, 'company') else Category.objects.order_by('pk').first()
body = {'kind': 'accessory', 'product': {'name': 'Funda registro R3', 'category': cat.pk, 'price': '39.90', 'sku': 'R3-FUNDA-' + uuid.uuid4().hex[:6], 'is_active': True, 'show_online': False},
        'stock': [{'branch': branch.pk, 'quantity': 6}]}
b3 = LabelBatch.objects.count()
r = admin.post('/api/admin/products/register/', body, format='json', HTTP_IDEMPOTENCY_KEY=kreg)
r2_ = admin.post('/api/admin/products/register/', body, format='json', HTTP_IDEMPOTENCY_KEY=kreg)
d = r.json() if r.status_code in (200, 201) else {}
res['C_register_accessory_6'] = (r.status_code, [s.get('labels_total') for s in d.get('label_batches', [])], r2_.status_code, LabelBatch.objects.count() - b3) if d else (r.status_code, r.content[:200])
# --- D. finance reads
fin = {}
for who in ('dev_admin', 'fin_mgr', 'fin_none', 'dev_inventory', 'dev_sales', 'dev_cashier' if User.objects.filter(username='dev_cashier').exists() else 'dev_sales'):
    c = cl(who)
    fin[who] = tuple(c.get(u).status_code for u in ('/api/admin/finance/summary/', '/api/admin/finance/obligations/', '/api/admin/finance/categories/', '/api/admin/finance/rules/'))
res['D_finance_reads(summary,obligations,categories,rules)'] = fin
r = admin.get('/api/admin/finance/obligations/'); j = r.json()
rows = j.get('results', j) if isinstance(j, dict) else j
res['D_obligations_listed'] = (r.status_code, len(rows), j.get('count') if isinstance(j, dict) else None)
# --- E. other company
other, _ = Company.objects.get_or_create(slug='otra-r3', defaults={'name': 'Otra R3'})
if not User.objects.filter(username='other_admin').exists():
    u = User.objects.create_user(username='other_admin', email='other_admin@r3.example.invalid', password='Demo123!')
    Membership.objects.create(user=u, company=other, role=UserProfile.ROLE_ADMIN, is_active=True)
oc = cl('other_admin')
pk = Obligation.objects.filter(company=company).order_by('pk').first().pk
rr = oc.get('/api/admin/finance/obligations/'); jj = rr.json()
res['E_other_company'] = (rr.status_code, len(jj.get('results', jj)) if rr.status_code == 200 else None,
                         'detail_of_foreign_obligation', oc.get(f'/api/admin/finance/obligations/{pk}/').status_code,
                         'summary', oc.get('/api/admin/finance/summary/').status_code)
res['E_foreign_payment_post'] = oc.post(f'/api/admin/finance/obligations/{pk}/payments/', {'amount': '1.00', 'method': 'cash', 'idempotency_key': uuid.uuid4().hex}, format='json').status_code
for k, v in res.items():
    print('R3B', k, v, flush=True)
