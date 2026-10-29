"""R2 code: synthetic rows relevant to R3. Counts only."""
import os
from decimal import Decimal
from django.contrib.auth import get_user_model
from store import service_services as service
from store.models import (Company, CompanyRole, Membership, MembershipRoleAssignment, Product, RepairOrder, UserProfile)
from store.tests import _prod
from store import inventory_services as inv
from store.models import Branch
User = get_user_model()
company = Company.objects.get(slug='black-dog-store')
branch = Branch.objects.filter(company=company, is_active=True).order_by('pk').first()
admin = User.objects.get(username='dev_admin')
# partial payment on the second (unpaid) repair
o2 = RepairOrder.objects.get(company=company, number='SRV-000002')
before = service.service_payment_summary(o2)
service.record_service_payment(repair_order=o2, amount=Decimal('30.00'), method='cash', actor=admin, idempotency_key='r3seed-pay-1')
after = service.service_payment_summary(o2)
print('repair2 summary', {k: str(v) for k, v in after.items() if k in ('total', 'paid', 'outstanding')}, 'before_out', before['outstanding'])
# custom roles
caps_all = list(CompanyRole.objects.get(company=company, slug='administrador').capabilities)
r_with = CompanyRole.objects.get_or_create(company=company, slug='gerencia-r3', defaults={'name': 'Gerencia R3', 'capabilities': ['company.manage', 'company.view', 'products.view', 'sales.orders.view']})[0]
r_without = CompanyRole.objects.get_or_create(company=company, slug='sin-finanzas-r3', defaults={'name': 'Sin finanzas R3', 'capabilities': ['company.view', 'products.view', 'inventory.view']})[0]
for username, role in (('fin_mgr', r_with), ('fin_none', r_without)):
    if not User.objects.filter(username=username).exists():
        u = User.objects.create_user(username=username, email=f'{username}@r3.example.invalid', password='Demo123!')
        m = Membership.objects.create(user=u, company=company, role=UserProfile.ROLE_CUSTOMER, is_active=True)
        MembershipRoleAssignment.objects.create(membership=m, is_active=True, role=role)
# accessory with stock (price>0) and active product with price 0
acc = _prod(company, 'Accesorio R3 stock', 'r3-acc-stock', price='25.00', inventory=0)
inv.apply_initial_stock(branch=branch, product=acc, quantity=20, actor=admin, reason='r3 seed')
zero = _prod(company, 'Producto precio cero R3', 'r3-zero-price', price='0.00', inventory=0)
inv.apply_initial_stock(branch=branch, product=zero, quantity=5, actor=admin, reason='r3 seed')
print('products', acc.pk, zero.pk, 'zero_active', Product.objects.filter(company=company, is_active=True, price__lte=0).count())
