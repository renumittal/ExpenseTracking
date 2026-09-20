from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import (
    Contractor,
    ContractorContract,
    ExpenseCategory,
    ExpenseTransaction,
    Manager,
    ManagerFund,
    ManagerLabourDistribution,
    Owner,
    PartyType,
    PaymentMode,
    Profile,
    Project,
    ProjectLabour,
    ProjectOwner,
    Role,
    Supplier,
    Labour,
    TransactionStatus,
    ZERO,
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


class ExpenseApiTests(APITestCase):
    """Phase 3: expense entry API -- totals, contractor balances, fund limits."""

    def setUp(self):
        self.project = Project.objects.create(name='Project A', code='A')

        self.owner_user = User.objects.create_user(username='owner_a', password='pass12345')
        Profile.objects.create(user=self.owner_user, role=Role.OWNER)
        self.owner = Owner.objects.create(user=self.owner_user, name='Owner A')
        ProjectOwner.objects.create(project=self.project, owner=self.owner)

        self.manager_user = User.objects.create_user(username='manager_1', password='pass12345')
        Profile.objects.create(user=self.manager_user, role=Role.MANAGER)
        self.manager = Manager.objects.create(user=self.manager_user, name='Manager 1')

        self.labour = Labour.objects.create(name='Ramu')
        self.contractor = Contractor.objects.create(name='Build Co')

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

    # -- Manager fund -> distribution -> project totals (no double counting) --

    def test_labour_distribution_reflects_once_in_project_totals(self):
        fund = ManagerFund.objects.create(
            project=self.project, manager=self.manager, fund_date='2026-01-01',
            fund_amount='1000.00', given_by_owner=self.owner, payment_mode=PaymentMode.CASH,
        )

        self.auth_as(self.manager_user)
        response = self.client.post('/api/manager-labour-distributions/', {
            'manager_fund': fund.id,
            'project': self.project.id,
            'manager': self.manager.id,
            'date': '2026-01-02',
            'labour': self.labour.id,
            'amount': '300.00',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNotNone(response.data['expense_transaction'])

        # Exactly one ExpenseTransaction was created for the distribution.
        self.assertEqual(ExpenseTransaction.objects.filter(expense_category=ExpenseCategory.LABOUR).count(), 1)

        self.auth_as(self.owner_user)
        summary = self.client.get(f'/api/projects/{self.project.id}/summary/')
        self.assertEqual(summary.status_code, status.HTTP_200_OK)
        # The fund itself (1000) must not be counted -- only the 300 actually distributed.
        self.assertEqual(Decimal(summary.data['total_expense']), Decimal('300.00'))
        self.assertEqual(Decimal(summary.data['category_breakup'][ExpenseCategory.LABOUR]), Decimal('300.00'))

    def test_distribution_exceeding_fund_balance_is_blocked(self):
        fund = ManagerFund.objects.create(
            project=self.project, manager=self.manager, fund_date='2026-01-01',
            fund_amount='100.00', given_by_owner=self.owner, payment_mode=PaymentMode.CASH,
        )
        self.auth_as(self.manager_user)
        response = self.client.post('/api/manager-labour-distributions/', {
            'manager_fund': fund.id,
            'project': self.project.id,
            'manager': self.manager.id,
            'date': '2026-01-02',
            'labour': self.labour.id,
            'amount': '150.00',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ExpenseTransaction.objects.count(), 0)

    # -- Contractor contract balance across multiple payments -----------------

    def test_contractor_balance_across_multiple_payments(self):
        contract = ContractorContract.objects.create(
            project=self.project, contractor=self.contractor, contract_date='2026-01-01',
            contract_amount='1000.00',
        )
        self.auth_as(self.owner_user)

        for amount in ('300.00', '200.00'):
            response = self.client.post('/api/expense-transactions/', {
                'project': self.project.id,
                'expense_date': '2026-01-05',
                'expense_category': ExpenseCategory.CONTRACTOR,
                'expense_type': 'Contract Payment',
                'party_type': PartyType.CONTRACTOR,
                'contractor_contract': contract.id,
                'paid_by_owner': self.owner.id,
                'amount': amount,
                'payment_mode': PaymentMode.BANK_TRANSFER,
            })
            self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        detail = self.client.get(f'/api/contractor-contracts/{contract.id}/')
        self.assertEqual(Decimal(detail.data['paid_amount']), Decimal('500.00'))
        self.assertEqual(Decimal(detail.data['balance_amount']), Decimal('500.00'))
        self.assertIsNone(detail.data['overpayment_warning'])

        # A cancelled transaction must drop out of the balance calculation.
        last_txn = ExpenseTransaction.objects.filter(contractor_contract=contract).order_by('-id').first()
        cancel_response = self.client.post(
            f'/api/expense-transactions/{last_txn.id}/cancel/', {'remarks': 'Entered twice by mistake'}
        )
        self.assertEqual(cancel_response.status_code, status.HTTP_200_OK)

        detail = self.client.get(f'/api/contractor-contracts/{contract.id}/')
        self.assertEqual(Decimal(detail.data['paid_amount']), Decimal('300.00'))
        self.assertEqual(Decimal(detail.data['balance_amount']), Decimal('700.00'))

    def test_contractor_overpayment_is_warned_not_blocked(self):
        contract = ContractorContract.objects.create(
            project=self.project, contractor=self.contractor, contract_date='2026-01-01',
            contract_amount='100.00',
        )
        self.auth_as(self.owner_user)
        response = self.client.post('/api/expense-transactions/', {
            'project': self.project.id,
            'expense_date': '2026-01-05',
            'expense_category': ExpenseCategory.CONTRACTOR,
            'expense_type': 'Contract Payment',
            'party_type': PartyType.CONTRACTOR,
            'contractor_contract': contract.id,
            'paid_by_owner': self.owner.id,
            'amount': '500.00',
            'payment_mode': PaymentMode.BANK_TRANSFER,
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        detail = self.client.get(f'/api/contractor-contracts/{contract.id}/')
        self.assertIsNotNone(detail.data['overpayment_warning'])

    # -- Category/party_type mismatch is rejected on create --------------------

    def test_category_party_type_mismatch_rejected(self):
        self.auth_as(self.owner_user)
        response = self.client.post('/api/expense-transactions/', {
            'project': self.project.id,
            'expense_date': '2026-01-05',
            'expense_category': ExpenseCategory.LABOUR,
            'expense_type': 'Daily Wages',
            'party_type': PartyType.SUPPLIER,
            'labour': self.labour.id,
            'paid_by_owner': self.owner.id,
            'amount': '100.00',
            'payment_mode': PaymentMode.CASH,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancel_requires_reason(self):
        txn = ExpenseTransaction.objects.create(
            project=self.project, expense_date='2026-01-05', expense_category=ExpenseCategory.LABOUR,
            expense_type='Daily Wages', party_type=PartyType.LABOUR, labour=self.labour,
            paid_by_owner=self.owner, amount='50.00', payment_mode=PaymentMode.CASH,
            created_by=self.owner_user,
        )
        self.auth_as(self.owner_user)
        response = self.client.post(f'/api/expense-transactions/{txn.id}/cancel/', {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        txn.refresh_from_db()
        self.assertEqual(txn.status, TransactionStatus.ACTIVE)


class ReportingTests(APITestCase):
    """Phase 4: reporting layer -- reconciliation across dashboard and reports."""

    def setUp(self):
        self.project = Project.objects.create(name='Project A', code='A')

        self.owner_user = User.objects.create_user(username='owner_a', password='pass12345')
        Profile.objects.create(user=self.owner_user, role=Role.OWNER)
        self.owner = Owner.objects.create(user=self.owner_user, name='Owner A')
        ProjectOwner.objects.create(project=self.project, owner=self.owner)

        self.manager_user = User.objects.create_user(username='manager_1', password='pass12345')
        Profile.objects.create(user=self.manager_user, role=Role.MANAGER)
        self.manager = Manager.objects.create(user=self.manager_user, name='Manager 1')

        self.labour = Labour.objects.create(name='Ramu')
        self.other_labour = Labour.objects.create(name='Shyam')
        self.contractor = Contractor.objects.create(name='Build Co')
        self.supplier = Supplier.objects.create(name='Cement Co')

        # Direct labour payment.
        ExpenseTransaction.objects.create(
            project=self.project, expense_date='2026-01-03', expense_category=ExpenseCategory.LABOUR,
            expense_type='Daily Wages', party_type=PartyType.LABOUR, labour=self.labour,
            paid_by_owner=self.owner, amount='400.00', payment_mode=PaymentMode.CASH,
            created_by=self.owner_user,
        )

        # Contractor payment.
        self.contract = ContractorContract.objects.create(
            project=self.project, contractor=self.contractor, contract_date='2026-01-01',
            contract_amount='2000.00',
        )
        ExpenseTransaction.objects.create(
            project=self.project, expense_date='2026-01-04', expense_category=ExpenseCategory.CONTRACTOR,
            expense_type='Contract Payment', party_type=PartyType.CONTRACTOR, contractor_contract=self.contract,
            paid_by_owner=self.owner, amount='800.00', payment_mode=PaymentMode.BANK_TRANSFER,
            created_by=self.owner_user,
        )

        # Supplier payment.
        ExpenseTransaction.objects.create(
            project=self.project, expense_date='2026-01-05', expense_category=ExpenseCategory.SUPPLIER,
            expense_type='Cement Purchase', party_type=PartyType.SUPPLIER, supplier=self.supplier,
            paid_by_owner=self.owner, amount='600.00', payment_mode=PaymentMode.CHEQUE,
            created_by=self.owner_user,
        )

        # Misc payment.
        ExpenseTransaction.objects.create(
            project=self.project, expense_date='2026-01-06', expense_category=ExpenseCategory.MISCELLANEOUS,
            expense_type='Site Office', party_type=PartyType.NONE, payee_name='Local Vendor',
            paid_by_owner=self.owner, amount='150.00', payment_mode=PaymentMode.CASH,
            created_by=self.owner_user,
        )

        # A cancelled transaction that must not count anywhere.
        cancelled = ExpenseTransaction.objects.create(
            project=self.project, expense_date='2026-01-07', expense_category=ExpenseCategory.MISCELLANEOUS,
            expense_type='Mistake Entry', party_type=PartyType.NONE, payee_name='N/A',
            paid_by_owner=self.owner, amount='9999.00', payment_mode=PaymentMode.CASH,
            created_by=self.owner_user,
        )
        cancelled.cancel(cancelled_by=self.owner_user, reason='Entered by mistake')

        # Manager fund -> labour distribution (must count exactly once, via the
        # auto-created ExpenseTransaction, never via ManagerFund.fund_amount).
        self.fund = ManagerFund.objects.create(
            project=self.project, manager=self.manager, fund_date='2026-01-01',
            fund_amount='1000.00', given_by_owner=self.owner, payment_mode=PaymentMode.CASH,
        )
        ManagerLabourDistribution.objects.create(
            manager_fund=self.fund, project=self.project, manager=self.manager, date='2026-01-08',
            labour=self.other_labour, amount='250.00',
        )

        self.expected_total = Decimal('400.00') + Decimal('800.00') + Decimal('600.00') + Decimal('150.00') + Decimal('250.00')

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

    def test_dashboard_total_equals_sum_of_category_breakup(self):
        self.auth_as(self.owner_user)
        response = self.client.get(f'/api/projects/{self.project.id}/dashboard/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        total = Decimal(response.data['total_expense'])
        self.assertEqual(total, self.expected_total)

        category_breakup = response.data['category_breakup']
        category_sum = sum(Decimal(v) for v in category_breakup.values())
        self.assertEqual(category_sum, total)

        # Labour category must include the manager-distributed 250, exactly once.
        self.assertEqual(Decimal(category_breakup[ExpenseCategory.LABOUR]), Decimal('650.00'))

        contractor_positions = response.data['contractor_positions']
        self.assertEqual(len(contractor_positions), 1)
        self.assertEqual(Decimal(contractor_positions[0]['paid_amount']), Decimal('800.00'))
        self.assertEqual(Decimal(contractor_positions[0]['balance']), Decimal('1200.00'))

        manager_fund_summary = response.data['manager_fund_summary']
        self.assertEqual(len(manager_fund_summary), 1)
        self.assertEqual(Decimal(manager_fund_summary[0]['distributed_amount']), Decimal('250.00'))
        self.assertEqual(Decimal(manager_fund_summary[0]['balance']), Decimal('750.00'))

    def test_category_report_matches_dashboard(self):
        self.auth_as(self.owner_user)
        response = self.client.get('/api/reports/category-expense/', {'project': self.project.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(response.data['grand_total']), self.expected_total)

    def test_contractor_report_reconciles_with_payment_register(self):
        self.auth_as(self.owner_user)
        report = self.client.get('/api/reports/contractor/', {'project': self.project.id})
        self.assertEqual(report.status_code, status.HTTP_200_OK)
        self.assertEqual(len(report.data['contracts']), 1)
        self.assertEqual(Decimal(report.data['contracts'][0]['paid_amount']), Decimal('800.00'))

        register = self.client.get('/api/reports/payment-register/', {
            'project': self.project.id, 'category': ExpenseCategory.CONTRACTOR,
        })
        self.assertEqual(register.status_code, status.HTTP_200_OK)
        register_total = sum(Decimal(row['amount']) for row in register.data['results'])
        self.assertEqual(register_total, Decimal(report.data['contracts'][0]['paid_amount']))

    def test_supplier_report_reconciles_with_payment_register(self):
        self.auth_as(self.owner_user)
        report = self.client.get('/api/reports/supplier/', {'project': self.project.id})
        self.assertEqual(report.status_code, status.HTTP_200_OK)
        self.assertEqual(len(report.data['suppliers']), 1)
        self.assertEqual(Decimal(report.data['suppliers'][0]['total_paid']), Decimal('600.00'))

        register = self.client.get('/api/reports/payment-register/', {
            'project': self.project.id, 'category': ExpenseCategory.SUPPLIER,
        })
        register_total = sum(Decimal(row['amount']) for row in register.data['results'])
        self.assertEqual(register_total, Decimal('600.00'))

    def test_labour_report_reconciles_with_payment_register_and_splits_manager_amount(self):
        self.auth_as(self.owner_user)
        report = self.client.get('/api/reports/labour/', {'project': self.project.id})
        self.assertEqual(report.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(report.data['grand_total']), Decimal('650.00'))

        rows_by_name = {row['labour_name']: row for row in report.data['labour']}
        self.assertEqual(Decimal(rows_by_name['Ramu']['direct_amount']), Decimal('400.00'))
        self.assertEqual(Decimal(rows_by_name['Ramu']['via_manager_amount']), ZERO)
        self.assertEqual(Decimal(rows_by_name['Shyam']['via_manager_amount']), Decimal('250.00'))
        self.assertEqual(Decimal(rows_by_name['Shyam']['direct_amount']), ZERO)

        register = self.client.get('/api/reports/payment-register/', {
            'project': self.project.id, 'category': ExpenseCategory.LABOUR,
        })
        register_total = sum(Decimal(row['amount']) for row in register.data['results'])
        self.assertEqual(register_total, Decimal('650.00'))

    def test_cancelled_transaction_excluded_from_reports(self):
        self.auth_as(self.owner_user)
        report = self.client.get('/api/reports/misc/', {'project': self.project.id})
        self.assertEqual(Decimal(report.data['grand_total']), Decimal('150.00'))

    def test_payment_register_csv_export(self):
        self.auth_as(self.owner_user)
        response = self.client.get('/api/reports/payment-register/', {'project': self.project.id, 'export': 'csv'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.decode()
        # Header + 5 ACTIVE rows for this project (cancelled row excluded).
        self.assertEqual(len(body.strip().split('\n')), 6)

    def test_manager_fund_report_scoped_to_own_funds_for_manager(self):
        other_project = Project.objects.create(name='Project B', code='B')
        other_manager_user = User.objects.create_user(username='manager_2', password='pass12345')
        Profile.objects.create(user=other_manager_user, role=Role.MANAGER)
        other_manager = Manager.objects.create(user=other_manager_user, name='Manager 2')
        ManagerFund.objects.create(
            project=other_project, manager=other_manager, fund_date='2026-01-01',
            fund_amount='500.00', given_by_owner=self.owner, payment_mode=PaymentMode.CASH,
        )

        self.auth_as(self.manager_user)
        response = self.client.get('/api/reports/manager-fund/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        fund_ids = {row['fund_id'] for row in response.data['manager_funds']}
        self.assertEqual(fund_ids, {self.fund.id})

    def test_owner_cannot_see_other_owners_project_dashboard(self):
        other_project = Project.objects.create(name='Project B', code='B')
        other_owner_user = User.objects.create_user(username='owner_b', password='pass12345')
        Profile.objects.create(user=other_owner_user, role=Role.OWNER)
        other_owner = Owner.objects.create(user=other_owner_user, name='Owner B')
        ProjectOwner.objects.create(project=other_project, owner=other_owner)

        self.auth_as(other_owner_user)
        response = self.client.get(f'/api/projects/{self.project.id}/dashboard/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SimpleFrontendSupportTests(APITestCase):
    """Small endpoints the mobile-first web UI relies on."""

    def setUp(self):
        self.project = Project.objects.create(name='Home Build', code='HB')
        self.user = User.objects.create_user(username='own', password='pass12345')
        Profile.objects.create(user=self.user, role=Role.OWNER)
        self.owner = Owner.objects.create(user=self.user, name='Ramesh')
        ProjectOwner.objects.create(project=self.project, owner=self.owner)
        Project.objects.create(name='Other', code='OT')
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + Token.objects.create(user=self.user).key)

    def test_me_returns_owner_id(self):
        data = self.client.get('/api/me/').data
        self.assertEqual(data['owner_id'], self.owner.id)
        self.assertEqual(data['name'], 'Ramesh')

    def test_owner_can_add_supplier_by_name_and_list_labour(self):
        self.assertEqual(self.client.post('/api/suppliers/', {'name': 'Sharma Bricks'}).status_code, 201)
        Labour.objects.create(name='Mohan')
        self.assertEqual(len(self.client.get('/api/labour/').data), 1)

    def test_owner_cannot_post_labour_but_admin_can(self):
        self.assertEqual(self.client.post('/api/labour/', {'name': 'Mohan'}).status_code, 403)
        self.assertEqual(Labour.objects.count(), 0)
        admin = User.objects.create_superuser(username='adm', password='pass12345', email='a@a.com')
        Profile.objects.create(user=admin, role=Role.ADMIN)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + Token.objects.create(user=admin).key)
        self.assertEqual(self.client.post('/api/labour/', {'name': 'Mohan'}).status_code, 201)
        self.assertEqual(Labour.objects.count(), 1)

    def test_labour_cannot_be_edited_or_deleted(self):
        labour = Labour.objects.create(name='Mohan')
        self.assertEqual(self.client.delete(f'/api/labour/{labour.id}/').status_code, 405)

    def test_contract_shows_contractor_name(self):
        contractor = Contractor.objects.create(name='Suresh')
        ContractorContract.objects.create(
            project=self.project, contractor=contractor, contract_date='2026-01-01', contract_amount='1000')
        rows = self.client.get('/api/contractor-contracts/').data
        self.assertEqual(rows[0]['contractor_name'], 'Suresh')


class HealthCheckTests(APITestCase):
    def test_health_needs_no_login_and_no_database(self):
        with self.assertNumQueries(0):
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})


class LabourPaymentEntryTests(APITestCase):
    """Multi-labour payment entry, project-wise active labour, add-labour."""

    def setUp(self):
        self.project = Project.objects.create(name='Plot 150', code='P150')
        self.other = Project.objects.create(name='Other', code='OT')
        self.user = User.objects.create_user(username='own', password='pass12345')
        Profile.objects.create(user=self.user, role=Role.OWNER)
        self.owner = Owner.objects.create(user=self.user, name='Ramesh')
        ProjectOwner.objects.create(project=self.project, owner=self.owner)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + Token.objects.create(user=self.user).key)
        self.rajesh = Labour.objects.create(name='Rajesh Kumar', mobile='9000000001')
        self.suresh = Labour.objects.create(name='Suresh', mobile='9000000002')
        self.amit = Labour.objects.create(name='Amit', mobile='9000000003')
        ProjectLabour.objects.create(project=self.project, labour=self.rajesh, is_active=False)
        for lab in (self.suresh, self.amit):
            ProjectLabour.objects.create(project=self.project, labour=lab)
        ProjectLabour.objects.create(project=self.other, labour=self.rajesh)  # active elsewhere

    def pay(self, lines, **extra):
        body = {
            'project': self.project.id, 'expense_date': '2026-09-19', 'paid_by_owner': self.owner.id,
            'payment_mode': 'CASH', 'payments': lines, **extra,
        }
        return self.client.post('/api/labour-payments/', body, format='json')

    def test_multi_labour_entry_stores_each_amount_against_its_labour(self):
        r = self.pay([{'labour': self.suresh.id, 'amount': '2000'}, {'labour': self.amit.id, 'amount': '500.50'}],
                     remarks='daily')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Decimal(str(r.data['total'])), Decimal('2500.50'))
        rows = ExpenseTransaction.objects.filter(payment_batch=r.data['payment_batch'])
        self.assertEqual(rows.count(), 2)
        self.assertEqual(rows.get(labour=self.suresh).amount, Decimal('2000.00'))
        self.assertEqual(rows.get(labour=self.amit).amount, Decimal('500.50'))
        self.assertTrue(all(t.expense_category == 'LABOUR' and t.party_type == 'LABOUR' and t.remarks == 'daily' for t in rows))

    def test_invalid_amounts_rejected_and_nothing_saved(self):
        for bad in ('0', '-5', '', None, 'abc'):
            r = self.pay([{'labour': self.suresh.id, 'amount': '100'}, {'labour': self.amit.id, 'amount': bad}])
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(ExpenseTransaction.objects.count(), 0)

    def test_duplicate_labour_in_one_entry_rejected(self):
        r = self.pay([{'labour': self.suresh.id, 'amount': '100'}, {'labour': self.suresh.id, 'amount': '200'}])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ExpenseTransaction.objects.count(), 0)

    def test_labour_must_belong_to_project_and_entry_needs_a_line(self):
        outsider = Labour.objects.create(name='Outsider', mobile='9000000009')
        self.assertEqual(self.pay([{'labour': outsider.id, 'amount': '100'}]).status_code, 400)
        self.assertEqual(self.pay([]).status_code, 400)

    def test_owner_cannot_pay_on_unlinked_project(self):
        ProjectLabour.objects.create(project=self.other, labour=self.suresh)
        r = self.client.post('/api/labour-payments/', {
            'project': self.other.id, 'expense_date': '2026-09-19', 'paid_by_owner': self.owner.id,
            'payment_mode': 'CASH', 'payments': [{'labour': self.suresh.id, 'amount': '100'}],
        }, format='json')
        self.assertEqual(r.status_code, 403)

    def test_default_list_is_active_only_and_inactive_is_separate(self):
        active = self.client.get(f'/api/project-labour/?project={self.project.id}').data
        self.assertEqual({r['name'] for r in active}, {'Suresh', 'Amit'})
        inactive = self.client.get(f'/api/project-labour/?project={self.project.id}&status=inactive').data
        self.assertEqual([r['name'] for r in inactive], ['Rajesh Kumar'])
        self.assertEqual(self.client.get(f'/api/project-labour/?project={self.other.id}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/project-labour/?project={self.other.id}').data, [])  # not my project

    def test_deactivate_keeps_master_history_and_reports(self):
        self.pay([{'labour': self.suresh.id, 'amount': '1000'}])
        link = ProjectLabour.objects.get(project=self.project, labour=self.suresh)
        r = self.client.post(f'/api/project-labour/{link.id}/set-active/', {'is_active': False}, format='json')
        self.assertEqual(r.status_code, 200)
        names = [x['name'] for x in self.client.get(f'/api/project-labour/?project={self.project.id}').data]
        self.assertNotIn('Suresh', names)
        self.assertTrue(Labour.objects.filter(id=self.suresh.id).exists())
        rep = self.client.get(f'/api/reports/labour/?project={self.project.id}').data
        self.assertEqual([(x['labour_name'], Decimal(str(x['total_paid']))) for x in rep['labour']],
                         [('Suresh', Decimal('1000.00'))])

    def test_reactivate_reuses_same_labour_and_paying_inactive_reactivates(self):
        n = Labour.objects.count()
        link = ProjectLabour.objects.get(project=self.project, labour=self.rajesh)
        self.client.post(f'/api/project-labour/{link.id}/set-active/', {'is_active': True}, format='json')
        self.assertTrue(ProjectLabour.objects.get(pk=link.pk).is_active)
        link.is_active = False
        link.save()
        self.assertEqual(self.pay([{'labour': self.rajesh.id, 'amount': '900'}], include_inactive=True).status_code, 201)
        self.assertTrue(ProjectLabour.objects.get(pk=link.pk).is_active)
        self.assertEqual(Labour.objects.count(), n)

    def add(self, **kw):
        body = {'project': self.project.id, 'name': 'Mahesh', 'mobile': '9812345678', **kw}
        return self.client.post('/api/project-labour/', body, format='json')

    def test_add_labour_creates_master_and_active_link(self):
        r = self.add(type='Mistri', remarks='new')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertFalse(r.data['reused'])
        lab = Labour.objects.get(name='Mahesh')
        self.assertEqual((lab.mobile, lab.type, lab.remarks), ('9812345678', 'Mistri', 'new'))
        self.assertTrue(ProjectLabour.objects.get(project=self.project, labour=lab).is_active)
        self.assertIn('Mahesh', [x['name'] for x in self.client.get(f'/api/project-labour/?project={self.project.id}').data])

    def test_add_labour_requires_name_and_mobile(self):
        self.assertEqual(self.add(name='  ').status_code, 400)
        self.assertEqual(self.add(mobile='').status_code, 400)
        self.assertEqual(self.add(mobile='123').status_code, 400)
        self.assertFalse(Labour.objects.filter(name='Mahesh').exists())

    def test_add_labour_reuses_existing_person_and_reactivates(self):
        n = Labour.objects.count()
        r = self.add(name='  rajesh   kumar ', mobile='90000 00001')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['reused'])
        self.assertEqual(r.data['labour'], self.rajesh.id)
        self.assertEqual(Labour.objects.count(), n)
        self.assertTrue(ProjectLabour.objects.get(project=self.project, labour=self.rajesh).is_active)

    def test_old_single_labour_transaction_api_and_reports_unchanged(self):
        r = self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': '2026-09-01', 'expense_category': 'LABOUR',
            'expense_type': 'Labour Payment', 'party_type': 'LABOUR', 'labour': self.suresh.id,
            'paid_by_owner': self.owner.id, 'amount': '750.00', 'payment_mode': 'CASH'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIsNone(ExpenseTransaction.objects.get(pk=r.data['id']).payment_batch)
        reg = self.client.get(f'/api/reports/payment-register/?project={self.project.id}').data
        self.assertEqual(len(reg.get('results', reg)), 1)


class LabourPaymentRulesTests(APITestCase):
    """Inactive opt-in, paid_by_owner, last paid, atomicity."""

    def setUp(self):
        self.project = Project.objects.create(name='Plot 150', code='P150')
        self.user = User.objects.create_user(username='own', password='pass12345')
        Profile.objects.create(user=self.user, role=Role.OWNER)
        self.owner = Owner.objects.create(user=self.user, name='Ramesh')
        ProjectOwner.objects.create(project=self.project, owner=self.owner)
        other_user = User.objects.create_user(username='own2', password='pass12345')
        Profile.objects.create(user=other_user, role=Role.OWNER)
        self.other_owner = Owner.objects.create(user=other_user, name='Someone Else')
        ProjectOwner.objects.create(project=self.project, owner=self.other_owner)  # same project, other person
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + Token.objects.create(user=self.user).key)
        self.active = Labour.objects.create(name='Suresh', mobile='9000000002')
        self.gone = Labour.objects.create(name='Rajesh Kumar', mobile='9000000001')
        ProjectLabour.objects.create(project=self.project, labour=self.active)
        ProjectLabour.objects.create(project=self.project, labour=self.gone, is_active=False)

    def pay(self, lines, owner=None, **extra):
        return self.client.post('/api/labour-payments/', {
            'project': self.project.id, 'expense_date': '2026-09-19', 'paid_by_owner': (owner or self.owner).id,
            'payment_mode': 'CASH', 'payments': lines, **extra}, format='json')

    def test_inactive_without_flag_rejected_and_nothing_saved(self):
        r = self.pay([{'labour': self.active.id, 'amount': '100'}, {'labour': self.gone.id, 'amount': '200'}])
        self.assertEqual(r.status_code, 400)
        self.assertIn('inactive', str(r.data))
        self.assertEqual(ExpenseTransaction.objects.count(), 0)
        self.assertFalse(ProjectLabour.objects.get(labour=self.gone).is_active)

    def test_inactive_with_flag_accepted_same_master_id_and_reactivated(self):
        masters = Labour.objects.count()
        r = self.pay([{'labour': self.gone.id, 'amount': '200'}], include_inactive=True)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(ExpenseTransaction.objects.get().labour_id, self.gone.id)
        self.assertTrue(ProjectLabour.objects.get(labour=self.gone).is_active)
        self.assertEqual(Labour.objects.count(), masters)

    def test_active_labour_needs_no_flag(self):
        self.assertEqual(self.pay([{'labour': self.active.id, 'amount': '100'}]).status_code, 201)

    def test_paid_by_own_owner_ok_and_other_owner_rejected(self):
        self.assertEqual(self.pay([{'labour': self.active.id, 'amount': '100'}]).status_code, 201)
        r = self.pay([{'labour': self.active.id, 'amount': '100'}], owner=self.other_owner)
        self.assertEqual(r.status_code, 400)
        self.assertIn('paid_by_owner', r.data)
        self.assertEqual(ExpenseTransaction.objects.count(), 1)

    def test_admin_may_record_for_any_owner(self):
        admin = User.objects.create_superuser(username='adm', password='pass12345', email='a@a.com')
        Profile.objects.create(user=admin, role=Role.ADMIN)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + Token.objects.create(user=admin).key)
        self.assertEqual(self.pay([{'labour': self.active.id, 'amount': '100'}], owner=self.other_owner).status_code, 201)

    def test_unauthenticated_rejected(self):
        self.client.credentials()
        self.assertIn(self.pay([{'labour': self.active.id, 'amount': '100'}]).status_code, (401, 403))
        self.assertEqual(ExpenseTransaction.objects.count(), 0)

    def test_amount_error_message_is_readable_and_no_partial_save(self):
        r = self.pay([{'labour': self.active.id, 'amount': '100'}, {'labour': self.gone.id, 'amount': '0'}], include_inactive=True)
        self.assertEqual(r.status_code, 400)
        self.assertIn('greater than zero', str(r.data))
        self.assertEqual(ExpenseTransaction.objects.count(), 0)

    def test_batch_shared_across_a_multi_labour_submission(self):
        r = self.pay([{'labour': self.active.id, 'amount': '100'}, {'labour': self.gone.id, 'amount': '200'}], include_inactive=True)
        batches = set(ExpenseTransaction.objects.values_list('payment_batch', flat=True))
        self.assertEqual(len(batches), 1)
        self.assertEqual(str(batches.pop()), r.data['payment_batch'])

    def test_last_paid_ignores_cancelled_and_other_projects_and_orders_recent_first(self):
        other = Project.objects.create(name='Other', code='OT')
        def txn(project, labour, day, status='ACTIVE'):
            return ExpenseTransaction.objects.create(
                project=project, expense_date=day, expense_category='LABOUR', expense_type='x', party_type='LABOUR',
                labour=labour, paid_by_owner=self.owner, amount='10', payment_mode='CASH', status=status,
                created_by=self.user)
        txn(self.project, self.active, '2026-09-10')
        txn(self.project, self.active, '2026-09-18')
        txn(self.project, self.active, '2026-09-25', status='CANCELLED')   # must be ignored
        txn(other, self.active, '2026-09-28')                               # other project: ignored
        older = Labour.objects.create(name='Amit', mobile='9000000003')
        ProjectLabour.objects.create(project=self.project, labour=older)
        txn(self.project, older, '2026-08-01')
        never = Labour.objects.create(name='Aaa Never', mobile='9000000004')
        ProjectLabour.objects.create(project=self.project, labour=never)
        rows = self.client.get(f'/api/project-labour/?project={self.project.id}').data
        self.assertEqual([(r['name'], r['last_paid']) for r in rows],
                         [('Suresh', '2026-09-18'), ('Amit', '2026-08-01'), ('Aaa Never', None)])

    def test_existing_single_labour_api_and_reports_still_work(self):
        r = self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': '2026-09-01', 'expense_category': 'LABOUR',
            'expense_type': 'Labour Payment', 'party_type': 'LABOUR', 'labour': self.active.id,
            'paid_by_owner': self.owner.id, 'amount': '750.00', 'payment_mode': 'CASH'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        rep = self.client.get(f'/api/reports/labour/?project={self.project.id}').data
        self.assertEqual([(x['labour_name'], Decimal(str(x['total_paid']))) for x in rep['labour']], [('Suresh', Decimal('750.00'))])
        dash = self.client.get(f'/api/projects/{self.project.id}/dashboard/').data
        self.assertEqual(Decimal(str(dash['total_expense'])), Decimal('750.00'))


class AddLabourDuplicateTests(APITestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Plot 150', code='P150')
        self.user = User.objects.create_user(username='own', password='pass12345')
        Profile.objects.create(user=self.user, role=Role.OWNER)
        owner = Owner.objects.create(user=self.user, name='Ramesh')
        ProjectOwner.objects.create(project=self.project, owner=owner)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + Token.objects.create(user=self.user).key)
        self.rajesh = Labour.objects.create(name='Rajesh Kumar', mobile='98765 43210')
        self.legacy = Labour.objects.create(name='Vijay Singh', mobile='')

    def add(self, **kw):
        return self.client.post('/api/project-labour/', {
            'project': self.project.id, 'name': 'Mahesh', 'mobile': '9812345678', **kw}, format='json')

    def test_same_mobile_reuses_even_with_different_spelling_and_format(self):
        n = Labour.objects.count()
        r = self.add(name='Rajesh K.', mobile='+91 98765-43210')
        self.assertEqual(r.status_code, 201)     # new link, reused master
        self.assertTrue(r.data['reused'])
        self.assertEqual(r.data['labour'], self.rajesh.id)
        self.assertEqual(Labour.objects.count(), n)
        self.assertEqual(Labour.objects.get(pk=self.rajesh.id).name, 'Rajesh Kumar')   # not renamed

    def test_same_normalized_name_with_blank_mobile_reuses_and_fills_mobile(self):
        n = Labour.objects.count()
        r = self.add(name='  vijay    SINGH ', mobile='9111111111')
        self.assertEqual(r.data['labour'], self.legacy.id)
        self.assertEqual(Labour.objects.count(), n)
        self.assertEqual(Labour.objects.get(pk=self.legacy.id).mobile, '9111111111')

    def test_same_name_different_mobile_returns_409_candidates_and_creates_nothing(self):
        ProjectLabour.objects.create(project=self.project, labour=self.rajesh)
        ExpenseTransaction.objects.create(
            project=self.project, expense_date='2026-09-18', expense_category='LABOUR', expense_type='x',
            party_type='LABOUR', labour=self.rajesh, paid_by_owner=Owner.objects.get(), amount='10',
            payment_mode='CASH', created_by=self.user)
        n, links = Labour.objects.count(), ProjectLabour.objects.count()
        r = self.add(name='rajesh  kumar', mobile='9000000000')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['code'], 'possible_duplicate')
        self.assertEqual(r.data['candidates'], [{
            'labour': self.rajesh.id, 'name': 'Rajesh Kumar', 'mobile_masked': '******3210', 'last_paid': date(2026, 9, 18)}])
        self.assertNotIn('9876543210', str(r.data))
        self.assertEqual((Labour.objects.count(), ProjectLabour.objects.count()), (n, links))

    def test_use_this_labour_reuses_same_id_and_activates(self):
        ProjectLabour.objects.create(project=self.project, labour=self.rajesh, is_active=False)
        n = Labour.objects.count()
        r = self.add(name='Rajesh Kumar', mobile='9000000000', use_labour=self.rajesh.id)
        self.assertIn(r.status_code, (200, 201))
        self.assertEqual(r.data['labour'], self.rajesh.id)
        self.assertTrue(r.data['is_active'])
        self.assertEqual(Labour.objects.count(), n)
        self.assertEqual(Labour.objects.get(pk=self.rajesh.id).mobile, '98765 43210')  # existing mobile untouched

    def test_use_labour_must_be_a_real_candidate(self):
        stranger = Labour.objects.create(name='Unrelated', mobile='9333333333')
        self.assertEqual(self.add(name='Rajesh Kumar', mobile='9000000000', use_labour=stranger.id).status_code, 400)

    def test_different_person_creates_new_master_once(self):
        n = Labour.objects.count()
        r = self.add(name='Rajesh Kumar', mobile='9000000000', confirm_new=True)
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.data['reused'])
        self.assertNotEqual(r.data['labour'], self.rajesh.id)
        self.assertEqual(Labour.objects.count(), n + 1)
        again = self.add(name='Rajesh Kumar', mobile='9000000000')      # now the same mobile -> reuse
        self.assertEqual(again.data['labour'], r.data['labour'])
        self.assertEqual(Labour.objects.count(), n + 1)

    def test_brand_new_person_is_one_step(self):
        r = self.add()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['last_paid'], None)


class ContractorFlowTests(APITestCase):
    """Contractor master (de-duplicated), several contracts per project, project-scoped payments."""

    def setUp(self):
        self.project = Project.objects.create(name='Plot 150', code='P150')
        self.other_project = Project.objects.create(name='Plot 200', code='P200')
        self.user = User.objects.create_user(username='own', password='pass12345')
        Profile.objects.create(user=self.user, role=Role.OWNER)
        self.owner = Owner.objects.create(user=self.user, name='Ramesh')
        ProjectOwner.objects.create(project=self.project, owner=self.owner)
        ProjectOwner.objects.create(project=self.other_project, owner=self.owner)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + Token.objects.create(user=self.user).key)
        self.raj = Contractor.objects.create(name='Raj Construction', mobile='98765 43210')

    def add_contractor(self, **kw):
        return self.client.post('/api/contractors/', {'name': 'Suresh Electric', 'mobile': '9812345678', **kw}, format='json')

    def add_contract(self, contractor=None, project=None, **kw):
        return self.client.post('/api/contractor-contracts/', {
            'project': (project or self.project).id, 'contractor': (contractor or self.raj).id,
            'work_description': 'RCC + Structure', 'contract_amount': '500000.00',
            'contract_date': '2026-09-01', **kw}, format='json')

    def pay(self, contract, amount, project=None, **kw):
        return self.client.post('/api/expense-transactions/', {
            'project': (project or self.project).id, 'expense_date': '2026-09-05',
            'expense_category': ExpenseCategory.CONTRACTOR, 'expense_type': 'Contractor Payment',
            'party_type': PartyType.CONTRACTOR, 'contractor_contract': contract.id,
            'paid_by_owner': self.owner.id, 'amount': amount, 'payment_mode': PaymentMode.CASH, **kw}, format='json')

    def contract_row(self, contract):
        return self.client.get(f'/api/contractor-contracts/{contract.id}/').data

    # 1-2. Contractor creation and duplicate handling
    def test_contractor_is_created(self):
        r = self.add_contractor(work_type='Electrical', remarks='ok')
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.data['reused'])
        c = Contractor.objects.get(pk=r.data['id'])
        self.assertEqual((c.name, c.mobile, c.work_type), ('Suresh Electric', '9812345678', 'Electrical'))

    def test_contractor_needs_name_and_valid_mobile(self):
        self.assertEqual(self.add_contractor(name='  ').status_code, 400)
        self.assertEqual(self.add_contractor(mobile='12345').status_code, 400)
        self.assertEqual(self.add_contractor(mobile='').status_code, 400)

    def test_same_mobile_reuses_contractor(self):
        n = Contractor.objects.count()
        r = self.add_contractor(name='Raj C.', mobile='+91 98765-43210')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['reused'])
        self.assertEqual(r.data['id'], self.raj.id)
        self.assertEqual(Contractor.objects.count(), n)
        self.assertEqual(Contractor.objects.get(pk=self.raj.id).name, 'Raj Construction')

    def test_same_name_without_mobile_reuses_and_fills_mobile(self):
        legacy = Contractor.objects.create(name='Old Timer', mobile='')
        n = Contractor.objects.count()
        r = self.add_contractor(name=' old   TIMER ', mobile='9111111111')
        self.assertEqual(r.data['id'], legacy.id)
        self.assertEqual(Contractor.objects.count(), n)
        self.assertEqual(Contractor.objects.get(pk=legacy.id).mobile, '9111111111')

    def test_same_name_different_mobile_asks_then_honours_the_answer(self):
        n = Contractor.objects.count()
        r = self.add_contractor(name='raj construction', mobile='9000000001')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['code'], 'possible_duplicate')
        self.assertEqual(r.data['candidates'][0]['contractor'], self.raj.id)
        self.assertEqual(r.data['candidates'][0]['mobile_masked'], '******3210')
        self.assertEqual(Contractor.objects.count(), n)

        used = self.add_contractor(name='raj construction', mobile='9000000001', use_contractor=self.raj.id)
        self.assertEqual(used.data['id'], self.raj.id)
        self.assertEqual(Contractor.objects.count(), n)

        different = self.add_contractor(name='raj construction', mobile='9000000001', confirm_new=True)
        self.assertEqual(different.status_code, 201)
        self.assertNotEqual(different.data['id'], self.raj.id)
        self.assertEqual(Contractor.objects.count(), n + 1)

    def test_use_contractor_must_be_a_real_candidate(self):
        other = Contractor.objects.create(name='Unrelated', mobile='9222222222')
        r = self.add_contractor(name='Raj Construction', mobile='9000000001', use_contractor=other.id)
        self.assertEqual(r.status_code, 400)

    def test_manager_cannot_use_contractor_endpoint(self):
        mgr = User.objects.create_user(username='mgr', password='pass12345')
        Profile.objects.create(user=mgr, role=Role.MANAGER)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + Token.objects.create(user=mgr).key)
        self.assertEqual(self.add_contractor().status_code, 403)
        self.assertEqual(self.client.get('/api/contractors/').status_code, 403)

    # 3-4. Several contracts on one project
    def test_same_contractor_can_have_two_contracts_on_one_project(self):
        a = self.add_contract(work_description='RCC + Structure', contract_amount='500000')
        b = self.add_contract(work_description='Plaster', contract_amount='120000')
        self.assertEqual((a.status_code, b.status_code), (201, 201))
        self.assertEqual(ContractorContract.objects.filter(project=self.project, contractor=self.raj).count(), 2)
        self.assertEqual(a.data['work_description'], 'RCC + Structure')

    def test_two_contractors_on_the_same_project(self):
        suresh = Contractor.objects.create(name='Suresh', mobile='9812345678')
        self.assertEqual(self.add_contract().status_code, 201)
        self.assertEqual(self.add_contract(contractor=suresh, work_description='Wiring').status_code, 201)
        rows = self.client.get('/api/contractor-contracts/', {'project': self.project.id}).data
        self.assertEqual({r['contractor_name'] for r in rows}, {'Raj Construction', 'Suresh'})

    # 5. Contract validation
    def test_contract_amount_must_be_positive(self):
        for bad in ('0', '0.00', '-5'):
            self.assertEqual(self.add_contract(contract_amount=bad).status_code, 400, bad)
        self.assertEqual(ContractorContract.objects.count(), 0)

    def test_work_description_is_required_on_create(self):
        self.assertEqual(self.add_contract(work_description='').status_code, 400)
        self.assertEqual(self.add_contract(work_description='   ').status_code, 400)

    # 6. ?project= filter
    def test_project_filter_returns_only_that_projects_contracts(self):
        mine = ContractorContract.objects.create(project=self.project, contractor=self.raj, contract_date='2026-09-01', contract_amount='10', work_description='A')
        theirs = ContractorContract.objects.create(project=self.other_project, contractor=self.raj, contract_date='2026-09-01', contract_amount='20', work_description='B')
        ids = lambda **q: {r['id'] for r in self.client.get('/api/contractor-contracts/', q).data}
        self.assertEqual(ids(project=self.project.id), {mine.id})
        self.assertEqual(ids(project=self.other_project.id), {theirs.id})
        self.assertEqual(ids(), {mine.id, theirs.id})

    def test_owner_cannot_create_contract_on_a_project_they_are_not_linked_to(self):
        stranger_project = Project.objects.create(name='Not mine', code='NM')
        self.assertEqual(self.add_contract(project=stranger_project).status_code, 403)

    # 7-8. Payment project rule
    def test_payment_against_contract_of_the_same_project_succeeds(self):
        contract = ContractorContract.objects.create(project=self.project, contractor=self.raj, contract_date='2026-09-01', contract_amount='1000')
        r = self.pay(contract, '250.00')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['contractor_contract'], contract.id)

    def test_payment_against_another_projects_contract_is_rejected(self):
        contract = ContractorContract.objects.create(project=self.other_project, contractor=self.raj, contract_date='2026-09-01', contract_amount='1000')
        r = self.pay(contract, '250.00', project=self.project)
        self.assertEqual(r.status_code, 400)
        self.assertIn('contractor_contract', r.data)
        self.assertEqual(ExpenseTransaction.objects.count(), 0)

    # 9-12. Accounting
    def test_multiple_payments_cancel_balance_and_overpayment(self):
        contract = ContractorContract.objects.create(project=self.project, contractor=self.raj, contract_date='2026-09-01', contract_amount='1000')
        for amount in ('300', '200'):
            self.assertEqual(self.pay(contract, amount).status_code, 201)
        row = self.contract_row(contract)
        self.assertEqual(Decimal(row['paid_amount']), Decimal('500'))
        self.assertEqual(Decimal(row['balance_amount']), Decimal('500'))
        self.assertIsNone(row['overpayment_warning'])

        last = ExpenseTransaction.objects.filter(contractor_contract=contract).order_by('-id').first()
        self.assertEqual(self.client.post(f'/api/expense-transactions/{last.id}/cancel/', {'remarks': 'typo'}).status_code, 200)
        row = self.contract_row(contract)
        self.assertEqual(Decimal(row['paid_amount']), Decimal('300'))
        self.assertEqual(Decimal(row['balance_amount']), Decimal('700'))

        # Overpaying is still allowed (warning only).
        self.assertEqual(self.pay(contract, '900').status_code, 201)
        row = self.contract_row(contract)
        self.assertEqual(Decimal(row['balance_amount']), Decimal('-200'))
        self.assertIsNotNone(row['overpayment_warning'])

    def test_two_contracts_of_one_contractor_keep_separate_totals_in_reports(self):
        a = ContractorContract.objects.create(project=self.project, contractor=self.raj, contract_date='2026-09-01', contract_amount='1000', work_description='RCC')
        b = ContractorContract.objects.create(project=self.project, contractor=self.raj, contract_date='2026-09-02', contract_amount='400', work_description='Plaster')
        self.pay(a, '600')
        self.pay(b, '100')
        report = self.client.get('/api/reports/contractor/', {'project': self.project.id}).data
        by_work = {r['work_description']: r for r in report['contracts']}
        self.assertEqual(Decimal(by_work['RCC']['balance']), Decimal('400'))
        self.assertEqual(Decimal(by_work['Plaster']['balance']), Decimal('300'))
        self.assertEqual(Decimal(report['contractor_totals'][0]['paid_amount']), Decimal('700'))
        dash = self.client.get(f'/api/projects/{self.project.id}/dashboard/').data
        self.assertEqual({p['work_description'] for p in dash['contractor_positions']}, {'RCC', 'Plaster'})

    # 13-14. The project rule is contractor-only
    def test_project_rule_does_not_affect_labour_supplier_or_misc(self):
        base = {'project': self.project.id, 'expense_date': '2026-09-05', 'paid_by_owner': self.owner.id,
                'amount': '10.00', 'payment_mode': PaymentMode.CASH, 'expense_type': 'x'}
        labour = Labour.objects.create(name='Mohan')
        supplier = Supplier.objects.create(name='Sharma Bricks')
        cases = [
            {'expense_category': 'LABOUR', 'party_type': 'LABOUR', 'labour': labour.id},
            {'expense_category': 'SUPPLIER', 'party_type': 'SUPPLIER', 'supplier': supplier.id},
            {'expense_category': 'MISCELLANEOUS', 'party_type': 'NONE', 'payee_name': 'Tea'},
        ]
        for extra in cases:
            self.assertEqual(self.client.post('/api/expense-transactions/', {**base, **extra}, format='json').status_code, 201, extra)


class SupplierFlowTests(APITestCase):
    """Supplier: no contract. De-duplicated master; every purchase is its own expense row."""

    def setUp(self):
        self.project = Project.objects.create(name='Plot 150', code='P150')
        self.user = User.objects.create_user(username='own', password='pass12345')
        Profile.objects.create(user=self.user, role=Role.OWNER)
        self.owner = Owner.objects.create(user=self.user, name='Ramesh')
        ProjectOwner.objects.create(project=self.project, owner=self.owner)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + Token.objects.create(user=self.user).key)
        self.sharma = Supplier.objects.create(name='Sharma Building Material', mobile='98765 43210')

    def add(self, **kw):
        return self.client.post('/api/suppliers/add/', {'name': 'Gupta Electricals', 'mobile': '9812345678', **kw}, format='json')

    def buy(self, supplier, amount, material, **kw):
        return self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': '2026-09-20', 'expense_category': ExpenseCategory.SUPPLIER,
            'expense_type': 'Supplier Payment', 'party_type': PartyType.SUPPLIER, 'supplier': supplier.id,
            'description': material, 'paid_by_owner': self.owner.id, 'amount': amount,
            'payment_mode': PaymentMode.UPI, **kw}, format='json')

    def test_supplier_is_created_with_type(self):
        r = self.add(supplier_type='Electrical Material', remarks='ok')
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.data['reused'])
        s = Supplier.objects.get(pk=r.data['id'])
        self.assertEqual((s.name, s.mobile, s.supplier_type), ('Gupta Electricals', '9812345678', 'Electrical Material'))

    def test_supplier_type_is_optional(self):
        self.assertEqual(self.add().status_code, 201)

    def test_name_and_valid_mobile_required(self):
        self.assertEqual(self.add(name='  ').status_code, 400)
        self.assertEqual(self.add(mobile='12345').status_code, 400)
        self.assertEqual(self.add(mobile='').status_code, 400)

    def test_same_mobile_reuses_supplier(self):
        n = Supplier.objects.count()
        r = self.add(name='Sharma B.M.', mobile='+91 98765-43210')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['reused'])
        self.assertEqual(r.data['id'], self.sharma.id)
        self.assertEqual(Supplier.objects.count(), n)

    def test_same_name_without_mobile_reuses_and_fills_mobile(self):
        legacy = Supplier.objects.create(name='Old Bricks', mobile='')
        n = Supplier.objects.count()
        r = self.add(name=' old   BRICKS ', mobile='9111111111')
        self.assertEqual(r.data['id'], legacy.id)
        self.assertEqual(Supplier.objects.count(), n)
        self.assertEqual(Supplier.objects.get(pk=legacy.id).mobile, '9111111111')

    def test_same_name_different_mobile_asks_then_honours_the_answer(self):
        n = Supplier.objects.count()
        r = self.add(name='sharma building material', mobile='9000000001')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['code'], 'possible_duplicate')
        self.assertEqual(r.data['candidates'][0]['supplier'], self.sharma.id)
        self.assertEqual(r.data['candidates'][0]['mobile_masked'], '******3210')
        self.assertEqual(Supplier.objects.count(), n)

        used = self.add(name='sharma building material', mobile='9000000001', use_supplier=self.sharma.id)
        self.assertEqual(used.data['id'], self.sharma.id)
        self.assertEqual(Supplier.objects.count(), n)

        different = self.add(name='sharma building material', mobile='9000000001', confirm_new=True)
        self.assertEqual(different.status_code, 201)
        self.assertNotEqual(different.data['id'], self.sharma.id)
        self.assertEqual(Supplier.objects.count(), n + 1)

    def test_use_supplier_must_be_a_real_candidate(self):
        other = Supplier.objects.create(name='Unrelated', mobile='9222222222')
        r = self.add(name='Sharma Building Material', mobile='9000000001', use_supplier=other.id)
        self.assertEqual(r.status_code, 400)

    def test_manager_cannot_add_supplier(self):
        mgr = User.objects.create_user(username='mgr', password='pass12345')
        Profile.objects.create(user=mgr, role=Role.MANAGER)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + Token.objects.create(user=mgr).key)
        self.assertEqual(self.add().status_code, 403)

    def test_each_purchase_is_a_separate_transaction_with_its_material(self):
        for material, amount in (('Cement', '25000'), ('Sand', '18000'), ('Cement', '12000')):
            r = self.buy(self.sharma, amount, material)
            self.assertEqual(r.status_code, 201, r.data)
            self.assertEqual(r.data['description'], material)
            self.assertEqual(r.data['party_type'], PartyType.SUPPLIER)
        rows = ExpenseTransaction.objects.filter(supplier=self.sharma)
        self.assertEqual(rows.count(), 3)
        self.assertEqual(sorted(r.description for r in rows), ['Cement', 'Cement', 'Sand'])
        report = self.client.get('/api/reports/supplier/', {'project': self.project.id}).data
        self.assertEqual(Decimal(report['suppliers'][0]['total_paid']), Decimal('55000'))
        self.assertEqual(Decimal(report['grand_total']), Decimal('55000'))

    def test_supplier_has_no_contract_fields(self):
        row = self.client.get('/api/suppliers/').data[0]
        self.assertFalse({'contract_amount', 'paid_amount', 'balance', 'balance_amount'} & set(row))
        r = self.buy(self.sharma, '100', 'Cement')
        self.assertIsNone(r.data['contractor_contract'])

    def test_legacy_add_by_name_still_works(self):
        self.assertEqual(self.client.post('/api/suppliers/', {'name': 'Quick Add'}).status_code, 201)


class BackfillMigrationTests(TransactionTestCase):
    """Historical payments -> ProjectLabour rows, using the real 0001 -> 0002 migration."""

    def test_backfill_uses_payment_history_not_payment_age(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('core', '0001_initial')])
        old = executor.loader.project_state([('core', '0001_initial')]).apps
        user = old.get_model('auth', 'User').objects.create(username='u')
        project = old.get_model('core', 'Project').objects.create(name='P', code='P')
        owner = old.get_model('core', 'Owner').objects.create(user=user, name='O')
        Labour_, Txn = old.get_model('core', 'Labour'), old.get_model('core', 'ExpenseTransaction')
        recent, ancient, cancelled_only, mixed, none = (
            Labour_.objects.create(name=n) for n in ('Recent', 'Ancient', 'CancelledOnly', 'Mixed', 'NoPayments'))

        def txn(labour, day, status='ACTIVE'):
            Txn.objects.create(project=project, expense_date=day, expense_category='LABOUR', expense_type='x',
                               party_type='LABOUR', labour=labour, paid_by_owner=owner, amount='10',
                               payment_mode='CASH', status=status, created_by=user)
        txn(recent, date.today() - timedelta(days=1))
        txn(ancient, date.today() - timedelta(days=900))           # old payment must NOT mean inactive
        txn(cancelled_only, date.today(), status='CANCELLED')
        txn(mixed, date.today(), status='CANCELLED')
        txn(mixed, date.today() - timedelta(days=400))
        before = (Txn.objects.count(), Labour_.objects.count())

        executor = MigrationExecutor(connection)
        executor.migrate([('core', '0002_project_labour_and_payment_batch')])

        links = {pl.labour.name: pl.is_active for pl in ProjectLabour.objects.select_related('labour')}
        self.assertEqual(links, {'Recent': True, 'Ancient': True, 'Mixed': True})
        self.assertNotIn('CancelledOnly', links)
        self.assertNotIn('NoPayments', links)
        self.assertEqual((ExpenseTransaction.objects.count(), Labour.objects.count()), before)
        self.assertFalse(ExpenseTransaction.objects.exclude(payment_batch=None).exists())
