"""
CATALOG-REFERENCE-01 · PR B — import models of the reference catalogue into ONE company
(`docs/adr-catalog-reference.md` §3 D-B-1, §4, §6).

    PREVIEW WRITES NOTHING. APPLY CREATES ONLY MODEL, OPTIONS, PALETTE AND EXCLUSIONS — HIDDEN, ONCE.

The caller chooses which reference models (`keys`; none by default). For each, `preview` says what applying
would do, as one of four states:

  * `new`            — the company has no such model: it is created hidden (`is_published_online=False`);
  * `reused`         — the company has it: by `ProductModel.reference_key`, else by its folded name (then the
                       key is written on it, nothing else). Its name, slug, category, cover, publication and its
                       variants are never touched;
  * `conflict`       — something would be guessed (a model or option that already holds ANOTHER reference, an
                       alias with that text, an attribute name taken without the role…): never applied, the
                       reason is said;
  * `pending_review` — the reference model is not `complete`: applied only when the caller names it in `approve`.

Per role (capacity, colour, SIM, region — `VariantAttribute.ROLE_*`) the company's attribute with that role is
used; a capacity attribute found by name or the only colour attribute are used as the pasted list does
(`catalog_proposal._Plan`); otherwise one is created with that role. Options are found by `reference_key`, then
by their folded label (`create_option`'s own rule), and linked; a company that renamed an option keeps its name
(D-REF-3). New colours get the neutral placeholder swatch. The model's palette gets its options (added, never
removed) and the SIM × region pairs the reference model does not have become `ModelOptionExclusion` rows.

NEVER: a `Product` (variant), `BranchStock`, `StockUnit` or movement; a price, photo or publication flag; a row of
another company. The company comes from the request context, never from the body. `apply` runs in ONE transaction
under the company lock (`catalog_proposal`'s), recomputes the plan after the lock, and keeps the request
fingerprint and `Idempotency-Key` contract of `catalog_proposal` (same helpers): a second apply changes nothing.
"""
from __future__ import annotations

import re

from django.db import transaction

from . import device_catalog, model_palette
from .catalog_aliases import model_alias_holding, option_alias_holding
from .catalog_constants import CAPACITY_NAME
from .catalog_proposal import (
    PLACEHOLDER_SWATCH, BadProposal, _norm, _unique, capacity_spellings, clean_idempotency_key, replay,
    request_fingerprint,
)
from .configuration_resolver import colour_key, phrase_key
from .models import (
    AdminAuditLog, Company, ModelOption, ModelOptionExclusion, ProductModel, ReferenceModel, VariantAttribute,
    VariantOption,
)

APPLIED = 'catalog_reference_applied'
MAX_KEYS = 100
ROLES = (VariantAttribute.ROLE_CAPACITY, VariantAttribute.ROLE_COLOR, VariantAttribute.ROLE_SIM,
         VariantAttribute.ROLE_REGION)
ROLE_NAMES = dict(VariantAttribute.ROLE_CHOICES)
_KEY = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


def clean_keys(value, *, field='keys') -> list[str]:
    """Reference keys from JSON: a list of slugs, at most `MAX_KEYS`, order kept, repeats dropped. None → []."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_KEYS:
        raise BadProposal(f'«{field}» es una lista de hasta {MAX_KEYS} claves de modelos de referencia.')
    keys = []
    for item in value:
        if not isinstance(item, str) or len(item) > 64 or not _KEY.match(item):
            raise BadProposal(f'«{field}» es una lista de claves de modelos de referencia.')
        if item not in keys:
            keys.append(item)
    return keys


def _fold(label) -> str:
    """How `device_catalog.create_option` compares two labels of one attribute (accents, case, joiners)."""
    return colour_key(phrase_key(label))


class _Plan:
    """One reading of the chosen reference models against the company. Pure: it writes nothing."""

    def __init__(self, company, keys, approve):
        self.company = company
        found = {m.key: m for m in ReferenceModel.objects.filter(key__in=keys, in_dataset=True)}
        unknown = [k for k in keys if k not in found]
        if unknown:
            raise BadProposal(f'No hay en el catálogo de referencia: {", ".join(unknown[:10])}.',
                              code='unknown_reference')
        stray = [k for k in approve if k not in found]
        if stray:
            raise BadProposal(f'Se aprueba algo que no se eligió: {", ".join(stray[:10])}.', code='unknown_reference')
        self.approve = set(approve)
        self.attributes = {role: self._attribute(role) for role in ROLES}
        # The company's options, read once, by attribute.
        self._options: dict[int, list[VariantOption]] = {}
        attribute_ids = [a['attribute'].pk for a in self.attributes.values() if a['attribute'] is not None]
        for option in VariantOption.objects.filter(company=company, attribute_id__in=attribute_ids).order_by('pk'):
            self._options.setdefault(option.attribute_id, []).append(option)
        self._linked_models = {m.reference_key: m for m in
                               ProductModel.objects.filter(company=company).exclude(reference_key='')}
        self.options: dict[str, dict] = {}       # reference_key -> what happens to that option (shared by models)
        self._new_labels: dict[tuple[str, str], str] = {}
        # Review of #371: which reference key each EXISTING option is taken for in this plan. Two keys never share one.
        self._claimed: dict[int, str] = {}
        self.items = [self._item(found[k]) for k in keys]

    # -- attributes ------------------------------------------------------------
    def _attribute(self, role):
        company = self.company
        attribute = VariantAttribute.objects.filter(company=company, role=role).first()
        if attribute is None and role == VariantAttribute.ROLE_CAPACITY:
            attribute = _unique(VariantAttribute.objects.filter(company=company, name__iregex=CAPACITY_NAME))
        if attribute is None and role == VariantAttribute.ROLE_COLOR:
            attribute = _unique(VariantAttribute.objects.filter(company=company, kind=VariantAttribute.KIND_COLOR))
        if attribute is not None:
            return {'role': role, 'status': 'reused', 'attribute': attribute, 'name': attribute.name, 'reason': ''}
        name = ROLE_NAMES[role]
        holder = VariantAttribute.objects.filter(company=company, name__iexact=name).first()
        if holder is not None:
            return {'role': role, 'status': 'conflict', 'attribute': None, 'name': name,
                    'reason': f'El atributo «{holder.name}» existe sin el rol «{name}»: asígnale el rol en '
                              'Catálogo › Modelos o cámbiale el nombre.'}
        return {'role': role, 'status': 'new', 'attribute': None, 'name': name, 'reason': ''}

    # -- options ---------------------------------------------------------------
    def _option(self, ref_option):
        reference_key = ref_option.reference_key
        if reference_key in self.options:
            return self.options[reference_key]
        role = ref_option.role
        label = ref_option.label_es[:60]
        entry = {'reference_key': reference_key, 'role': role, 'label': label, 'status': 'new',
                 'option_id': None, 'link': False, 'reason': ''}
        attribute = self.attributes[role]
        if attribute['status'] == 'conflict':
            entry.update(status='conflict', reason=attribute['reason'])
        elif attribute['attribute'] is not None:
            existing = self._options.get(attribute['attribute'].pk, [])
            match = next((o for o in existing if o.reference_key == reference_key), None)
            if match is None:
                match = self._by_label(existing, ref_option)
                if match is not None and match.reference_key:
                    entry.update(status='conflict', option_id=match.pk, label=match.label,
                                 reason=f'«{match.label}» ya representa otra opción de referencia '
                                        f'({match.reference_key}).')
            if match is not None and entry['status'] != 'conflict':
                claimed = self._claimed.setdefault(match.pk, reference_key)
                if claimed != reference_key:
                    entry.update(status='conflict', option_id=match.pk, label=match.label,
                                 reason=f'«{match.label}» sería la opción de dos referencias ({claimed} y '
                                        f'{reference_key}): renombra una de ellas o únelas a mano.')
                else:
                    entry.update(status='reused', option_id=match.pk, label=match.label,
                                 link=not match.reference_key)
        if entry['status'] == 'new':
            alias = option_alias_holding(self.company, label)
            claimed = self._new_labels.setdefault((role, _fold(label)), reference_key)
            if alias is not None:
                entry.update(status='conflict', reason=f'«{label}» es un alias de «{alias.option.label}» en el '
                                                       'vocabulario de proveedores.')
            elif claimed != reference_key:
                entry.update(status='conflict', reason=f'«{label}» sería la etiqueta de dos opciones de referencia '
                                                       f'({claimed} y {reference_key}).')
        self.options[reference_key] = entry
        return entry

    @staticmethod
    def _by_label(existing, ref_option):
        if ref_option.role == VariantAttribute.ROLE_CAPACITY and ref_option.capacity_gb:
            spellings = capacity_spellings(ref_option.capacity_gb)
            found = next((o for o in existing if _norm(o.label) in spellings), None)
            if found is not None:
                return found
        for label in dict.fromkeys((ref_option.label_es, ref_option.label_en)):
            found = next((o for o in existing if _fold(o.label) == _fold(label)), None)
            if found is not None:
                return found
        return None

    # -- one reference model -----------------------------------------------------
    def _model(self, ref):
        linked = self._linked_models.get(ref.key)
        if linked is not None:
            return {'model_status': 'reused', 'model': linked, 'link': False, 'reason': ''}
        holder = device_catalog.model_name_holder(self.company.pk, ref.name)
        if holder is not None:
            if holder.reference_key:
                return {'model_status': 'conflict', 'model': holder, 'link': False,
                        'reason': f'El modelo «{holder.name}» ya está unido a otra referencia ({holder.reference_key}).'}
            return {'model_status': 'reused', 'model': holder, 'link': True, 'reason': ''}
        alias = model_alias_holding(self.company, ref.name)
        if alias is not None:
            return {'model_status': 'conflict', 'model': None, 'link': False,
                    'reason': f'«{ref.name}» es un alias del modelo «{alias.model.name}» en el vocabulario de '
                              'proveedores: únelo a mano o borra el alias.'}
        return {'model_status': 'new', 'model': None, 'link': False, 'reason': ''}

    def _item(self, ref):
        found = self._model(ref)
        model = found['model']
        ref_options = list(ref.options.filter(in_dataset=True).order_by('role', 'sort_order', 'key'))
        entries = [self._option(o) for o in ref_options]
        reasons = [found['reason']] if found['reason'] else []
        reasons += [e['reason'] for e in entries if e['status'] == 'conflict']
        taken = [e['option_id'] for e in entries if e['option_id']]
        if len(taken) != len(set(taken)):
            reasons.append('Dos opciones de referencia de este modelo caen en la misma opción de la empresa.')
        palette, exclusions = set(), set()
        if model is not None and found['model_status'] == 'reused':
            palette = set(ModelOption.objects.filter(model=model, company=self.company)
                          .values_list('option_id', flat=True))
            exclusions = {frozenset(p) for p in ModelOptionExclusion.objects.filter(
                model=model, company=self.company).values_list('option_a_id', 'option_b_id')}
        palette_add = [e['reference_key'] for e in entries if e['option_id'] is None or e['option_id'] not in palette]
        pairs = self._impossible_pairs(ref, ref_options)
        exclusions_add = [p for p in pairs if not (all(self.options[k]['option_id'] for k in p)
                                                   and frozenset(self.options[k]['option_id'] for k in p) in exclusions)]
        attributes_new = sorted({e['role'] for e in entries if self.attributes[e['role']]['status'] == 'new'})
        needs_approval = ref.completeness != 'complete'
        if reasons:
            state = 'conflict'
        elif needs_approval:
            state = 'pending_review'
        else:
            state = found['model_status']
        changes = bool(found['model_status'] == 'new' or found['link'] or attributes_new or palette_add
                       or exclusions_add or any(e['status'] == 'new' or e['link'] for e in entries))
        return {
            'key': ref.key, 'name': ref.name, 'manufacturer': ref.manufacturer, 'family': ref.family,
            'completeness': ref.completeness, 'missing': ref.missing, 'needs_approval': needs_approval,
            'state': state, 'model_status': found['model_status'],
            'model_id': model.pk if model is not None else None, 'model_name': model.name if model is not None else '',
            'link_model': found['link'], 'reason': ' '.join(reasons),
            'warnings': self._warnings(model, found, entries),
            'attributes_new': attributes_new,
            'options': [{k: e[k] for k in ('reference_key', 'role', 'label', 'status', 'option_id', 'reason')}
                        for e in entries],
            'palette_add': palette_add, 'exclusions_add': len(exclusions_add), 'changes': changes,
            '_pairs': exclusions_add, '_ref': ref,
        }

    @staticmethod
    def _impossible_pairs(ref, ref_options):
        """(sim reference_key, region reference_key) pairs the reference model does NOT have."""
        sims = [o for o in ref_options if o.role == VariantAttribute.ROLE_SIM]
        regions = [o for o in ref_options if o.role == VariantAttribute.ROLE_REGION]
        real = {(e['key'], e['region_key']) for e in ref.sim_entries}
        return [(s.reference_key, r.reference_key) for s in sims for r in regions if (s.key, r.key) not in real]

    def _warnings(self, model, found, entries):
        """
        A reused model with variants: what the import changes for them. Nothing is rewritten, but a palette without
        the roles the import adds leaves those variants «incompleta» and asks every new variant for those roles.
        """
        if model is None or found['model_status'] != 'reused':
            return []
        count = model.variants.count()
        if not count:
            return []
        covered = set(ModelOption.objects.filter(model=model, company=self.company)
                      .values_list('option__attribute_id', flat=True))
        if not covered:
            return [f'«{model.name}» tiene {count} variante(s) y aún no tiene paleta: las que no encajen se informarán '
                    '«fuera de paleta» o «incompleta»; ninguna se modifica.']
        added = []
        for role in ROLES:
            if not any(e['role'] == role for e in entries):
                continue
            attribute = self.attributes[role]['attribute']
            if attribute is None or attribute.pk not in covered:
                added.append(ROLE_NAMES[role])
        if not added:
            return []
        roles = ', '.join(added)
        return [f'La paleta de «{model.name}» no tiene {roles}: al importar, sus {count} variante(s) quedarán '
                f'«incompleta» hasta que lleven {roles} (no se modifican) y cada variante nueva deberá llevarlos.']

    # -- answer ------------------------------------------------------------------
    def payload(self) -> dict:
        counts = {'new': 0, 'reused': 0, 'conflict': 0, 'pending_review': 0}
        for item in self.items:
            counts[item['state']] += 1
        return {
            'items': [{k: v for k, v in item.items() if not k.startswith('_')} for item in self.items],
            'attributes': [{k: a[k] for k in ('role', 'status', 'name', 'reason')} | {
                'attribute_id': a['attribute'].pk if a['attribute'] is not None else None}
                for a in self.attributes.values()],
            'counts': counts,
        }


def preview(company, keys) -> dict:
    return _Plan(company, clean_keys(keys), []).payload()


def models_list(company) -> list[dict]:
    """The reference models in the dataset, with the company's model linked to each (None when not imported)."""
    linked = dict(ProductModel.objects.filter(company=company).exclude(reference_key='')
                  .values_list('reference_key', 'pk'))
    return [{
        'key': m.key, 'name': m.name, 'manufacturer': m.manufacturer, 'family': m.family, 'line': m.line,
        'generation': m.generation, 'announced': m.announced.isoformat() if m.announced else None,
        'completeness': m.completeness, 'missing': m.missing, 'verified_at': m.verified_at.isoformat(),
        'dataset_version': m.dataset_version, 'linked_model_id': linked.get(m.key),
    } for m in ReferenceModel.objects.filter(in_dataset=True)]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _audit(action, *, company, actor, request, target_type, target_id, **metadata):
    AdminAuditLog.log(actor=actor, action=action, target_type=target_type, target_id=target_id,
                      metadata=metadata, request=request, company=company)


def apply(*, company, keys, approve=None, idempotency_key='', actor, request=None) -> dict:
    """Apply the chosen reference models to `company`. Returns the answer body; raises `BadProposal`/`CatalogError`."""
    keys = clean_keys(keys)
    approve = clean_keys(approve, field='approve')
    key = clean_idempotency_key(idempotency_key)
    with transaction.atomic():
        # One apply at a time per company (the lock of `catalog_proposal.apply`); the plan is read AFTER it.
        # NO KEY UPDATE: rows that only reference the company are not blocked (no deadlock against a model save).
        Company.objects.select_for_update(no_key=True).get(pk=company.pk)
        fingerprint = request_fingerprint({'reference_keys': sorted(keys), 'approve': sorted(approve)})
        prior = replay(company, APPLIED, key, fingerprint)
        if prior is not None:
            return prior
        plan = _Plan(company, keys, approve)
        for item in plan.items:
            if item['state'] == 'conflict':
                raise BadProposal(f'«{item["name"]}»: {item["reason"]}', code='conflict')
            if item['needs_approval'] and item['key'] not in plan.approve:
                raise BadProposal(f'«{item["name"]}» no está completo en el catálogo de referencia '
                                  f'({item["completeness"]}): apruébalo expresamente para importarlo.',
                                  code='approval_required')
        body = _execute(plan, actor=actor, request=request)
        AdminAuditLog.log(
            actor=actor, action=APPLIED, target_type='company', target_id=company.pk, request=request, company=company,
            metadata={'company_id': company.pk, 'idempotency_key': key, 'fingerprint': fingerprint,
                      'reference_keys': keys, 'approved': sorted(plan.approve),
                      'models_created': len(body['created']['models']),
                      'options_created': len(body['created']['options']), 'result': body},
        )
    return body


def _execute(plan, *, actor, request) -> dict:
    company = plan.company
    created = {'models': [], 'attributes': [], 'options': []}
    linked = {'models': 0, 'options': 0}
    attributes = {}
    for role, entry in plan.attributes.items():
        attributes[role] = entry['attribute']

    def attribute_for(role):
        if attributes[role] is None:
            attribute = device_catalog.create_attribute(
                company=company, name=plan.attributes[role]['name'], role=role,
                kind=VariantAttribute.KIND_COLOR if role == VariantAttribute.ROLE_COLOR else VariantAttribute.KIND_TEXT,
                sort_order=VariantAttribute.ROLE_ORDER.index(role), actor=actor, request=request)
            attributes[role] = attribute
            created['attributes'].append({'role': role, 'attribute_id': attribute.pk, 'name': attribute.name})
        return attributes[role]

    option_ids: dict[str, int] = {}

    def option_for(entry):
        reference_key = entry['reference_key']
        if reference_key in option_ids:
            return option_ids[reference_key]
        if entry['status'] == 'reused':
            if entry['link'] and VariantOption.objects.filter(
                    company=company, pk=entry['option_id'], reference_key='').update(reference_key=reference_key):
                linked['options'] += 1
                _audit('catalog_reference_linked', company=company, actor=actor, request=request,
                       target_type='variant_option', target_id=entry['option_id'], reference_key=reference_key)
            option_ids[reference_key] = entry['option_id']
        else:
            attribute = attribute_for(entry['role'])
            option = device_catalog.create_option(
                company=company, attribute_id=attribute.pk, label=entry['label'],
                swatch_hex=PLACEHOLDER_SWATCH if attribute.kind == VariantAttribute.KIND_COLOR else '',
                actor=actor, request=request)
            VariantOption.objects.filter(pk=option.pk).update(reference_key=reference_key)
            created['options'].append({'reference_key': reference_key, 'option_id': option.pk, 'label': option.label})
            option_ids[reference_key] = option.pk
        return option_ids[reference_key]

    palette_added = exclusions_added = 0
    items = []
    for item in plan.items:
        ref = item['_ref']
        if item['model_status'] == 'new':
            model = device_catalog.create_model(company=company, name=ref.name, is_published_online=False,
                                                actor=actor, request=request)
            ProductModel.objects.filter(pk=model.pk).update(reference_key=ref.key)
            created['models'].append({'key': ref.key, 'model_id': model.pk, 'name': model.name})
        else:
            model = ProductModel.objects.get(company=company, pk=item['model_id'])
            if item['link_model'] and ProductModel.objects.filter(
                    pk=model.pk, reference_key='').update(reference_key=ref.key):
                linked['models'] += 1
                _audit('catalog_reference_linked', company=company, actor=actor, request=request,
                       target_type='product_model', target_id=model.pk, reference_key=ref.key)
        wanted = [option_for(plan.options[o['reference_key']]) for o in item['options']]
        current = set(ModelOption.objects.filter(model=model, company=company).values_list('option_id', flat=True))
        missing = [pk for pk in wanted if pk not in current]
        if missing:
            try:
                if current:
                    palette_added += model_palette.add_to_palette(model, option_ids=missing, actor=actor,
                                                                  request=request)['added']
                else:
                    # Review of #371: `current` was read without the model's lock. `expected_option_ids` makes
                    # `set_palette` refuse (stale) a palette somebody saved in between: it only ever grows.
                    palette_added += model_palette.set_palette(model, option_ids=wanted, expected_option_ids=[],
                                                               actor=actor, request=request)['added']
            except device_catalog.CatalogError as exc:
                if exc.code != 'stale':
                    raise
                raise BadProposal(f'Otra persona cambió la paleta de «{model.name}» mientras se importaba: vuelve a '
                                  'previsualizar e inténtalo de nuevo.', code='stale', status=409) from None
        if item['_pairs']:
            pairs = [(option_ids[a], option_ids[b]) for a, b in item['_pairs']]
            exclusions_added += model_palette.add_exclusions(model, pairs=pairs, actor=actor,
                                                             request=request)['added']
        items.append({'key': ref.key, 'model_id': model.pk, 'state': item['state']})
    return {'created': created, 'linked': linked, 'palette_added': palette_added,
            'exclusions_added': exclusions_added, 'items': items, 'replayed': False}
