from decimal import Decimal

from django.contrib.auth import get_user_model
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
