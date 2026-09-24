"""
Users, project members, passwords and the role-permission matrix.

RBAC v2: membership is a `UserAccess` row (scope PROJECT, role OWNER/MANAGER); there is no
`ProjectOwner`/`ProjectManager` table any more. `Owner`/`Manager` still hold real business data
(name, mobile, salary) and are created here when a person is first given that role, but they no
longer carry any access meaning themselves -- only `UserAccess` does. Every check is done on the
server; the web app only shows or hides things.
"""

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import services
from .models import (
    AccessRole, ExpenseTransaction, Manager, ManagerFund, Owner, Project, Role, RolePermission, ScopeType,
    UserAccess,
)
from .permissions import (
    CAN_MANAGE_PROJECT_MEMBERS, CAN_MANAGE_USERS, CAN_RESET_USER_PASSWORD, has_permission, is_admin,
    owns_project,
)

User = get_user_model()

# The web app's role keys <-> the server's role names.
WEB_ROLES = {'super_admin': 'ADMIN', 'owner': 'OWNER', 'manager': 'MANAGER', 'viewer': 'VIEWER'}
SERVER_TO_WEB = {v: k for k, v in WEB_ROLES.items()}
# Never editable through the matrix: admin always has everything, and only an admin manages permissions.
LOCKED_PERMISSIONS = {'canManagePermissions', 'canChangeOwnPassword'}
PERMISSION_KEY = re.compile(r'^can[A-Za-z]{2,45}$')


def _display_name(user):
    owner = getattr(user, 'owner_profile', None)
    manager = getattr(user, 'manager_profile', None)
    return (owner.name if owner else manager.name if manager else user.get_full_name()) or user.get_username()


def can_reset_password(actor, target):
    """Whether `actor` may set a new password for `target` (the permission says the capability exists; this says who)."""
    if actor.id == target.id or not has_permission(actor, CAN_RESET_USER_PASSWORD):
        return False
    if target.is_superuser:
        return actor.is_superuser
    if is_admin(actor):
        return True
    # Anyone else: only a non-owner member of a project the actor owns (never an admin).
    if is_admin(target) or UserAccess.objects.filter(user=target, role__name='OWNER').exists():
        return False
    owned_projects = UserAccess.objects.filter(
        user=actor, role__name='OWNER', scope_type=ScopeType.PROJECT).values_list('project_id', flat=True)
    return UserAccess.objects.filter(
        user=target, role__name='MANAGER', scope_type=ScopeType.PROJECT, project_id__in=owned_projects).exists()


def _check_new_password(password, user):
    try:
        validate_password(password or '', user)
    except DjangoValidationError as e:
        raise ValidationError({'new_password': list(e.messages)})


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

class ChangePasswordView(APIView):
    """POST {old_password, new_password}: the caller changes their own password. Other sessions are signed out."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if not user.check_password(request.data.get('old_password') or ''):
            raise ValidationError({'old_password': 'Current password is wrong.'})
        new = request.data.get('new_password') or ''
        _check_new_password(new, user)
        user.set_password(new)
        user.save(update_fields=['password'])
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        return Response({'token': token.key})


class ResetPasswordView(APIView):
    """POST {new_password}: set another user's password. The user is signed out everywhere."""

    permission_classes = [IsAuthenticated]

    def post(self, request, user_id):
        target = User.objects.filter(pk=user_id).first()
        if target is None or not can_reset_password(request.user, target):
            raise PermissionDenied('You cannot reset this password.')
        new = request.data.get('new_password') or ''
        _check_new_password(new, target)
        target.set_password(new)
        target.save(update_fields=['password'])
        Token.objects.filter(user=target).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class UserListView(APIView):
    """
    GET: every user with their role and project memberships (needs canManageUsers).
    POST (super admin only): create a global account = Django User + UserAccess-free Owner/Manager
    record. It is not tied to any project; projects are assigned afterwards from each project's
    Members screen (or from Settings -> Access Control).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.query_params.get('project'):
            return self._candidates(request)
        if not has_permission(request.user, CAN_MANAGE_USERS):
            raise PermissionDenied('You cannot manage users.')
        users = list(User.objects.select_related('owner_profile', 'manager_profile').order_by('username'))
        projects = {}
        for grant in UserAccess.objects.filter(scope_type=ScopeType.PROJECT).select_related('role'):
            if grant.role.name in ('OWNER', 'MANAGER'):
                projects.setdefault(grant.user_id, []).append({'project_id': grant.project_id, 'role': grant.role.name})
        return Response([{
            'id': u.id, 'username': u.username, 'name': _display_name(u), 'role': services.display_role(u),
            'is_active': u.is_active, 'projects': projects.get(u.id, []),
            'can_reset': can_reset_password(request.user, u),
        } for u in users])

    def _candidates(self, request):
        """?project=<id>: existing users who could be added to that project (for its Add Member picker)."""
        try:
            project = _members_project(request, int(request.query_params['project']))
        except ValueError:
            raise NotFound('Project not found.')
        taken = set(UserAccess.objects.filter(project=project).values_list('user_id', flat=True))
        users = (User.objects.filter(is_active=True, is_superuser=False)
                 .exclude(id__in=UserAccess.objects.filter(role__is_superadmin=True).values('user_id'))
                 .exclude(id__in=taken)
                 .select_related('owner_profile', 'manager_profile').order_by('username'))
        return Response([{'id': u.id, 'username': u.username, 'email': u.email, 'name': _display_name(u),
                          'role': services.display_role(u)} for u in users])

    @transaction.atomic
    def post(self, request):
        if not is_admin(request.user):
            raise PermissionDenied('Only the super admin can create users.')
        d = request.data
        name = (d.get('name') or '').strip()
        username = (d.get('username') or '').strip()
        mobile = (d.get('mobile') or '').strip()
        role = d.get('role')
        errors = {}
        if not name:
            errors['name'] = 'Enter a name.'
        if not username:
            errors['username'] = 'Enter a username or email.'
        elif User.objects.filter(Q(username__iexact=username) | Q(email__iexact=username)).exists():
            errors['username'] = 'This username or email is already in use.'
        if role not in (Role.OWNER, Role.MANAGER):
            errors['role'] = 'Role must be OWNER or MANAGER.'
        if len(mobile) > 20:
            errors['mobile'] = 'Mobile number is too long.'
        if d.get('password') != d.get('confirm_password'):
            errors['confirm_password'] = 'The two passwords are not the same.'
        if errors:
            raise ValidationError(errors)
        user = User(username=username, first_name=name[:150], email=username if '@' in username else '')
        _check_new_password(d.get('password'), user)
        user.set_password(d.get('password'))
        user.save()
        model = Owner if role == Role.OWNER else Manager
        model.objects.create(user=user, name=name, mobile=mobile)
        return Response({'id': user.id}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Project members
# ---------------------------------------------------------------------------

def _members_project(request, pk):
    project = Project.objects.filter(pk=pk).first()
    if project is None or not (is_admin(request.user) or owns_project(request.user, project)):
        raise NotFound('Project not found.')
    # Project-aware: respects a per-project override, not just "does this role ever get this".
    if not services.has_perm(request.user, CAN_MANAGE_PROJECT_MEMBERS, project):
        raise PermissionDenied('You cannot manage project members.')
    return project


def _member_rows(request, project):
    grants = (UserAccess.objects.filter(project=project, scope_type=ScopeType.PROJECT, role__name__in=('OWNER', 'MANAGER'))
              .select_related('user', 'role'))
    members = [{'id': g.user.id, 'username': g.user.username, 'name': _display_name(g.user), 'role': g.role.name,
                'can_reset': can_reset_password(request.user, g.user)} for g in grants]
    admins = User.objects.filter(
        Q(is_superuser=True) | Q(id__in=UserAccess.objects.filter(role__is_superadmin=True).values('user_id'))
    ).distinct()
    return {
        'members': sorted(members, key=lambda m: (m['name'].lower(), m['role'])),
        'admins': [{'id': u.id, 'username': u.username, 'name': _display_name(u)} for u in admins],
    }


def _ensure_business_record(user, role_name):
    """Give `user` the Owner/Manager master-data record `role_name` needs. A person may hold both
    (OWNER on one project, MANAGER on another) -- there is no longer a "one role for life" rule."""
    if role_name == Role.OWNER:
        return Owner.objects.get_or_create(user=user, defaults={'name': _display_name(user)})[0]
    return Manager.objects.get_or_create(user=user, defaults={'name': _display_name(user)})[0]


def _check_removable(project, user, role_name):
    """A member with financial history on this project cannot be dropped: that would orphan the records."""
    if role_name == Role.OWNER:
        used = (ExpenseTransaction.objects.filter(project=project, paid_by_owner__user=user).exists()
                or ManagerFund.objects.filter(project=project, given_by_owner__user=user).exists())
    else:
        used = ManagerFund.objects.filter(project=project, manager__user=user).exists()
    if used:
        raise ValidationError('This person has expenses or funds recorded on this project, so they cannot be removed.')


class ProjectMembersView(APIView):
    """GET the members of a project; POST {username, role} adds an existing user as OWNER or MANAGER."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return Response(_member_rows(request, _members_project(request, pk)))

    @transaction.atomic
    def post(self, request, pk):
        project = _members_project(request, pk)
        who = (request.data.get('username') or '').strip()
        role_name = request.data.get('role')
        if not who:
            raise ValidationError({'username': 'Enter an email or username.'})
        if role_name not in (Role.OWNER, Role.MANAGER):
            raise ValidationError({'role': 'Role must be OWNER or MANAGER.'})
        matches = list(User.objects.filter(Q(username__iexact=who) | Q(email__iexact=who))[:2])
        if not matches:
            raise ValidationError({'username': 'No user with that email or username. The administrator must create the account first.'})
        if len(matches) > 1:
            raise ValidationError({'username': 'More than one user matches. Use the exact username.'})
        user = matches[0]
        if is_admin(user):
            raise ValidationError({'username': 'This user is an admin and already has access to every project.'})
        if UserAccess.objects.filter(project=project, user=user).exists():
            raise ValidationError({'username': 'This user is already a member of this project.'})
        _ensure_business_record(user, role_name)
        access_role = AccessRole.objects.get(name=role_name)
        UserAccess.objects.create(project=project, user=user, role=access_role, scope_type=ScopeType.PROJECT)
        return Response(_member_rows(request, project), status=status.HTTP_201_CREATED)


class ProjectMemberDetailView(APIView):
    """PATCH {role}: change a member's role. DELETE: remove them from the project."""

    permission_classes = [IsAuthenticated]

    def _member(self, request, pk, user_id):
        project = _members_project(request, pk)
        if user_id == request.user.id:
            raise ValidationError('You cannot change or remove yourself.')
        grant = UserAccess.objects.filter(
            project=project, user_id=user_id, scope_type=ScopeType.PROJECT, role__name__in=('OWNER', 'MANAGER'),
        ).select_related('user', 'role').first()
        if grant is None:
            raise NotFound('This person is not a member of this project.')
        return project, grant

    @transaction.atomic
    def patch(self, request, pk, user_id):
        project, grant = self._member(request, pk, user_id)
        new_role = request.data.get('role')
        if new_role not in (Role.OWNER, Role.MANAGER):
            raise ValidationError({'role': 'Role must be OWNER or MANAGER.'})
        if new_role == grant.role.name:
            return Response(_member_rows(request, project))
        _check_removable(project, grant.user, grant.role.name)
        _ensure_business_record(grant.user, new_role)
        grant.role = AccessRole.objects.get(name=new_role)
        grant.save(update_fields=['role', 'updated_at'])
        return Response(_member_rows(request, project))

    @transaction.atomic
    def delete(self, request, pk, user_id):
        project, grant = self._member(request, pk, user_id)
        _check_removable(project, grant.user, grant.role.name)
        grant.delete()
        return Response(_member_rows(request, project))


# ---------------------------------------------------------------------------
# Role & Permissions matrix
# ---------------------------------------------------------------------------

def stored_matrix():
    """{permission: {web_role: bool}} for every saved RolePermission row (missing = the built-in default)."""
    matrix = {}
    for row in RolePermission.objects.all():
        if row.role in SERVER_TO_WEB:
            matrix.setdefault(row.permission, {})[SERVER_TO_WEB[row.role]] = row.allowed
    return matrix


class PermissionMatrixView(APIView):
    """
    PUT {matrix: {permission: {role: bool}}} saves the role permissions; DELETE resets them to the defaults.
    Super admin only. Reading is part of /me/, so every device applies the same matrix.
    """

    permission_classes = [IsAuthenticated]

    def _admin(self, request):
        if not is_admin(request.user):
            raise PermissionDenied('Only the super admin can change role permissions.')

    @transaction.atomic
    def put(self, request):
        self._admin(request)
        matrix = request.data.get('matrix')
        if not isinstance(matrix, dict) or len(matrix) > 100:
            raise ValidationError({'matrix': 'Invalid permissions.'})
        for permission, roles in matrix.items():
            if permission in LOCKED_PERMISSIONS or not PERMISSION_KEY.match(str(permission)) or not isinstance(roles, dict):
                continue
            for web_role, allowed in roles.items():
                role = WEB_ROLES.get(web_role)
                if role in (None, 'ADMIN') or not isinstance(allowed, bool):
                    continue                    # admin cannot be edited (it is never locked out)
                RolePermission.objects.update_or_create(role=role, permission=permission, defaults={'allowed': allowed})
        return Response(stored_matrix())

    @transaction.atomic
    def delete(self, request):
        self._admin(request)
        RolePermission.objects.all().delete()
        return Response(stored_matrix())
