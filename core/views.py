from django.db.models import Sum
from rest_framework import status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    ContractorContract,
    ExpenseCategory,
    ExpenseTransaction,
    Labour,
    ManagerFund,
    ManagerLabourDistribution,
    Project,
    Role,
    Supplier,
    TransactionStatus,
    ZERO,
)
from .permissions import RoleAllowed, get_role, is_admin
from .serializers import (
    ContractorContractSerializer,
    ExpenseTransactionSerializer,
    LabourSerializer,
    ManagerFundSerializer,
    ManagerLabourDistributionSerializer,
    ProjectSerializer,
    SupplierSerializer,
)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginView(ObtainAuthToken):
    """POST {username, password} -> {token, role, username}."""

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        token, _ = Token.objects.get_or_create(user=user)
        return Response({
            'token': token.key,
            'username': user.username,
            'role': get_role(user),
        })


class LogoutView(APIView):
    """POST (authenticated) -> deletes the caller's token."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """GET (authenticated) -> who is logged in and their owner id (needed as `paid_by_owner`)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        owner = getattr(user, 'owner_profile', None)
        return Response({
            'username': user.username,
            'role': get_role(user),
            'owner_id': owner.id if owner else None,
            'name': owner.name if owner else user.get_username(),
        })


# ---------------------------------------------------------------------------
# Owner-facing endpoints
# ---------------------------------------------------------------------------

class ProjectViewSet(viewsets.ReadOnlyModelViewSet):
    """Admin: all projects. Owner: only projects they're linked to via ProjectOwner."""

    serializer_class = ProjectSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}

    def get_queryset(self):
        user = self.request.user
        if is_admin(user):
            return Project.objects.all()
        return Project.objects.filter(project_owners__owner__user=user).distinct()

    @action(detail=True, methods=['get'])
    def summary(self, request, pk=None):
        """Total expense + category-wise breakup (ACTIVE transactions only)."""
        project = self.get_object()
        active = project.expense_transactions.filter(status=TransactionStatus.ACTIVE)
        total = active.aggregate(total=Sum('amount'))['total'] or ZERO
        category_breakup = {
            category: active.filter(expense_category=category).aggregate(total=Sum('amount'))['total'] or ZERO
            for category, _ in ExpenseCategory.choices
        }
        return Response({
            'project': project.code,
            'total_expense': total,
            'category_breakup': category_breakup,
        })

    @action(detail=True, methods=['get'], url_path='owners-summary')
    def owners_summary(self, request, pk=None):
        """Owner-wise contribution breakup (ACTIVE transactions only)."""
        project = self.get_object()
        active = project.expense_transactions.filter(status=TransactionStatus.ACTIVE)
        rows = (
            active.values('paid_by_owner_id', 'paid_by_owner__name')
            .annotate(total=Sum('amount'))
            .order_by('-total')
        )
        return Response({
            'project': project.code,
            'owners': [
                {
                    'owner_id': row['paid_by_owner_id'],
                    'owner_name': row['paid_by_owner__name'],
                    'total': row['total'] or ZERO,
                }
                for row in rows
            ],
        })

    @action(detail=True, methods=['get'])
    def dashboard(self, request, pk=None):
        """
        Project Dashboard (requirement doc Section 12): total expense,
        category-wise breakup, owner-wise contribution, contractor position
        list, and manager fund summary -- all in one response.

        category_breakup's 4 values always sum exactly to total_expense
        because every ACTIVE ExpenseTransaction has exactly one of those 4
        categories, and ManagerFund itself is never summed here (see
        ManagerLabourDistribution's docstring in models.py) -- only the
        ExpenseTransaction rows it creates are counted, so there is no
        double counting.
        """
        project = self.get_object()
        active = project.expense_transactions.filter(status=TransactionStatus.ACTIVE)

        total = active.aggregate(total=Sum('amount'))['total'] or ZERO
        category_breakup = {
            category: active.filter(expense_category=category).aggregate(total=Sum('amount'))['total'] or ZERO
            for category, _ in ExpenseCategory.choices
        }

        owner_rows = (
            active.values('paid_by_owner_id', 'paid_by_owner__name')
            .annotate(total=Sum('amount'))
            .order_by('-total')
        )
        owner_contribution = [
            {'owner_id': row['paid_by_owner_id'], 'owner_name': row['paid_by_owner__name'], 'total': row['total'] or ZERO}
            for row in owner_rows
        ]

        contractor_positions = [
            {
                'contract_id': contract.id,
                'contractor_id': contract.contractor_id,
                'contractor_name': contract.contractor.name,
                'contract_amount': contract.contract_amount,
                'paid_amount': contract.paid_amount,
                'balance': contract.balance,
            }
            for contract in project.contractor_contracts.select_related('contractor')
        ]

        manager_fund_summary = [
            {
                'fund_id': fund.id,
                'manager_id': fund.manager_id,
                'manager_name': fund.manager.name,
                'fund_amount': fund.fund_amount,
                'distributed_amount': fund.distributed_amount,
                'balance': fund.balance,
            }
            for fund in project.manager_funds.select_related('manager')
        ]

        return Response({
            'project': project.code,
            'total_expense': total,
            'category_breakup': category_breakup,
            'owner_contribution': owner_contribution,
            'contractor_positions': contractor_positions,
            'manager_fund_summary': manager_fund_summary,
        })


class ExpenseTransactionViewSet(viewsets.ModelViewSet):
    """
    Admin: all transactions.
    Owner: transactions for any project they're linked to (all owners' entries
    on that project, not just their own -- owners can see each other there).

    No hard delete/update: only create, list, retrieve, and the `cancel` action
    (which sets status=CANCELLED with a required reason, never removes the row).
    """

    serializer_class = ExpenseTransactionSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        qs = ExpenseTransaction.objects.all() if is_admin(user) else ExpenseTransaction.objects.filter(
            project__project_owners__owner__user=user
        ).distinct()

        params = self.request.query_params
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
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('labour'):
            qs = qs.filter(labour_id=params['labour'])
        if params.get('contractor_contract'):
            qs = qs.filter(contractor_contract_id=params['contractor_contract'])
        if params.get('supplier'):
            qs = qs.filter(supplier_id=params['supplier'])
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        project = serializer.validated_data['project']
        if not is_admin(user):
            owned = Project.objects.filter(
                project_owners__owner__user=user, pk=project.pk
            ).exists()
            if not owned:
                raise PermissionDenied('You are not authorized on this project.')
        serializer.save(created_by=user)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Reversal, never a hard delete: sets status=CANCELLED with a required reason."""
        instance = self.get_object()
        reason = request.data.get('remarks') or request.data.get('reason')
        if not reason:
            raise ValidationError({'remarks': 'A reason is required to cancel a transaction.'})
        if instance.status == TransactionStatus.CANCELLED:
            raise ValidationError('Transaction is already cancelled.')
        instance.cancel(cancelled_by=request.user, reason=reason)
        return Response(self.get_serializer(instance).data)


# ---------------------------------------------------------------------------
# Manager-facing endpoints
# ---------------------------------------------------------------------------

class ManagerFundViewSet(viewsets.ModelViewSet):
    """Admin: all funds. Manager: only funds received by themselves."""

    serializer_class = ManagerFundSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.MANAGER}

    def get_queryset(self):
        user = self.request.user
        if is_admin(user):
            return ManagerFund.objects.all()
        return ManagerFund.objects.filter(manager__user=user)

    def perform_create(self, serializer):
        user = self.request.user
        manager = serializer.validated_data['manager']
        if not is_admin(user) and manager.user_id != user.id:
            raise PermissionDenied('You may only record funds received by yourself.')
        serializer.save()


class ManagerLabourDistributionViewSet(viewsets.ModelViewSet):
    """
    Admin: all distributions. Manager: only distributions made by themselves.

    create() also auto-creates the linked LABOUR ExpenseTransaction, handled
    entirely by ManagerLabourDistribution.save() in models.py -- not duplicated here.
    """

    serializer_class = ManagerLabourDistributionSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.MANAGER}
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        if is_admin(user):
            return ManagerLabourDistribution.objects.all()
        return ManagerLabourDistribution.objects.filter(manager__user=user)

    def perform_create(self, serializer):
        user = self.request.user
        manager_fund = serializer.validated_data['manager_fund']
        if not is_admin(user) and manager_fund.manager.user_id != user.id:
            raise PermissionDenied('You may only distribute from your own funds.')
        serializer.save()


class ManagerSummaryView(APIView):
    """Manager's own distributed/balance summary across their ManagerFund records."""

    permission_classes = [RoleAllowed]
    allowed_roles = {Role.MANAGER}

    def get(self, request):
        user = request.user
        funds = ManagerFund.objects.all() if is_admin(user) else ManagerFund.objects.filter(manager__user=user)
        data = [
            {
                'id': fund.id,
                'project': fund.project.code,
                'fund_amount': fund.fund_amount,
                'distributed_amount': fund.distributed_amount,
                'balance': fund.balance,
            }
            for fund in funds
        ]
        return Response(data)


# ---------------------------------------------------------------------------
# Reference data (owner read access scoped to their own projects, admin full)
# ---------------------------------------------------------------------------

class SupplierViewSet(viewsets.ModelViewSet):
    """List + add only (owners can add a new supplier by name while entering an expense)."""

    serializer_class = SupplierSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}
    queryset = Supplier.objects.all()
    http_method_names = ['get', 'post', 'head', 'options']


class LabourViewSet(viewsets.ModelViewSet):
    """List + add only (owners can add a new labour by name while entering an expense)."""

    serializer_class = LabourSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}
    queryset = Labour.objects.all()
    http_method_names = ['get', 'post', 'head', 'options']


class ContractorContractViewSet(viewsets.ModelViewSet):
    serializer_class = ContractorContractSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}

    def get_queryset(self):
        user = self.request.user
        if is_admin(user):
            return ContractorContract.objects.all()
        return ContractorContract.objects.filter(project__project_owners__owner__user=user).distinct()

    def perform_create(self, serializer):
        user = self.request.user
        project = serializer.validated_data['project']
        if not is_admin(user):
            owned = Project.objects.filter(
                project_owners__owner__user=user, pk=project.pk
            ).exists()
            if not owned:
                raise PermissionDenied('You are not authorized on this project.')
        serializer.save()
