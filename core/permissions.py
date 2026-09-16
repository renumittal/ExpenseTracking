"""
Role-based access control for the expense tracker API.

Design choice: custom DRF permission classes keyed off `Profile.role`,
instead of Django's built-in Group/Permission system. The three roles here
(ADMIN / OWNER / MANAGER) aren't simple "can add/change/delete this model"
grants -- they're row-level rules ("owners only see projects they're linked
to via ProjectOwner", "managers only see their own ManagerFund rows"). The
Group/Permission system only answers yes/no at the model level, so we would
still need hand-written queryset filtering underneath it; layering Groups on
top would just add a second place (group membership) that has to stay in
sync with the first (Profile.role). A single custom permission class per
view, paired with role-aware `get_queryset()` filtering, is the simpler
thing to keep correct and is the one source of truth for both "can this user
hit this endpoint" and "which rows can they see".
"""

from rest_framework import permissions

from .models import Role


def get_role(user):
    """Return the user's Role, or None if they have no Profile."""
    profile = getattr(user, 'profile', None)
    return profile.role if profile else None


def is_admin(user):
    return bool(user and user.is_authenticated and (user.is_superuser or get_role(user) == Role.ADMIN))


def is_owner(user):
    return bool(user and user.is_authenticated and get_role(user) == Role.OWNER)


def is_manager(user):
    return bool(user and user.is_authenticated and get_role(user) == Role.MANAGER)


class RoleAllowed(permissions.BasePermission):
    """
    Generic per-view role gate.

    A view sets `allowed_roles = {Role.OWNER, ...}` as a class attribute.
    ADMIN (Profile.role == ADMIN, or is_superuser) is always allowed,
    regardless of `allowed_roles`, since admins have full access everywhere.
    Everyone else must have a Profile whose role is in `allowed_roles`.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if is_admin(request.user):
            return True
        allowed_roles = getattr(view, 'allowed_roles', frozenset())
        return get_role(request.user) in allowed_roles
