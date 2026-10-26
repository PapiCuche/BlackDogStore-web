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
