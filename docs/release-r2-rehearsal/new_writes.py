"""Run with the NEW code (c3ee89e) on the migrated database: `OLD_COOKIES=<file> TAG=x python3 manage.py shell < new_writes.py`.
Writes what only R2 writes (web sessions, label codes and settings, a generated product code, a migration
count with pending lines, a «no se cobró» key) and checks the candidate's API flows. Status codes and
counts only."""
import io
import json
import os
import uuid

from django.conf import settings
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from store import stock_unit_services as units
from store.models import (AuthSession, Branch, Company, InventoryCount, InventoryCountItem, PosSaleKeyReservation,
                          Product, ProductBarcode, StockUnit)
from store.tests import _prod

tag = os.environ['TAG']
res = {}
company = Company.objects.get(slug=os.environ.get('COMPANY_SLUG', 'black-dog-store'))
branch = Branch.objects.filter(company=company, is_active=True).order_by('pk').first()
User = get_user_model()


def client(username, password='Demo123!'):
    c = APIClient(raise_request_exception=False)
    r = c.post('/api/auth/login/', {'username': username, 'password': password}, format='json')
    return c, r.status_code


# 1. Sessions left by the OLD version are closed once (forced logout at deploy).
path = os.environ.get('OLD_COOKIES')
if path and os.path.exists(path):
    for who, jar in json.load(open(path)).items():
        c = APIClient(raise_request_exception=False)
        for k, v in jar.items():
            c.cookies[k] = v
        me = c.get('/api/auth/me/')
        browse = c.get('/api/products/')
        refresh = c.post('/api/auth/refresh/', {}, format='json')
        res[f'old_cookie_{who}'] = (me.status_code, me.headers.get('X-Session-Expired'), browse.status_code,
                                    refresh.status_code, (refresh.json() if refresh.content else {}).get('code'))

# 2. Password login, session status.
admin, res['login_admin'] = client(os.environ.get('SMOKE_USER', 'dev_admin'), os.environ.get('SMOKE_PASS', 'Demo123!'))
s = admin.get('/api/auth/session/')
res['session_status'] = (s.status_code, s.headers.get('X-Session-Idle-Remaining'))
res['auth_sessions_open'] = AuthSession.objects.filter(ended_at__isnull=True).count()

# 3. Device codes for what exists (dry run, then apply) and the collision audit.
out = io.StringIO(); call_command('audit_code_collisions', stdout=out)
res['audit_collisions_lines'] = len([l for l in out.getvalue().splitlines() if l.strip()])
out = io.StringIO(); call_command('assign_unit_codes', '--company', company.slug, stdout=out)
res['assign_dry_run_coded'] = StockUnit.objects.filter(company=company, label_code__isnull=False).count()
out = io.StringIO(); call_command('assign_unit_codes', '--company', company.slug, '--apply', stdout=out)
res['assign_apply_coded'] = (StockUnit.objects.filter(company=company, label_code__isnull=False).count(),
                             StockUnit.objects.filter(company=company).count())

# 4. A product without a code gets one; the existing `P000001` is skipped.
fresh = _prod(company, f'Producto sin código {tag}', f'r2-{tag}-nocode', price='10.00', inventory=0)
g = admin.post(f'/api/admin/products/{fresh.pk}/barcodes/generate/', {}, format='json')
res['generate_code'] = (g.status_code, list(ProductBarcode.objects.filter(product=fresh).values_list('code', flat=True)))

# 5. A device received by the new reception gets its label code at once.
phone = Product.objects.filter(company=company, is_serialized=True).order_by('pk').first()
n = StockUnit.objects.count() + 1
body = f'35770{n:09d}'
d = [int(x) for x in body]
chk = sum((x * 2 - 9 if x * 2 > 9 else x * 2) if i % 2 == 0 else x for i, x in enumerate(reversed(d)))
got = units.receive_units(branch=branch, product=phone, units=[{'imei': body + str((10 - chk % 10) % 10),
                                                                  'serial_number': f'NEW{tag}{n}'}], reason='r2')
res['new_unit_label_code'] = bool(got[0].label_code)

# 6. Labels: preview and real-scale PDF (internal profile), settings.
unit_ids = ','.join(str(u) for u in StockUnit.objects.filter(company=company, status='available')
                    .values_list('pk', flat=True)[:3])
q = f'?branch={branch.pk}&products={fresh.pk}:2&units={unit_ids}'
p = admin.get('/api/admin/inventory/labels/preview/' + q)
res['labels_preview'] = p.status_code
p = admin.get('/api/admin/inventory/labels/pdf/' + q)
res['labels_pdf'] = (p.status_code, p.headers.get('Content-Type'), p.headers.get('X-Labels-Pages'),
                     p.headers.get('X-Labels-Review'), p.content[:4] == b'%PDF')
ls = admin.get('/api/admin/label-settings/')
res['label_settings'] = (ls.status_code, admin.patch('/api/admin/label-settings/', {'unit_code_prefix': 'U'},
                                                     format='json').status_code)

# 7. Counts: an ordinary one approved, a migration one left open with pending lines.
cable = Product.objects.filter(company=company, slug__startswith='r2-', slug__endswith='-cable').order_by('pk').first()
if cable is None:
    from store import inventory_services as inv
    cable = _prod(company, f'Cable ensayo {tag}', f'r2-{tag}-cable', price='40.00', inventory=0)
    inv.apply_initial_stock(branch=branch, product=cable, quantity=10, reason='ensayo R2')
r = admin.post('/api/admin/inventory/counts/', {'branch': branch.pk, 'reason': f'ordinario {tag}'}, format='json')
res['regular_create'] = r.status_code
if r.status_code == 201:
    pk = r.json()['id']
    res['regular_items'] = admin.put(f'/api/admin/inventory/counts/{pk}/items/',
                                     [{'product': cable.pk, 'physical_quantity': 3}], format='json').status_code
    a = admin.post(f'/api/admin/inventory/counts/{pk}/approve/', {}, format='json')
    res['regular_approve'] = (a.status_code, (a.json() or {}).get('code') if a.status_code != 200 else '')
r = admin.post('/api/admin/inventory/counts/', {'branch': branch.pk, 'kind': 'migration', 'reason': f'migración {tag}'},
               format='json')
res['migration_create'] = (r.status_code, (r.json() or {}).get('code') if r.status_code != 201 else '')
mig = InventoryCount.objects.filter(company=company, kind='migration').order_by('-pk').first()
if mig is not None:
    sc = admin.post(f'/api/admin/inventory/counts/{mig.pk}/scan/', {'mode': 'increment', 'product': cable.pk},
                    format='json')
    res['migration_scan'] = sc.status_code
    res['migration_detail'] = admin.get(f'/api/admin/inventory/counts/{mig.pk}/').status_code
    res['migration_null_theoretical_lines'] = InventoryCountItem.objects.filter(
        count=mig, theoretical_at_start__isnull=True).count()

# 8. Till: a sale replayed with its key, the lookup by key, «no se cobró» and the refused reuse.
k1, k2 = str(uuid.uuid4()), str(uuid.uuid4())
sale = {'branch': branch.pk, 'items': [{'product': cable.pk, 'quantity': 1}], 'payment_method': 'cash',
        'terms_confirmed': True, 'amount_received': '40.00'}
s1 = admin.post('/api/admin/pos/sales/', {**sale, 'idempotency_key': k1}, format='json')
s2 = admin.post('/api/admin/pos/sales/', {**sale, 'idempotency_key': k1}, format='json')
res['pos_sale_and_replay'] = (s1.status_code, s2.status_code)
res['pos_by_key_found'] = admin.get(f'/api/admin/pos/sales/by-key/?key={k1}').status_code
res['pos_by_key_missing'] = admin.get(f'/api/admin/pos/sales/by-key/?key={k2}').status_code
dec = admin.post('/api/admin/pos/sales/by-key/decision/', {'key': k2, 'decision': 'not_charged'}, format='json')
res['pos_not_charged'] = dec.status_code
s3 = admin.post('/api/admin/pos/sales/', {**sale, 'idempotency_key': k2}, format='json')
res['pos_reserved_key_sale'] = (s3.status_code, (s3.json() or {}).get('code'))
res['pos_reservations'] = PosSaleKeyReservation.objects.count()

# 9. Repairs (V1 internal, same session) and the accounting centre.
res['v1_service_orders'] = admin.get(f'/api/v1/internal/{company.slug}/service/orders/').status_code
for name in ('summary', 'operations', 'fiscal-documents', 'pending'):
    res[f'accounting_{name}'] = admin.get(f'/api/admin/accounting/{name}/').status_code
sales, _ = client(os.environ.get('SALES_USER', 'dev_sales'), os.environ.get('SMOKE_PASS', 'Demo123!'))
res['accounting_as_sales'] = sales.get('/api/admin/accounting/summary/').status_code
res['labels_as_sales'] = sales.get('/api/admin/inventory/labels/preview/' + q).status_code

# 10. Two companies: a staff member of the other company never reaches this one.
other = Company.objects.exclude(pk=company.pk).filter(slug__startswith='rel-').order_by('pk').first()
if other is not None:
    stranger, code = client(f'{other.slug}_staff', 'Pass123!')
    res['stranger_login'] = code
    res['stranger_counts_other'] = stranger.get(f'/api/admin/inventory/counts/?company={company.pk}').status_code
    res['stranger_accounting_other'] = stranger.get(f'/api/admin/accounting/summary/?company={company.pk}').status_code
    res['stranger_labels_other'] = stranger.get(f'/api/admin/label-settings/?company={company.pk}').status_code
    res['stranger_counts_own'] = stranger.get('/api/admin/inventory/counts/').status_code

# 11. Mobile V1: login / refresh / me with Bearer, unchanged.
c = APIClient(raise_request_exception=False)
v = c.post('/api/v1/auth/login/', {'email': User.objects.get(username=os.environ.get('CUSTOMER_USER', 'dev_customer')).email, 'password': os.environ.get('SMOKE_PASS', 'Demo123!')}, format='json')
tokens = v.json() if v.status_code == 200 else {}
access, refresh = tokens.get('access'), tokens.get('refresh')
res['v1_login'] = (v.status_code, sorted(k for k in tokens if k in ('access', 'refresh')))
if access:
    b = APIClient(raise_request_exception=False)
    b.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
    res['v1_me'] = b.get(f'/api/v1/customer/{company.slug}/').status_code
    rr = c.post('/api/v1/auth/refresh/', {'refresh': refresh}, format='json')
    res['v1_refresh'] = rr.status_code
    res['v1_sessions_rows'] = AuthSession.objects.filter(user__username=os.environ.get('CUSTOMER_USER', 'dev_customer')).count()

# 12. Logout ends the web session.
res['logout'] = admin.post('/api/auth/logout/', {}, format='json').status_code
res['me_after_logout'] = admin.get('/api/auth/me/').status_code
print('NEW_WRITES', tag, json.dumps(res, default=str))
