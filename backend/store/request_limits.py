"""
BODY-LIMIT: how large a request body may be, per route.

Three layers refuse a body that is too large, each for what it can know:

    deploy/Caddyfile   the same table, so an absurd body never crosses the proxy
    this middleware    the declared length, before Django reads a byte
    the view           the rule it can explain ("la imagen pesa más de 8 MB")

Django has no limit of its own that covers this API. `DATA_UPLOAD_MAX_MEMORY_SIZE`
guards `request.body` and form fields; a DRF JSON body is read straight from the
stream, and a multipart upload is written to a temporary file of any size — and
every endpoint accepts multipart, the sign-in included.

A route that is not listed gets `default_max_bytes()`. A new upload endpoint needs
a tier here AND in the proxy file; `store.test_request_limits` fails until both say
the same.

A body with no declared length (chunked) is not a way round: Django gives the view
a stream limited to the declared length, which is then zero.
"""
import re
from dataclasses import dataclass
from functools import lru_cache

from django.http import JsonResponse

KB = 1024
MB = 1024 * KB

#: What an ordinary JSON request may weigh. The largest ones this API receives —
#: a sale with all its lines, the storefront content — are a few kilobytes.
DEFAULT_MAX_BYTES = 1 * MB

#: A gateway notification is a few kilobytes. The payment notifications take no
#: session and no throttle, so their size is their own limit (WEBHOOK-BODY).
PAYMENT_NOTIFICATION_BYTES = 128 * KB

#: Meta batches delivery reports; its documented ceiling for one call is 3 MB.
WHATSAPP_WEBHOOK_BYTES = 3 * MB

#: Multipart boundaries, part headers and the text fields that travel with a file.
MULTIPART_SLACK = 1 * MB


@dataclass(frozen=True)
class Tier:
    name: str
    #: Anchored regular expression over the request path. Written so that Caddy
    #: (RE2) and Python read it the same way; the trailing slash is optional
    #: because the proxy matches before it adds one.
    pattern: str
    max_bytes: int
    #: The view reads the body before anybody has proved who they are (a call
    #: from a gateway). The proxy must then read it whole first: see SLOW-BODY
    #: in deploy/Caddyfile. Uploads are the opposite case — large, and not read
    #: until the session, the CSRF header and the permission have been checked.
    read_without_session: bool = False


def tiers() -> tuple:
    """The routes that take more — or less — than the default, in proxy order."""
    from . import evidence_images, import_media, storefront_media, xlsx_reader

    image = storefront_media.max_upload_bytes() + MULTIPART_SLACK
    evidence = evidence_images.max_upload_bytes() + MULTIPART_SLACK
    workbook = xlsx_reader.MAX_UPLOAD_BYTES + MULTIPART_SLACK
    # A product import carries the workbook and its images in ONE request.
    bulk = xlsx_reader.MAX_UPLOAD_BYTES + import_media.max_total_bytes() + MULTIPART_SLACK
    return (
        Tier('payment_notification',
             r'^/api/payments/(izipay|micuentaweb)/notification/?$',
             PAYMENT_NOTIFICATION_BYTES, read_without_session=True),
        Tier('whatsapp_webhook',
             r'^/api/v1/webhooks/whatsapp/[^/]+/?$',
             WHATSAPP_WEBHOOK_BYTES, read_without_session=True),
        Tier('image',
             r'^/api/admin/(storefront/images|products/[0-9]+/images)/?$',
             image),
        Tier('evidence',
             r'^/api/v1/internal/[^/]+/service/orders/[0-9]+/evidence/?$',
             evidence),
        Tier('workbook',
             r'^/api/admin/(imports/inspect|inventory/import/preview|inventory/units/import/preview)/?$',
             workbook),
        Tier('product_import',
             r'^/api/admin/products/import/preview/?$',
             bulk),
    )


def default_max_bytes() -> int:
    return DEFAULT_MAX_BYTES


@lru_cache(maxsize=None)
def _compiled(pattern: str):
    return re.compile(pattern)


def limit_for(path: str) -> int:
    """The largest body, in bytes, that a request to `path` may declare."""
    for tier in tiers():
        if _compiled(tier.pattern).match(path):
            return tier.max_bytes
    return default_max_bytes()


class RequestBodyLimitMiddleware:
    """Refuse a request on its DECLARED length, before anything reads the body."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        declared = request.META.get('CONTENT_LENGTH')
        if declared in (None, ''):
            return self.get_response(request)
        try:
            length = int(declared)
        except (TypeError, ValueError):
            length = -1
        if length < 0:
            return JsonResponse({'detail': 'Petición no válida.'}, status=400)
        if length > limit_for(request.path):
            return JsonResponse({'detail': 'La petición es demasiado grande.'}, status=413)
        return self.get_response(request)
