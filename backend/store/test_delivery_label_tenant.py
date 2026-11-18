"""
TENANT-LEAK-DELIVERY — the local-delivery label names the ORDER's company city.

The stored choice is `delivery_arequipa`, a key that carries the first shop's
city, and its model label is «Delivery Arequipa». Every customer-facing place
used to print that label as-is, so a second company's customers read that
their order was being delivered in a city where that company has no shop.

What is pinned here:
  - the confirmation e-mail and the v1 customer/internal payloads use the
    tenant-neutral `pdf_services.delivery_label`, fed with the order's city;
  - a company in another city never sees «Arequipa»;
  - the pilot company, whose city IS Arequipa, reads exactly what it read before;
  - the API field names and types do not change (only the display value);
  - the DB `choices` do not change (no migration).
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import email_services
from .company_settings import build_identity_snapshot
from .models import Company, CompanySettings, Customer, Order, OrderItem, Product
from .serializers import CheckoutInputSerializer
from .v1_customer_serializers import V1CustomerOrderSerializer
from .v1_internal_serializers import V1InternalOrderDetailSerializer


def _company(name, slug, tax_id, city):
    company = Company.objects.create(
        name=name, legal_name=f'{name} S.A.C.', tax_id=tax_id, slug=slug,
    )
    CompanySettings.objects.create(company=company, city=city)
    return company


def _paid_order(company, *, method=Order.DeliveryMethod.DELIVERY_AREQUIPA, snapshot=True, user=None):
    order = Order.objects.create(
        company=company, user=user, customer_email='cliente@example.com',
        customer_name='Cliente', total=Decimal('100.00'),
        status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
        delivery_method=method,
        address_line='Av. Principal 1', district='Centro',
        company_snapshot=build_identity_snapshot(company) if snapshot else {},
    )
    product = Product.objects.create(
        company=company, name='Funda', slug=f'funda-{company.slug}-{order.pk}',
        price=Decimal('100.00'), inventory=5,
    )
    OrderItem.objects.create(order=order, product=product, quantity=1, price=Decimal('100.00'))
    return order


class DeliveryLabelTenantBase(TestCase):
    def setUp(self):
        cache.clear()
        self.pilot = _company('Piloto', 'tl-piloto', '20100000001', 'Arequipa')
        self.other = _company('Otra Tienda', 'tl-otra', '20100000002', 'Cusco')


class ConfirmationEmailDeliveryLabelTest(DeliveryLabelTenantBase):
    def _texts(self, order):
        ctx = email_services.build_order_confirmation_context(order)
        subject, text, html = email_services._customer_bodies(order, ctx, receipt_attached=False)
        internal = email_services._internal_bodies(order, ctx, admin_url='https://admin.example')
        return ctx, [subject, text, html, *(body for body in internal if body)]

    def test_a_company_in_another_city_never_reads_arequipa(self):
        order = _paid_order(self.other)

        ctx, bodies = self._texts(order)

        self.assertEqual(ctx['delivery_label'], 'Delivery Cusco')
        for body in bodies:
            self.assertNotIn('Arequipa', body)

    def test_the_pilot_company_reads_what_it_read_before(self):
        order = _paid_order(self.pilot)

        ctx, _ = self._texts(order)

        self.assertEqual(ctx['delivery_label'], 'Delivery Arequipa')

    def test_an_order_without_snapshot_uses_the_live_company_city(self):
        order = _paid_order(self.other, snapshot=False)

        ctx, _ = self._texts(order)

        self.assertEqual(ctx['delivery_label'], 'Delivery Cusco')

    def test_a_company_with_no_city_reads_a_neutral_label(self):
        CompanySettings.objects.filter(company=self.other).update(city='')
        order = _paid_order(Company.objects.get(pk=self.other.pk), snapshot=True)

        ctx, _ = self._texts(order)

        self.assertEqual(ctx['delivery_label'], 'Delivery local')

    def test_the_other_methods_are_unchanged(self):
        pickup = _paid_order(self.other, method=Order.DeliveryMethod.PICKUP_STORE)
        national = _paid_order(self.pilot, method=Order.DeliveryMethod.NATIONAL_SHIPPING)

        self.assertEqual(
            email_services.build_order_confirmation_context(pickup)['delivery_label'],
            'Recojo en tienda',
        )
        self.assertEqual(
            email_services.build_order_confirmation_context(national)['delivery_label'],
            'Envío nacional',
        )


class V1DeliveryMethodLabelTest(DeliveryLabelTenantBase):
    def test_customer_payload_of_another_company_never_reads_arequipa(self):
        data = V1CustomerOrderSerializer(_paid_order(self.other)).data

        self.assertEqual(data['delivery_method'], 'delivery_arequipa')   # the stored key is unchanged
        self.assertEqual(data['delivery_method_label'], 'Delivery Cusco')

    def test_internal_payload_of_another_company_never_reads_arequipa(self):
        data = V1InternalOrderDetailSerializer(_paid_order(self.other)).data

        self.assertEqual(data['delivery_method'], 'delivery_arequipa')
        self.assertEqual(data['delivery_method_label'], 'Delivery Cusco')

    def test_the_pilot_payloads_are_unchanged(self):
        order = _paid_order(self.pilot)

        self.assertEqual(V1CustomerOrderSerializer(order).data['delivery_method_label'], 'Delivery Arequipa')
        self.assertEqual(V1InternalOrderDetailSerializer(order).data['delivery_method_label'], 'Delivery Arequipa')

    def test_other_methods_and_empty_method_keep_their_labels(self):
        pickup = _paid_order(self.other, method=Order.DeliveryMethod.PICKUP_STORE)
        legacy = _paid_order(self.pilot, method='')

        self.assertEqual(V1CustomerOrderSerializer(pickup).data['delivery_method_label'], 'Recojo en tienda')
        self.assertEqual(V1CustomerOrderSerializer(legacy).data['delivery_method_label'], '')
        self.assertEqual(V1InternalOrderDetailSerializer(legacy).data['delivery_method_label'], '')

    def test_the_customer_endpoint_of_another_company_never_reads_arequipa(self):
        user = User.objects.create_user(
            username='tlana', email='tlana@example.com', password='Pass123!',
        )
        Customer.objects.create(company=self.other, user=user, first_name='Ana')
        _paid_order(self.other, user=user)
        _paid_order(self.other, user=user, snapshot=False)
        client = APIClient()
        token = client.post(
            '/api/v1/auth/login/', {'email': 'tlana@example.com', 'password': 'Pass123!'},
            format='json',
        ).json()['access']
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        response = client.get('/api/v1/customer/tl-otra/orders/')

        self.assertEqual(response.status_code, 200)
        labels = [row['delivery_method_label'] for row in response.json()]
        self.assertEqual(labels, ['Delivery Cusco', 'Delivery Cusco'])
        self.assertNotIn('Arequipa', response.content.decode())


class CheckoutValidationMessageTest(TestCase):
    def test_local_delivery_errors_do_not_name_a_city(self):
        serializer = CheckoutInputSerializer(data={
            'customer_name': 'Cliente', 'customer_email': 'c@example.com',
            'customer_phone': '987654321', 'document_type': 'dni',
            'document_number': '12345678', 'receipt_type': 'boleta',
            'delivery_method': 'delivery_arequipa',
            'accepted_terms': True, 'accepted_warranty_policy': True, 'session_key': 'sesion-1',
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn('address_line', serializer.errors)
        self.assertNotIn('Arequipa', str(serializer.errors))


class DeliveryChoicesUnchangedTest(TestCase):
    def test_the_stored_choices_are_unchanged(self):
        # No migration: only the DISPLAY of the label moves to the neutral helper.
        self.assertEqual(Order.DeliveryMethod.DELIVERY_AREQUIPA.value, 'delivery_arequipa')
        self.assertEqual(Order.DeliveryMethod.DELIVERY_AREQUIPA.label, 'Delivery Arequipa')
