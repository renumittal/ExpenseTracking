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
    ManagerFund,
    ManagerLabourDistribution,
    PartyType,
    ProjectLabour,
    TransactionStatus,
    active_distributions,
)


def _manager_other_expenses(project, manager):
    """Active MISCELLANEOUS ('Other') expenses this manager personally recorded on this project.

    A manager's "Total Distributed" is not only what they've handed to labour out of a
    ManagerFund (ManagerLabourDistribution) -- it also includes Other-category expenses they
    record directly (canAddMiscExpense). Those never touch ManagerLabourDistribution, so a
    formula that only summed distributions under-counted a manager's real spend. Identified by
    created_by (the manager's own user), not paid_by_owner (which is always an Owner row).
    """
    return ExpenseTransaction.objects.filter(
        project=project, expense_category=ExpenseCategory.MISCELLANEOUS,
        status=TransactionStatus.ACTIVE, created_by=manager.user_id,
    )

ZERO = Decimal('0.00')


def _sum(queryset, field):
    return queryset.aggregate(total=Sum(field))['total'] or ZERO


def position(project, manager):
    """
    A manager's complete fund position on one project (computed from the database every time).

    total_distributed = active labour distributions (money handed to labour out of this
    manager's fund) + active Other-category expenses this manager recorded themselves. Previously
    this only counted labour distributions, which under-stated how much of the fund a manager who
    also records Other expenses had actually spent.
    """
    received = _sum(ManagerFund.objects.filter(project=project, manager=manager), 'fund_amount')
    distributed = (
        _sum(active_distributions(ManagerLabourDistribution.objects.filter(project=project, manager=manager)), 'amount')
        + _sum(_manager_other_expenses(project, manager), 'amount')
    )
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
    funds = ManagerFund.objects.filter(project=project, manager=manager).order_by('fund_date', 'id')
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
        available = sum((max(remaining, ZERO) for _, remaining in lots), ZERO)
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
        'remaining': lots.get(f.id, ZERO),
    } for f in funds]

    dists = list(
        ManagerLabourDistribution.objects.filter(project=project, manager=manager)
        .select_related('labour', 'expense_transaction', 'created_by').order_by('date', 'id'))
    dist_rows = [{
        'id': d.id, 'date': d.date, 'labour_id': d.labour_id, 'labour_name': d.labour.name, 'amount': d.amount,
        'status': 'ACTIVE' if d.is_active else 'CANCELLED', 'manager_fund_id': d.manager_fund_id,
        'payment_batch': str(d.expense_transaction.payment_batch) if d.expense_transaction and d.expense_transaction.payment_batch else None,
        'remarks': d.remarks, 'created_by': d.created_by.get_username() if d.created_by else None,
    } for d in dists]

    entries = [{'kind': 'FUND', 'order': 0, 'id': r['id'], 'date': r['date'], 'amount': r['amount'],
                'label': f"Fund from {r['given_by_owner_name']}", 'status': 'ACTIVE'} for r in fund_rows]
    entries += [{'kind': 'DISTRIBUTION', 'order': 1, 'id': r['id'], 'date': r['date'], 'amount': r['amount'],
                 'label': f"To {r['labour_name']}", 'status': r['status']} for r in dist_rows]
    entries.sort(key=lambda e: (e['date'], e['order'], e['id']))
    running = ZERO
    for e in entries:                       # cancelled distributions are listed but do not move the balance
        if e['kind'] == 'FUND':
            running += e['amount']
        elif e['status'] == 'ACTIVE':
            running -= e['amount']
        e['running_balance'] = running
        del e['order']

    return {'position': position(project, manager), 'funds': fund_rows, 'distributions': dist_rows, 'ledger': entries}
