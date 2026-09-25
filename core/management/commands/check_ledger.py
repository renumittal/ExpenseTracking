"""
Reconciliation report for core/ledger.py: recomputes every money identity per project and prints
any mismatch. Exits non-zero if anything failed, so it can be wired into CI.

    python manage.py check_ledger
"""
from django.core.management.base import BaseCommand

from core import ledger
from core.models import ExpenseCategory, ManagerLabourDistribution, Project, TransactionStatus


class Command(BaseCommand):
    help = 'Recompute the ledger identities for every project and report mismatches.'

    def handle(self, *args, **options):
        failures = 0
        for project in Project.objects.order_by('code'):
            failures += self._check_project(project)

        if failures:
            self.stderr.write(self.style.ERROR(f'{failures} ledger mismatch(es) found.'))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS('Ledger is consistent for every project.'))

    def _check_project(self, project):
        failures = 0
        managers = list(ledger.project_managers(project))

        total_expense = ledger.total_expense(project)
        owner_direct = ledger.owner_direct(project)
        with_managers = ledger.with_managers(project)
        total_project_spend = ledger.total_project_spend(project)
        fund_given_total = sum((ledger.fund_given(project, m) for m in managers), ledger.ZERO)
        manager_spent_total = sum((ledger.manager_spent(project, m) for m in managers), ledger.ZERO)

        # 1. total_project_spend(P) == owner_direct(P) + Σ_m fund_given(P,m)
        failures += self._assert(
            project, '1. total_project_spend == owner_direct + Σ fund_given',
            total_project_spend, owner_direct + fund_given_total,
        )

        # 2. Σ_m manager_spent(P,m) + owner_direct(P) == total_expense(P)
        failures += self._assert(
            project, '2. Σ manager_spent + owner_direct == total_expense',
            manager_spent_total + owner_direct, total_expense,
        )

        # 3. Σ category_totals(P) == total_expense(P)
        category_sum = sum(ledger.category_totals(project).values(), ledger.ZERO)
        failures += self._assert(
            project, '3. Σ category_totals == total_expense', category_sum, total_expense,
        )

        # 4. Each active ManagerLabourDistribution <-> exactly one active ExpenseTransaction
        for dist in ManagerLabourDistribution.objects.filter(project=project).select_related('expense_transaction'):
            expense = dist.expense_transaction
            if expense is None:
                self._fail(project, f'4. distribution #{dist.id} has no mirrored expense')
                failures += 1
                continue
            if dist.is_active != (expense.status == TransactionStatus.ACTIVE):
                self._fail(
                    project,
                    f'4. distribution #{dist.id} active={dist.is_active} but '
                    f'expense #{expense.id} status={expense.status}',
                )
                failures += 1
            if expense.expense_category != ExpenseCategory.LABOUR or expense.amount != dist.amount:
                self._fail(
                    project,
                    f'4. distribution #{dist.id} (amount {dist.amount}) does not match its expense '
                    f'#{expense.id} ({expense.expense_category}, {expense.amount})',
                )
                failures += 1

        return failures

    def _assert(self, project, label, left, right):
        if left != right:
            self._fail(project, f'{label}: {left} != {right}')
            return 1
        return 0

    def _fail(self, project, message):
        self.stderr.write(self.style.ERROR(f'[{project.code}] {message}'))
