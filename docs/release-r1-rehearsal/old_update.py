"""OLD code: update an existing offer with no sales to an unknown quantity, and read through the old API."""
from django.conf import settings
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
from rest_framework.test import APIClient
from store import supply_services as supply
from store.models import SupplierOffer
offer = SupplierOffer.objects.filter(confirmed_quantity__isnull=False, committed_quantity=0).order_by('pk').first()
if offer is None:
    from store.test_supplier_offers import today
    from datetime import timedelta
    offer = SupplierOffer.objects.filter(committed_quantity=0).order_by('pk').first()
    offer = supply.update_offer(offer, actor=offer.created_by, may_view_costs=True, confirmed_quantity=4)
    print('set_to_4', offer.pk)
upd = supply.update_offer(offer, actor=offer.created_by, may_view_costs=True, confirmed_quantity=None)
print('updated_to_null', upd.pk, upd.confirmed_quantity)
client = APIClient(); client.force_authenticate(user=offer.created_by)
r = client.get(f'/api/admin/supplier-offers/?company={offer.company_id}')
print('old_api_list', r.status_code)
r = client.get(f'/api/admin/supplier-offers/export/?company={offer.company_id}')
print('old_api_export', r.status_code)
print('OK update')
