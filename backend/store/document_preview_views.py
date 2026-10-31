"""
DOC-UX-02 — the preview of a document, with made-up data.

    POST /api/admin/document-preview/?company=<id>      `company.manage`

    {"document": "sales_note" | "order_receipt" | "ticket80_sales_note"
               | "boleta" | "factura" | "ticket80_boleta" | "ticket80_factura"}

answers `application/pdf`, inline, `Cache-Control: no-store`: a sample drawn by
the same functions as the real papers from a fixed synthetic context
(`store.document_preview`). Every page says «MUESTRA · NO FISCAL».

WHY `company.manage`. The preview exists to design the company's papers, and
what changes them (a later `PATCH` of the document design) is `company.manage`;
a surface that shows how a change will look should not be wider than the
surface that makes it. It also shows the company's logo and header. The
capabilities a seller holds (`sales.notes.manage`, `company.view`) are for
printing REAL papers and reading the company, not for configuring it.

READ-ONLY, and tested as such: nothing is inserted, updated or deleted. The
company comes from the server's resolution of the caller (`?company=` only
selects among the companies the caller already reaches; any other answers 404
like one that does not exist), and the only input is the document kind: any
other key of the body is ignored, and a body over 2 KB is refused before it is
parsed.
"""
from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import document_preview
from .settings_views import CAP_COMPANY_MANAGE, _company_for
from .throttles import DocumentPreviewThrottle

#: The whole body is `{"document": "<kind>"}`.
MAX_BODY_BYTES = 2048


class AdminDocumentPreviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [DocumentPreviewThrottle]

    def post(self, request):
        company, error = _company_for(request, CAP_COMPANY_MANAGE)
        if error:
            return error

        declared = request.META.get('CONTENT_LENGTH') or '0'
        if not declared.isdigit() or int(declared) > MAX_BODY_BYTES:
            return Response({'detail': 'La petición es demasiado grande.'},
                            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        data = request.data if isinstance(request.data, dict) else {}
        kind = data.get('document')
        if not isinstance(kind, str) or kind not in document_preview.KINDS:
            return Response(
                {'document': ['Elige uno de: ' + ', '.join(document_preview.KINDS) + '.']},
                status=status.HTTP_400_BAD_REQUEST)

        pdf = document_preview.render_preview(kind, company=company)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{document_preview.file_name(kind)}"'
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
