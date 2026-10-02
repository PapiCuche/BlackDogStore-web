from django.test import SimpleTestCase

from backend.urls import build_urlpatterns


class ProductionUrlSurfaceTest(SimpleTestCase):
    """SEC-SET-02 — Django Admin is a development-only surface."""

    @staticmethod
    def routes(*, debug):
        return [str(pattern.pattern) for pattern in build_urlpatterns(debug=debug)]

    def test_production_does_not_register_django_admin(self):
        routes = self.routes(debug=False)
        self.assertNotIn('admin/', routes)

    def test_development_keeps_django_admin_available(self):
        routes = self.routes(debug=True)
        self.assertIn('admin/', routes)

    def test_api_surfaces_exist_in_both_environments(self):
        for debug in (False, True):
            with self.subTest(debug=debug):
                routes = self.routes(debug=debug)
                self.assertIn('api/', routes)
                self.assertIn('api/v1/', routes)
