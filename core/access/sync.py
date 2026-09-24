"""
`RolePermission` still carries two representations of the same fact: the deprecated `role`/
`permission` strings (written by the old Settings -> Role & Permissions screen,
`people.PermissionMatrixView`) and the real `role_fk`/`resource_permission` FKs the RBAC v2 engine
actually reads. This signal keeps the FKs populated whenever a writer only sets the strings, so that
screen (and the Django admin, and any script) doesn't need to be rewritten to know about the new
columns -- one signal, every writer covered.

There is no UserAccess <-> legacy-table sync here (there is no legacy table left: ProjectOwner/
ProjectManager were removed once UserAccess became the only source of access truth -- see
SCHEMA_PLAN.md). `Profile.role` still exists as a column (existing rows are never deleted) but is no
longer read for any access decision and nothing writes it any more either.
"""
from django.db.models.signals import pre_save
from django.dispatch import receiver

from ..models import AccessRole, ResourcePermission, RolePermission

_ROLE_CACHE = {}
_RP_CACHE = {}

# core_rolepermission.role (string) <-> AccessRole.name.
LEGACY_ROLE_NAME = {'ADMIN': 'SUPER_ADMIN', 'OWNER': 'OWNER', 'MANAGER': 'MANAGER', 'VIEWER': 'VIEWER'}


def _role(name):
    role = _ROLE_CACHE.get(name)
    if role is None:
        role = AccessRole.objects.filter(name=name).first()
        if role is not None:
            _ROLE_CACHE[name] = role
    return role


@receiver(pre_save, sender=RolePermission)
def sync_role_permission_fks(sender, instance, **kwargs):
    """Fill `role_fk`/`resource_permission` from the deprecated `role`/`permission` strings whenever
    a writer only set those (the old Settings screen, the Django admin, a script)."""
    if instance.role_fk_id is None and instance.role:
        name = LEGACY_ROLE_NAME.get(instance.role)
        if name:
            instance.role_fk = _role(name)
    if instance.resource_permission_id is None and instance.permission:
        rp = _RP_CACHE.get(instance.permission) or ResourcePermission.objects.filter(code=instance.permission).first()
        if rp:
            _RP_CACHE[instance.permission] = rp
            instance.resource_permission = rp
