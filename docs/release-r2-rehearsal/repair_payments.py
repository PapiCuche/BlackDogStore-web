"""Run with the NEW code on the migrated rehearsal database: `TAG=x python3 manage.py shell < repair_payments.py`.
Repair payments through the internal V1 API (#186): two identical legitimate payments with their own keys both
land, a retry with the same key replays, a key reused for another amount is a 409, a reversal is idempotent and
the balance is the arithmetic of the rows. Status codes and amounts only."""
import os
import uuid
from decimal import Decimal

from django.conf import settings
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
from rest_framework.test import APIClient

from store.models import Company, RepairOrder, RepairPayment

res = {}
company = Company.objects.get(slug=os.environ.get('COMPANY_SLUG', 'black-dog-store'))
order = RepairOrder.objects.filter(company=company).order_by('pk').first()
c = APIClient(raise_request_exception=False)
res['login'] = c.post('/api/auth/login/', {'username': 'dev_admin', 'password': 'Demo123!'}, format='json').status_code
base = f'/api/v1/internal/{company.slug}/service/orders/{order.pk}'


def summary():
    r = c.get(f'{base}/payment-summary/')
    return r.status_code, {k: str(v) for k, v in r.json().items() if k in ('total', 'paid', 'outstanding')}


def live():
    return list(RepairPayment.objects.filter(repair_order=order, reversed_at__isnull=True).values_list('amount', flat=True))


res['summary_start'] = summary()
first = RepairPayment.objects.filter(repair_order=order, reversed_at__isnull=True).first()
if first is not None:
    r = c.post(f'{base}/payments/{first.pk}/reverse/', {'reason': 'Ensayo de reversión'}, format='json')
    res['reverse_existing'] = r.status_code
    r = c.post(f'{base}/payments/{first.pk}/reverse/', {'reason': 'Ensayo de reversión'}, format='json')
    res['reverse_again'] = r.status_code
res['summary_after_reverse'] = summary()

key_a, key_b = uuid.uuid4().hex, uuid.uuid4().hex
body = lambda key, amount='50.00': {'amount': amount, 'method': 'cash', 'idempotency_key': key}
ra = c.post(f'{base}/payments/', body(key_a), format='json')
rb = c.post(f'{base}/payments/', body(key_b), format='json')
res['two_identical_payments'] = (ra.status_code, rb.status_code, len(live()), str(sum(live())))
again = c.post(f'{base}/payments/', body(key_a), format='json')
res['same_key_replays'] = (again.status_code, again.json().get('id') == ra.json().get('id'), len(live()))
conflict = c.post(f'{base}/payments/', body(key_a, '60.00'), format='json')
res['same_key_other_amount'] = (conflict.status_code, conflict.json().get('code'), len(live()))
res['normalised_amount_replays'] = c.post(f'{base}/payments/', body(key_a, '50'), format='json').status_code
over = c.post(f'{base}/payments/', body(uuid.uuid4().hex, '99999.00'), format='json')
res['overpayment_refused'] = over.status_code
res['summary_after_payments'] = summary()
res['payments_total_in_db'] = str(sum(live()))
print('REPAIR_PAYMENTS', res)
