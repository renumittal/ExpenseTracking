"""Manager Fund ledger: fund -> manager -> labour distributions. Balance is always computed from the database."""
import threading
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import TransactionTestCase, skipUnlessDBFeature
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from . import ledger
from .models import (
    AccessRole,
    ExpenseCategory,
    ExpenseTransaction,
    Labour,
    Manager,
    ManagerFund,
    ManagerLabourDistribution,
    Owner,
    PartyType,
    PaymentMode,
    Project,
    ProjectLabour,
    Role,
    RolePermission,
    ScopeType,
    UserAccess,
    TransactionStatus,
)

User = get_user_model()
D = Decimal


def grant(user, role_name, project):
    """UserAccess is the only source of access truth now: a PROJECT-scoped role grant."""
    return UserAccess.objects.create(
        user=user, role=AccessRole.objects.get(name=role_name), project=project, scope_type=ScopeType.PROJECT,
    )


def make_person(username, role, project=None, name=None, owner=True):
    user = User.objects.create_user(username=username, password='pass12345')
    profile = Owner.objects.create(user=user, name=name or username) if owner else Manager.objects.create(user=user, name=name or username)
    if project is not None:
        grant(user, role, project)
    return user, profile


class LedgerBase(APITestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Project A', code='A')
        self.other_project = Project.objects.create(name='Project B', code='B')

        self.owner_user, self.owner = make_person('owner_a', Role.OWNER, project=self.project, name='Owner A')
        self.owner2_user, self.owner2 = make_person('owner_a2', Role.OWNER, project=self.project, name='Owner A2')
        self.owner_b_user, self.owner_b = make_person('owner_b', Role.OWNER, project=self.other_project, name='Owner B')

        self.manager_user, self.manager = make_person('manager_1', Role.MANAGER, project=self.project, name='Manager 1', owner=False)
        self.manager2_user, self.manager2 = make_person('manager_2', Role.MANAGER, project=self.project, name='Manager 2', owner=False)

        # is_superuser=True alone makes services.is_superadmin() true -- no UserAccess grant needed.
        self.admin_user = User.objects.create_user(username='admin1', password='pass12345', is_superuser=True, is_staff=True)

        self.labours = [Labour.objects.create(name=n) for n in ('Labour A', 'Labour B', 'Labour C', 'Labour D', 'Labour E')]
        for labour in self.labours:
            ProjectLabour.objects.create(project=self.project, labour=labour)
        self.outsider = Labour.objects.create(name='Not on project')

    # -- helpers ------------------------------------------------------------
    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

    def give(self, amount, date='2026-09-20', owner=None, manager=None):
        """Record a fund straight in the database (the API path is tested separately)."""
        return ManagerFund.objects.create(
            project=self.project, manager=manager or self.manager, fund_date=date, fund_amount=amount,
            given_by_owner=owner or self.owner, payment_mode=PaymentMode.CASH)

    def batch(self, payments, date='2026-09-21', manager=None, user=None):
        self.auth_as(user or self.manager_user)
        return self.client.post('/api/manager-labour-distributions/batch/', {
            'project': self.project.id, 'manager': (manager or self.manager).id, 'date': date,
            'payments': [{'labour': l.id, 'amount': str(a)} for l, a in payments]}, format='json')

    def position(self, manager=None):
        return ledger.position(self.project, manager or self.manager)

    def project_labour_total(self):
        return sum((t.amount for t in ExpenseTransaction.objects.filter(project=self.project, status=TransactionStatus.ACTIVE)), D('0'))


class GivingFundTests(LedgerBase):
    def post_fund(self, user, **over):
        self.auth_as(user)
        body = {'project': self.project.id, 'manager': self.manager.id, 'fund_date': '2026-09-20', 'fund_amount': '100000.00',
                'given_by_owner': self.owner.id, 'payment_mode': 'CASH'}
        body.update(over)
        return self.client.post('/api/manager-funds/', body, format='json')

    def test_owner_gives_fund_and_it_is_traceable(self):
        r = self.post_fund(self.owner_user)
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        fund = ManagerFund.objects.get(pk=r.data['id'])
        self.assertEqual(fund.created_by, self.owner_user)
        self.assertEqual(fund.given_by_owner, self.owner)

    def test_manager_cannot_record_a_fund_for_themselves(self):
        r = self.post_fund(self.manager_user)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(ManagerFund.objects.count(), 0)

    def test_owner_must_record_funds_given_by_themselves(self):
        r = self.post_fund(self.owner_user, given_by_owner=self.owner2.id)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_may_record_on_behalf_of_a_project_owner(self):
        r = self.post_fund(self.admin_user, given_by_owner=self.owner2.id)
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        self.assertEqual(ManagerFund.objects.get(pk=r.data['id']).created_by, self.admin_user)

    def test_owner_of_another_project_is_refused(self):
        r = self.post_fund(self.owner_b_user, given_by_owner=self.owner_b.id)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_must_belong_to_project(self):
        r = self.post_fund(self.admin_user, given_by_owner=self.owner_b.id)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_manager_must_belong_to_project(self):
        stray_user, stray = make_person('stray', Role.MANAGER, name='Stray', owner=False)
        r = self.post_fund(self.owner_user, manager=stray.id)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_fund_amount_must_be_positive(self):
        for bad in ('0', '0.00', '-5'):
            self.assertEqual(self.post_fund(self.owner_user, fund_amount=bad).status_code, status.HTTP_400_BAD_REQUEST, bad)

    def test_database_rejects_non_positive_amounts(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.give('0.00')
        fund = self.give('100.00')
        with self.assertRaises(IntegrityError), transaction.atomic():
            ManagerLabourDistribution.objects.create(
                manager_fund=fund, project=self.project, manager=self.manager, date='2026-09-21',
                labour=self.labours[0], amount='-1.00')

    def test_funds_cannot_be_edited_or_deleted_through_the_api(self):
        fund = self.give('1000.00')
        self.auth_as(self.owner_user)
        self.assertEqual(self.client.patch(f'/api/manager-funds/{fund.id}/', {'fund_amount': '1'}).status_code, 405)
        self.assertEqual(self.client.delete(f'/api/manager-funds/{fund.id}/').status_code, 405)

    def test_fund_is_not_an_expense(self):
        self.post_fund(self.owner_user)
        self.assertEqual(ExpenseTransaction.objects.count(), 0)


class BalanceTests(LedgerBase):
    def test_business_example_received_distributed_balance(self):
        self.give('100000.00', '2026-09-20')
        l = self.labours
        self.assertEqual(self.batch([(l[0], 2000), (l[1], 3000)], '2026-09-21').status_code, 201)
        self.assertEqual(self.batch([(l[2], 4000)], '2026-09-23').status_code, 201)
        self.assertEqual(self.batch([(l[3], '2500')], '2026-09-25').status_code, 201)
        p = self.position()
        self.assertEqual((p['total_received'], p['total_distributed'], p['available_balance']),
                         (D('100000'), D('11500'), D('88500')))
        self.give('50000.00', '2026-09-26')
        p = self.position()
        self.assertEqual((p['total_received'], p['total_distributed'], p['available_balance']),
                         (D('150000'), D('11500'), D('138500')))

    def test_batch_of_five_saves_five_rows_five_expenses_one_batch_id(self):
        self.give('100000.00')
        l = self.labours
        r = self.batch([(l[0], 1000), (l[1], 1500), (l[2], 800), (l[3], 2000), (l[4], 700)])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(D(r.data['total']), D('6000'))
        self.assertEqual(D(r.data['position']['available_balance']), D('94000'))
        self.assertEqual(ManagerLabourDistribution.objects.count(), 5)
        expenses = ExpenseTransaction.objects.all()
        self.assertEqual(expenses.count(), 5)
        self.assertEqual({e.expense_category for e in expenses}, {'LABOUR'})
        self.assertEqual(len({e.payment_batch for e in expenses}), 1)
        self.assertEqual(str(expenses[0].payment_batch), r.data['payment_batch'])
        # each labourer is traceable
        self.assertEqual({(x.labour_id, x.amount) for x in ManagerLabourDistribution.objects.all()},
                         {(l[0].id, D('1000')), (l[1].id, D('1500')), (l[2].id, D('800')), (l[3].id, D('2000')), (l[4].id, D('700'))})
        self.assertTrue(all(x.created_by == self.manager_user for x in ManagerLabourDistribution.objects.all()))

    def test_project_expense_counts_each_distribution_once_and_never_the_fund(self):
        self.give('100000.00')
        self.batch([(self.labours[0], 10000)])
        self.assertEqual(self.project_labour_total(), D('10000'))             # not 100000, not 110000
        self.auth_as(self.owner_user)
        dash = self.client.get(f'/api/projects/{self.project.id}/dashboard/')
        self.assertEqual(D(dash.data['total_expense']), D('10000'))

    def test_owner_direct_labour_payment_does_not_touch_manager_balance(self):
        self.give('100000.00')
        self.auth_as(self.owner_user)
        r = self.client.post('/api/labour-payments/', {
            'project': self.project.id, 'expense_date': '2026-09-21', 'paid_by_owner': self.owner.id, 'payment_mode': 'CASH',
            'payments': [{'labour': self.labours[0].id, 'amount': '500.00'}]}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.position()['available_balance'], D('100000'))


class OverdrawTests(LedgerBase):
    def test_batch_exceeding_balance_is_rejected_and_saves_nothing(self):
        self.give('100000.00')
        self.batch([(self.labours[0], 95000)])
        r = self.batch([(self.labours[1], 6000)])
        self.assertEqual(r.status_code, 400)
        self.assertIn('exceeds the available fund balance', str(r.data))
        self.assertEqual(ManagerLabourDistribution.objects.count(), 1)
        self.assertEqual(ExpenseTransaction.objects.count(), 1)
        self.assertEqual(self.position()['available_balance'], D('5000'))

    def test_partly_affordable_batch_saves_none_of_it(self):
        self.give('5000.00')
        r = self.batch([(self.labours[0], 3000), (self.labours[1], 3000)])     # each fits, the total does not
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ManagerLabourDistribution.objects.count(), 0)
        self.assertEqual(ExpenseTransaction.objects.count(), 0)

    def test_exact_balance_is_allowed_then_nothing_left(self):
        self.give('5000.00')
        self.assertEqual(self.batch([(self.labours[0], 5000)]).status_code, 201)
        self.assertEqual(self.batch([(self.labours[1], '0.01')]).status_code, 400)

    def test_no_fund_means_no_distribution(self):
        self.assertEqual(self.batch([(self.labours[0], 1)]).status_code, 400)

    def test_input_validation(self):
        self.give('1000.00')
        l = self.labours
        self.assertEqual(self.batch([(l[0], 0)]).status_code, 400)
        self.assertEqual(self.batch([(l[0], -5)]).status_code, 400)
        self.assertEqual(self.batch([(l[0], 10), (l[0], 20)]).status_code, 400)             # same labourer twice
        self.assertEqual(self.batch([(self.outsider, 10)]).status_code, 400)                # not on this project
        self.assertEqual(self.batch([]).status_code, 400)
        self.assertEqual(ManagerLabourDistribution.objects.count(), 0)

    def test_labour_batch_excludes_manager_misc_expense_from_available_balance(self):
        """manager_balance(P,m), not the raw FIFO-lot sum, is what caps a labour distribution: a Misc
        expense the manager already recorded spends fund money without touching any lot."""
        self.give('5000.00')
        ExpenseTransaction.objects.create(
            project=self.project, expense_date='2026-09-21', expense_category=ExpenseCategory.MISCELLANEOUS,
            expense_type='Other', party_type=PartyType.NONE, payee_name='Cement shop', paid_by_owner=self.owner,
            amount='4000.00', payment_mode=PaymentMode.CASH, created_by=self.manager_user)
        self.assertEqual(self.position()['available_balance'], D('1000'))
        r = self.batch([(self.labours[0], '1500')])
        self.assertEqual(r.status_code, 400)
        self.assertIn('exceeds the available fund balance', str(r.data))
        self.assertEqual(ManagerLabourDistribution.objects.count(), 0)
        r2 = self.batch([(self.labours[0], '1000')])
        self.assertEqual(r2.status_code, 201, r2.data)

    def test_single_distribution_endpoint_still_works_and_ignores_a_named_fund(self):
        fund = self.give('100.00')
        self.auth_as(self.manager_user)
        body = {'project': self.project.id, 'manager': self.manager.id, 'date': '2026-09-21',
                'labour': self.labours[0].id, 'amount': '60.00', 'manager_fund': fund.id}
        self.assertEqual(self.client.post('/api/manager-labour-distributions/', body).status_code, 201)
        body['amount'] = '60.00'
        self.assertEqual(self.client.post('/api/manager-labour-distributions/', body).status_code, 400)   # only 40 left
        self.assertEqual(self.position()['available_balance'], D('40'))


class OldestFundFirstTests(LedgerBase):
    def test_allocation_takes_oldest_fund_first_and_splits_across_funds(self):
        first = self.give('60000.00', '2026-09-01', owner=self.owner)
        second = self.give('40000.00', '2026-09-05', owner=self.owner2)
        r = self.batch([(self.labours[0], 70000)])
        self.assertEqual(r.status_code, 201, r.data)
        rows = list(ManagerLabourDistribution.objects.order_by('id'))
        self.assertEqual([(x.manager_fund_id, x.amount) for x in rows], [(first.id, D('60000')), (second.id, D('10000'))])
        # owner attribution follows the fund the money came from
        self.assertEqual([x.expense_transaction.paid_by_owner_id for x in rows], [self.owner.id, self.owner2.id])
        self.assertEqual(self.position()['available_balance'], D('30000'))
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(first.balance, D('0'))
        self.assertEqual(second.balance, D('30000'))

    def test_next_batch_continues_from_the_oldest_fund_with_money_left(self):
        first = self.give('1000.00', '2026-09-01')
        second = self.give('1000.00', '2026-09-02')
        self.batch([(self.labours[0], 400)])
        self.batch([(self.labours[1], 400)])
        self.batch([(self.labours[2], 400)])           # 200 from the first fund, 200 from the second
        self.assertEqual({x.manager_fund_id for x in ManagerLabourDistribution.objects.all()}, {first.id, second.id})
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.balance, second.balance), (D('0'), D('800')))


class CancellationTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.give('100000.00')
        r = self.batch([(self.labours[0], 1000), (self.labours[1], 2000)])
        self.d1, self.d2 = ManagerLabourDistribution.objects.order_by('id')
        self.assertEqual(r.status_code, 201)

    def cancel(self, dist, user, reason='Entered by mistake'):
        self.auth_as(user)
        return self.client.post(f'/api/manager-labour-distributions/{dist.id}/cancel/', {'remarks': reason} if reason else {})

    def test_owner_cancels_one_distribution_only(self):
        self.assertEqual(self.position()['available_balance'], D('97000'))
        r = self.cancel(self.d1, self.owner_user)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(D(r.data['position']['available_balance']), D('98000'))      # +1000 exactly
        self.d1.refresh_from_db(); self.d2.refresh_from_db()
        self.assertEqual(self.d1.expense_transaction.status, TransactionStatus.CANCELLED)
        self.assertEqual(self.d2.expense_transaction.status, TransactionStatus.ACTIVE)   # the other one is untouched
        self.assertEqual(ManagerLabourDistribution.objects.count(), 2)                  # history kept
        self.assertEqual(self.project_labour_total(), D('2000'))                       # project total excludes it

    def test_cancelled_distribution_stays_in_the_history_marked_cancelled(self):
        self.cancel(self.d1, self.owner_user)
        self.auth_as(self.manager_user)
        stmt = self.client.get('/api/manager-funds/statement/').data['statements'][0]
        by_id = {d['id']: d for d in stmt['distributions']}
        self.assertEqual((by_id[self.d1.id]['status'], by_id[self.d2.id]['status']), ('CANCELLED', 'ACTIVE'))
        self.assertEqual(stmt['ledger'][-1]['running_balance'], stmt['position']['available_balance'])

    def test_manager_cannot_cancel(self):
        self.assertEqual(self.cancel(self.d1, self.manager_user).status_code, 403)
        self.assertEqual(self.position()['available_balance'], D('97000'))

    def test_owner_of_another_project_cannot_cancel(self):
        self.assertIn(self.cancel(self.d1, self.owner_b_user).status_code, (403, 404))
        self.assertEqual(self.position()['available_balance'], D('97000'))

    def test_reason_required_and_double_cancel_refused(self):
        self.assertEqual(self.cancel(self.d1, self.owner_user, reason=None).status_code, 400)
        self.assertEqual(self.cancel(self.d1, self.owner_user).status_code, 200)
        self.assertEqual(self.cancel(self.d1, self.owner_user).status_code, 400)
        self.assertEqual(self.position()['available_balance'], D('98000'))

    def test_admin_can_cancel(self):
        self.assertEqual(self.cancel(self.d2, self.admin_user).status_code, 200)

    def test_cancelling_the_mirrored_expense_directly_gives_the_same_result(self):
        self.auth_as(self.owner_user)
        r = self.client.post(f'/api/expense-transactions/{self.d1.expense_transaction_id}/cancel/', {'remarks': 'wrong'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.position()['available_balance'], D('98000'))

    def test_unrelated_labour_expense_cancellation_does_not_change_the_fund(self):
        self.auth_as(self.owner_user)
        r = self.client.post('/api/labour-payments/', {
            'project': self.project.id, 'expense_date': '2026-09-22', 'paid_by_owner': self.owner.id, 'payment_mode': 'CASH',
            'payments': [{'labour': self.labours[2].id, 'amount': '700.00'}]}, format='json')
        direct = r.data['transactions'][0]['id']
        self.assertEqual(self.client.post(f'/api/expense-transactions/{direct}/cancel/', {'remarks': 'x'}).status_code, 200)
        self.assertEqual(self.position()['available_balance'], D('97000'))

    def test_cancelled_amount_can_be_distributed_again(self):
        self.cancel(self.d1, self.owner_user)
        self.assertEqual(self.batch([(self.labours[2], 98000)]).status_code, 201)
        self.assertEqual(self.position()['available_balance'], D('0'))

    def test_a_fund_with_distributions_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.d1.manager_fund.delete()


class FundCancellationTests(LedgerBase):
    def cancel_fund(self, fund, user, reason='Given by mistake'):
        self.auth_as(user)
        return self.client.post(f'/api/manager-funds/{fund.id}/cancel/', {'remarks': reason} if reason else {})

    def test_cancelled_fund_excluded_from_fund_given(self):
        active = self.give('5000.00', '2026-09-20')
        cancelled = self.give('3000.00', '2026-09-21')
        self.assertEqual(self.position()['total_received'], D('8000'))

        r = self.cancel_fund(cancelled, self.owner_user)
        self.assertEqual(r.status_code, 200, r.data)
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, TransactionStatus.CANCELLED)
        self.assertEqual(cancelled.cancelled_by, self.owner_user)
        self.assertEqual(cancelled.cancel_reason, 'Given by mistake')

        self.assertEqual(ledger.fund_given(self.project, self.manager), D('5000'))
        self.assertEqual(self.position()['total_received'], D('5000'))
        active.refresh_from_db()
        self.assertEqual(active.status, TransactionStatus.ACTIVE)

    def test_cancelled_fund_money_is_not_available_to_distribute(self):
        self.give('5000.00', '2026-09-20')
        fund2 = self.give('5000.00', '2026-09-21')
        self.cancel_fund(fund2, self.owner_user)
        self.assertEqual(self.position()['available_balance'], D('5000'))
        r = self.batch([(self.labours[0], '6000')])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.batch([(self.labours[0], '5000')]).status_code, 201)

    def test_manager_cannot_cancel_a_fund(self):
        fund = self.give('1000.00')
        self.assertEqual(self.cancel_fund(fund, self.manager_user).status_code, 403)

    def test_reason_required_and_double_cancel_refused(self):
        fund = self.give('1000.00')
        self.assertEqual(self.cancel_fund(fund, self.owner_user, reason=None).status_code, 400)
        self.assertEqual(self.cancel_fund(fund, self.owner_user).status_code, 200)
        self.assertEqual(self.cancel_fund(fund, self.owner_user).status_code, 400)


class VisibilityAndAuthorizationTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.f1 = self.give('100000.00', '2026-09-20', owner=self.owner)
        self.f2 = self.give('50000.00', '2026-09-22', owner=self.owner2)
        self.give('9999.00', '2026-09-20', manager=self.manager2)
        self.batch([(self.labours[0], 35000)])

    def summary(self, user):
        self.auth_as(user)
        return self.client.get('/api/manager-funds/summary/')

    def test_manager_sees_only_own_position(self):
        r = self.summary(self.manager_user)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data['summary']), 1)
        row = r.data['summary'][0]
        self.assertEqual((D(row['total_received']), D(row['total_distributed']), D(row['available_balance'])),
                         (D('150000'), D('35000'), D('115000')))

    def test_owner_sees_all_managers_on_own_projects_only(self):
        self.assertEqual({row['manager_id'] for row in self.summary(self.owner_user).data['summary']}, {self.manager.id, self.manager2.id})
        self.assertEqual(self.summary(self.owner_b_user).data['summary'], [])

    def test_admin_sees_everything(self):
        self.assertEqual(len(self.summary(self.admin_user).data['summary']), 2)

    def test_fund_history_keeps_which_owner_gave_what(self):
        self.auth_as(self.owner_user)
        stmt = [s for s in self.client.get('/api/manager-funds/statement/').data['statements'] if s['position']['manager_id'] == self.manager.id][0]
        self.assertEqual([(str(f['date']), f['given_by_owner_name'], D(f['amount'])) for f in stmt['funds']],
                         [('2026-09-20', 'Owner A', D('100000')), ('2026-09-22', 'Owner A2', D('50000'))])

    def test_manager_cannot_see_another_managers_funds_or_distributions(self):
        self.auth_as(self.manager2_user)
        ids = {row['id'] for row in self.client.get('/api/manager-funds/').data}
        self.assertNotIn(self.f1.id, ids)
        self.assertEqual(self.client.get('/api/manager-labour-distributions/').data, [])

    def test_owner_cannot_distribute_and_other_manager_cannot_use_my_fund(self):
        self.assertEqual(self.batch([(self.labours[1], 10)], user=self.owner_user).status_code, 403)
        self.assertEqual(self.batch([(self.labours[1], 10)], manager=self.manager, user=self.manager2_user).status_code, 403)
        self.assertEqual(self.position()['total_distributed'], D('35000'))

    def test_unassigned_manager_cannot_distribute(self):
        # RBAC v2: a user with no UserAccess grant at all fails the RoleAllowed gate outright (403)
        # rather than reaching serializer validation (400) -- "no role anywhere" is a permission
        # question, not a data question.
        stray_user, stray = make_person('stray', Role.MANAGER, name='Stray', owner=False)
        self.assertEqual(self.batch([(self.labours[1], 10)], manager=stray, user=stray_user).status_code, 403)

    def test_permission_matrix_can_switch_capabilities_off(self):
        RolePermission.objects.update_or_create(role='MANAGER', permission='canDistributeManagerFund', defaults={'allowed': False})
        self.assertEqual(self.batch([(self.labours[1], 10)]).status_code, 403)
        RolePermission.objects.update_or_create(role='MANAGER', permission='canViewManagerFund', defaults={'allowed': False})
        self.assertEqual(self.summary(self.manager_user).status_code, 403)

    def test_viewer_role_has_no_access_by_default(self):
        RolePermission.objects.update_or_create(role='OWNER', permission='canViewManagerFund', defaults={'allowed': False})
        self.assertEqual(self.summary(self.owner_user).status_code, 403)


class ModelRuleTests(LedgerBase):
    def test_cannot_reduce_a_fund_below_what_was_distributed(self):
        fund = self.give('1000.00')
        self.batch([(self.labours[0], 600)])
        fund.fund_amount = D('500.00')
        from django.core.exceptions import ValidationError as DjangoValidationError
        with self.assertRaises(DjangoValidationError):
            fund.clean()

    def test_ledger_service_rejects_bad_batches_directly(self):
        self.give('100.00')
        with self.assertRaises(ValidationError):
            ledger.distribute(project=self.project, manager=self.manager, date='2026-09-21', actor=self.manager_user,
                              payments=[(self.labours[0], D('101.00'))])
        self.assertEqual(ManagerLabourDistribution.objects.count(), 0)


class ProjectPeopleTests(LedgerBase):
    def get(self, user, project=None):
        if user:
            self.auth_as(user)
        return self.client.get(f'/api/projects/{(project or self.project).id}/people/')

    def test_requires_authentication(self):
        self.client.credentials()
        self.assertEqual(self.get(None).status_code, 401)

    def test_owner_sees_only_the_people_of_that_project(self):
        r = self.get(self.owner_user)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([o['name'] for o in r.data['owners']], ['Owner A', 'Owner A2'])
        self.assertEqual([m['name'] for m in r.data['managers']], ['Manager 1', 'Manager 2'])
        self.assertEqual(sorted(l['name'] for l in r.data['labour']), ['Labour A', 'Labour B', 'Labour C', 'Labour D', 'Labour E'])
        self.assertNotIn('Owner B', str(r.data))                # another project's owner
        self.assertNotIn('Not on project', str(r.data))         # a labourer not linked to this project
        self.assertEqual(r.data['project']['code'], 'A')

    def test_no_mobile_numbers_or_other_private_fields(self):
        r = self.get(self.owner_user)
        for row in r.data['owners'] + r.data['managers']:
            self.assertEqual(set(row), {'id', 'name'})
        self.assertEqual(set(r.data['labour'][0]), {'id', 'name', 'type', 'is_active'})

    def test_an_assigned_manager_can_read_it(self):
        self.assertEqual(self.get(self.manager_user).status_code, 200)

    def test_people_from_another_project_are_never_shown(self):
        grant(self.manager2_user, Role.MANAGER, self.other_project)
        other_user, other_manager = make_person('mgr_b', Role.MANAGER, project=self.other_project, name='Manager B', owner=False)
        r = self.get(self.owner_user)
        self.assertNotIn('Manager B', str(r.data))
        self.assertEqual(self.get(self.owner_b_user, self.other_project).data['managers'][0]['name'], 'Manager 2')

    def test_non_members_get_404_not_403(self):
        stray_user, _ = make_person('stray', Role.MANAGER, name='Stray', owner=False)
        for user in (self.owner_b_user, stray_user):
            self.assertEqual(self.get(user).status_code, 404)
        self.assertEqual(self.client.get('/api/projects/99999/people/').status_code, 404)

    def test_admin_may_read_any_project(self):
        self.assertEqual(self.get(self.admin_user, self.other_project).status_code, 200)

    def test_it_is_read_only(self):
        self.auth_as(self.owner_user)
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(self.client, method)(f'/api/projects/{self.project.id}/people/', {}).status_code, 405, method)
        self.assertEqual(UserAccess.objects.filter(project=self.project, role__name='OWNER').count(), 2)

    def test_inactive_labour_is_flagged(self):
        ProjectLabour.objects.filter(project=self.project, labour=self.labours[0]).update(is_active=False)
        row = [l for l in self.get(self.owner_user).data['labour'] if l['id'] == self.labours[0].id][0]
        self.assertFalse(row['is_active'])


class MeEffectivePermissionsTests(LedgerBase):
    def me(self, user):
        self.auth_as(user)
        r = self.client.get('/api/me/')
        self.assertEqual(r.status_code, 200)
        return r.data

    def test_existing_fields_are_unchanged(self):
        d = self.me(self.owner_user)
        self.assertEqual((d['username'], d['role'], d['owner_id'], d['name']), ('owner_a', 'OWNER', self.owner.id, 'Owner A'))
        self.assertIsNone(d['manager_id'])
        self.assertFalse(d['is_super_admin'])

    def test_owner_permissions_follow_membership(self):
        d = self.me(self.owner_user)
        self.assertEqual(d['permissions'], {
            'canUploadBill': True, 'canViewBill': True, 'canViewManagerFund': True,
            'canGiveManagerFund': True, 'canDistributeManagerFund': False})
        self.assertEqual(set(d['project_permissions']), {str(self.project.id)})          # not project B
        self.assertEqual(set(d['project_permissions'][str(self.project.id)]),
                         {'canViewManagerFund', 'canGiveManagerFund', 'canUploadBill', 'canViewBill'})

    def test_manager_gets_only_view_and_distribute(self):
        d = self.me(self.manager_user)
        self.assertEqual(d['manager_id'], self.manager.id)
        self.assertEqual(d['name'], 'Manager 1')
        self.assertEqual(set(d['project_permissions'][str(self.project.id)]), {'canViewManagerFund', 'canDistributeManagerFund'})
        self.assertFalse(d['permissions']['canGiveManagerFund'])

    def test_super_admin_has_everything_on_every_project(self):
        d = self.me(self.admin_user)
        self.assertTrue(d['is_super_admin'])
        self.assertTrue(all(d['permissions'].values()))
        self.assertEqual(set(d['project_permissions']), {str(self.project.id), str(self.other_project.id)})
        self.assertEqual(len(d['project_permissions'][str(self.project.id)]), 5)

    def test_the_permission_matrix_changes_what_is_reported(self):
        RolePermission.objects.update_or_create(role='MANAGER', permission='canDistributeManagerFund', defaults={'allowed': False})
        d = self.me(self.manager_user)
        self.assertFalse(d['permissions']['canDistributeManagerFund'])
        self.assertEqual(d['project_permissions'][str(self.project.id)], ['canViewManagerFund'])

    def test_what_me_says_matches_what_the_api_does(self):
        self.give('1000.00')
        d = self.me(self.manager_user)
        self.assertIn('canDistributeManagerFund', d['project_permissions'][str(self.project.id)])
        self.assertEqual(self.batch([(self.labours[0], 10)]).status_code, 201)
        d = self.me(self.owner_user)
        self.assertNotIn('canDistributeManagerFund', d['project_permissions'][str(self.project.id)])
        self.assertEqual(self.batch([(self.labours[0], 10)], user=self.owner_user).status_code, 403)


@skipUnlessDBFeature('has_select_for_update')
class ConcurrencyTests(TransactionTestCase):
    """Real row locking only exists on PostgreSQL; on SQLite this class is skipped (it needs a real database)."""

    def test_two_simultaneous_distributions_cannot_spend_the_same_balance(self):
        project = Project.objects.create(name='P', code='P')
        owner_user, owner = make_person('o', Role.OWNER, project=project)
        mgr_user, manager = make_person('m', Role.MANAGER, project=project, owner=False)
        labours = [Labour.objects.create(name=f'L{i}') for i in range(6)]
        ManagerFund.objects.create(project=project, manager=manager, fund_date='2026-09-20', fund_amount='100000.00',
                                   given_by_owner=owner, payment_mode=PaymentMode.CASH)
        results, gate = [], threading.Barrier(6)

        def worker(labour):
            try:
                gate.wait()
                ledger.distribute(project=project, manager=manager, date='2026-09-21', actor=mgr_user,
                                  payments=[(labour, D('30000.00'))])
                results.append('ok')
            except ValidationError:
                results.append('rejected')
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(l,)) for l in labours]
        [t.start() for t in threads]
        [t.join() for t in threads]

        self.assertEqual(sorted(results), ['ok', 'ok', 'ok', 'rejected', 'rejected', 'rejected'])   # 3 x 30000 fits in 100000
        p = ledger.position(project, manager)
        self.assertEqual((p['total_distributed'], p['available_balance']), (D('90000'), D('10000')))
        self.assertEqual(ManagerLabourDistribution.objects.count(), 3)
        self.assertEqual(ExpenseTransaction.objects.count(), 3)
