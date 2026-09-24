"""
API for Settings -> Access Control (Super Admin only). Three screens from the spec:
  Roles & Permissions  -- GET/PUT /access/role-permissions/, DELETE to reset to default
  User Access          -- /access/users/, /access/users/<id>/access/[<access_id>/[overrides/]]
  Check Access         -- /access/check/<user_id>/, /access/who-can/

Every write is atomic, validates dependencies (access_catalog.DEPENDENCIES), writes a
PermissionAuditLog row, and invalidates the RBAC cache for any affected user. No permission logic
lives here -- every decision still goes through access.services; this module only reads/writes the
tables services.py reads and translates them to/from the plain-language JSON the UI needs.
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..access_catalog import (
    ANY_USER_CODES, DEPENDENCIES, GROUPS, PERMISSIONS, RESET_DEFAULTS, SUPER_ADMIN_ONLY_CODES,
)
from ..models import (
    AccessOverride, AccessRole, OverrideEffect, PermissionAuditLog, Project, ResourcePermission, ScopeType,
    UserAccess,
)
from ..people import _display_name
from . import services

User = get_user_model()


def _require_super_admin(request):
    if not services.is_superadmin(request.user):
        raise PermissionDenied('Only the super admin can open Access Control.')


def _log(request, action, target_user=None, **detail):
    PermissionAuditLog.objects.create(
        actor=request.user, target_user=target_user, action=action, detail=detail,
    )


def _invalidate(*users):
    for u in users:
        if u is not None:
            services.invalidate(u)


# ---------------------------------------------------------------------------
# Catalogue (roles, groups, permission definitions) -- static-ish, feeds every screen
# ---------------------------------------------------------------------------

class CatalogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_super_admin(request)
        roles = list(AccessRole.objects.all())
        used_by = dict(
            UserAccess.objects.values('role_id').annotate(n=Count('user_id', distinct=True))
            .values_list('role_id', 'n')
        )
        return Response({
            'groups': [{'id': gid, 'label': label} for gid, label in GROUPS],
            'permissions': [
                {
                    'code': code, 'resource': resource, 'action': action, 'group': group,
                    'label': label, 'description': description,
                    'any_user': code in ANY_USER_CODES,
                    'locked_for': ['SUPER_ADMIN'] if code in SUPER_ADMIN_ONLY_CODES else [],
                    'depends_on': sorted(DEPENDENCIES.get(code, ())),
                }
                for code, (resource, action, group, label, description) in PERMISSIONS.items()
            ],
            'roles': [
                {
                    'id': r.id, 'name': r.name, 'description': r.description,
                    'is_superadmin': r.is_superadmin, 'is_system': r.is_system,
                    'used_by': used_by.get(r.id, 0),
                }
                for r in roles
            ],
        })


# ---------------------------------------------------------------------------
# Roles & Permissions (the toggle grid)
# ---------------------------------------------------------------------------

def _role_permission_matrix():
    matrix = {}
    for role in AccessRole.objects.all():
        allowed = set(
            role.role_permissions.filter(allowed=True, resource_permission__isnull=False)
            .values_list('resource_permission__code', flat=True)
        )
        matrix[role.name] = {code: (code in allowed or code in ANY_USER_CODES) for code in PERMISSIONS}
    return matrix


class RolePermissionMatrixView(APIView):
    """GET the whole grid; PUT {role_name: {code: bool}} saves it; DELETE resets to seeded defaults."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_super_admin(request)
        return Response(_role_permission_matrix())

    @transaction.atomic
    def put(self, request):
        _require_super_admin(request)
        matrix = request.data.get('matrix')
        if not isinstance(matrix, dict):
            raise ValidationError({'matrix': 'Invalid permissions.'})
        roles_by_name = {r.name: r for r in AccessRole.objects.all()}
        rps_by_code = {rp.code: rp for rp in ResourcePermission.objects.all()}
        changes = {}
        for role_name, codes in matrix.items():
            role = roles_by_name.get(role_name)
            if role is None or role.is_superadmin or not isinstance(codes, dict):
                continue  # super admin's row is never editable -- it always has everything.
            allowed_now = set()
            for code, allowed in codes.items():
                if code not in PERMISSIONS or code in ANY_USER_CODES or code in SUPER_ADMIN_ONLY_CODES:
                    continue
                if isinstance(allowed, bool) and allowed:
                    allowed_now.add(code)
            # The UI auto-enables dependencies before it ever sends a save; this is the safety net for
            # any other client -- an inconsistent set (a permission on without something it needs) is
            # rejected outright rather than silently "fixed", per the spec.
            try:
                services.validate_dependencies(allowed_now)
            except ValueError as e:
                raise ValidationError({role_name: str(e)})

            for code, rp in rps_by_code.items():
                if code in ANY_USER_CODES or code in SUPER_ADMIN_ONLY_CODES:
                    continue
                desired = code in allowed_now
                obj, created = role.role_permissions.update_or_create(
                    resource_permission=rp,
                    defaults={'allowed': desired, 'role': role.name, 'permission': code},
                )
            changes[role_name] = sorted(allowed_now)

        _log(request, 'ROLE_PERMISSIONS_CHANGED', changes=changes)
        for user in User.objects.filter(access_grants__role__name__in=matrix.keys()).distinct():
            services.invalidate(user)
        return Response(_role_permission_matrix())

    @transaction.atomic
    def delete(self, request):
        _require_super_admin(request)
        for role in AccessRole.objects.filter(is_superadmin=False):
            for rp in ResourcePermission.objects.all():
                default = RESET_DEFAULTS.get(rp.code, {}).get(role.name, False)
                role.role_permissions.update_or_create(
                    resource_permission=rp,
                    defaults={'allowed': default, 'role': role.name, 'permission': rp.code},
                )
        _log(request, 'ROLE_PERMISSIONS_RESET')
        for user in User.objects.all():
            services.invalidate(user)
        return Response(_role_permission_matrix())


# ---------------------------------------------------------------------------
# User Access (person-centric)
# ---------------------------------------------------------------------------

def _access_row(a):
    overrides = list(a.overrides.select_related('resource_permission'))
    return {
        'id': a.id,
        'scope_type': a.scope_type,
        'project_id': a.project_id,
        'project_name': a.project.name if a.project_id else None,
        'role_id': a.role_id,
        'role': a.role.name,
        'override_count': len(overrides),
    }


class UserAccessListView(APIView):
    """GET: every user, with a search-friendly summary, for the left-hand list."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_super_admin(request)
        users = User.objects.select_related('profile').prefetch_related('access_grants').order_by('username')
        q = (request.query_params.get('q') or '').strip().lower()
        rows = []
        for u in users:
            name = _display_name(u)
            if q and q not in name.lower() and q not in u.username.lower():
                continue
            grants = list(u.access_grants.all())
            rows.append({
                'id': u.id, 'username': u.username, 'name': name,
                'initials': ''.join(p[0].upper() for p in name.split()[:2]) or u.username[:2].upper(),
                'is_superadmin': services.is_superadmin(u),
                'project_count': len([g for g in grants if g.scope_type == ScopeType.PROJECT]),
                'has_global': any(g.scope_type == ScopeType.GLOBAL for g in grants),
            })
        return Response(rows)


class UserAccessDetailView(APIView):
    """GET the person card (their UserAccess rows); the 3-step wizard's final save (POST)."""

    permission_classes = [IsAuthenticated]

    def _user(self, user_id):
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise NotFound('User not found.')
        return user

    def get(self, request, user_id):
        _require_super_admin(request)
        user = self._user(user_id)
        grants = UserAccess.objects.filter(user=user).select_related('role', 'project').prefetch_related(
            'overrides__resource_permission')
        return Response({
            'user': {'id': user.id, 'name': _display_name(user), 'username': user.username},
            'is_superadmin': services.is_superadmin(user),
            'access': [_access_row(a) for a in grants],
        })

    @transaction.atomic
    def post(self, request, user_id):
        """{scope_type, project_ids?, role} -- add one GLOBAL row or one PROJECT row per project id."""
        _require_super_admin(request)
        user = self._user(user_id)
        if user.id == request.user.id:
            raise ValidationError('You cannot change your own access.')
        role_name = request.data.get('role')
        role = AccessRole.objects.filter(name=role_name).first()
        if role is None:
            raise ValidationError({'role': 'Pick a role.'})
        scope_type = request.data.get('scope_type', ScopeType.PROJECT)
        created = []
        if scope_type == ScopeType.GLOBAL:
            obj, _ = UserAccess.objects.update_or_create(
                user=user, project=None, defaults={'role': role, 'scope_type': ScopeType.GLOBAL},
            )
            created.append(obj)
        else:
            project_ids = request.data.get('project_ids') or []
            if not project_ids:
                raise ValidationError({'project_ids': 'Pick at least one project.'})
            for pid in project_ids:
                project = Project.objects.filter(pk=pid).first()
                if project is None:
                    continue
                obj, _ = UserAccess.objects.update_or_create(
                    user=user, project=project, defaults={'role': role, 'scope_type': ScopeType.PROJECT},
                )
                created.append(obj)
        _invalidate(user)
        _log(request, 'USER_ACCESS_GRANTED', target_user=user,
             role=role.name, scope_type=scope_type, access_ids=[a.id for a in created])
        return Response([_access_row(a) for a in created], status=201)


class UserAccessGrantView(APIView):
    """PATCH {role} changes one grant's role; DELETE removes it. Guards: no self-edit, no last-superadmin."""

    permission_classes = [IsAuthenticated]

    def _grant(self, user_id, access_id):
        grant = UserAccess.objects.filter(pk=access_id, user_id=user_id).select_related('role', 'user').first()
        if grant is None:
            raise NotFound('This access grant no longer exists.')
        return grant

    @transaction.atomic
    def patch(self, request, user_id, access_id):
        _require_super_admin(request)
        grant = self._grant(user_id, access_id)
        if grant.user_id == request.user.id:
            raise ValidationError('You cannot change your own access.')
        new_role = AccessRole.objects.filter(name=request.data.get('role')).first()
        if new_role is None:
            raise ValidationError({'role': 'Pick a role.'})
        if grant.role.is_superadmin and not new_role.is_superadmin and services.is_last_superadmin(grant.user):
            raise ValidationError('This is the last super admin -- give someone else that role first.')
        old_role = grant.role.name
        grant.role = new_role
        grant.save(update_fields=['role', 'updated_at'])
        _invalidate(grant.user)
        _log(request, 'USER_ACCESS_ROLE_CHANGED', target_user=grant.user,
             access_id=grant.id, old_role=old_role, new_role=new_role.name)
        return Response(_access_row(grant))

    @transaction.atomic
    def delete(self, request, user_id, access_id):
        _require_super_admin(request)
        grant = self._grant(user_id, access_id)
        if grant.user_id == request.user.id:
            raise ValidationError('You cannot change your own access.')
        if grant.role.is_superadmin and services.is_last_superadmin(grant.user):
            raise ValidationError('This is the last super admin -- their access cannot be removed.')
        detail = _access_row(grant)
        grant.delete()
        _invalidate(grant.user)
        _log(request, 'USER_ACCESS_REMOVED', target_user_id=user_id, **detail)
        return Response(status=204)


class UserAccessRemoveAllView(APIView):
    """DELETE: quick action 'Remove from all projects' (keeps/removes GLOBAL too if asked)."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def delete(self, request, user_id):
        _require_super_admin(request)
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise NotFound('User not found.')
        if user.id == request.user.id:
            raise ValidationError('You cannot change your own access.')
        grants = UserAccess.objects.filter(user=user)
        if any(g.role.is_superadmin for g in grants.select_related('role')) and services.is_last_superadmin(user):
            raise ValidationError('This is the last super admin -- their access cannot be removed.')
        count = grants.count()
        grants.delete()
        _invalidate(user)
        _log(request, 'USER_ACCESS_REMOVED_ALL', target_user=user, count=count)
        return Response(status=204)


class CopyAccessView(APIView):
    """POST {from_user_id}: quick action 'Copy access from another person' onto `user_id`."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, user_id):
        _require_super_admin(request)
        target = User.objects.filter(pk=user_id).first()
        source_id = request.data.get('from_user_id')
        source = User.objects.filter(pk=source_id).first()
        if target is None or source is None:
            raise NotFound('User not found.')
        if target.id == request.user.id:
            raise ValidationError('You cannot change your own access.')
        created = []
        for grant in UserAccess.objects.filter(user=source).select_related('role'):
            obj, _ = UserAccess.objects.update_or_create(
                user=target, project_id=grant.project_id,
                defaults={'role': grant.role, 'scope_type': grant.scope_type},
            )
            created.append(obj)
        _invalidate(target)
        _log(request, 'USER_ACCESS_COPIED', target_user=target, from_user_id=source.id)
        return Response([_access_row(a) for a in created], status=201)


class SameRoleAllView(APIView):
    """POST {role}: quick action 'Give same role on all projects' this person already has access to."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, user_id):
        _require_super_admin(request)
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise NotFound('User not found.')
        if user.id == request.user.id:
            raise ValidationError('You cannot change your own access.')
        role = AccessRole.objects.filter(name=request.data.get('role')).first()
        if role is None:
            raise ValidationError({'role': 'Pick a role.'})
        grants = list(UserAccess.objects.filter(user=user))
        for g in grants:
            g.role = role
            g.save(update_fields=['role', 'updated_at'])  # per-row so `updated_at` (auto_now) is correct
        _invalidate(user)
        _log(request, 'USER_ACCESS_SAME_ROLE_ALL', target_user=user, role=role.name)
        return Response([_access_row(a) for a in UserAccess.objects.filter(user=user).select_related('role', 'project')])


# ---------------------------------------------------------------------------
# Special changes (per-grant overrides drawer)
# ---------------------------------------------------------------------------

class OverridesView(APIView):
    """GET the tri-state rows for one grant; PUT {code: 'same'|'allow'|'deny'} saves them."""

    permission_classes = [IsAuthenticated]

    def _grant(self, user_id, access_id):
        grant = UserAccess.objects.filter(pk=access_id, user_id=user_id).select_related('role', 'user').first()
        if grant is None:
            raise NotFound('This access grant no longer exists.')
        return grant

    def _rows(self, grant):
        role_allowed = set(
            grant.role.role_permissions.filter(allowed=True, resource_permission__isnull=False)
            .values_list('resource_permission__code', flat=True)
        )
        current_overrides = {
            o.resource_permission.code: o.effect for o in grant.overrides.select_related('resource_permission')
        }
        rows = []
        for code, (resource, action, group, label, description) in PERMISSIONS.items():
            if code in ANY_USER_CODES:
                continue
            role_gives = code in role_allowed
            state = 'same'
            if current_overrides.get(code) == OverrideEffect.ALLOW:
                state = 'allow'
            elif current_overrides.get(code) == OverrideEffect.DENY:
                state = 'deny'
            rows.append({
                'code': code, 'label': label, 'description': description, 'group': group,
                'role_gives': role_gives, 'state': state, 'changed': state != 'same',
            })
        return rows

    def get(self, request, user_id, access_id):
        _require_super_admin(request)
        grant = self._grant(user_id, access_id)
        return Response({'access_id': grant.id, 'rows': self._rows(grant)})

    @transaction.atomic
    def put(self, request, user_id, access_id):
        _require_super_admin(request)
        grant = self._grant(user_id, access_id)
        if grant.user_id == request.user.id:
            raise ValidationError('You cannot change your own access.')
        changes = request.data.get('changes')
        if not isinstance(changes, dict):
            raise ValidationError({'changes': 'Invalid.'})
        rps = {rp.code: rp for rp in ResourcePermission.objects.filter(code__in=changes.keys())}
        for code, state in changes.items():
            rp = rps.get(code)
            if rp is None or code in ANY_USER_CODES or code in SUPER_ADMIN_ONLY_CODES:
                continue
            if state == 'same':
                AccessOverride.objects.filter(user_access=grant, resource_permission=rp).delete()
            elif state in ('allow', 'deny'):
                AccessOverride.objects.update_or_create(
                    user_access=grant, resource_permission=rp,
                    defaults={'effect': OverrideEffect.ALLOW if state == 'allow' else OverrideEffect.DENY,
                              'created_by': request.user},
                )
        _invalidate(grant.user)
        _log(request, 'ACCESS_OVERRIDES_CHANGED', target_user=grant.user, access_id=grant.id, changes=changes)
        return Response({'access_id': grant.id, 'rows': self._rows(grant)})


# ---------------------------------------------------------------------------
# Check Access
# ---------------------------------------------------------------------------

class CheckAccessView(APIView):
    """GET: the full effective matrix for one user (every scope they have)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        _require_super_admin(request)
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise NotFound('User not found.')
        matrix = services.effective_matrix(user)
        projects = {p.id: p.name for p in Project.objects.filter(id__in=[k for k in matrix if k != 'GLOBAL'])}
        scopes = []
        for key, perms in matrix.items():
            scopes.append({
                'key': 'GLOBAL' if key == 'GLOBAL' else str(key),
                'label': 'Every project' if key == 'GLOBAL' else projects.get(key, f'Project {key}'),
                'permissions': perms,
            })
        return Response({'user': {'id': user.id, 'name': _display_name(user)}, 'scopes': scopes})


class WhoCanView(APIView):
    """GET ?code=canEditExpense&project=<id|GLOBAL> -- reverse lookup: who can do this, and why."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_super_admin(request)
        code = request.query_params.get('code')
        if code not in PERMISSIONS:
            raise ValidationError({'code': 'Unknown permission.'})
        project_param = request.query_params.get('project')
        project = None
        if project_param and project_param != 'GLOBAL':
            project = Project.objects.filter(pk=project_param).first()
        people = []
        for user in User.objects.all():
            if services.has_perm(user, code, project):
                data = _load_source(user, code, project)
                people.append({'id': user.id, 'name': _display_name(user), 'reason': data})
        return Response(people)


def _load_source(user, code, project):
    if services.is_superadmin(user):
        return 'Super admin'
    data = services._load(user)  # noqa: SLF001 -- internal, but this module owns the read-only reporting path
    project_id = project.id if project else None
    assignments = services._matching_assignments(data['assignments'], project_id)  # noqa: SLF001
    allowed, source = services._resolve(assignments, code)  # noqa: SLF001
    if source == 'allow':
        return 'Given extra access'
    if source == 'role':
        for a in assignments:
            if any(rp.resource_permission_id and rp.resource_permission.code == code and rp.allowed
                   for rp in a.role.role_permissions.all()):
                return f'From {a.role.name.replace("_", " ").title()} role'
    return 'Allowed'
