"""
WHAT A CALLER MAY SEE, AND WHAT THEY ASKED TO SEE (ADR §6).

Everything starts from `tenancy.visible_orders` — the one boundary of every
internal order surface — and from the branches the caller operates. A filter
can only NARROW that set: a branch the caller may not see answers with nothing,
exactly as the sales dashboard does, and never widens anything.

Orders that no branch answers for (history before branches) are seen only by a
company-wide caller, as `visible_orders` already decides (decision D1).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from store.models import Membership, RepairOrder
from store.tenancy import has_company_wide_scope, visible_branches, visible_orders

CHANNELS = ('online', 'pos', 'service')
KINDS = ('sale', 'repair')
OPERATIONS = ('pagada', 'anulada', 'devuelta', 'reembolsada', 'servicio')
DOCUMENTS = ('factura', 'boleta', 'nc', 'nd', 'nota_interna', 'sin_documento', 'anulado', 'pendiente',
             'rechazado')
ENVIRONMENTS = ('production', 'beta')
DEFAULT_PAGE, MAX_PAGE = 100, 500


class FilterError(ValueError):
    """A filter value the API does not know. A person reads the message."""


@dataclass
class Scope:
    company: object
    company_wide: bool
    branches: list                      # the branches this answer covers
    branch_ids: list | None             # None: every order, unbranched history included
    orders: object                      # queryset
    repairs: object                     # queryset

    def payload(self) -> dict:
        return {
            'company_wide': self.company_wide,
            'branches': [{'id': b.pk, 'name': b.name} for b in self.branches],
            'includes_unbranched': self.branch_ids is None,
        }


@dataclass
class Filters:
    channels: frozenset = field(default_factory=frozenset)
    kinds: frozenset = field(default_factory=frozenset)
    operations: frozenset = field(default_factory=frozenset)
    documents: frozenset = field(default_factory=frozenset)
    seller_id: int | None = None
    environment: str = 'production'
    offset: int = 0
    page_size: int = DEFAULT_PAGE

    def payload(self) -> dict:
        return {
            'channel': sorted(self.channels), 'kind': sorted(self.kinds),
            'operation': sorted(self.operations), 'doc': sorted(self.documents),
            'seller': self.seller_id, 'environment': self.environment,
        }


def _int(raw, name):
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise FilterError(f'«{name}» debe ser un número.') from None


def _choices(params, name, allowed) -> frozenset:
    raw = (params.get(name) or '').strip()
    if not raw:
        return frozenset()
    values = frozenset(v.strip() for v in raw.split(',') if v.strip())
    unknown = sorted(values - set(allowed))
    if unknown:
        raise FilterError(f'«{name}» no admite {", ".join(unknown)}. Valores: {", ".join(allowed)}.')
    return values


def resolve_scope(user, company, params) -> Scope:
    allowed = list(visible_branches(user, company))
    company_wide = has_company_wide_scope(user, company)
    raw = params.get('branch')
    if raw not in (None, '', 'all'):
        branch_id = _int(raw, 'branch')
        branches = [b for b in allowed if b.pk == branch_id]
        branch_ids = [b.pk for b in branches]
    elif company_wide:
        branches, branch_ids = allowed, None
    else:
        branches = allowed
        branch_ids = [b.pk for b in allowed]

    orders = visible_orders(user, company)
    repairs = RepairOrder.objects.filter(company=company)
    if branch_ids is not None:
        orders = orders.filter(fulfillment_branch_id__in=branch_ids)
        repairs = repairs.filter(branch_id__in=branch_ids)
    return Scope(company=company, company_wide=company_wide, branches=branches, branch_ids=branch_ids,
                 orders=orders, repairs=repairs)


def decode_cursor(raw) -> int:
    import base64

    if not raw:
        return 0
    try:
        text = base64.urlsafe_b64decode(raw.encode() + b'=' * (-len(raw) % 4)).decode()
        prefix, value = text.split(':', 1)
        offset = int(value)
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        raise FilterError('El cursor no es válido: vuelve a la primera página.') from None
    if prefix != 'o' or offset < 0:
        raise FilterError('El cursor no es válido: vuelve a la primera página.')
    return offset


def encode_cursor(offset: int) -> str:
    import base64

    return base64.urlsafe_b64encode(f'o:{offset}'.encode()).decode().rstrip('=')


def resolve_filters(params, company) -> Filters:
    seller = params.get('seller')
    seller_id = None
    if seller not in (None, ''):
        seller_id = _int(seller, 'seller')
        if not Membership.objects.filter(company=company, user_id=seller_id).exists():
            raise FilterError('«seller» no es una persona de esta empresa.')
    environment = (params.get('environment') or 'production').strip()
    if environment not in ENVIRONMENTS:
        raise FilterError('«environment» debe ser production o beta.')
    page_size = DEFAULT_PAGE
    if params.get('page_size') not in (None, ''):
        page_size = _int(params.get('page_size'), 'page_size')
        if not 1 <= page_size <= MAX_PAGE:
            raise FilterError(f'«page_size» va de 1 a {MAX_PAGE}.')
    return Filters(
        channels=_choices(params, 'channel', CHANNELS),
        kinds=_choices(params, 'kind', KINDS),
        operations=_choices(params, 'operation', OPERATIONS),
        documents=_choices(params, 'doc', DOCUMENTS),
        seller_id=seller_id, environment=environment,
        offset=decode_cursor(params.get('cursor')), page_size=page_size,
    )


def page(rows: list, filters: Filters) -> tuple[list, str | None]:
    """One page of an already ordered list, and the cursor of the next one."""
    chunk = rows[filters.offset:filters.offset + filters.page_size]
    following = filters.offset + filters.page_size
    return chunk, (encode_cursor(following) if following < len(rows) else None)
