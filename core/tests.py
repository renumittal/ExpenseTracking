from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import (
    ExpenseCategory,
    Manager,
    ManagerFund,
    Owner,
    PartyType,
    PaymentMode,
    Profile,
    Project,
    ProjectOwner,
    Role,
    Supplier,
)

User = get_user_model()


class RoleBasedAccessTests(APITestCase):
    def setUp(self):
        # Two projects, two owners: owner_a on project A only, owner_b on project B only.
        self.project_a = Project.objects.create(name='Project A', code='A')
        self.project_b = Project.objects.create(name='Project B', code='B')

        self.owner_a_user = User.objects.create_user(username='owner_a', password='pass12345')
        Profile.objects.create(user=self.owner_a_user, role=Role.OWNER)
        self.owner_a = Owner.objects.create(user=self.owner_a_user, name='Owner A')
        ProjectOwner.objects.create(project=self.project_a, owner=self.owner_a)

        self.owner_b_user = User.objects.create_user(username='owner_b', password='pass12345')
        Profile.objects.create(user=self.owner_b_user, role=Role.OWNER)
        self.owner_b = Owner.objects.create(user=self.owner_b_user, name='Owner B')
        ProjectOwner.objects.create(project=self.project_b, owner=self.owner_b)

        self.manager_user = User.objects.create_user(username='manager_1', password='pass12345')
        Profile.objects.create(user=self.manager_user, role=Role.MANAGER)
        self.manager = Manager.objects.create(user=self.manager_user, name='Manager 1')

        self.other_manager_user = User.objects.create_user(username='manager_2', password='pass12345')
        Profile.objects.create(user=self.other_manager_user, role=Role.MANAGER)
        self.other_manager = Manager.objects.create(user=self.other_manager_user, name='Manager 2')

        self.admin_user = User.objects.create_superuser(username='admin_1', password='pass12345', email='a@a.com')
        Profile.objects.create(user=self.admin_user, role=Role.ADMIN)

        self.fund = ManagerFund.objects.create(
            project=self.project_a, manager=self.manager, fund_date='2026-01-01',
            fund_amount='1000.00', given_by_owner=self.owner_a, payment_mode=PaymentMode.CASH,
        )
        self.other_fund = ManagerFund.objects.create(
            project=self.project_b, manager=self.other_manager, fund_date='2026-01-02',
            fund_amount='500.00', given_by_owner=self.owner_b, payment_mode=PaymentMode.CASH,
        )

        Supplier.objects.create(name='Cement Co')

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

    # -- Manager: cannot touch supplier / contractor endpoints ------------

    def test_manager_cannot_access_supplier_endpoint(self):
        self.auth_as(self.manager_user)
        response = self.client.get('/api/suppliers/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_cannot_access_contractor_contract_endpoint(self):
        self.auth_as(self.manager_user)
        response = self.client.get('/api/contractor-contracts/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_cannot_access_expense_transactions(self):
        self.auth_as(self.manager_user)
        response = self.client.get('/api/expense-transactions/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -- Manager: sees only own ManagerFund rows ---------------------------

    def test_manager_sees_only_own_funds(self):
        self.auth_as(self.manager_user)
        response = self.client.get('/api/manager-funds/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row['id'] for row in response.data}
        self.assertEqual(ids, {self.fund.id})
        self.assertNotIn(self.other_fund.id, ids)

    # -- Owner: sees only their assigned projects --------------------------

    def test_owner_sees_only_assigned_projects(self):
        self.auth_as(self.owner_a_user)
        response = self.client.get('/api/projects/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        codes = {row['code'] for row in response.data}
        self.assertEqual(codes, {'A'})
        self.assertNotIn('B', codes)

    def test_owner_cannot_access_manager_funds(self):
        self.auth_as(self.owner_a_user)
        response = self.client.get('/api/manager-funds/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -- Admin: full access --------------------------------------------------

    def test_admin_sees_all_projects_and_funds(self):
        self.auth_as(self.admin_user)
        response = self.client.get('/api/projects/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

        response = self.client.get('/api/manager-funds/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    # -- Auth: unauthenticated is rejected -----------------------------------

    def test_unauthenticated_request_rejected(self):
        response = self.client.get('/api/projects/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_returns_token_and_role(self):
        response = self.client.post('/api/auth/login/', {'username': 'owner_a', 'password': 'pass12345'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('token', response.data)
        self.assertEqual(response.data['role'], Role.OWNER)
