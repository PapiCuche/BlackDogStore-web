"""Run with the OLD code (6834bc2) on a database at 0145, after `seed_demo_users --company-slug
black-dog-store --e2e-fixtures`: `TAG=x python3 manage.py shell < old_seed.py`.

Writes through the OLD services only: stocked accessories, a serialized phone received with IMEIs,
barcodes (one in the new `P` series, one equal to an IMEI), a till sale, an approved ordinary count and
an open one, a repair paid off, a BETA fiscal document row (no SUNAT call), and two edited roles."""
import os
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from store import inventory_services as inv, pos_services as pos, service_services as service
from store import stock_unit_services as units
from store.models import (
    Branch, Company, CompanyRole, FiscalDocument, FiscalDocumentType, FiscalEnvironment, FiscalSeries,
    InventoryCount, Product, ProductBarcode, RepairOrder,
)
from store.tests import _prod

tag = os.environ['TAG']
User = get_user_model()
company = Company.objects.get(slug='black-dog-store')
branch = Branch.objects.filter(company=company, is_active=True).order_by('pk').first()
admin = User.objects.get(username='dev_admin')


def luhn(body):
    digits = [int(c) for c in body]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return body + str((10 - total % 10) % 10)


# 1. Accessories with stock (initial load) and their codes.
cable = _prod(company, f'Cable R2 {tag}', f'r2-{tag}-cable', price='40.00', inventory=0)
case = _prod(company, f'Funda R2 {tag}', f'r2-{tag}-funda', price='60.00', inventory=0)
inv.apply_initial_stock(branch=branch, product=cable, quantity=20, actor=admin, reason='Carga ensayo R2')
inv.apply_initial_stock(branch=branch, product=case, quantity=10, actor=admin, reason='Carga ensayo R2')
ProductBarcode.objects.create(company=company, product=cable, code=f'77500{tag}0000011'[:13], symbology='unknown',
                              is_primary=True)
# A code that the NEW generator would produce first (`P000001`): generation must skip it.
if not ProductBarcode.objects.filter(company=company, code='P000001').exists():
    ProductBarcode.objects.create(company=company, product=case, code='P000001', symbology='internal',
                                  is_primary=True)

# 2. A serialized phone received with IMEIs (old reception).
phone = _prod(company, f'iPhone R2 {tag} 128 GB', f'r2-{tag}-phone', price='3999.00', inventory=0)
phone = units.set_serialized(phone, serialized=True, requires_imei=True, actor=admin)
seed = abs(hash(tag)) % 10**6
imeis = [luhn(f'35{seed:06d}{n:06d}') for n in range(1, 4)]
received = units.receive_units(branch=branch, product=phone, actor=admin, reason='Compra ensayo R2',
                               units=[{'imei': i, 'serial_number': f'R2{tag.upper()}SN{n}', 'cost': '3000.00'}
                                      for n, i in enumerate(imeis, 1)])
# A legacy barcode equal to a device IMEI (what `audit_code_collisions` must report).
other = _prod(company, f'Protector R2 {tag}', f'r2-{tag}-prot', price='20.00', inventory=0)
ProductBarcode.objects.create(company=company, product=other, code=imeis[2], symbology='unknown', is_primary=True)

# 3. A till sale of 2 cables.
sale = pos.create_pos_sale(actor=admin, company=company, branch=branch,
                           items=[{'product': cable.pk, 'quantity': 2}],
                           payment_method='cash', idempotency_key=str(uuid.uuid4()), terms_confirmed=True,
                           amount_received=Decimal('80.00'))
order = sale[0] if isinstance(sale, tuple) else sale
print('pos_sale', getattr(order, 'pk', order), getattr(order, 'status', ''))

# 4. Counts: one approved (ordinary) and one left open.
approved = inv.create_inventory_count(company=company, branch=branch, actor=admin, reason='Recuento R2 aprobado')
inv.set_count_item(approved, product=case, physical_quantity=9, note='falta una')
inv.approve_inventory_count(approved, actor=admin)
opened = inv.create_inventory_count(company=company, branch=branch, actor=admin, reason='Recuento R2 abierto')
inv.set_count_item(opened, product=cable, physical_quantity=17)
print('counts', InventoryCount.objects.filter(company=company).values_list('pk', 'status').order_by('pk')[::1])

# 5. The E2E repair, paid off.
repair = RepairOrder.objects.filter(company=company).order_by('pk').first()
owed = service.service_payment_summary(repair)['outstanding']
if owed > 0:
    service.record_service_payment(repair_order=repair, amount=owed, method='cash', actor=admin,
                                   idempotency_key=str(uuid.uuid4()))
print('repair', repair.number, service.service_payment_summary(repair)['outstanding'])

# 6. A BETA boleta row on the till sale (no SUNAT call: the row is written directly).
if hasattr(order, 'pk'):
    series, _ = FiscalSeries.objects.get_or_create(company=company, document_type=FiscalDocumentType.RECEIPT,
                                                    series='B001')
    number = (FiscalDocument.objects.filter(company=company, series='B001').count() or 0) + 1
    FiscalDocument.objects.create(
        order=order, company=company, series_ref=series, document_type=FiscalDocumentType.RECEIPT,
        series='B001', number=number, issued_at=timezone.now(), environment=FiscalEnvironment.BETA,
        issuer_tax_id='20000000001', issuer_legal_name='Ensayo S.A.C.', customer_doc_type='0',
        customer_doc_number='-', customer_legal_name='CLIENTE VARIOS', currency='PEN',
        taxable_amount=Decimal('67.80'), tax_amount=Decimal('12.20'), total=Decimal('80.00'),
        tax_rate=Decimal('0.18'), status='accepted', signed_xml='<Invoice/>', cdr_xml='<ApplicationResponse/>')

# 7. Roles a tenant edited.
CompanyRole.objects.get_or_create(company=company, slug=f'bodega-{tag}', defaults={
    'name': f'Bodega editada {tag}', 'capabilities': ['inventory.adjust', 'inventory.view', 'products.view']})
base = CompanyRole.objects.filter(company=company, capabilities__contains=['company.manage']).order_by('pk').first()
CompanyRole.objects.get_or_create(company=company, slug=f'admin-editado-{tag}', defaults={
    'name': f'Administrador editado {tag}',
    'capabilities': sorted(set(base.capabilities) - {'reports.view'})})
CompanyRole.objects.get_or_create(company=company, slug=f'caja-{tag}', defaults={
    'name': f'Caja {tag}', 'capabilities': ['sales.pos.use', 'sales.orders.view']})
print('OK', tag)
