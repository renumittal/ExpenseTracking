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

from django.db import models
from rest_framework import permissions

from .models import Project, ProjectManager, ProjectOwner, Role, RolePermission


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


class AdminOnly(permissions.BasePermission):
    """Only ADMIN (Profile.role == ADMIN or superuser)."""

    def has_permission(self, request, view):
        return is_admin(request.user)


# ---------------------------------------------------------------------------
# Manager Fund capabilities
#
# Each needs BOTH: the role permission from the central matrix (has_permission, below: WHAT the role may
# do) and the project relationship checked here (WHERE): SUPER_ADMIN any project; an owner only projects
# they are linked to (ProjectOwner); a manager only their own fund on projects they are assigned to
# (ProjectManager).
# ---------------------------------------------------------------------------

def owns_project(user, project):
    """SUPER_ADMIN (all projects) or an owner linked to this project."""
    if is_admin(user):
        return True
    return ProjectOwner.objects.filter(project=project, owner__user=user).exists()


def can_view_manager_fund(user):
    return has_permission(user, CAN_VIEW_MANAGER_FUND)


def can_give_manager_fund(user, project):
    """Only an owner of the project (or admin) gives a fund. A manager never records their own fund."""
    return has_permission(user, CAN_GIVE_MANAGER_FUND) and owns_project(user, project)


def can_distribute_manager_fund(user, project, manager):
    """A manager distributes only their own fund on a project they are assigned to (admin: any)."""
    if not has_permission(user, CAN_DISTRIBUTE_MANAGER_FUND):
        return False
    if is_admin(user):
        return True
    return manager.user_id == user.id and ProjectManager.objects.filter(project=project, manager=manager).exists()


def can_cancel_distribution(user, project):
    """Reversing a distribution is an owner-level action (same permission as giving a fund), checked
    against the distribution's own project."""
    return has_permission(user, CAN_GIVE_MANAGER_FUND) and owns_project(user, project)


# ---------------------------------------------------------------------------
# Server-side permission matrix (RolePermission)
# ---------------------------------------------------------------------------
# The database table is the authority. These defaults are what a role gets when it has no row
# (and what the seed migration writes), so a missing row can never grant more than the default.
# ADMIN / superuser always has every permission and cannot be locked out by editing rows.

CAN_UPLOAD_BILL = 'canUploadBill'
CAN_VIEW_BILL = 'canViewBill'
CAN_VIEW_MANAGER_FUND = 'canViewManagerFund'
CAN_GIVE_MANAGER_FUND = 'canGiveManagerFund'
CAN_DISTRIBUTE_MANAGER_FUND = 'canDistributeManagerFund'
CAN_EDIT_EXPENSE = 'canEditExpense'
CAN_DELETE_EXPENSE = 'canDeleteExpense'
CAN_MANAGE_USERS = 'canManageUsers'
CAN_MANAGE_PROJECT_MEMBERS = 'canManageProjectMembers'
CAN_MANAGE_PROJECT_SETTINGS = 'canManageProjectSettings'
CAN_RESET_USER_PASSWORD = 'canResetUserPassword'

PERMISSION_DEFAULTS = {
    CAN_UPLOAD_BILL: {'ADMIN': True, 'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    CAN_VIEW_BILL: {'ADMIN': True, 'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    # Manager Fund. WHAT a role may do is here; WHICH project/manager is checked by the helpers above.
    CAN_VIEW_MANAGER_FUND: {'ADMIN': True, 'OWNER': True, 'MANAGER': True, 'VIEWER': False},
    CAN_GIVE_MANAGER_FUND: {'ADMIN': True, 'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    CAN_DISTRIBUTE_MANAGER_FUND: {'ADMIN': True, 'OWNER': False, 'MANAGER': True, 'VIEWER': False},
    # Expense correction and people management. Same defaults as the web app's built-in matrix.
    CAN_EDIT_EXPENSE: {'ADMIN': True, 'OWNER': True, 'MANAGER': True, 'VIEWER': False},
    CAN_DELETE_EXPENSE: {'ADMIN': True, 'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    CAN_MANAGE_USERS: {'ADMIN': True, 'OWNER': False, 'MANAGER': False, 'VIEWER': False},
    CAN_MANAGE_PROJECT_MEMBERS: {'ADMIN': True, 'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    CAN_MANAGE_PROJECT_SETTINGS: {'ADMIN': True, 'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    CAN_RESET_USER_PASSWORD: {'ADMIN': True, 'OWNER': True, 'MANAGER': False, 'VIEWER': False},
}


def has_permission(user, permission):
    """True if the user's role is allowed `permission`. Unknown permission or no role -> False."""
    if not (user and user.is_authenticated) or permission not in PERMISSION_DEFAULTS:
        return False
    if is_admin(user):
        return True
    role = get_role(user)
    if role is None:
        return False
    row = RolePermission.objects.filter(role=role, permission=permission).values_list('allowed', flat=True).first()
    if row is not None:
        return row
    return PERMISSION_DEFAULTS[permission].get(role, False)


# ---------------------------------------------------------------------------
# Effective permissions for the logged-in user (sent to the frontend in /me/)
# ---------------------------------------------------------------------------

def is_project_member(user, project):
    """Admin, an owner of the project, or a manager assigned to it."""
    return owns_project(user, project) or ProjectManager.objects.filter(project=project, manager__user=user).exists()


def manages_project(user, project):
    return is_admin(user) or ProjectManager.objects.filter(project=project, manager__user=user).exists()


# WHERE each permission applies (the role permission says WHAT; membership says WHERE). Same rules the API enforces.
PROJECT_RULES = {
    CAN_VIEW_MANAGER_FUND: is_project_member,
    CAN_GIVE_MANAGER_FUND: owns_project,
    CAN_DISTRIBUTE_MANAGER_FUND: manages_project,
    CAN_UPLOAD_BILL: owns_project,
    CAN_VIEW_BILL: owns_project,
}


def effective_permissions(user):
    """
    (role_level, per_project): what the server will actually allow this user.
      role_level  {permission: bool}          from the permission matrix for the user's role
      per_project {project_id: [permission]}  role permission AND the user's membership of that project
    Server-authoritative: the API enforces the same rules; the frontend only uses this to show/hide things.
    """
    role_level = {key: has_permission(user, key) for key in PROJECT_RULES}
    if is_admin(user):
        projects = Project.objects.all()
    else:
        projects = Project.objects.filter(
            models.Q(project_owners__owner__user=user) | models.Q(project_managers__manager__user=user)).distinct()
    per_project = {
        str(project.id): [key for key, allowed in role_level.items()
                          if allowed and key in PROJECT_RULES and PROJECT_RULES[key](user, project)]
        for project in projects
    }
    return role_level, per_project
