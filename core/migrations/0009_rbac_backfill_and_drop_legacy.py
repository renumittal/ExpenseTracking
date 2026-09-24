"""
Backfill any real ProjectOwner/ProjectManager/Profile(ADMIN) data into UserAccess, THEN drop
ProjectOwner and ProjectManager -- in that order, in this migration, so a fresh apply against an
environment that still has real rows in those tables never loses them. (This project's own dev/test
databases have never held real data in these tables, but the migration file itself has to be correct
for any environment that applies it from scratch.)

Idempotent: every write is get_or_create, so re-running (or applying to a database where 0006-0008
already ran under an earlier version of this migration set) is a no-op past the first run.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Profile = apps.get_model('core', 'Profile')
    ProjectOwner = apps.get_model('core', 'ProjectOwner')
    ProjectManager = apps.get_model('core', 'ProjectManager')
    AccessRole = apps.get_model('core', 'AccessRole')
    UserAccess = apps.get_model('core', 'UserAccess')
    User = apps.get_model(*_auth_user_model())

    owner_role = AccessRole.objects.filter(name='OWNER').first()
    manager_role = AccessRole.objects.filter(name='MANAGER').first()
    super_role = AccessRole.objects.filter(name='SUPER_ADMIN').first()
    if not (owner_role and manager_role and super_role):
        return  # 0007 hasn't seeded roles yet in some unexpected ordering -- nothing safe to do.

    admin_user_ids = set(Profile.objects.filter(role='ADMIN').values_list('user_id', flat=True))
    admin_user_ids |= set(User.objects.filter(is_superuser=True).values_list('id', flat=True))
    for user_id in admin_user_ids:
        UserAccess.objects.get_or_create(
            user_id=user_id, project=None, defaults={'role': super_role, 'scope_type': 'GLOBAL'},
        )

    for link in ProjectOwner.objects.select_related('owner').all():
        UserAccess.objects.get_or_create(
            user_id=link.owner.user_id, project_id=link.project_id,
            defaults={'role': owner_role, 'scope_type': 'PROJECT'},
        )
    for link in ProjectManager.objects.select_related('manager').all():
        UserAccess.objects.get_or_create(
            user_id=link.manager.user_id, project_id=link.project_id,
            defaults={'role': manager_role, 'scope_type': 'PROJECT'},
        )


def _auth_user_model():
    from django.conf import settings
    return settings.AUTH_USER_MODEL.split('.')


def unbackfill(apps, schema_editor):
    pass  # Reverse of DeleteModel (below) recreates empty ProjectOwner/ProjectManager tables; there
    # is nothing to restore into them (the backfill only ever copies forward, never the other way).


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_fix_manager_expense_permissions'),
    ]

    operations = [
        migrations.RunPython(backfill, reverse_code=unbackfill),
        # A plain DeleteModel (no preceding AlterUniqueTogether/RemoveField -- neither model is
        # pointed at by an FK from anywhere else) so its reverse (CreateModel) reconstructs the full
        # original field list + unique_together + index from the pre-migration state, rather than a
        # state whose fields were already stripped by earlier RemoveField operations in the same
        # migration (that ordering broke reversing all the way back to 0001).
        migrations.DeleteModel(
            name='ProjectManager',
        ),
        migrations.DeleteModel(
            name='ProjectOwner',
        ),
    ]
