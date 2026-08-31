from django.apps import AppConfig


class StoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'store'

    def ready(self):
        import store.checks  # noqa: F401  — registers the deployment checks
        import store.signals  # noqa: F401
