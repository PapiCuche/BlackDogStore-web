"""ONLY on a LOCAL restore: `R2_PASS=<local> python3 manage.py shell < local_accounts.py` (any code version).
Creates three local test accounts (admin, sales, customer) with `.invalid` e-mails, assigned to the
company's untouched preset roles. Never touches an existing user."""
import os

from django.contrib.auth import get_user_model
from store.models import Company, CompanyRole, Membership, MembershipRoleAssignment, UserProfile

password = os.environ['R2_PASS']
company = Company.objects.get(slug=os.environ.get('COMPANY_SLUG', 'black-dog-store'))
User = get_user_model()
for username, role_slug in (('r2_local_admin', 'administrador'), ('r2_local_sales', 'ventas'), ('r2_local_customer', None)):
    if User.objects.filter(username=username).exists():
        continue
    user = User.objects.create_user(username=username, email=f'{username}@r2.example.invalid', password=password)
    if role_slug:
        membership = Membership.objects.create(user=user, company=company, role=UserProfile.ROLE_CUSTOMER,
                                               is_active=True)
        MembershipRoleAssignment.objects.create(membership=membership, is_active=True,
                                                role=CompanyRole.objects.get(company=company, slug=role_slug))
print('local accounts', User.objects.filter(username__startswith='r2_local_').count())
