"""
Deploy-safety system check: RBAC v2 is fail-closed by design (see core/access/services.py -- no
assignment, no RolePermission row, no access), which means an *empty* catalogue or role template is
silently indistinguishable from "everyone is locked out" rather than an error. This check turns that
silence into a loud one at `manage.py check` / `migrate` / `runserver` time.

Registered in CoreConfig.ready() (core/apps.py). Deliberately swallows any database error: this must
never be what breaks `makemigrations`, `shell`, or a first-ever `migrate` before the RBAC tables exist.
"""
from django.core.checks import Error, register


@register()
def check_rbac_catalogue_not_empty(app_configs, **kwargs):
    from django.db import connection

    from .models import ResourcePermission, RolePermission

    errors = []
    try:
        existing_tables = connection.introspection.table_names()
        if 'core_resourcepermission' not in existing_tables or 'core_rolepermission' not in existing_tables:
            return errors  # migrations not applied yet (e.g. a fresh DB before the first `migrate`)

        if not ResourcePermission.objects.exists():
            errors.append(Error(
                'The RBAC permission catalogue is empty (no ResourcePermission rows).',
                hint='Run `python manage.py seed_rbac` (after `python manage.py migrate`).',
                id='core.E001',
            ))
        if not RolePermission.objects.filter(resource_permission__isnull=False, allowed=True).exists():
            errors.append(Error(
                'No RolePermission row grants anything -- every non-super-admin permission check '
                'will fail closed (deny) for every user.',
                hint='Run `python manage.py seed_rbac` and confirm migration 0007_rbac_backfill has '
                     'applied, or `python manage.py verify_rbac_migration`.',
                id='core.E002',
            ))
    except Exception:
        # Advisory only: a DB that can't be reached yet (no connection configured, container still
        # starting) must never be what fails `makemigrations`/`shell`/a health check.
        return []
    return errors
