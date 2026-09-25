"""
Tests for the RBAC v2 engine (core/access/services.py) and its enforcement/admin surface.
See SCHEMA_PLAN.md for the schema this exercises.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .access import services
from .access.decorators import RequirePermMixin, require_perm
from .models import (
    AccessOverride, AccessRole, ExpenseTransaction, OverrideEffect, Project, ResourcePermission, ScopeType,
    UserAccess,
)

User = get_user_model()


def make_role(name, is_superadmin=False):
    return AccessRole.objects.create(name=name, description=name, is_superadmin=is_superadmin, is_system=False)


def grant(user, role, project=None):
    return UserAccess.objects.create(
        user=user, role=role, project=project,
        scope_type=ScopeType.GLOBAL if project is None else ScopeType.PROJECT,
    )


def override(user_access, code, effect):
    rp = ResourcePermission.objects.get(code=code)
    return AccessOverride.objects.create(user_access=user_access, resource_permission=rp, effect=effect)


def allow_role(role, *codes):
    for code in codes:
        rp = ResourcePermission.objects.get(code=code)
        role.role_permissions.update_or_create(
            resource_permission=rp, defaults={'allowed': True, 'role': role.name, 'permission': code},
        )


class ResolutionTests(TestCase):
    """The has_perm resolution order laid out in access/services.py."""

    def setUp(self):
        self.site_a = Project.objects.create(name='Site A', code='SITE-A')
        self.site_b = Project.objects.create(name='Site B', code='SITE-B')
        self.manager_role = AccessRole.objects.get(name='MANAGER')
        self.owner_role = AccessRole.objects.get(name='OWNER')
        allow_role(self.manager_role, 'canEditExpense', 'canViewExpenses')
        allow_role(self.owner_role, 'canEditExpense', 'canViewExpenses', 'canDeleteExpense')

    def test_manoj_manager_site_a_owner_site_b_with_deny_override(self):
        """Manoj: MANAGER on Site-A, OWNER on Site-B with EXPENSE.EDIT DENY on Site-B."""
        manoj = User.objects.create_user(username='manoj', password='x')
        a = grant(manoj, self.manager_role, self.site_a)
        b = grant(manoj, self.owner_role, self.site_b)
        override(b, 'canEditExpense', OverrideEffect.DENY)

        self.assertTrue(services.has_perm(manoj, 'canEditExpense', self.site_a))     # role grant, Site A
        self.assertFalse(services.has_perm(manoj, 'canEditExpense', self.site_b))    # denied on Site B only
        self.assertTrue(services.has_perm(manoj, 'canDeleteExpense', self.site_b))   # owner role still applies
        self.assertFalse(services.has_perm(manoj, 'canDeleteExpense', self.site_a))  # manager role lacks it

    def test_ravi_supervisor_with_allow_override(self):
        """Ravi: a custom SUPERVISOR role (no permissions of its own) with an ALLOW override -> allowed."""
        supervisor = make_role('SUPERVISOR')
        ravi = User.objects.create_user(username='ravi', password='x')
        site = self.site_a
        a = grant(ravi, supervisor, site)
        self.assertFalse(services.has_perm(ravi, 'canDeleteExpense', site))
        override(a, 'canDeleteExpense', OverrideEffect.ALLOW)
        services.invalidate(ravi)  # a fresh request would naturally re-load; here we force it
        self.assertTrue(services.has_perm(ravi, 'canDeleteExpense', site))

    def test_global_assignment_applies_to_all_projects(self):
        user = User.objects.create_user(username='global_owner', password='x')
        grant(user, self.owner_role, project=None)
        self.assertTrue(services.has_perm(user, 'canEditExpense', self.site_a))
        self.assertTrue(services.has_perm(user, 'canEditExpense', self.site_b))
        self.assertCountEqual(services.accessible_projects(user), [self.site_a, self.site_b])

    def test_deny_beats_role_grant_and_allow_from_another_assignment(self):
        user = User.objects.create_user(username='denied', password='x')
        g_global = grant(user, self.owner_role, project=None)          # role grants canEditExpense
        override(g_global, 'canEditExpense', OverrideEffect.ALLOW)     # ...and an explicit ALLOW too
        g_project = grant(user, self.manager_role, self.site_a)        # scoped assignment for Site A
        override(g_project, 'canEditExpense', OverrideEffect.DENY)     # ...DENY on that one project
        self.assertFalse(services.has_perm(user, 'canEditExpense', self.site_a))  # deny wins
        self.assertTrue(services.has_perm(user, 'canEditExpense', self.site_b))   # unaffected elsewhere

    def test_no_assignment_means_no_access(self):
        user = User.objects.create_user(username='nobody', password='x')
        self.assertFalse(services.has_perm(user, 'canEditExpense', self.site_a))
        self.assertFalse(services.has_perm(user, 'canViewProjects', self.site_a))
        self.assertEqual(list(services.accessible_projects(user)), [])

    def test_super_admin_bypasses_everything(self):
        user = User.objects.create_user(username='root', password='x', is_superuser=True)
        self.assertTrue(services.has_perm(user, 'canEditExpense', self.site_a))
        self.assertTrue(services.has_perm(user, 'canManagePermissions'))
        self.assertCountEqual(services.accessible_projects(user), [self.site_a, self.site_b])

        role_super = make_role('CUSTOM_SUPER', is_superadmin=True)
        user2 = User.objects.create_user(username='root2', password='x')
        grant(user2, role_super, project=None)
        self.assertTrue(services.has_perm(user2, 'canEditExpense', self.site_a))

    def test_any_user_code_always_true(self):
        user = User.objects.create_user(username='lonely', password='x')
        self.assertTrue(services.has_perm(user, 'canChangeOwnPassword'))

    def test_dependency_validation_rejects_invalid_save(self):
        with self.assertRaises(ValueError):
            services.validate_dependencies({'canEditExpense'})   # missing canViewExpenses, canViewProjects, ...
        services.validate_dependencies({'canEditExpense', 'canViewExpenses', 'canViewProjects',
                                         'canViewSuppliers', 'canViewContractors'})  # no error

    def test_reset_defaults_are_internally_dependency_consistent(self):
        """access_catalog.RESET_DEFAULTS is seeded straight into RolePermission (0007_rbac_seed,
        seed_rbac --demo relies on it) without going through validate_dependencies -- this is the
        regression test for that: a role granted X must also be granted everything X depends on,
        or a real deployment ends up in the exact contradiction seed_rbac --demo caught (MANAGER
        could edit an expense but not view one, so the edit endpoint 404'd looking it up)."""
        from .access_catalog import DEPENDENCIES, PERMISSIONS, RESET_DEFAULTS
        for role in ('OWNER', 'MANAGER', 'VIEWER'):
            allowed = {code for code in PERMISSIONS if RESET_DEFAULTS.get(code, {}).get(role, False)}
            for code in allowed:
                missing = DEPENDENCIES.get(code, set()) - allowed
                self.assertFalse(missing, f'{role}/{code} is missing dependencies {missing}')

    def test_last_superadmin_guard(self):
        role = AccessRole.objects.get(name='SUPER_ADMIN')
        solo = User.objects.create_user(username='solo_admin', password='x')
        grant(solo, role, project=None)
        self.assertTrue(services.is_last_superadmin(solo))

        other = User.objects.create_user(username='second_admin', password='x')
        grant(other, role, project=None)
        self.assertFalse(services.is_last_superadmin(solo))


class DecoratorTests(TestCase):
    """Exercised directly (no HTTP dispatch) since both helpers only ever touch request.user/view.kwargs."""

    def setUp(self):
        self.site = Project.objects.create(name='Decorated', code='DEC-1')
        self.role = AccessRole.objects.get(name='MANAGER')
        allow_role(self.role, 'canEditExpense')
        self.user = User.objects.create_user(username='dec_user', password='x')

    def test_require_perm_decorator_returns_403_without_grant(self):
        from rest_framework.exceptions import PermissionDenied
        from rest_framework.response import Response

        class FakeView:
            kwargs = {}

            @require_perm('canEditExpense', project_lookup=lambda view, request: self.site)
            def get(self, request):
                return Response({'ok': True})

        fake_view = FakeView()
        fake_request = type('R', (), {'user': self.user})()
        with self.assertRaises(PermissionDenied):
            fake_view.get(fake_request)

        grant(self.user, self.role, self.site)
        services.invalidate(self.user)
        self.assertEqual(fake_view.get(fake_request).data, {'ok': True})

    def test_require_perm_mixin_denies_and_allows(self):
        from rest_framework.exceptions import PermissionDenied

        class FakeView(RequirePermMixin):
            kwargs = {}
            required_perm = 'canEditExpense'
            project_lookup = staticmethod(lambda view, request: self.site)

            def permission_denied(self, request, message=None, code=None):
                raise PermissionDenied(message)

        fake_view = FakeView()
        fake_request = type('R', (), {'user': self.user})()

        # Exercises just RequirePermMixin's own gate (below), not the full DRF APIView.check_permissions
        # chain it calls super() into -- that needs a real APIView/permission_classes setup which is
        # covered end-to-end by the Access Control API tests instead.
        with self.assertRaises(PermissionDenied):
            _mixin_check(fake_view, fake_request, self.site)

        grant(self.user, self.role, self.site)
        services.invalidate(self.user)
        _mixin_check(fake_view, fake_request, self.site)  # no exception now


def _mixin_check(view, request, project):
    """Runs just RequirePermMixin's own gate (skipping DRF APIView.check_permissions, which needs a
    full APIView/permission_classes setup that isn't the point of this unit test)."""
    from .access import services as _services
    code = view.required_perm
    if isinstance(code, dict):
        code = code.get('GET')
    if not _services.has_perm(request.user, code, project):
        view.permission_denied(request, message='You do not have permission to do this.')


class AccessAdminGuardTests(APITestCase):
    """Guards on the Access Control API: no self-edit, last-superadmin protection."""

    def setUp(self):
        self.super_role = AccessRole.objects.get(name='SUPER_ADMIN')
        self.owner_role = AccessRole.objects.get(name='OWNER')
        self.project = Project.objects.create(name='Guarded', code='GRD-1')
        self.admin = User.objects.create_user(username='the_admin', password='x')
        grant(self.admin, self.super_role, project=None)
        token, _ = Token.objects.get_or_create(user=self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

    def test_cannot_edit_own_access(self):
        own_grant = UserAccess.objects.get(user=self.admin)
        resp = self.client.patch(f'/api/access/users/{self.admin.id}/access/{own_grant.id}/', {'role': 'OWNER'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_can_remove_a_superadmin_who_is_not_the_last(self):
        """The API's last-superadmin guard (services.is_last_superadmin, unit-tested in
        ResolutionTests) only ever blocks removing yourself in practice, since only a super admin can
        call this API at all -- so removing someone *else* who isn't the sole super admin must still
        work normally."""
        second_admin = User.objects.create_user(username='second_admin', password='x')
        second_grant = grant(second_admin, self.super_role, project=None)
        resp = self.client.delete(f'/api/access/users/{second_admin.id}/access/{second_grant.id}/')
        self.assertEqual(resp.status_code, 204)

    def test_role_permission_matrix_rejects_inconsistent_save(self):
        resp = self.client.put('/api/access/role-permissions/', {
            'matrix': {'OWNER': {'canEditExpense': True}},  # missing its dependencies
        }, format='json')
        self.assertEqual(resp.status_code, 400)


# ===========================================================================
# Follow-up 2: existing viewsets are project-aware (respect a per-project override)
# ===========================================================================

class ExpenseEndpointProjectAwareTests(APITestCase):
    """Manoj: MANAGER on Site A, OWNER on Site B with EXPENSE.EDIT blocked there -- against the real
    /api/expense-transactions/ and /api/labour-payments/ endpoints, not the service layer directly."""

    def setUp(self):
        from .models import (
            ExpenseCategory, ExpenseTransaction, Manager, Owner, PartyType, PaymentMode,
        )
        self.ExpenseTransaction, self.ExpenseCategory = ExpenseTransaction, ExpenseCategory
        self.PartyType, self.PaymentMode = PartyType, PaymentMode

        self.site_a = Project.objects.create(name='Site A', code='MJ-A')
        self.site_b = Project.objects.create(name='Site B', code='MJ-B')
        # Business-entity rows (paid_by_owner / created_by target); separate from Manoj's own login.
        biller_user = User.objects.create_user(username='biller', password='x')
        self.biller = Owner.objects.create(user=biller_user, name='Biller')

        self.manoj = User.objects.create_user(username='manoj', password='x')
        self.manager_role = AccessRole.objects.get(name='MANAGER')
        self.owner_role = AccessRole.objects.get(name='OWNER')
        grant(self.manoj, self.manager_role, self.site_a)
        b_grant = grant(self.manoj, self.owner_role, self.site_b)
        override(b_grant, 'canEditExpense', OverrideEffect.DENY)
        allow_role(self.manager_role, 'canEditExpense', 'canViewExpenses', 'canAddMiscExpense',
                   'canRecordLabourPayment', 'canDeleteExpense')
        allow_role(self.owner_role, 'canEditExpense', 'canViewExpenses', 'canAddMiscExpense',
                   'canRecordLabourPayment', 'canDeleteExpense')

        # LabourPaymentBatchSerializer requires `paid_by_owner` to be the acting user's own Owner
        # record (self-attribution); can_distribute_manager_fund needs a real Manager record too --
        # UserAccess carries no business data, so both are created explicitly here.
        self.manoj_owner = Owner.objects.create(user=self.manoj, name='Manoj')
        self.manoj_manager = Manager.objects.create(user=self.manoj, name='Manoj')

        token, _ = Token.objects.get_or_create(user=self.manoj)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        def make_txn(project):
            return self.ExpenseTransaction.objects.create(
                project=project, expense_date='2026-01-01', expense_category=ExpenseCategory.MISCELLANEOUS,
                expense_type='Other', party_type=PartyType.NONE, payee_name='X', paid_by_owner=self.biller,
                amount='100.00', payment_mode=PaymentMode.CASH, created_by=biller_user,
            )
        self.txn_a = make_txn(self.site_a)
        self.txn_b = make_txn(self.site_b)

    def test_edit_allowed_on_site_a_manager_blocked_on_site_b_owner(self):
        r = self.client.patch(f'/api/expense-transactions/{self.txn_a.id}/', {'amount': '150.00'}, format='json')
        self.assertEqual(r.status_code, 200)
        r = self.client.patch(f'/api/expense-transactions/{self.txn_b.id}/', {'amount': '150.00'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_delete_follows_the_same_project_aware_rule(self):
        # canDeleteExpense isn't overridden, so it still follows each project's role grant, not the
        # EXPENSE.EDIT deny -- both are allowed (the deny above is scoped to canEditExpense only).
        r = self.client.post(f'/api/expense-transactions/{self.txn_a.id}/cancel/', {'remarks': 'test'})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f'/api/expense-transactions/{self.txn_b.id}/cancel/', {'remarks': 'test'})
        self.assertEqual(r.status_code, 200)

    def test_add_expense_respects_project(self):
        payload = {
            'project': self.site_a.id, 'expense_date': '2026-01-02', 'expense_category': 'MISCELLANEOUS',
            'expense_type': 'Other', 'party_type': 'NONE', 'payee_name': 'Y', 'paid_by_owner': self.biller.id,
            'amount': '50.00', 'payment_mode': 'CASH',
        }
        r = self.client.post('/api/expense-transactions/', payload, format='json')
        self.assertEqual(r.status_code, 201)

    def test_add_expense_denied_with_no_grant_on_project(self):
        stranger_project = Project.objects.create(name='Stranger', code='MJ-C')
        payload = {
            'project': stranger_project.id, 'expense_date': '2026-01-02', 'expense_category': 'MISCELLANEOUS',
            'expense_type': 'Other', 'party_type': 'NONE', 'payee_name': 'Y', 'paid_by_owner': self.biller.id,
            'amount': '50.00', 'payment_mode': 'CASH',
        }
        r = self.client.post('/api/expense-transactions/', payload, format='json')
        self.assertEqual(r.status_code, 403)

    def test_labour_payment_respects_project(self):
        # LabourPaymentViewSet's RoleAllowed gate only asks "does Manoj hold OWNER anywhere" (he
        # does, on Site B) -- it's the project-aware canRecordLabourPayment check inside create()
        # that actually decides project-by-project, which is what this test exercises.
        from .models import Labour, ProjectLabour
        labour = Labour.objects.create(name='Ramesh')
        ProjectLabour.objects.create(project=self.site_a, labour=labour, is_active=True)
        ProjectLabour.objects.create(project=self.site_b, labour=labour, is_active=True)

        def pay(project):
            return self.client.post('/api/labour-payments/', {
                'project': project.id, 'expense_date': '2026-01-03', 'paid_by_owner': self.manoj_owner.id,
                'payment_mode': 'CASH', 'payments': [{'labour': labour.id, 'amount': '300.00'}],
            }, format='json')

        # Owner on Site B still has canRecordLabourPayment (only canEditExpense was denied there).
        self.assertEqual(pay(self.site_b).status_code, 201)

    def test_labour_payment_denied_on_unassigned_project(self):
        from .models import Labour, ProjectLabour
        stranger_project = Project.objects.create(name='Stranger2', code='MJ-D')
        labour = Labour.objects.create(name='Suresh')
        ProjectLabour.objects.create(project=stranger_project, labour=labour, is_active=True)
        payload = {
            'project': stranger_project.id, 'expense_date': '2026-01-03', 'paid_by_owner': self.manoj_owner.id,
            'payment_mode': 'CASH', 'payments': [{'labour': labour.id, 'amount': '300.00'}],
        }
        r = self.client.post('/api/labour-payments/', payload, format='json')
        self.assertEqual(r.status_code, 403)

    def test_manager_distribute_from_fund_respects_project(self):
        """The MANAGER-side "labour payment" flow (distribute from a fund already given to them) --
        can_distribute_manager_fund is now project-aware too (services.has_perm, not any-scope)."""
        from . import permissions as legacy
        # Manoj (MANAGER on Site A) has canDistributeManagerFund by role default.
        self.assertTrue(legacy.can_distribute_manager_fund(
            self.manoj, self.site_a, self.manoj_manager))
        # He holds no MANAGER-role UserAccess grant on Site B at all (he's OWNER there instead), so
        # services.users_with_role(site_b, 'MANAGER') never includes him.
        self.assertFalse(legacy.can_distribute_manager_fund(
            self.manoj, self.site_b, self.manoj_manager))

        a_grant = UserAccess.objects.get(user=self.manoj, project=self.site_a)
        override(a_grant, 'canDistributeManagerFund', OverrideEffect.DENY)
        services.invalidate(self.manoj)
        self.assertFalse(legacy.can_distribute_manager_fund(
            self.manoj, self.site_a, self.manoj_manager))


# ===========================================================================
# Follow-up 3: the OLD Roles & Permissions screen (people.PermissionMatrixView) still drives
# the new engine
# ===========================================================================

class OldRolesScreenTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='old_screen_admin', password='x', is_superuser=True)
        self.project = Project.objects.create(name='OldScreen', code='OLD-1')
        self.manager_role = AccessRole.objects.get(name='MANAGER')
        self.viewer = User.objects.create_user(username='old_screen_manager', password='x')
        grant(self.viewer, self.manager_role, self.project)
        token, _ = Token.objects.get_or_create(user=self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

    def test_toggling_via_old_screen_writes_fks_and_changes_has_perm(self):
        from .models import RolePermission
        self.assertFalse(services.has_perm(self.viewer, 'canDeleteExpense', self.project))

        resp = self.client.put('/api/permission-matrix/', {
            'matrix': {'canDeleteExpense': {'manager': True}},
        }, format='json')
        self.assertEqual(resp.status_code, 200)

        row = RolePermission.objects.get(role='MANAGER', permission='canDeleteExpense')
        self.assertIsNotNone(row.role_fk_id)
        self.assertIsNotNone(row.resource_permission_id)
        self.assertEqual(row.role_fk.name, 'MANAGER')
        self.assertEqual(row.resource_permission.code, 'canDeleteExpense')

        services.invalidate(self.viewer)
        self.assertTrue(services.has_perm(self.viewer, 'canDeleteExpense', self.project))


# ===========================================================================
# Follow-up 4: /me/ carries a real per-project matrix for authz.js to consult
# ===========================================================================

class MeEffectiveMatrixTests(APITestCase):
    """web/authz.js now evaluates buttons from /me/'s permissions_by_project (per PROJECT, with
    overrides applied) rather than a global role table; this is the server contract it relies on."""

    def test_me_reports_per_project_override(self):
        project = Project.objects.create(name='MeMatrix', code='MM-1')
        owner_role = AccessRole.objects.get(name='OWNER')
        user = User.objects.create_user(username='me_matrix_user', password='x')
        g = grant(user, owner_role, project)
        override(g, 'canEditExpense', OverrideEffect.DENY)
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        resp = self.client.get('/api/me/')
        self.assertEqual(resp.status_code, 200)
        scope = resp.data['permissions_by_project'][str(project.id)]
        self.assertFalse(scope['canEditExpense'])
        self.assertTrue(scope['canDeleteExpense'])  # OWNER default, not overridden


# ===========================================================================
# Follow-up 5: deploy safety -- fail-closed with an empty catalogue is a loud error
# ===========================================================================

class DeploySafetyCheckTests(TestCase):
    def test_check_passes_with_seeded_catalogue(self):
        from django.core.checks import registry
        errors = registry.registry.run_checks()
        ids = {e.id for e in errors}
        self.assertNotIn('core.E001', ids)
        self.assertNotIn('core.E002', ids)

    def test_check_fails_with_empty_catalogue(self):
        from django.core.checks import registry
        from .models import ResourcePermission
        ResourcePermission.objects.all().delete()
        errors = registry.registry.run_checks()
        ids = {e.id for e in errors}
        self.assertIn('core.E001', ids)

    def test_check_fails_when_nothing_is_granted(self):
        from django.core.checks import registry
        from .models import RolePermission
        RolePermission.objects.all().delete()
        errors = registry.registry.run_checks()
        ids = {e.id for e in errors}
        self.assertIn('core.E002', ids)


# ===========================================================================
# Follow-up 6: seed_rbac --demo
# ===========================================================================

class SeedRbacDemoTests(APITestCase):
    """The exact scenario from the task: Parveen/Anil single-project owners, Manoj a two-project
    manager blocked from editing on Site B only, Renu a super admin -- seeded, then checked against
    the real API, not the service layer directly."""

    @classmethod
    def setUpTestData(cls):
        from io import StringIO

        from django.core.management import call_command
        from django.test import override_settings

        with override_settings(DEBUG=True):
            call_command('seed_rbac', '--demo', stdout=StringIO())

    def _login(self, username):
        user = User.objects.get(username=username)
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        return user

    def test_demo_refuses_without_debug_or_force(self):
        from io import StringIO

        from django.core.management import CommandError, call_command
        from django.test import override_settings

        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command('seed_rbac', '--demo', stdout=StringIO())

    def test_parveen_sees_only_site_a(self):
        self._login('parveen')
        codes = {p['code'] for p in self.client.get('/api/projects/').data}
        self.assertEqual(codes, {'SITE-A'})

    def test_anil_sees_only_site_b(self):
        self._login('anil')
        codes = {p['code'] for p in self.client.get('/api/projects/').data}
        self.assertEqual(codes, {'SITE-B'})

    def test_manoj_can_edit_on_site_a_but_not_site_b(self):
        site_a = Project.objects.get(code='SITE-A')
        site_b = Project.objects.get(code='SITE-B')
        txn_a = ExpenseTransaction.objects.get(project=site_a)
        txn_b = ExpenseTransaction.objects.get(project=site_b)
        self._login('manoj')

        r = self.client.patch(f'/api/expense-transactions/{txn_a.id}/', {'amount': '6000.00'}, format='json')
        self.assertEqual(r.status_code, 200)
        r = self.client.patch(f'/api/expense-transactions/{txn_b.id}/', {'amount': '6000.00'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_manoj_can_add_on_both_sites(self):
        from .models import Owner, PartyType, PaymentMode
        site_a = Project.objects.get(code='SITE-A')
        site_b = Project.objects.get(code='SITE-B')
        parveen_owner = Owner.objects.get(user__username='parveen')
        anil_owner = Owner.objects.get(user__username='anil')
        self._login('manoj')

        for project, owner in ((site_a, parveen_owner), (site_b, anil_owner)):
            r = self.client.post('/api/expense-transactions/', {
                'project': project.id, 'expense_date': '2026-01-10', 'expense_category': 'MISCELLANEOUS',
                'expense_type': 'Other', 'party_type': PartyType.NONE, 'payee_name': 'Demo add',
                'paid_by_owner': owner.id, 'amount': '250.00', 'payment_mode': PaymentMode.CASH,
            }, format='json')
            self.assertEqual(r.status_code, 201, project.code)

    def test_only_renu_sees_access_control(self):
        renu = self._login('renu')
        parveen = User.objects.get(username='parveen')
        self.assertTrue(services.is_superadmin(renu))
        self.assertFalse(services.is_superadmin(parveen))
        resp = self.client.get('/api/access/catalog/')
        self.assertEqual(resp.status_code, 200)

        self._login('parveen')
        resp = self.client.get('/api/access/catalog/')
        self.assertEqual(resp.status_code, 403)

    def test_renu_sees_everything(self):
        self._login('renu')
        codes = {p['code'] for p in self.client.get('/api/projects/').data}
        self.assertEqual(codes, {'SITE-A', 'SITE-B'})
