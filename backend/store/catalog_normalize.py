"""
CATALOG-F11 — normalise ONE company's device catalogue after an import (Fase 1.1, `docs/adr-catalog-reference.md` §9).

    PLAN WRITES NOTHING. APPLY ONLY GIVES THE CAPACITY AND COLOUR ROLES AND WRITES CAPACITIES THE CATALOGUE WAY.

A company that sold devices before the reference catalogue has its own «Capacidad» and «Color» attributes with the
role «Otro», and capacities written by hand («128Gb», «1TB») ordered 0, 1, 2… The reference import reuses them as they
are (D-PRG-8: it never renames nor reorders what exists), so the POS asks SIM and region before capacity and colour,
new variant names put the options in that order, and «16 GB» sorts after «1TB». This module fixes exactly that:

  * roles — the attribute that `Vocabulary.roles_of` already READS as capacity (the unique one named like a capacity)
    and as colour (the unique «Otro» attribute of kind colour) gets that role, only when nobody holds it. A role the
    company assigned is never moved; two candidates are ambiguous and left alone;
  * capacities — every option of the capacity attribute whose label reads as a size («128Gb», «1 tb») is written the
    way the catalogue writes it («128 GB», «1 TB», `catalog_reference.capacity_label`) and ordered by its size
    (`sort_order` = GB). A label that is not a size, or whose new label another option or a supplier alias already
    holds, is reported and left alone (merging two options is not this module's job).

Every write goes through `device_catalog.update_attribute` / `update_option`: their checks, their audit (with company
and actor, and now the value before). `reference_key` is never touched, so the import still recognises each option. A
`Product` keeps its name (frozen at creation) and its options; past sales keep their snapshots. `apply` runs in one
transaction under the company lock, recomputes the plan after the lock, refuses a plan that changed since the caller
read it (`expected` fingerprint) and writes one `catalog_normalized` summary. Nothing to do = nothing written, so a
second apply is a no-op. The engine names no manufacturer: sizes and roles only (AGENTS §2).
"""
from __future__ import annotations

import hashlib
import json
import re

from django.db import transaction

from . import device_catalog
from .catalog_aliases import option_alias_holding
from .catalog_constants import CAPACITY_NAME
from .catalog_reference import capacity_label
from .configuration_resolver import Vocabulary
from .models import AdminAuditLog, Company, VariantAttribute, VariantOption

APPLIED = 'catalog_normalized'
CAPACITY = VariantAttribute.ROLE_CAPACITY
COLOUR = VariantAttribute.ROLE_COLOR
OTHER = VariantAttribute.ROLE_OTHER
_SIZE = re.compile(r'^\s*(\d{1,5})\s*(gb|tb)\s*$', re.IGNORECASE)


class StalePlan(Exception):
    """The catalogue changed between the plan the caller read and the apply."""


def capacity_gb(label) -> int | None:
    """«128Gb» → 128, «1 tb» → 1024; None when the label is not a plain size."""
    match = _SIZE.match(str(label or ''))
    if not match:
        return None
    size = int(match.group(1))
    return size * 1024 if match.group(2).lower() == 'tb' else size


def _roles(attributes) -> list[dict]:
    read = Vocabulary.roles_of(attributes)
    out = []
    for role in (CAPACITY, COLOUR):
        holder = next((a for a in attributes if a.role == role), None)
        if holder is not None:
            out.append({'role': role, 'attribute_id': holder.pk, 'name': holder.name, 'status': 'held'})
            continue
        candidates = [a for a in attributes if a.role == OTHER and read.get(a.pk) == role]
        if role == CAPACITY and not candidates:
            named = [a for a in attributes if re.search(CAPACITY_NAME, a.name or '', re.IGNORECASE)]
            status = 'ambiguous' if len(named) > 1 else 'missing'
            out.append({'role': role, 'attribute_id': None, 'name': '', 'status': status})
        elif len(candidates) != 1:
            out.append({'role': role, 'attribute_id': None, 'name': '',
                        'status': 'ambiguous' if candidates else 'missing'})
        else:
            out.append({'role': role, 'attribute_id': candidates[0].pk, 'name': candidates[0].name,
                        'status': 'assign'})
    return out


def _capacities(company, attribute) -> list[dict]:
    if attribute is None:
        return []
    options = list(VariantOption.objects.filter(company=company, attribute=attribute).order_by('pk'))
    out = []
    for option in options:
        row = {'option_id': option.pk, 'current': option.label, 'current_sort_order': option.sort_order,
               'label': option.label, 'sort_order': option.sort_order, 'status': 'unchanged', 'reason': ''}
        size = capacity_gb(option.label)
        if size is None:
            row.update(status='unreadable', reason='La etiqueta no se lee como una capacidad (GB o TB).')
            out.append(row)
            continue
        label, order = capacity_label(size), size
        if (label, order) == (option.label, option.sort_order):
            out.append(row)
            continue
        if label != option.label:
            holder = next((o for o in options if o.pk != option.pk and o.label.casefold() == label.casefold()), None)
            if holder is not None or device_catalog._reads_like_a_sibling(attribute, label, exclude=option):
                taken = holder.label if holder is not None else label
                row.update(status='conflict', reason=f'Otra opción ya se llama o se lee como «{taken}».')
                out.append(row)
                continue
            alias = option_alias_holding(company, label)
            if alias is not None:
                row.update(status='conflict', reason=f'«{label}» es un alias del vocabulario de proveedores.')
                out.append(row)
                continue
        row.update(label=label, sort_order=order, status='change')
        out.append(row)
    return out


def plan(company) -> dict:
    """What `apply` would do to `company`, read now. Writes nothing."""
    attributes = list(VariantAttribute.objects.filter(company=company).order_by('pk'))
    roles = _roles(attributes)
    capacity = next((r for r in roles if r['role'] == CAPACITY), None)
    attribute = next((a for a in attributes if capacity and a.pk == capacity['attribute_id']), None)
    capacities = _capacities(company, attribute)
    changes = sum(1 for r in roles if r['status'] == 'assign') + sum(1 for c in capacities if c['status'] == 'change')
    skipped = (sum(1 for r in roles if r['status'] == 'ambiguous')
               + sum(1 for c in capacities if c['status'] in ('conflict', 'unreadable')))
    body = {'roles': roles, 'capacities': capacities, 'changes': changes, 'skipped': skipped}
    body['fingerprint'] = hashlib.sha256(json.dumps(
        {'company': company.pk, 'roles': roles, 'capacities': capacities}, sort_keys=True).encode()).hexdigest()[:16]
    return body


def apply(company, *, actor, request=None, expected=None) -> dict:
    """Apply the plan to `company`. Raises `StalePlan` when `expected` is not the plan read under the lock."""
    with transaction.atomic():
        Company.objects.select_for_update(no_key=True).get(pk=company.pk)
        body = plan(company)
        if expected and expected != body['fingerprint']:
            raise StalePlan('El catálogo cambió desde la simulación: vuelve a simular y revisa el plan.')
        for row in body['roles']:
            if row['status'] == 'assign':
                attribute = VariantAttribute.objects.get(company=company, pk=row['attribute_id'])
                device_catalog.update_attribute(attribute, role=row['role'], actor=actor, request=request)
        for row in body['capacities']:
            if row['status'] != 'change':
                continue
            option = VariantOption.objects.select_related('attribute').get(company=company, pk=row['option_id'])
            changes = {}
            if row['label'] != option.label:
                changes['label'] = row['label']
            if row['sort_order'] != option.sort_order:
                changes['sort_order'] = row['sort_order']
            device_catalog.update_option(option, actor=actor, request=request, **changes)
        if body['changes']:
            AdminAuditLog.log(
                actor=actor, action=APPLIED, target_type='company', target_id=company.pk, request=request,
                company=company,
                metadata={'company_id': company.pk, 'fingerprint': body['fingerprint'], 'changes': body['changes'],
                          'skipped': body['skipped'],
                          'roles': [r for r in body['roles'] if r['status'] != 'held'],
                          'capacities': [c for c in body['capacities'] if c['status'] != 'unchanged']})
    return body
