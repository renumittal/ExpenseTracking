"""
Centralized-ledger reconciliation: every money formula in the requirement doc (fund_given,
manager_spent, manager_balance, owner_direct, category_totals, total_expense, with_managers,
total_project_spend) plus the cross-checking identities, exercised against one fixture with 2
managers, owner-direct payments, all 4 categories, a cancelled row, a negative balance, and a
manager who works on two projects.
"""
import io
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError

from . import ledger
from .models import (
    Contractor,
    ContractorContract,
    ExpenseCategory,
    ExpenseTransaction,
    ManagerFund,
    PartyType,
    PaymentMode,
    Supplier,
    TransactionStatus,
)
from .test_manager_fund import LedgerBase

D = Decimal


class LedgerFixture(LedgerBase):
    """
    Project A: manager_1 has a fund and spends it across LABOUR (distribution), MISCELLANEOUS and
    SUPPLIER (recorded directly) -- more than the fund holds, so manager_1's balance goes negative.
    manager_2 has a smaller fund, spends less than it (positive balance), and also has a fund +
    expense on Project B (the multi-project manager). The owner pays a CONTRACTOR expense directly
    (not through any manager).
    """

    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(name='Steel Co')
        self.contractor = Contractor.objects.create(name='BuildIt')
        self.contract = ContractorContract.objects.create(
            project=self.project, contractor=self.contractor, contract_date='2026-09-01',
            contract_amount=D('50000'), work_description='Foundation',
        )

        # manager_1: fund 10000, spends 12000 (labour 5000 + misc 4000 + supplier 3000) -> balance -2000
        self.give('10000.00', '2026-09-01', manager=self.manager)
        self.batch([(self.labours[0], D('5000'))], date='2026-09-02', manager=self.manager)
        self.cancelled_expense_id = self._record_expense(
            self.manager_user, ExpenseCategory.MISCELLANEOUS, D('1000'), date='2026-09-03',
        )  # this one gets cancelled below -- must not count
        self._record_expense(self.manager_user, ExpenseCategory.MISCELLANEOUS, D('4000'), date='2026-09-03')
        self._record_supplier_expense(self.manager_user, D('3000'), date='2026-09-04')

        self.auth_as(self.owner_user)
        self.client.post(
            f'/api/expense-transactions/{self.cancelled_expense_id}/cancel/', {'remarks': 'entered twice'},
        )

        # manager_2: fund 20000, spends 6000 (labour only) -> balance +14000
        self.give('20000.00', '2026-09-01', manager=self.manager2)
        r = self.batch([(self.labours[1], D('6000'))], date='2026-09-05', manager=self.manager2, user=self.manager2_user)
        assert r.status_code == 201, r.data

        # owner-direct: a CONTRACTOR expense, not tied to any manager
        self.auth_as(self.owner_user)
        ExpenseTransaction.objects.create(
            project=self.project, expense_date='2026-09-06', expense_category=ExpenseCategory.CONTRACTOR,
            expense_type='Contract payment', party_type=PartyType.CONTRACTOR, contractor_contract=self.contract,
            paid_by_owner=self.owner, amount=D('8000'), payment_mode=PaymentMode.CASH, created_by=self.owner_user,
        )

        # multi-project manager: manager_2 also works Project B
        from .test_manager_fund import grant, make_person
        from .models import Role, UserAccess, ScopeType, AccessRole
        UserAccess.objects.create(
            user=self.manager2_user, role=AccessRole.objects.get(name=Role.MANAGER),
            project=self.other_project, scope_type=ScopeType.PROJECT,
        )
        owner_b_user = self.owner_b_user
        ManagerFund.objects.create(
            project=self.other_project, manager=self.manager2, fund_date='2026-09-01', fund_amount=D('3000'),
            given_by_owner=self.owner_b, payment_mode=PaymentMode.CASH,
        )
        self.auth_as(self.manager2_user)
        self.client.post('/api/manager-labour-distributions/batch/', {
            'project': self.other_project.id, 'manager': self.manager2.id, 'date': '2026-09-02',
            'payments': [{'labour': self._other_project_labour().id, 'amount': '1000.00'}],
        }, format='json')

    def _other_project_labour(self):
        from .models import Labour, ProjectLabour
        labour = Labour.objects.create(name='Labour B-project')
        ProjectLabour.objects.create(project=self.other_project, labour=labour)
        return labour

    def _record_expense(self, user, category, amount, date):
        self.auth_as(user)
        r = self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': date, 'expense_category': category,
            'expense_type': 'Site expense', 'party_type': PartyType.NONE, 'payee_name': 'Tea stall',
            'paid_by_owner': self.owner.id, 'amount': str(amount), 'payment_mode': PaymentMode.CASH,
        }, format='json')
        assert r.status_code == 201, r.data
        return r.data['id']

    def _record_supplier_expense(self, user, amount, date):
        self.auth_as(user)
        r = self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': date, 'expense_category': ExpenseCategory.SUPPLIER,
            'expense_type': 'Material', 'party_type': PartyType.SUPPLIER, 'supplier': self.supplier.id,
            'paid_by_owner': self.owner.id, 'amount': str(amount), 'payment_mode': PaymentMode.CASH,
        }, format='json')
        assert r.status_code == 201, r.data
        return r.data['id']


class FormulaTests(LedgerFixture):
    def test_fund_given(self):
        self.assertEqual(ledger.fund_given(self.project, self.manager), D('10000'))
        self.assertEqual(ledger.fund_given(self.project, self.manager2), D('20000'))

    def test_manager_spent_covers_every_category_and_ignores_cancelled(self):
        # 5000 labour + 4000 misc + 3000 supplier == 12000 (the cancelled 1000 misc row excluded)
        self.assertEqual(ledger.manager_spent(self.project, self.manager), D('12000'))
        self.assertEqual(ledger.manager_spent(self.project, self.manager2), D('6000'))

    def test_manager_balance_can_go_negative(self):
        self.assertEqual(ledger.manager_balance(self.project, self.manager), D('-2000'))
        self.assertEqual(ledger.manager_balance(self.project, self.manager2), D('14000'))

    def test_owner_direct_excludes_every_manager_spend(self):
        # total_expense = 5000 + 4000 + 3000 + 6000 (managers) + 8000 (owner contractor) = 26000
        self.assertEqual(ledger.total_expense(self.project), D('26000'))
        self.assertEqual(ledger.owner_direct(self.project), D('8000'))

    def test_category_totals_sum_to_total_expense(self):
        totals = ledger.category_totals(self.project)
        self.assertEqual(totals[ExpenseCategory.LABOUR], D('11000'))  # manager_1's 5000 + manager_2's 6000
        self.assertEqual(totals[ExpenseCategory.MISCELLANEOUS], D('4000'))
        self.assertEqual(totals[ExpenseCategory.SUPPLIER], D('3000'))
        self.assertEqual(totals[ExpenseCategory.CONTRACTOR], D('8000'))
        self.assertEqual(sum(totals.values(), D('0')), ledger.total_expense(self.project))

    def test_with_managers_and_total_project_spend(self):
        # with_managers = -2000 + 14000 = 12000; total_project_spend = 26000 + 12000 = 38000
        self.assertEqual(ledger.with_managers(self.project), D('12000'))
        self.assertEqual(ledger.total_project_spend(self.project), D('38000'))
        # identity 1: total_project_spend == owner_direct + Σ fund_given
        fund_total = ledger.fund_given(self.project, self.manager) + ledger.fund_given(self.project, self.manager2)
        self.assertEqual(ledger.total_project_spend(self.project), ledger.owner_direct(self.project) + fund_total)

    def test_multi_project_manager_total_balance(self):
        # manager_2: +14000 on Project A, and on Project B fund 3000 - spent 1000 = +2000
        self.assertEqual(ledger.manager_balance(self.other_project, self.manager2), D('2000'))
        self.assertEqual(ledger.manager_total_balance(self.manager2), D('16000'))

    def test_decimal_precision_is_exact(self):
        self.give('123.45', '2026-09-10', manager=self.manager)
        self.assertIsInstance(ledger.fund_given(self.project, self.manager), Decimal)
        self.assertEqual(ledger.fund_given(self.project, self.manager), D('10123.45'))

    def test_cancelling_a_fund_only_changes_expected_totals(self):
        """Cancelling one manager's expense changes only that manager's spend/balance and the totals
        that roll it up -- nothing about the other manager or owner_direct moves."""
        before_other = ledger.manager_balance(self.project, self.manager2)
        before_owner_direct = ledger.owner_direct(self.project)
        self.auth_as(self.owner_user)
        tx_id = self._record_expense(self.manager_user, ExpenseCategory.MISCELLANEOUS, D('500'), date='2026-09-07')
        self.auth_as(self.owner_user)
        self.client.post(f'/api/expense-transactions/{tx_id}/cancel/', {'remarks': 'mistake'})
        self.assertEqual(ledger.manager_balance(self.project, self.manager2), before_other)
        self.assertEqual(ledger.owner_direct(self.project), before_owner_direct)
        self.assertEqual(ledger.manager_balance(self.project, self.manager), D('-2000'))  # unaffected: net zero


class ReconciliationIdentityTests(LedgerFixture):
    def test_identity_1_total_project_spend(self):
        managers = ledger.project_managers(self.project)
        fund_total = sum((ledger.fund_given(self.project, m) for m in managers), D('0'))
        self.assertEqual(ledger.total_project_spend(self.project), ledger.owner_direct(self.project) + fund_total)

    def test_identity_2_manager_spend_plus_owner_direct(self):
        managers = ledger.project_managers(self.project)
        spent_total = sum((ledger.manager_spent(self.project, m) for m in managers), D('0'))
        self.assertEqual(spent_total + ledger.owner_direct(self.project), ledger.total_expense(self.project))

    def test_identity_3_category_totals(self):
        self.assertEqual(sum(ledger.category_totals(self.project).values(), D('0')), ledger.total_expense(self.project))

    def test_identity_4_distribution_mirrors_one_expense(self):
        from .models import ManagerLabourDistribution
        for dist in ManagerLabourDistribution.objects.filter(project=self.project):
            self.assertIsNotNone(dist.expense_transaction_id)
            self.assertEqual(dist.expense_transaction.expense_category, ExpenseCategory.LABOUR)
            self.assertEqual(dist.expense_transaction.amount, dist.amount)

    def test_check_ledger_command_passes_on_a_consistent_fixture(self):
        out, err = io.StringIO(), io.StringIO()
        call_command('check_ledger', stdout=out, stderr=err)
        self.assertIn('consistent', out.getvalue())

    def test_check_ledger_command_flags_a_broken_mirror(self):
        from .models import ManagerLabourDistribution
        dist = ManagerLabourDistribution.objects.filter(project=self.project).first()
        dist.expense_transaction.amount = dist.expense_transaction.amount + D('1')
        dist.expense_transaction.save(update_fields=['amount'])
        out, err = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit):
            call_command('check_ledger', stdout=out, stderr=err)
        self.assertIn('does not match', err.getvalue())
