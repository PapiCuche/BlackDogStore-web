"""Fixtures shared by the FINANCE-OBLIGATIONS-01 tests. Not a test module."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.cache import cache
from rest_framework.test import APIClient

D = Decimal

ALL_FINANCE = (
    'finance.view', 'finance.manage', 'finance.payments.record', 'finance.payments.reverse',
    'finance.adjust', 'finance.void', 'finance.dispute',
)


def company(slug='fin-a', name='Finanzas A'):
    from store.tests import _p3_company

    cache.clear()
    return _p3_company(slug, name)


def branch(company_, name):
    from store.tests import _p2d_branch

    return _p2d_branch(company_, name)


def member(company_, username, capabilities=ALL_FINANCE, *, branches=None):
    from store.tests import _p2d_member

    user, _membership = _p2d_member(company_, username, capabilities, branches=branches)
    return user


def client_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def rule(company_, actor, *, today=date(2026, 1, 1), **over):
    from store.finance import services

    fields = {
        'concept': 'Alquiler del local', 'amount': D('1500.00'), 'frequency': 'monthly',
        'anchor_date': date(2026, 1, 1), 'due_day': 5,
    }
    fields.update(over)
    return services.create_rule(company_, actor=actor, today=today, **fields)


# ---------------------------------------------------------------------------
# FIN-E — repair orders with an approved quote, built through the real services
# ---------------------------------------------------------------------------

RECEIVABLES = ALL_FINANCE + ('finance.receivables.manual',)


class RepairWorld:
    """A company with a customer, a device and a technician, and the repairs they bring."""

    def __init__(self, slug='fin-rep', name='Taller Finanzas', *, company_=None):
        from store.models import Device, Membership
        from store.tests import _m7_user, _v1_customer

        self.company = company_ or company(slug, name)
        self.branch = self.company.branches.order_by('pk').first() or branch(self.company, 'Principal')
        self.slug = slug
        self.client_user = _m7_user(f'{slug}_cliente')
        self.customer = _v1_customer(self.company, self.client_user, first_name='Ana', last_name='Quispe')
        self.device = Device.objects.create(
            company=self.company, customer=self.customer, device_type=Device.TYPE_PHONE, brand='G', model='X',
        )
        self.staff = _m7_user(f'{slug}_tecnico')
        Membership.objects.create(user=self.staff, company=self.company, role='sales')
        self._count = 0

    def new_customer(self, first_name='Luis'):
        from store.tests import _m7_user, _v1_customer

        self._count += 1
        user = _m7_user(f'{self.slug}_c{self._count}')
        return _v1_customer(self.company, user, first_name=first_name), user

    def order(self, total='500.00', *, approve=True, publish=True, customer=None, branch_=None,
              customer_user=None):
        """A repair order carrying a quote of `total`: approved by default, sent, or left in draft."""
        from store import service_services as svc
        from store.models import RepairStatusCode

        customer = customer or self.customer
        customer_user = customer_user or self.client_user
        order = svc.create_repair_order(
            company=self.company, branch=branch_ or self.branch, customer=customer, device=self.device,
            reported_issue='No enciende.', actor=self.staff,
        )
        svc.transition_repair_order(repair_order=order, to_status=RepairStatusCode.DIAGNOSING, actor=self.staff)
        order.refresh_from_db()
        diagnostic = svc.create_diagnostic(
            repair_order=order, description='Batería.', recommended_action='Cambiar.', actor=self.staff,
        )
        quote = svc.create_quote(repair_order=order, diagnostic=diagnostic, actor=self.staff)
        svc.add_quote_item(quote=quote, description='Trabajo', quantity=1, unit_price=Decimal(total))
        quote.refresh_from_db()
        if publish:
            svc.publish_quote(quote=quote, actor=self.staff)
            quote.refresh_from_db()
        if approve:
            self.decide(quote, customer, customer_user, 'approve')
        order.refresh_from_db()
        return order

    def decide(self, quote, customer, user, decision):
        from store import service_services as svc

        return svc.record_quote_decision(quote=quote, customer=customer, user=user, decision=decision)

    def pay(self, order, amount, *, key=''):
        from store import service_services as svc
        from store.models import PaymentMethod

        return svc.record_service_payment(
            repair_order=order, amount=Decimal(amount), method=PaymentMethod.CASH, actor=self.staff,
            idempotency_key=key,
        )

    def requote(self, order, total):
        """Reopen the approved quote and approve a new revision at `total`."""
        from store import service_services as svc

        quote = svc.approved_quote(order)
        fresh = svc.reopen_approved_quote(quote=quote, actor=self.staff, reason='Cambió el trabajo.')
        item = fresh.items.get()
        svc.remove_quote_item(item=item)
        fresh.refresh_from_db()
        svc.add_quote_item(quote=fresh, description='Trabajo', quantity=1, unit_price=Decimal(total))
        fresh.refresh_from_db()
        svc.publish_quote(quote=fresh, actor=self.staff)
        fresh.refresh_from_db()
        return fresh

    def move(self, order, to_status, *, at=None):
        """Walk one event edge through the real history writer, optionally at a fixed instant."""
        from unittest import mock

        from django.db import transaction
        from django.utils import timezone

        from store import service_services as svc
        from store.models import RepairOrder, RepairStatusHistory

        def run():
            with transaction.atomic():
                locked = RepairOrder.objects.select_for_update().get(pk=order.pk)
                svc._apply_transition(locked, to_status=to_status, actor=self.staff,
                                      origin=RepairStatusHistory.ORIGIN_INTERNAL, comment='')

        if at is None:
            run()
        else:
            with mock.patch.object(timezone, 'now', return_value=at):
                run()
        order.refresh_from_db()
        return order

    def force_status(self, order, status):
        """Fixture shortcut: skip the bench steps that are not the subject (no history row)."""
        from store.models import RepairOrder

        RepairOrder.objects.filter(pk=order.pk).update(status=status)
        order.refresh_from_db()
        return order
