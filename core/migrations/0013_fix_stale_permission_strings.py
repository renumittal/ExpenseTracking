"""
Corrective data migration: 0012 renamed the ResourcePermission row `canAddExpense` -> `canAddMiscExpense`
in place (same row id, same FK), but never touched the deprecated `RolePermission.permission` string
column on rows that already pointed at that row via `resource_permission_id` -- those rows were left
with the stale string `canAddExpense` even though their FK (and therefore the real RBAC v2 permission
they grant) is `canAddMiscExpense`.

That drift breaks `people.PermissionMatrixView.put()` (the Settings -> Role & Permissions screen),
which still looks rows up by the deprecated `role`/`permission` strings: saving the matrix for a role
whose `canAddMiscExpense` row is stale looks up `permission='canAddMiscExpense'`, finds nothing, and
tries to create a new row -- which then collides with the existing row's `(role_fk, resource_permission)`
unique constraint (both end up pointing at the same role + the same renamed ResourcePermission), raising
an uncaught IntegrityError (500) instead of saving.

Fix: for every RolePermission row, if its `permission` string disagrees with the `code` of the
ResourcePermission its own FK already points at, resync the string to match the FK -- the FK is the
source of truth. Idempotent: plain filter/update, safe to run any number of times.
"""
from django.db import migrations


def fix_stale_permission_strings(apps, schema_editor):
    RolePermission = apps.get_model('core', 'RolePermission')
    for row in RolePermission.objects.select_related('resource_permission').exclude(resource_permission__isnull=True):
        code = row.resource_permission.code
        if row.permission != code:
            RolePermission.objects.filter(pk=row.pk).update(permission=code)


def unfix(apps, schema_editor):
    pass  # Not reversible: the stale strings being corrected are simply wrong, not a prior valid state.


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_split_add_expense_by_category'),
    ]

    operations = [
        migrations.RunPython(fix_stale_permission_strings, reverse_code=unfix),
    ]
