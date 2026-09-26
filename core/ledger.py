"""
Manager Fund ledger -- the ONE place that decides a manager's money position.

    Owner  --fund-->  Manager  --distribution-->  Labour

    ManagerFund              money given to a manager for a project (NOT an expense)
    ManagerLabourDistribution money the manager hands to a labourer; mirrors exactly ONE
                             ExpenseTransaction (LABOUR), which is what project expense
                             totals and labour reports read.

Available balance = SUM(funds) - SUM(ACTIVE distributions). Nothing is stored: a distribution is
"active" while its mirrored expense is ACTIVE, so cancelling that expense (see cancel_distribution)
gives the money back and the distribution row stays for history. Project totals read only
ExpenseTransaction; the fund is never counted as expense, so nothing is counted twice.

Concurrency: distribute() runs in one transaction and locks the manager's fund rows
(SELECT ... FOR UPDATE, fixed order), so two simultaneous distributions cannot spend the same balance.
"""
import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from .models import (
    ExpenseCategory,
    ExpenseTransaction,
    Manager,
    ManagerFund,
    ManagerLabourDistribution,
    PartyType,
    ProjectLabour,
    TransactionStatus,
    active_distributions,
)

# Categories a manager can spend fund money on that are NOT mirrored into ManagerLabourDistribution.
# LABOUR is excluded from this list (see _manager_direct_labour_expenses below, which handles it
# separately to avoid double-counting rows that *are* mirrored via active_distributions()).
_MANAGER_DIRECT_CATEGORIES = (ExpenseCategory.MISCELLANEOUS, ExpenseCategory.CONTRACTOR, ExpenseCategory.SUPPLIER)


def _manager_direct_expenses(project, manager):
    """Active Other/Supplier/Contractor expenses this manager personally recorded on this project.

    A manager's spend is not only what they've handed to labour out of a ManagerFund
    (ManagerLabourDistribution) -- it also includes any other category they're granted (Other,
    Supplier, Contractor) that they record directly. Those never touch ManagerLabourDistribution,
    so a formula that only summed distributions (or only Other) under-counted a manager's real
    spend. Identified by created_by (the manager's own user), not paid_by_owner (always an Owner row).
    """
    return ExpenseTransaction.objects.filter(
        project=project, expense_category__in=_MANAGER_DIRECT_CATEGORIES,
        status=TransactionStatus.ACTIVE, created_by=manager.user_id,
    )


def _manager_direct_labour_expenses(project, manager):
    """Active LABOUR expenses this manager recorded directly via LabourPaymentViewSet (the Add
    Expense -> Labour screen), as opposed to through ledger.distribute() / ManagerLabourDistribution.

    Both are legitimate ways for a manager to pay labour out of their fund, and both must count
    towards manager_spent() -- but a distribution's mirrored ExpenseTransaction is already summed via
    active_distributions() above, so this only picks up LABOUR rows with no linked distribution
    (manager_labour_distribution__isnull=True) to avoid counting the same payment twice.
    """
    return ExpenseTransaction.objects.filter(
        project=project, expense_category=ExpenseCategory.LABOUR,
        status=TransactionStatus.ACTIVE, created_by=manager.user_id,
        manager_labour_distribution__isnull=True,
    )

ZERO = Decimal('0.00')


def _sum(queryset, field):
    return queryset.aggregate(total=Sum(field))['total'] or ZERO


def fund_given(project, manager):
    """fund_given(P,m): all ACTIVE (non-cancelled) ManagerFund given to this manager on this project."""
    return _sum(
        ManagerFund.objects.filter(project=project, manager=manager, status=TransactionStatus.ACTIVE), 'fund_amount'
    )


def manager_spent(project, manager):
    """
    manager_spent(P,m): every active ExpenseTransaction on this project paid by this manager out
    of their fund -- labour distributions (via ManagerLabourDistribution), LABOUR expenses recorded
    directly (via LabourPaymentViewSet, with no linked distribution), plus any other category
    they're granted (Other, Supplier, Contractor). Never sums ManagerLabourDistribution.amount
    directly (it already creates the ExpenseTransaction this counts), and never counts ManagerFund
    itself (it is not an expense).
    """
    return (
        _sum(active_distributions(ManagerLabourDistribution.objects.filter(project=project, manager=manager)), 'amount')
        + _sum(_manager_direct_labour_expenses(project, manager), 'amount')
        + _sum(_manager_direct_expenses(project, manager), 'amount')
    )


def manager_balance(project, manager):
    """manager_balance(P,m) = fund_given - manager_spent. May be negative (an over-spend)."""
    return fund_given(project, manager) - manager_spent(project, manager)


def project_managers(project):
    """Every Manager with any fund or recorded expense on this project (the set with_managers/owner_direct sum over)."""
    fund_manager_ids = ManagerFund.objects.filter(project=project).values_list('manager_id', flat=True)
    expense_user_ids = ExpenseTransaction.objects.filter(project=project).values_list('created_by_id', flat=True).distinct()
    manager_ids = set(fund_manager_ids) | set(
        Manager.objects.filter(user_id__in=expense_user_ids).values_list('id', flat=True)
    )
    return Manager.objects.filter(id__in=manager_ids)


def category_totals(project):
    """category_totals(P): active-only ExpenseTransaction totals per category. Always sums to total_expense(P)."""
    active = project.expense_transactions.filter(status=TransactionStatus.ACTIVE)
    return {category: _sum(active.filter(expense_category=category), 'amount') for category, _ in ExpenseCategory.choices}


def total_expense(project):
    """total_expense(P) = sum of category_totals(P): the Total Expense screen / reports figure."""
    return sum(category_totals(project).values(), ZERO)


def owner_direct(project):
    """owner_direct(P): active ExpenseTransaction on this project not paid by a manager (total_expense minus every manager's spend)."""
    spent_by_managers = sum((manager_spent(project, m) for m in project_managers(project)), ZERO)
    return total_expense(project) - spent_by_managers


def with_managers(project):
    """with_managers(P) = Σ_m manager_balance(P,m): fund money still sitting with managers (may be negative overall)."""
    return sum((manager_balance(project, m) for m in project_managers(project)), ZERO)


def total_project_spend(project):
    """total_project_spend(P) (Owner) = total_expense(P) + with_managers(P) = owner_direct(P) + Σ_m fund_given(P,m)."""
    return total_expense(project) + with_managers(project)


def manager_total_balance(manager):
    """total_balance for a manager across every project they hold a fund on."""
    project_ids = ManagerFund.objects.filter(manager=manager).values_list('project_id', flat=True).distinct()
    from .models import Project
    return sum((manager_balance(p, manager) for p in Project.objects.filter(id__in=project_ids)), ZERO)


def position(project, manager):
    """A manager's complete fund position on one project (computed from the database every time)."""
    received = fund_given(project, manager)
    distributed = manager_spent(project, manager)
    return {
        'project_id': project.id,
        'project_code': project.code,
        'manager_id': manager.id,
        'manager_name': manager.name,
        'total_received': received,
        'total_distributed': distributed,
        'available_balance': received - distributed,
    }


def _lots(project, manager, lock=False):
    """[[fund, remaining], ...] oldest fund first. With lock=True the fund rows are locked for update."""
    funds = ManagerFund.objects.filter(
        project=project, manager=manager, status=TransactionStatus.ACTIVE
    ).order_by('fund_date', 'id')
    if lock:
        funds = funds.select_for_update()
    funds = list(funds)
    used = dict(
        active_distributions(ManagerLabourDistribution.objects.filter(manager_fund__in=funds))
        .order_by().values('manager_fund').annotate(total=Sum('amount')).values_list('manager_fund', 'total')
    )
    return [[fund, fund.fund_amount - used.get(fund.id, ZERO)] for fund in funds]


def distribute(*, project, manager, date, payments, actor, remarks=''):
    """
    Save a batch of per-labour distributions, all or nothing.

    payments: [(labour, Decimal amount), ...]. Money is taken from the manager's oldest fund first; a
    payment larger than one fund's remainder is split across funds, so every row keeps the fund (and the
    owner who gave it) it came from. Returns (payment_batch uuid, [ManagerLabourDistribution, ...]).
    """
    total = sum((amount for _, amount in payments), ZERO)
    if not payments or total <= 0:
        raise ValidationError({'payments': 'The batch total must be greater than zero.'})

    with transaction.atomic():
        lots = _lots(project, manager, lock=True)
        # The single source of truth for "how much may this manager still spend" is
        # manager_balance(P,m) (fund_given - manager_spent), not the raw FIFO-lot sum: a manager who
        # has already spent fund money on a direct Misc/Supplier/Contractor expense has less available
        # than the lots alone show (lots only track labour distributions). manager_balance is always
        # <= the lot sum, so the FIFO walk below still finds enough remaining lot balance to cover it.
        available = manager_balance(project, manager)
        if total > available:
            raise ValidationError({
                'payments': f'Batch total {total} exceeds the available fund balance {available}. Nothing was saved.'
            })

        batch = uuid.uuid4()
        rows = []
        for labour, amount in payments:
            need = amount
            for lot in lots:
                if need <= 0:
                    break
                fund, remaining = lot
                take = min(need, remaining)
                if take <= 0:
                    continue
                expense = ExpenseTransaction.objects.create(
                    project=project,
                    expense_date=date,
                    expense_category=ExpenseCategory.LABOUR,
                    expense_type='Labour Payment (via Manager Fund)',
                    party_type=PartyType.LABOUR,
                    labour=labour,
                    paid_by_owner=fund.given_by_owner,
                    amount=take,
                    payment_mode=fund.payment_mode,
                    description=f'Distributed by manager {manager.name} from fund #{fund.id}',
                    remarks=remarks or None,
                    payment_batch=batch,
                    created_by=actor,
                )
                rows.append(ManagerLabourDistribution.objects.create(
                    manager_fund=fund, project=project, manager=manager, date=date, labour=labour,
                    amount=take, remarks=remarks or '', created_by=actor, expense_transaction=expense,
                ))
                lot[1] -= take
                need -= take
            if need > 0:  # cannot happen after the balance check above; never save a partial payment
                raise ValidationError({'payments': 'The fund balance changed. Nothing was saved.'})

        # Paying an inactive labour means they are working on this project again (same as Owner labour payments).
        ProjectLabour.objects.filter(
            project=project, labour__in=[labour for labour, _ in payments], is_active=False
        ).update(is_active=True)
    return batch, rows


def cancel_distribution(distribution, cancelled_by, reason):
    """
    Reverse ONE distribution: cancel its own mirrored expense. The distribution row stays (history) and
    stops counting, so the manager's balance goes up by exactly its amount. No other row is touched.
    """
    with transaction.atomic():
        distribution = (
            ManagerLabourDistribution.objects.select_for_update(of=('self',))   # lock this row only (PostgreSQL
            .select_related('expense_transaction').get(pk=distribution.pk)      # cannot lock a nullable join side)
        )
        expense = distribution.expense_transaction
        if expense is None:
            raise ValidationError('This distribution has no linked expense to cancel.')
        if expense.status == TransactionStatus.CANCELLED:
            raise ValidationError('This distribution is already cancelled.')
        expense.cancel(cancelled_by=cancelled_by, reason=reason)
    return distribution


def statement(project, manager):
    """Fund history, distribution history and a date-wise ledger with a running balance."""
    funds = list(
        ManagerFund.objects.filter(project=project, manager=manager)
        .select_related('given_by_owner', 'created_by').order_by('fund_date', 'id'))
    lots = {fund.id: remaining for fund, remaining in _lots(project, manager)}
    fund_rows = [{
        'id': f.id, 'date': f.fund_date, 'given_by_owner_id': f.given_by_owner_id,
        'given_by_owner_name': f.given_by_owner.name, 'amount': f.fund_amount, 'payment_mode': f.payment_mode,
        'remarks': f.remarks, 'created_by': f.created_by.get_username() if f.created_by else None,
        'remaining': lots.get(f.id, ZERO), 'status': f.status,
    } for f in funds]

    dists = list(
        ManagerLabourDistribution.objects.filter(project=project, manager=manager)
        .select_related('labour', 'expense_transaction', 'created_by').order_by('date', 'id'))
    dist_rows = [{
        'id': d.id, 'date': d.date, 'labour_id': d.labour_id, 'labour_name': d.labour.name, 'amount': d.amount,
        'status': 'ACTIVE' if d.is_active else 'CANCELLED', 'manager_fund_id': d.manager_fund_id,
        'payment_batch': str(d.expense_transaction.payment_batch) if d.expense_transaction and d.expense_transaction.payment_batch else None,
        'remarks': d.remarks, 'created_by': d.created_by.get_username() if d.created_by else None, 'source': 'DISTRIBUTION',
    } for d in dists]

    # A manager can also pay labour directly (LabourPaymentViewSet, the Add Expense -> Labour screen)
    # without ever creating a ManagerLabourDistribution row -- see _manager_direct_labour_expenses().
    # Both are real money out of this manager's fund and must appear in their history/ledger the same
    # way manager_spent() already counts both towards the balance above. include all statuses (not
    # just ACTIVE) so a cancelled direct payment shows up in history exactly like a cancelled
    # distribution does. manager_fund_id is None: a direct payment isn't drawn from one specific lot.
    directs = list(
        ExpenseTransaction.objects.filter(
            project=project, expense_category=ExpenseCategory.LABOUR,
            created_by=manager.user_id, manager_labour_distribution__isnull=True,
        ).select_related('labour', 'created_by').order_by('expense_date', 'id'))
    dist_rows += [{
        'id': e.id, 'date': e.expense_date, 'labour_id': e.labour_id, 'labour_name': e.labour.name if e.labour_id else '',
        'amount': e.amount, 'status': e.status, 'manager_fund_id': None,
        'payment_batch': str(e.payment_batch) if e.payment_batch else None,
        'remarks': e.remarks, 'created_by': e.created_by.get_username() if e.created_by else None, 'source': 'DIRECT',
    } for e in directs]
    dist_rows.sort(key=lambda r: (r['date'], r['id']))

    entries = [{'kind': 'FUND', 'order': 0, 'id': r['id'], 'date': r['date'], 'amount': r['amount'],
                'label': f"Fund from {r['given_by_owner_name']}", 'status': r['status']} for r in fund_rows]
    entries += [{'kind': 'DISTRIBUTION', 'order': 1, 'id': r['id'], 'date': r['date'], 'amount': r['amount'],
                 'label': f"To {r['labour_name']}" + ('' if r['source'] == 'DISTRIBUTION' else ' (direct)'),
                 'status': r['status']} for r in dist_rows]
    entries.sort(key=lambda e: (e['date'], e['order'], e['id']))
    running = ZERO
    for e in entries:                       # cancelled funds/distributions are listed but do not move the balance
        if e['status'] != 'ACTIVE':
            pass
        elif e['kind'] == 'FUND':
            running += e['amount']
        else:
            running -= e['amount']
        e['running_balance'] = running
        del e['order']

    return {'position': position(project, manager), 'funds': fund_rows, 'distributions': dist_rows, 'ledger': entries}
