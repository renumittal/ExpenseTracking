"""
RBAC v2 -- the single source of truth for every access decision.

    has_perm(user, code, project=None) -> bool
    accessible_projects(user) -> Project queryset
    effective_matrix(user) -> {scope_key: {code: {'allowed': bool, 'source': 'role'|'allow'|'deny'|None}}}

Resolution order (see SCHEMA_PLAN.md / the task spec):
    1. SUPER_ADMIN (AccessRole.is_superadmin, or Django is_superuser) -> True, always.
    2. `canChangeOwnPassword` (access_catalog.ANY_USER_CODES) -> True for anyone logged in.
    3. Assignments = this user's GLOBAL UserAccess rows + PROJECT rows for `project`. None -> False.
    4. Any DENY override on those assignments for `code` -> False. Deny always wins.
    5. Any ALLOW override for `code`, or `code` in any assigned role's RolePermission(allowed=True) -> True.
    6. Else False.

Everything is loaded in 1-2 queries per user and cached on the user object for the lifetime of the
request (Django/DRF give each request its own `request.user` instance, so this is naturally
request-scoped); `invalidate(user)` drops the cache, called by every view that changes access data.
"""
from django.contrib.auth import get_user_model

from ..access_catalog import ANY_USER_CODES, DEPENDENCIES, PERMISSIONS
from ..models import AccessOverride, OverrideEffect, Project, ScopeType, UserAccess

_CACHE_ATTR = '_rbac_v2_cache'


def invalidate(user):
    """Drop the cached assignments for `user` (call after any UserAccess/AccessOverride/RolePermission write)."""
    if user is not None:
        try:
            delattr(user, _CACHE_ATTR)
        except AttributeError:
            pass


def _load(user):
    cached = getattr(user, _CACHE_ATTR, None)
    if cached is not None:
        return cached
    assignments = list(
        UserAccess.objects.filter(user=user)
        .select_related('role')
        .prefetch_related('role__role_permissions__resource_permission', 'overrides__resource_permission')
    )
    is_super = bool(getattr(user, 'is_superuser', False)) or any(a.role.is_superadmin for a in assignments)
    data = {'super': is_super, 'assignments': assignments}
    setattr(user, _CACHE_ATTR, data)
    return data


def _project_id(project):
    return getattr(project, 'id', project)


def _matching_assignments(assignments, project_id):
    return [
        a for a in assignments
        if a.scope_type == ScopeType.GLOBAL or (project_id is not None and a.project_id == project_id)
    ]


def _resolve(assignments, code):
    """(allowed, source) for `code` across a set of assignments. Deny wins over everything."""
    for a in assignments:
        for o in a.overrides.all():
            if o.resource_permission.code == code and o.effect == OverrideEffect.DENY:
                return False, 'deny'
    for a in assignments:
        for o in a.overrides.all():
            if o.resource_permission.code == code and o.effect == OverrideEffect.ALLOW:
                return True, 'allow'
        for rp in a.role.role_permissions.all():
            if rp.resource_permission_id and rp.resource_permission.code == code and rp.allowed:
                return True, 'role'
    return False, None


def is_superadmin(user):
    """True if `user` bypasses every check (Django is_superuser, or an AccessRole with is_superadmin)."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    return _load(user)['super']


def has_perm(user, code, project=None):
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if code in ANY_USER_CODES:
        return True
    data = _load(user)
    if data['super']:
        return True
    assignments = _matching_assignments(data['assignments'], _project_id(project))
    if not assignments:
        return False
    allowed, _source = _resolve(assignments, code)
    return allowed


def has_perm_any_scope(user, code):
    """
    True if `code` is granted by *any* of the user's assignments, on any project (or GLOBAL) --
    i.e. "does this person's role ever let them do this", independent of which project. Used where
    the caller checks project membership separately (core/permissions.py's PROJECT_RULES pattern:
    "WHAT the role may do" here, "WHICH project" there).
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if code in ANY_USER_CODES:
        return True
    data = _load(user)
    if data['super']:
        return True
    return any(_resolve([a], code)[0] for a in data['assignments'])


def accessible_projects(user):
    if not (user and getattr(user, 'is_authenticated', False)):
        return Project.objects.none()
    data = _load(user)
    if data['super']:
        return Project.objects.all()
    if any(a.scope_type == ScopeType.GLOBAL for a in data['assignments']):
        return Project.objects.all()
    project_ids = {a.project_id for a in data['assignments'] if a.scope_type == ScopeType.PROJECT}
    return Project.objects.filter(id__in=project_ids)


def effective_matrix(user):
    """
    {scope_key: {code: {'allowed': bool, 'source': ...}}} for every scope the user has (GLOBAL and/or
    each project id they are assigned to), across every known permission code. Used by "Check Access".
    """
    codes = list(PERMISSIONS.keys())
    if not (user and getattr(user, 'is_authenticated', False)):
        return {}
    data = _load(user)
    if data['super']:
        scope_keys = ['GLOBAL'] + [p.id for p in Project.objects.all()]
        return {key: {code: {'allowed': True, 'source': 'role'} for code in codes} for key in scope_keys}

    scope_keys = set()
    for a in data['assignments']:
        scope_keys.add('GLOBAL' if a.scope_type == ScopeType.GLOBAL else a.project_id)
    result = {}
    for key in scope_keys:
        project_id = None if key == 'GLOBAL' else key
        assignments = _matching_assignments(data['assignments'], project_id)
        result[key] = {}
        for code in codes:
            if code in ANY_USER_CODES:
                result[key][code] = {'allowed': True, 'source': 'role'}
                continue
            allowed, source = _resolve(assignments, code)
            result[key][code] = {'allowed': allowed, 'source': source}
    return result


# ---------------------------------------------------------------------------
# Dependency validation (server-side; mirrors the UI's auto-toggle behaviour)
# ---------------------------------------------------------------------------

def validate_dependencies(allowed_codes):
    """
    `allowed_codes`: the set of permission codes that would be `allowed=True` for one role after a
    save. Raises ValueError(message) naming the first violation if some code is on but a code it
    depends on is not -- the save-time mirror of the UI's auto-enable-dependencies behaviour.
    """
    for code in allowed_codes:
        missing = DEPENDENCIES.get(code, set()) - allowed_codes
        if missing:
            missing_labels = ', '.join(PERMISSIONS[m][3] for m in sorted(missing))
            this_label = PERMISSIONS[code][3]
            raise ValueError(f'"{this_label}" needs "{missing_labels}" to also be turned on.')


def users_with_role(project, role_name):
    """
    Everyone with a PROJECT-scoped UserAccess row naming `role_name` on `project` -- no super-admin
    bypass. Answers a business-identity question ("who is literally assigned OWNER/MANAGER here"),
    not a permission question -- used where a model needs to validate a real-world assignment fact
    (e.g. "is this Manager actually assigned to this project"), not "can they do X".
    """
    User = get_user_model()
    project_id = _project_id(project)
    return User.objects.filter(
        access_grants__project_id=project_id, access_grants__scope_type=ScopeType.PROJECT,
        access_grants__role__name=role_name,
    ).distinct()


def projects_with_role(user, role_name):
    """The reverse of `users_with_role`: every project `user` holds a PROJECT-scoped `role_name` grant
    on (no super-admin bypass, no GLOBAL grants -- just literal per-project assignment rows)."""
    return Project.objects.filter(
        user_access__user=user, user_access__scope_type=ScopeType.PROJECT, user_access__role__name=role_name,
    ).distinct()


def display_role(user):
    """
    A single label for `user` to show in a list ('ADMIN'/'OWNER'/'MANAGER'/None) -- display only,
    never used for an access decision. A person can hold different roles on different projects; this
    picks the highest-priority one that exists anywhere, purely so the Users screen has one word to
    show next to their name.
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    if is_superadmin(user):
        return 'ADMIN'
    grants = UserAccess.objects.filter(user=user).select_related('role')
    names = {g.role.name for g in grants}
    for candidate in ('OWNER', 'MANAGER'):
        if candidate in names:
            return candidate
    return next(iter(names), None)


def is_last_superadmin(user):
    """True if `user` is the only person left with SUPER_ADMIN access (used to block removing them)."""
    User = get_user_model()
    superadmin_user_ids = set(
        UserAccess.objects.filter(role__is_superadmin=True).values_list('user_id', flat=True)
    ) | set(User.objects.filter(is_superuser=True).values_list('id', flat=True))
    return user.id in superadmin_user_ids and len(superadmin_user_ids) <= 1
