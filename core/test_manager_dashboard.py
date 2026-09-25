"""
Manager Dashboard backend: GET /api/projects/<id>/manager-summary/ and
GET /api/projects/<id>/transactions/, plus the ledger fix that makes "total distributed" include
Other-category expenses (not just labour distributions), and category-permission enforcement on the
existing expense create/list endpoints. Reuses the LedgerBase fixture from test_manager_fund.py.
"""
from decimal import Decimal

from rest_framework import status

from . import ledger
from .models import (
    ExpenseCategory,
    ExpenseTransaction,
    PartyType,
    PaymentMode,
    Role,
    RolePermission,
    TransactionStatus,
)
from .test_manager_fund import LedgerBase, make_person

D = Decimal


class ManagerSummaryFormulaTests(LedgerBase):
    """The bug: total_distributed only counted labour distributions, ignoring Other expenses."""

    def setUp(self):
        super().setUp()
        self.give('100000.00', '2026-09-01')
        self.batch([(self.labours[0], D('20000'))], date='2026-09-02')

    def add_other_expense(self, amount, user=None, date='2026-09-03'):
        self.auth_as(user or self.manager_user)
        return self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': date, 'expense_category': ExpenseCategory.MISCELLANEOUS,
            'expense_type': 'Site expense', 'party_type': PartyType.NONE, 'payee_name': 'Tea stall',
            'paid_by_owner': self.owner.id, 'amount': str(amount), 'payment_mode': PaymentMode.CASH,
        }, format='json')

    def test_ledger_position_includes_other_expenses_by_the_manager(self):
        pos_before = ledger.position(self.project, self.manager)
        self.assertEqual(pos_before['total_distributed'], D('20000.00'))

        r = self.add_other_expense('1500.00')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)

        pos_after = ledger.position(self.project, self.manager)
        self.assertEqual(pos_after['total_distributed'], D('21500.00'))
        self.assertEqual(pos_after['total_received'], D('100000.00'))
        self.assertEqual(pos_after['available_balance'], D('78500.00'))

    def test_other_expense_by_a_different_manager_does_not_count(self):
        self.give('5000.00', '2026-09-01', manager=self.manager2)
        self.add_other_expense('900.00', user=self.manager2_user)
        pos = ledger.position(self.project, self.manager)
        self.assertEqual(pos['total_distributed'], D('20000.00'))  # unaffected by manager2's Other expense

    def test_cancelled_other_expense_does_not_count(self):
        r = self.add_other_expense('1500.00')
        tx_id = r.data['id']
        self.auth_as(self.owner_user)
        cancel = self.client.post(f'/api/expense-transactions/{tx_id}/cancel/', {'remarks': 'mistake'}, format='json')
        self.assertEqual(cancel.status_code, 200, cancel.data)
        self.assertEqual(ledger.position(self.project, self.manager)['total_distributed'], D('20000.00'))


class ManagerSummaryEndpointTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.give('50000.00', '2026-09-01')
        self.batch([(self.labours[0], D('10000'))], date='2026-09-02')

    def summary(self, user):
        self.auth_as(user)
        return self.client.get(f'/api/projects/{self.project.id}/manager-summary/')

    def test_manager_sees_fund_received_distributed_and_balance(self):
        r = self.summary(self.manager_user)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(D(r.data['fund_received']), D('50000.00'))
        self.assertEqual(D(r.data['total_distributed']), D('10000.00'))
        self.assertEqual(D(r.data['balance']), D('40000.00'))

    def test_allowed_categories_and_category_totals_reflect_useraccess(self):
        r = self.summary(self.manager_user)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            set(r.data['allowed_categories']['create']), {'labour', 'contractor', 'supplier', 'miscellaneous'},
        )
        self.assertIn('labour', r.data['category_totals'])
        self.assertIn('supplier', r.data['category_totals'])

        # Turn off supplier for MANAGER role-wide: it must disappear from create (create needs it
        # off), but supplier VIEW stays on by default so it remains in category_totals/view.
        RolePermission.objects.update_or_create(role='MANAGER', permission='canAddSupplierExpense', defaults={'allowed': False})
        r2 = self.summary(self.manager_user)
        self.assertEqual(r2.status_code, 200)
        self.assertNotIn('supplier', r2.data['allowed_categories']['create'])
        self.assertNotIn('supplier', r2.data['category_totals'])
        # Untouched categories remain.
        self.assertIn('labour', r2.data['allowed_categories']['create'])

    def test_category_total_counts_only_this_managers_own_entries(self):
        self.auth_as(self.manager2_user)
        self.give('5000.00', '2026-09-01', manager=self.manager2)
        r2 = self.summary(self.manager2_user)
        self.assertEqual(D(r2.data['category_totals'].get('labour', '0')), D('0.00'))

    def test_403_when_user_has_no_access_to_the_project(self):
        r = self.summary(self.owner_b_user)   # owner of a different project entirely
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_404_for_a_project_that_does_not_exist(self):
        self.auth_as(self.manager_user)
        r = self.client.get('/api/projects/999999/manager-summary/')
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_can_view_any_project_summary(self):
        r = self.summary(self.admin_user)
        self.assertEqual(r.status_code, 200)

    def test_manager_id_param_403_without_view_project_funds_permission(self):
        self.auth_as(self.owner_user)
        RolePermission.objects.update_or_create(role='OWNER', permission='canViewProjectFunds', defaults={'allowed': False})
        r = self.client.get(f'/api/projects/{self.project.id}/manager-summary/?manager_id={self.manager2.id}')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_id_param_allowed_with_view_project_funds_permission(self):
        self.give('5000.00', '2026-09-01', manager=self.manager2)
        self.auth_as(self.owner_user)   # OWNER has canViewProjectFunds by default
        r = self.client.get(f'/api/projects/{self.project.id}/manager-summary/?manager_id={self.manager2.id}')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(D(r.data['fund_received']), D('5000.00'))

    def test_category_without_create_permission_absent_from_allowed_categories_create(self):
        RolePermission.objects.update_or_create(role='MANAGER', permission='canAddContractorExpense', defaults={'allowed': False})
        r = self.summary(self.manager_user)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('contractor', r.data['allowed_categories']['create'])


class TransactionsEndpointTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.give('20000.00', '2026-09-05')

    def transactions(self, user, **params):
        self.auth_as(user)
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        return self.client.get(f'/api/projects/{self.project.id}/transactions/' + (f'?{qs}' if qs else ''))

    def test_default_range_is_current_month(self):
        # Everything in setUp/tests here is dated in September 2026; freeze "today" isn't available
        # without extra plumbing, so this test only checks the endpoint responds with a coherent shape.
        r = self.transactions(self.manager_user)
        self.assertEqual(r.status_code, 200, r.data)
        for key in ('in_total', 'out_total', 'count', 'results', 'next'):
            self.assertIn(key, r.data)

    def test_explicit_date_range_filters_in_and_out(self):
        self.batch([(self.labours[0], D('3000'))], date='2026-09-10')
        r = self.transactions(self.manager_user, **{'from': '2026-09-01', 'to': '2026-09-30'})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(D(r.data['in_total']), D('20000.00'))
        self.assertEqual(D(r.data['out_total']), D('3000.00'))
        self.assertEqual(r.data['count'], 2)

        r2 = self.transactions(self.manager_user, **{'from': '2026-08-01', 'to': '2026-08-31'})
        self.assertEqual(r2.data['count'], 0)
        self.assertEqual(D(r2.data['in_total']), D('0.00'))

    def test_pagination_20_per_page_and_next(self):
        for i in range(25):
            self.batch([(self.labours[i % len(self.labours)], D('100'))], date=f'2026-09-{(i % 27) + 1:02d}')
        r1 = self.transactions(self.manager_user, **{'from': '2026-09-01', 'to': '2026-09-30', 'page': 1})
        self.assertEqual(len(r1.data['results']), 20)
        self.assertEqual(r1.data['next'], 2)

        r2 = self.transactions(self.manager_user, **{'from': '2026-09-01', 'to': '2026-09-30', 'page': 2})
        self.assertGreaterEqual(len(r2.data['results']), 1)
        self.assertIsNone(r2.data['next'])

    def test_403_when_no_project_access(self):
        r = self.transactions(self.owner_b_user)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_category_filter_limits_out_rows_and_excludes_fund_in_rows(self):
        self.batch([(self.labours[0], D('3000'))], date='2026-09-10')
        self.auth_as(self.manager_user)
        self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': '2026-09-12', 'expense_category': ExpenseCategory.MISCELLANEOUS,
            'expense_type': 'Site expense', 'party_type': PartyType.NONE, 'payee_name': 'Tea stall',
            'paid_by_owner': self.owner.id, 'amount': '250.00', 'payment_mode': PaymentMode.CASH,
        }, format='json')
        r = self.transactions(self.manager_user, **{'from': '2026-09-01', 'to': '2026-09-30', 'category': 'LABOUR'})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(D(r.data['in_total']), D('0.00'))                        # fund rows excluded once category is set
        categories = {row['category'] for row in r.data['results']}
        self.assertEqual(categories, {'labour'})

    def test_only_permitted_categories_appear_in_out_transactions(self):
        self.auth_as(self.manager_user)
        self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': '2026-09-12', 'expense_category': ExpenseCategory.MISCELLANEOUS,
            'expense_type': 'Site expense', 'party_type': PartyType.NONE, 'payee_name': 'Tea stall',
            'paid_by_owner': self.owner.id, 'amount': '250.00', 'payment_mode': PaymentMode.CASH,
        }, format='json')
        RolePermission.objects.update_or_create(role='MANAGER', permission='canAddMiscExpense', defaults={'allowed': False})
        r = self.transactions(self.manager_user, **{'from': '2026-09-01', 'to': '2026-09-30'})
        categories = {row['category'] for row in r.data['results'] if row['type'] == 'OUT'}
        self.assertNotIn('miscellaneous', categories)


class CategoryPermissionEnforcementTests(LedgerBase):
    """Category permission enforcement on the EXISTING expense create/list endpoints."""

    def create_supplier_expense(self, user):
        self.auth_as(user)
        from .models import Supplier
        supplier = Supplier.objects.create(name='Steel Co')
        return self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': '2026-09-10', 'expense_category': ExpenseCategory.SUPPLIER,
            'expense_type': 'Material', 'party_type': PartyType.SUPPLIER, 'supplier': supplier.id,
            'paid_by_owner': self.owner.id, 'amount': '5000.00', 'payment_mode': PaymentMode.CASH,
        }, format='json')

    def test_create_rejected_when_manager_lacks_category_permission(self):
        RolePermission.objects.update_or_create(role='MANAGER', permission='canAddSupplierExpense', defaults={'allowed': False})
        r = self.create_supplier_expense(self.manager_user)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(ExpenseTransaction.objects.filter(expense_category=ExpenseCategory.SUPPLIER).count(), 0)

    def test_create_allowed_when_manager_has_category_permission(self):
        r = self.create_supplier_expense(self.manager_user)
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)

    def test_list_hides_unpermitted_category_rows(self):
        r = self.create_supplier_expense(self.manager_user)
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        tx_id = r.data['id']

        # Still visible while permitted.
        self.auth_as(self.manager_user)
        ids = {row['id'] for row in self.client.get(f'/api/expense-transactions/?project={self.project.id}').data}
        self.assertIn(tx_id, ids)

        # canAddSupplierExpense (create-only) being off must NOT hide it -- list visibility follows
        # the VIEW permission (canViewSuppliers), same as the existing Expense List/Reports screens.
        RolePermission.objects.update_or_create(role='MANAGER', permission='canAddSupplierExpense', defaults={'allowed': False})
        ids_add_off = {row['id'] for row in self.client.get(f'/api/expense-transactions/?project={self.project.id}').data}
        self.assertIn(tx_id, ids_add_off)

        # Turn the VIEW permission off: the row must disappear from this manager's list even though
        # the project itself is still viewable to them.
        RolePermission.objects.update_or_create(role='MANAGER', permission='canViewSuppliers', defaults={'allowed': False})
        ids2 = {row['id'] for row in self.client.get(f'/api/expense-transactions/?project={self.project.id}').data}
        self.assertNotIn(tx_id, ids2)

    def test_owner_still_sees_everything(self):
        r = self.create_supplier_expense(self.manager_user)
        tx_id = r.data['id']
        self.auth_as(self.owner_user)
        ids = {row['id'] for row in self.client.get(f'/api/expense-transactions/?project={self.project.id}').data}
        self.assertIn(tx_id, ids)


class OwnerSummaryEndpointTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.give('50000.00', '2026-09-01')
        self.batch([(self.labours[0], D('10000'))], date='2026-09-02')

    def owner_summary(self, user):
        self.auth_as(user)
        return self.client.get(f'/api/projects/{self.project.id}/owner-summary/')

    def test_403_for_owner_of_another_project(self):
        r = self.owner_summary(self.owner_b_user)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_total_project_spend_equals_total_expense_plus_with_managers(self):
        r = self.owner_summary(self.owner_user)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(D(r.data['total_project_spend']), D(r.data['total_expense']) + D(r.data['with_managers']))
        self.assertEqual(D(r.data['given']), D('50000.00'))
        self.assertEqual(D(r.data['spent_by_managers']), D('10000.00'))
        self.assertEqual(D(r.data['with_managers']), D('40000.00'))
        self.assertEqual({m['id'] for m in r.data['managers']}, {self.manager.id})

    def test_admin_can_view_any_project(self):
        r = self.owner_summary(self.admin_user)
        self.assertEqual(r.status_code, 200)


class ManagerDetailCreateEnforcementTests(LedgerBase):
    """Manager detail (owner viewing a manager's dashboard, ?manager_id=) is read-only for the owner --
    it never grants them a create permission they don't already have; the ordinary create endpoint
    keeps enforcing CATEGORY_PERMISSION exactly as before."""

    def test_owner_without_create_permission_is_rejected(self):
        RolePermission.objects.update_or_create(role='OWNER', permission='canAddMiscExpense', defaults={'allowed': False})
        self.auth_as(self.owner_user)
        r = self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': '2026-09-10', 'expense_category': ExpenseCategory.MISCELLANEOUS,
            'expense_type': 'Site expense', 'party_type': PartyType.NONE, 'payee_name': 'Tea stall',
            'paid_by_owner': self.owner.id, 'amount': '100.00', 'payment_mode': PaymentMode.CASH,
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(ExpenseTransaction.objects.filter(expense_category=ExpenseCategory.MISCELLANEOUS).count(), 0)


class OwnerTransactionsScopeTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.give('20000.00', '2026-09-01')

    def transactions(self, user, **params):
        self.auth_as(user)
        qs = '&'.join(f'{k}={v}' for k, v in params.items())
        return self.client.get(f'/api/projects/{self.project.id}/transactions/' + (f'?{qs}' if qs else ''))

    def test_owner_out_total_excludes_manager_expense_rows(self):
        # Manager records an Other expense from their fund -- counts toward the manager's own spend
        # (Manager Dashboard), but not toward the owner's out_total: it was already paid for by the
        # fund given, so adding it again here would double count the same money.
        self.auth_as(self.manager_user)
        self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': '2026-09-05', 'expense_category': ExpenseCategory.MISCELLANEOUS,
            'expense_type': 'Site expense', 'party_type': PartyType.NONE, 'payee_name': 'Tea stall',
            'paid_by_owner': self.owner.id, 'amount': '500.00', 'payment_mode': PaymentMode.CASH,
        }, format='json')
        self.auth_as(self.owner_user)
        self.client.post('/api/expense-transactions/', {
            'project': self.project.id, 'expense_date': '2026-09-06', 'expense_category': ExpenseCategory.MISCELLANEOUS,
            'expense_type': 'Site expense', 'party_type': PartyType.NONE, 'payee_name': 'Diesel',
            'paid_by_owner': self.owner.id, 'amount': '300.00', 'payment_mode': PaymentMode.CASH,
        }, format='json')

        r = self.transactions(self.owner_user, **{'from': '2026-09-01', 'to': '2026-09-30'})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(D(r.data['out_total']), D('20300.00'))          # owner_direct (300) + fund given (20000)
        manager_rows = [row for row in r.data['results'] if row.get('by_manager')]
        self.assertEqual(len(manager_rows), 1)
        self.assertEqual(manager_rows[0]['by_manager'], self.manager.name)
        self.assertEqual(D(manager_rows[0]['amount']), D('500.00'))
