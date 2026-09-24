from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from .access import sync  # noqa: F401  (registers the RolePermission FK-backfill signal)
        from . import checks  # noqa: F401  (registers the "RBAC catalogue not empty" deploy-safety check)
