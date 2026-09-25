"""
Report (does not fix) ExpenseTransaction/ManagerFund rows whose created_at, converted to IST,
falls on a different calendar day than the date field the row was saved with (expense_date /
fund_date). Prints a count and the row ids for each; does not touch the database.

    python manage.py check_dates
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import ExpenseTransaction, ManagerFund


class Command(BaseCommand):
    help = 'Report ExpenseTransaction/ManagerFund rows whose created_at (IST) != stored date.'

    def handle(self, *args, **options):
        self._check(ExpenseTransaction.objects.all(), 'expense_date', 'ExpenseTransaction')
        self._check(ManagerFund.objects.all(), 'fund_date', 'ManagerFund')

    def _check(self, queryset, date_field, label):
        mismatched_ids = [
            row.id for row in queryset.only('id', 'created_at', date_field)
            if timezone.localtime(row.created_at).date() != getattr(row, date_field)
        ]
        self.stdout.write(f'{label}: {len(mismatched_ids)} mismatch(es) found.')
        if mismatched_ids:
            self.stdout.write(f'{label} ids: {mismatched_ids}')
