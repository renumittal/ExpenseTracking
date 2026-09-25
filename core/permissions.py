"""
Role-based access control for the expense tracker API.

RBAC v2: `UserAccess` (role template x per-user/per-project assignment x overrides, see
core/access/services.py) is the *only* source of access truth. There is no `Profile.role` or
`ProjectOwner`/`ProjectManager` membership table any more -- "is this user an owner of this
project" is answered by `services.users_with_role(project, 'OWNER')`, and "what can this role do"
by `services.has_perm(user, code, project)`. This module stays as the DRF-facing layer (permission
classes, the small helpers views.py/people.py call) so call sites don't each import `access.services`
directly for every check, but it holds no state and no decision logic of its own any more.
"""

from rest_framework import permissions

from .access import services
from .models import Project, Role, UserAccess


def is_admin(user):
    """Super-admin: Django is_superuser, or a UserAccess row whose AccessRole.is_superadmin is set."""
    return bool(user and user.is_authenticated and services.is_superadmin(user))


class RoleAllowed(permissions.BasePermission):
    """
    Generic per-view role gate: a coarse "does this user hold this role anywhere" pre-check.

    A view sets `allowed_roles = {Role.OWNER, ...}` as a class attribute. SUPER_ADMIN is always
    allowed. Everyone else must hold at least one UserAccess grant whose role name is in
    `allowed_roles` (any project -- the real per-project decision is made inside the view/serializer
    via `services.has_perm`/`services.users_with_role`, same "WHAT here, WHERE there" split as before).
    """

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if is_admin(user):
            return True
        allowed_roles = {r for r in getattr(view, 'allowed_roles', frozenset()) if r != Role.ADMIN}
        if not allowed_roles:
            return False
        return UserAccess.objects.filter(user=user, role__name__in=allowed_roles).exists()


class AdminOnly(permissions.BasePermission):
    """Only the super admin."""

    def has_permission(self, request, view):
        return is_admin(request.user)


# ---------------------------------------------------------------------------
# Manager Fund capabilities
#
# Each needs BOTH: the role permission from the central matrix (has_permission, below: WHAT the role may
# do) and the project relationship checked here (WHERE): SUPER_ADMIN any project; an owner only projects
# they hold an OWNER grant on; a manager only their own fund on projects they hold a MANAGER grant on.
# ---------------------------------------------------------------------------

def owns_project(user, project):
    """SUPER_ADMIN (all projects), or a real OWNER-role assignment on this project."""
    if is_admin(user):
        return True
    return services.users_with_role(project, 'OWNER').filter(pk=user.id).exists()


def can_view_manager_fund(user):
    return has_permission(user, CAN_VIEW_MANAGER_FUND)


def can_give_manager_fund(user, project):
    """Only an owner of the project (or admin) gives a fund. A manager never records their own fund.
    Project-aware: respects a per-project override on CAN_GIVE_MANAGER_FUND."""
    return services.has_perm(user, CAN_GIVE_MANAGER_FUND, project) and owns_project(user, project)


def can_distribute_manager_fund(user, project, manager):
    """A manager distributes only their own fund on a project they are assigned to (admin: any).
    Project-aware: respects a per-project override on CAN_DISTRIBUTE_MANAGER_FUND."""
    if not services.has_perm(user, CAN_DISTRIBUTE_MANAGER_FUND, project):
        return False
    if is_admin(user):
        return True
    return manager.user_id == user.id and services.users_with_role(project, 'MANAGER').filter(pk=user.id).exists()


def can_view_project_funds(user, project):
    """Owner-level: may look at ANOTHER manager's fund position on this project (Manager Dashboard's
    manager_id query param), not just their own. Same shape as can_give_manager_fund."""
    return services.has_perm(user, CAN_VIEW_PROJECT_FUNDS, project) and owns_project(user, project)


def can_cancel_distribution(user, project):
    """Reversing a distribution is an owner-level action (same permission as giving a fund), checked
    against the distribution's own project."""
    return services.has_perm(user, CAN_GIVE_MANAGER_FUND, project) and owns_project(user, project)


# ---------------------------------------------------------------------------
# Server-side permission codes (kept as plain strings -- see core/access_catalog.py for the full
# catalogue with resource/action/description/dependencies; these constants are just the names call
# sites in views.py/people.py/reports.py import instead of typing the string each time).
# ---------------------------------------------------------------------------

CAN_UPLOAD_BILL = 'canUploadBill'
CAN_VIEW_BILL = 'canViewBill'
CAN_VIEW_MANAGER_FUND = 'canViewManagerFund'
CAN_GIVE_MANAGER_FUND = 'canGiveManagerFund'
CAN_DISTRIBUTE_MANAGER_FUND = 'canDistributeManagerFund'
CAN_VIEW_PROJECT_FUNDS = 'canViewProjectFunds'
CAN_VIEW_PROJECTS = 'canViewProjects'
CAN_VIEW_EXPENSES = 'canViewExpenses'
CAN_VIEW_REPORTS = 'canViewReports'
CAN_VIEW_LABOUR = 'canViewLabour'
CAN_VIEW_SUPPLIERS = 'canViewSuppliers'
CAN_VIEW_CONTRACTORS = 'canViewContractors'
CAN_MANAGE_LABOUR = 'canManageLabour'
CAN_MANAGE_SUPPLIERS = 'canManageSuppliers'
CAN_MANAGE_CONTRACTORS = 'canManageContractors'
CAN_ADD_SUPPLIER_EXPENSE = 'canAddSupplierExpense'
CAN_ADD_CONTRACTOR_EXPENSE = 'canAddContractorExpense'
CAN_ADD_MISC_EXPENSE = 'canAddMiscExpense'
CAN_EDIT_EXPENSE = 'canEditExpense'
CAN_DELETE_EXPENSE = 'canDeleteExpense'
CAN_RECORD_LABOUR_PAYMENT = 'canRecordLabourPayment'
CAN_MANAGE_USERS = 'canManageUsers'
CAN_MANAGE_PROJECT_MEMBERS = 'canManageProjectMembers'
CAN_MANAGE_PROJECT_SETTINGS = 'canManageProjectSettings'
CAN_RESET_USER_PASSWORD = 'canResetUserPassword'


def has_permission(user, permission):
    """
    True if `user`'s role ever grants `permission`, on any project (WHAT they may do; WHICH project
    is a separate check -- see PROJECT_RULES/effective_permissions below). Delegates to
    access.services.has_perm_any_scope, the RBAC v2 engine.
    """
    return services.has_perm_any_scope(user, permission)


# ---------------------------------------------------------------------------
# Effective permissions for the logged-in user (sent to the frontend in /me/)
# ---------------------------------------------------------------------------

def is_project_member(user, project):
    """Admin, a real OWNER-role assignment, or a real MANAGER-role assignment on this project."""
    return owns_project(user, project) or services.users_with_role(project, 'MANAGER').filter(pk=user.id).exists()


def manages_project(user, project):
    return is_admin(user) or services.users_with_role(project, 'MANAGER').filter(pk=user.id).exists()


# WHERE each permission applies (the role permission says WHAT; membership says WHERE). Same rules the API enforces.
PROJECT_RULES = {
    CAN_VIEW_MANAGER_FUND: is_project_member,
    CAN_GIVE_MANAGER_FUND: owns_project,
    CAN_DISTRIBUTE_MANAGER_FUND: manages_project,
    CAN_UPLOAD_BILL: owns_project,
    CAN_VIEW_BILL: owns_project,
    CAN_VIEW_PROJECT_FUNDS: owns_project,
}


def effective_permissions(user):
    """
    (role_level, per_project): what the server will actually allow this user.
      role_level  {permission: bool}          whether the user's role(s) ever grant this, on any project
      per_project {project_id: [permission]}  the RBAC v2 engine's actual per-project decision
                                               (role template + per-project overrides), via
                                               access.services.has_perm -- this is what respects a
                                               DENY/ALLOW override scoped to one specific project.
    Server-authoritative: the API enforces the same rules; the frontend only uses this to show/hide things.
    """
    role_level = {key: has_permission(user, key) for key in PROJECT_RULES}
    projects = Project.objects.all() if is_admin(user) else services.accessible_projects(user)
    per_project = {
        str(project.id): [key for key in PROJECT_RULES if services.has_perm(user, key, project)]
        for project in projects
    }
    return role_level, per_project
