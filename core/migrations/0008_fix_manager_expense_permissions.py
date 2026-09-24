"""
Corrective data migration: an environment that already ran 0007_rbac_seed before
access_catalog.RESET_DEFAULTS was fixed would have MANAGER granted `canEditExpense` without
`canViewExpenses`/`canViewProjects` -- a real dependency violation (access_catalog.DEPENDENCIES says
EDIT on a resource requires VIEW on it, and every non-PROJECT permission requires PROJECT.VIEW), and
in practice it meant a manager's PATCH to edit an expense 404'd, because the queryset that looks the
object up filters on `canViewExpenses` first. Fresh applies of 0007 already seed the corrected
defaults; this migration only matters for a database where the old values already landed. Idempotent:
plain `filter().update()`, safe to run any number of times.
"""
from django.db import migrations


def fix_manager_permissions(apps, schema_editor):
    RolePermission = apps.get_model('core', 'RolePermission')
    if RolePermission.objects.filter(role='MANAGER', permission='canEditExpense', allowed=True).exists():
        RolePermission.objects.filter(
            role='MANAGER', permission__in=('canViewExpenses', 'canViewProjects'),
        ).update(allowed=True)


def unfix(apps, schema_editor):
    pass  # Not reversible: we don't know whether MANAGER's canViewExpenses/canViewProjects were
    # already True independently of this fix -- reversing would risk turning off something else set it.


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_rbac_seed'),
    ]

    operations = [
        migrations.RunPython(fix_manager_permissions, reverse_code=unfix),
    ]
