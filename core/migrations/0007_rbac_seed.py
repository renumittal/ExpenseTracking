"""
Seed the RBAC v2 catalogue (Resource/Action/ResourcePermission), the built-in roles (SUPER_ADMIN/
OWNER/MANAGER/VIEWER) and their default RolePermission rows. No legacy-table backfill: there is no
`ProjectOwner`/`ProjectManager`/`Profile.role` data to migrate (RBAC v2 shipped before any production
data existed), so this is pure seeding, idempotent (safe to run twice) via get_or_create/
update_or_create throughout. The same logic is also available as `python manage.py seed_rbac` for
catching up the catalogue later without a new migration.
"""
from django.db import migrations

from core.access_catalog import PERMISSIONS, RESET_DEFAULTS

ROLE_DESCRIPTIONS = {
    'SUPER_ADMIN': ('Full access to every project and every setting.', True),
    'OWNER': ('Runs a project day to day: expenses, funds, members.', False),
    'MANAGER': ('Records expenses and labour payments on assigned projects.', False),
    'VIEWER': ('Read-only access: can see but not change anything.', False),
}

# The pre-RBAC-v2 migration 0005 already seeded a couple of RolePermission rows (canUploadBill/
# canViewBill for ADMIN/OWNER/MANAGER/VIEWER) using historical models, so no signal ran to fill their
# role_fk/resource_permission -- those rows exist with the *string* columns set and the FKs null.
# core_rolepermission.role (string) <-> AccessRole.name, for backfilling exactly those leftovers.
LEGACY_ROLE_NAME = {'ADMIN': 'SUPER_ADMIN', 'OWNER': 'OWNER', 'MANAGER': 'MANAGER', 'VIEWER': 'VIEWER'}


def seed(apps, schema_editor):
    Resource = apps.get_model('core', 'Resource')
    Action = apps.get_model('core', 'Action')
    ResourcePermission = apps.get_model('core', 'ResourcePermission')
    AccessRole = apps.get_model('core', 'AccessRole')
    RolePermission = apps.get_model('core', 'RolePermission')

    resources, actions, rps = {}, {}, {}
    for order, (code, (resource, action, group, label, description)) in enumerate(PERMISSIONS.items()):
        if resource not in resources:
            resources[resource] = Resource.objects.get_or_create(code=resource, defaults={'label': resource.title()})[0]
        if action not in actions:
            actions[action] = Action.objects.get_or_create(code=action, defaults={'label': action.title()})[0]
        rp, _ = ResourcePermission.objects.update_or_create(
            code=code,
            defaults={
                'resource': resources[resource], 'action': actions[action], 'group': group,
                'label': label, 'description': description, 'sort_order': order,
            },
        )
        rps[code] = rp

    roles = {}
    for name, (description, is_superadmin) in ROLE_DESCRIPTIONS.items():
        role, _ = AccessRole.objects.get_or_create(
            name=name, defaults={'description': description, 'is_superadmin': is_superadmin, 'is_system': True},
        )
        roles[name] = role

    for role_name in ('OWNER', 'MANAGER', 'VIEWER'):
        role = roles[role_name]
        for code, rp in rps.items():
            default = RESET_DEFAULTS.get(code, {}).get(role_name, False)
            # Keyed by the *string* columns (the pre-existing unique index) so this lands on the same
            # row 0005 already created for canUploadBill/canViewBill, instead of colliding with it.
            RolePermission.objects.update_or_create(
                role=role_name, permission=code,
                defaults={'allowed': default, 'role_fk': role, 'resource_permission': rp},
            )

    # Backfill any row still missing its FKs (0005's ADMIN-role rows, created before role_fk/
    # resource_permission existed) so 0008 can safely make them NOT NULL.
    for row in RolePermission.objects.filter(role_fk__isnull=True):
        name = LEGACY_ROLE_NAME.get(row.role)
        rp = rps.get(row.permission)
        role = roles.get(name) if name else None
        if role and rp:
            row.role_fk = role
            row.resource_permission = rp
            row.save(update_fields=['role_fk', 'resource_permission'])


def unseed(apps, schema_editor):
    apps.get_model('core', 'RolePermission').objects.filter(role_fk__isnull=False).delete()
    apps.get_model('core', 'ResourcePermission').objects.all().delete()
    apps.get_model('core', 'AccessRole').objects.all().delete()
    apps.get_model('core', 'Resource').objects.all().delete()
    apps.get_model('core', 'Action').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_rbac_schema'),
    ]

    operations = [
        migrations.RunPython(seed, reverse_code=unseed),
    ]
