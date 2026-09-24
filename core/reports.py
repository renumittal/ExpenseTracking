"""
Standalone reporting endpoints (requirement doc Section 12), separate from
the CRUD viewsets in views.py. The Project Dashboard itself lives as an
action on ProjectViewSet (see views.py) since it's project-detail-scoped
like the existing summary/owners-summary actions; everything here is a
cross-cutting report reachable at /api/reports/<name>/.

Money-in-flight rule (see ManagerLabourDistribution in models.py): a
ManagerFund is not itself an expense. Only ExpenseTransaction rows are ever
summed for expense totals here -- never ManagerFund.fund_amount -- so labour
paid via manager distribution is counted exactly once, through the
ExpenseTransaction that distribution auto-creates.
"""

import csv

from django.db.models import Q, Sum
from django.http import HttpResponse
from rest_framework import generics, pagination
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    ContractorContract,
    ExpenseCategory,
    ExpenseTransaction,
    ManagerFund,
    Project,
    Role,
    TransactionStatus,
    ZERO,
)
from .access import services
from .permissions import RoleAllowed, is_admin
from .serializers import ExpenseTransactionSerializer


# ---------------------------------------------------------------------------
# Shared scoping / filtering helpers
# ---------------------------------------------------------------------------

def scoped_projects(user):
    """All projects for admin; only linked projects for an owner."""
    if is_admin(user):
        return Project.objects.all()
    return services.projects_with_role(user, 'OWNER')


def scoped_transactions(user):
    """ExpenseTransaction rows the user is allowed to see, any status."""
    return ExpenseTransaction.objects.filter(project__in=scoped_projects(user))


def get_scoped_project(user, pk):
    try:
        return scoped_projects(user).get(pk=pk)
    except Project.DoesNotExist:
        raise NotFound('Project not found.')


def apply_report_filters(qs, params, default_active_only=True):
    """Common query-param filters shared by every report below."""
    if params.get('project'):
        qs = qs.filter(project_id=params['project'])
    if params.get('category'):
        qs = qs.filter(expense_category=params['category'])
    if params.get('date_from'):
        qs = qs.filter(expense_date__gte=params['date_from'])
    if params.get('date_to'):
        qs = qs.filter(expense_date__lte=params['date_to'])
    if params.get('owner'):
        qs = qs.filter(paid_by_owner_id=params['owner'])
    if params.get('payment_mode'):
        qs = qs.filter(payment_mode=params['payment_mode'])
    if params.get('party_type'):
        qs = qs.filter(party_type=params['party_type'])
    if params.get('labour'):
        qs = qs.filter(labour_id=params['labour'])
    if params.get('contractor'):
        qs = qs.filter(contractor_contract__contractor_id=params['contractor'])
    if params.get('contractor_contract'):
        qs = qs.filter(contractor_contract_id=params['contractor_contract'])
    if params.get('supplier'):
        qs = qs.filter(supplier_id=params['supplier'])

    status_param = params.get('status')
    if status_param:
        if status_param != 'ALL':
            qs = qs.filter(status=status_param)
    elif default_active_only:
        qs = qs.filter(status=TransactionStatus.ACTIVE)

    return qs


# ---------------------------------------------------------------------------
# 2. Category-wise Expense Report
# ---------------------------------------------------------------------------

class CategoryExpenseReportView(APIView):
    """GET /reports/category-expense/?project=&category=&date_from=&date_to="""

    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}

    def get(self, request):
        qs = apply_report_filters(scoped_transactions(request.user), request.query_params)

        totals_by_category = [
            {'category': row['expense_category'], 'total': row['total'] or ZERO}
            for row in qs.values('expense_category').annotate(total=Sum('amount')).order_by('expense_category')
        ]
        breakdown = [
            {
                'category': row['expense_category'],
                'expense_type': row['expense_type'],
                'total': row['total'] or ZERO,
            }
            for row in qs.values('expense_category', 'expense_type')
            .annotate(total=Sum('amount'))
            .order_by('expense_category', '-total')
        ]

        return Response({
            'grand_total': qs.aggregate(total=Sum('amount'))['total'] or ZERO,
            'totals_by_category': totals_by_category,
            'breakdown': breakdown,
        })


# ---------------------------------------------------------------------------
# 3. Contractor-wise Report
# ---------------------------------------------------------------------------

class ContractorReportView(APIView):
    """GET /reports/contractor/?project= (contract vs paid vs balance)."""

    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}

    def get(self, request):
        projects = scoped_projects(request.user)
        if request.query_params.get('project'):
            projects = projects.filter(pk=request.query_params['project'])

        contracts = ContractorContract.objects.filter(project__in=projects).select_related('contractor', 'project')
        if request.query_params.get('contractor'):
            contracts = contracts.filter(contractor_id=request.query_params['contractor'])

        rows = [
            {
                'project_id': c.project_id,
                'project_code': c.project.code,
                'contract_id': c.id,
                'contractor_id': c.contractor_id,
                'contractor_name': c.contractor.name,
                'work_description': c.work_description,
                'contract_amount': c.contract_amount,
                'paid_amount': c.paid_amount,
                'balance': c.balance,
            }
            for c in contracts
        ]

        totals_by_contractor = {}
        for row in rows:
            key = row['contractor_id']
            bucket = totals_by_contractor.setdefault(key, {
                'contractor_id': key,
                'contractor_name': row['contractor_name'],
                'contract_amount': ZERO,
                'paid_amount': ZERO,
                'balance': ZERO,
            })
            bucket['contract_amount'] += row['contract_amount']
            bucket['paid_amount'] += row['paid_amount']
            bucket['balance'] += row['balance']

        return Response({
            'contracts': rows,
            'contractor_totals': list(totals_by_contractor.values()),
        })


# ---------------------------------------------------------------------------
# 4. Supplier-wise Payment Report
# ---------------------------------------------------------------------------

class SupplierReportView(APIView):
    """GET /reports/supplier/?project= -- total paid per supplier."""

    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}

    def get(self, request):
        qs = apply_report_filters(scoped_transactions(request.user), request.query_params)
        qs = qs.filter(expense_category=ExpenseCategory.SUPPLIER)

        rows = (
            qs.values('supplier_id', 'supplier__name')
            .annotate(total_paid=Sum('amount'))
            .order_by('-total_paid')
        )
        return Response({
            'suppliers': [
                {
                    'supplier_id': row['supplier_id'],
                    'supplier_name': row['supplier__name'],
                    'total_paid': row['total_paid'] or ZERO,
                }
                for row in rows
            ],
            'grand_total': qs.aggregate(total=Sum('amount'))['total'] or ZERO,
        })


# ---------------------------------------------------------------------------
# 5. Labour-wise Payment Report
# ---------------------------------------------------------------------------

class LabourReportView(APIView):
    """
    GET /reports/labour/?project= -- total paid per labour/mistri.

    Includes amounts paid via Manager distribution: those already exist as
    ordinary LABOUR ExpenseTransaction rows (see ManagerLabourDistribution.save
    in models.py), so they fall out of this aggregation for free. The
    via_manager_amount/direct_amount split below is informational only and
    always sums back to total_paid for that labour.
    """

    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}

    def get(self, request):
        qs = apply_report_filters(scoped_transactions(request.user), request.query_params)
        qs = qs.filter(expense_category=ExpenseCategory.LABOUR)

        rows = []
        for row in qs.values('labour_id', 'labour__name').annotate(total_paid=Sum('amount')).order_by('-total_paid'):
            via_manager = (
                qs.filter(labour_id=row['labour_id'], manager_labour_distribution__isnull=False)
                .aggregate(total=Sum('amount'))['total']
                or ZERO
            )
            total_paid = row['total_paid'] or ZERO
            rows.append({
                'labour_id': row['labour_id'],
                'labour_name': row['labour__name'],
                'total_paid': total_paid,
                'via_manager_amount': via_manager,
                'direct_amount': total_paid - via_manager,
            })

        return Response({
            'labour': rows,
            'grand_total': qs.aggregate(total=Sum('amount'))['total'] or ZERO,
        })


# ---------------------------------------------------------------------------
# 6. Miscellaneous Expense Report
# ---------------------------------------------------------------------------

class MiscExpenseReportView(APIView):
    """GET /reports/misc/?project= -- grouped by expense_type."""

    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}

    def get(self, request):
        qs = apply_report_filters(scoped_transactions(request.user), request.query_params)
        qs = qs.filter(expense_category=ExpenseCategory.MISCELLANEOUS)

        rows = qs.values('expense_type').annotate(total=Sum('amount')).order_by('-total')
        return Response({
            'expense_types': [
                {'expense_type': row['expense_type'], 'total': row['total'] or ZERO}
                for row in rows
            ],
            'grand_total': qs.aggregate(total=Sum('amount'))['total'] or ZERO,
        })


# ---------------------------------------------------------------------------
# 7. Manager Fund Report
# ---------------------------------------------------------------------------

class ManagerFundReportView(APIView):
    """
    GET /reports/manager-fund/?project= -- distributed/balance per manager
    per project. Admin/Owner see funds within their project scope; a Manager
    sees only their own fund rows.
    """

    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER, Role.MANAGER}

    def get(self, request):
        user = request.user
        if is_admin(user):
            funds = ManagerFund.objects.all()
        else:
            # Rows on any project this user holds an OWNER-role grant on, plus their own rows
            # anywhere as a manager (a person can hold both, on different projects).
            owned = services.projects_with_role(user, 'OWNER')
            funds = ManagerFund.objects.filter(Q(project__in=owned) | Q(manager__user=user))

        if request.query_params.get('project'):
            funds = funds.filter(project_id=request.query_params['project'])
        if request.query_params.get('manager'):
            funds = funds.filter(manager_id=request.query_params['manager'])

        funds = funds.select_related('manager', 'project')
        rows = [
            {
                'fund_id': fund.id,
                'project_id': fund.project_id,
                'project_code': fund.project.code,
                'manager_id': fund.manager_id,
                'manager_name': fund.manager.name,
                'fund_date': fund.fund_date,
                'fund_amount': fund.fund_amount,
                'distributed_amount': fund.distributed_amount,
                'balance': fund.balance,
            }
            for fund in funds
        ]
        return Response({'manager_funds': rows})


# ---------------------------------------------------------------------------
# 8. Date-wise Expense Report
# ---------------------------------------------------------------------------

class DateWiseExpenseReportView(APIView):
    """GET /reports/date-wise/?project=&date_from=&date_to= -- daily totals."""

    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}

    def get(self, request):
        qs = apply_report_filters(scoped_transactions(request.user), request.query_params)
        rows = qs.values('expense_date').annotate(total=Sum('amount')).order_by('expense_date')
        return Response({
            'daily_totals': [
                {'date': row['expense_date'], 'total': row['total'] or ZERO}
                for row in rows
            ],
            'grand_total': qs.aggregate(total=Sum('amount'))['total'] or ZERO,
        })


# ---------------------------------------------------------------------------
# 9. Complete Payment Register
# ---------------------------------------------------------------------------

class PaymentRegisterPagination(pagination.PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 500


class PaymentRegisterView(generics.ListAPIView):
    """
    GET /reports/payment-register/ -- paginated, filterable (date range,
    project, category, party, owner, payment mode), sorted by date desc.

    GET /reports/payment-register/?export=csv streams the same filtered,
    unpaginated result set as a CSV download.
    """

    serializer_class = ExpenseTransactionSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}
    pagination_class = PaymentRegisterPagination

    def get_queryset(self):
        qs = apply_report_filters(scoped_transactions(self.request.user), self.request.query_params)
        return qs.select_related(
            'project', 'paid_by_owner', 'labour', 'supplier', 'contractor_contract__contractor',
        ).order_by('-expense_date', '-id')

    def list(self, request, *args, **kwargs):
        if request.query_params.get('export') == 'csv':
            return self._export_csv(self.get_queryset())
        return super().list(request, *args, **kwargs)

    @staticmethod
    def _party_label(txn):
        if txn.labour_id:
            return txn.labour.name
        if txn.contractor_contract_id:
            return txn.contractor_contract.contractor.name
        if txn.supplier_id:
            return txn.supplier.name
        return txn.payee_name or ''

    def _export_csv(self, queryset):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="payment_register.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'ID', 'Date', 'Project', 'Category', 'Expense Type', 'Party Type', 'Party',
            'Owner', 'Amount', 'Payment Mode', 'Reference No', 'Status',
        ])
        for txn in queryset:
            writer.writerow([
                txn.id, txn.expense_date, txn.project.code, txn.expense_category, txn.expense_type,
                txn.party_type, self._party_label(txn), txn.paid_by_owner.name, txn.amount,
                txn.payment_mode, txn.reference_no or '', txn.status,
            ])
        return response
