"""
What the storefront asks, without a session, to know what it may measure with.

    GET /api/measurement/config/

It answers with the PUBLIC identifier of each measurement provider a master has
activated — a measurement ID, a pixel ID: what a `<script>` tag would show
anyway — and who sends the purchase. It is the reason changing an ID in the
console changes the next page view with no rebuild: nothing about a provider is
compiled into the frontend.

It never carries a secret, a test code or a mode, and it does not know who is
asking: whether a visitor is measured at all is decided by their consent, in
their browser.

(«Measurement», not «tracking»: in this codebase tracking is following a repair
order — `tracking_services`, `/api/v1/tracking/<token>/`.)
"""
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from . import measurement


class MeasurementConfigView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]
    throttle_classes: list = []
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        response = Response({'providers': measurement.public_config()})
        # Short, and shared: it is the same for every visitor, and a change in the
        # console should reach the shop within a minute, not at the next deploy.
        response['Cache-Control'] = 'public, max-age=60'
        return response
