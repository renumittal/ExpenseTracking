import uuid

from django.db import transaction
from django.db.models import F, Max, Sum
from rest_framework import mixins, status, viewsets
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
    PartyType,
    Project,
    ProjectLabour,
    Role,
    Supplier,
    TransactionStatus,
    ZERO,
    annotate_last_paid,
)
from .permissions import AdminOnly, RoleAllowed, get_role, is_admin
from .serializers import (
    ContractorContractSerializer,
    ExpenseTransactionSerializer,
    LabourPaymentBatchSerializer,
    LabourSerializer,
    ManagerFundSerializer,
    ManagerLabourDistributionSerializer,
    NewProjectLabourSerializer,
    ProjectLabourSerializer,
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
    """
    Labour master. Owners may list; only ADMIN may add here. Owners add labour for a
    project through project-labour/ (which de-duplicates and links the project).
    """

    serializer_class = LabourSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}
    queryset = Labour.objects.all()
    http_method_names = ['get', 'post', 'head', 'options']

    def get_permissions(self):
        if self.action == 'create':
            return [AdminOnly()]
        return super().get_permissions()


def _digits(text):
    return ''.join(ch for ch in text or '' if ch.isdigit())


def _norm(name):
    """Name for comparison: trimmed, single-spaced, case-insensitive."""
    return ' '.join((name or '').split()).casefold()


def _candidate(labour):
    """What the app may show to help the user decide whether this is the same person."""
    last = (
        ExpenseTransaction.objects.filter(labour=labour).exclude(status=TransactionStatus.CANCELLED)
        .aggregate(last=Max('expense_date'))['last']
    )
    tail = _digits(labour.mobile)[-4:]
    return {
        'labour': labour.id,
        'name': labour.name,
        'mobile_masked': f'******{tail}' if tail else '',
        'last_paid': last,
    }


def _require_project_access(user, project):
    """Same rule as expense entry: admin, or an owner linked to the project."""
    if not is_admin(user) and not Project.objects.filter(project_owners__owner__user=user, pk=project.pk).exists():
        raise PermissionDenied('You are not authorized on this project.')


class ProjectLabourViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    Project-wise labour list for the payment entry screen.

    GET  ?project=<id>&status=active|inactive|all   (default active)
    POST {project, name, mobile, type?, remarks?}   add labour to the project. Reuses an
         existing Labour master row where the person is identifiable (see create());
         a same-name/different-mobile match answers 409 with candidates and needs
         {use_labour: <id>} or {confirm_new: true} to proceed.
    POST <id>/set-active/ {is_active}               stop / resume a labour on this project
    """

    serializer_class = ProjectLabourSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        qs = annotate_last_paid(ProjectLabour.objects.select_related('labour'))
        if not is_admin(user):
            qs = qs.filter(project__project_owners__owner__user=user)
        params = self.request.query_params
        if params.get('project'):
            qs = qs.filter(project_id=params['project'])
        state = params.get('status', 'all' if self.kwargs.get('pk') else 'active')
        if state == 'active':
            qs = qs.filter(is_active=True)
        elif state == 'inactive':
            qs = qs.filter(is_active=False)
        return qs.order_by(F('last_paid').desc(nulls_last=True), 'labour__name', 'labour_id')

    def create(self, request):
        ser = NewProjectLabourSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        _require_project_access(request.user, data['project'])
        name = ' '.join(data['name'].split())
        mobile = _digits(data['mobile'])[-10:]
        if not name:
            raise ValidationError({'name': 'This field may not be blank.'})
        if len(mobile) < 10:
            raise ValidationError({'mobile': 'Enter a valid mobile number.'})

        with transaction.atomic():
            everyone = list(Labour.objects.only('id', 'name', 'mobile').order_by('id'))
            same_name = [l for l in everyone if _norm(l.name) == _norm(name)]
            same_mobile = [l for l in everyone if _digits(l.mobile)[-10:] == mobile]
            labour = None

            if data['use_labour']:
                # The user confirmed "this is the same person"; only accept a real candidate.
                labour = next((l for l in same_name + same_mobile if l.id == data['use_labour']), None)
                if labour is None:
                    raise ValidationError({'use_labour': 'That is not a matching labour.'})
            elif same_mobile:                                             # rule 1
                labour = same_mobile[0]
            else:
                labour = next((l for l in same_name if not l.mobile.strip()), None)   # rule 2
                if labour is None and same_name and not data['confirm_new']:          # rule 3
                    return Response({
                        'code': 'possible_duplicate',
                        'detail': 'A labour with this name already exists. Is it the same person?',
                        'candidates': [_candidate(l) for l in same_name],
                    }, status=status.HTTP_409_CONFLICT)

            reused = labour is not None
            if labour is None:
                labour = Labour.objects.create(
                    name=name, mobile=mobile, type=data['type'].strip(), remarks=data['remarks'].strip())
            elif not labour.mobile.strip():
                labour.mobile = mobile
                labour.save(update_fields=['mobile'])
            link, created = ProjectLabour.objects.get_or_create(project=data['project'], labour=labour)
            if not link.is_active:
                link.is_active = True
                link.save(update_fields=['is_active'])
        body = ProjectLabourSerializer(annotate_last_paid(ProjectLabour.objects.select_related('labour')).get(pk=link.pk)).data
        body['reused'] = reused
        return Response(body, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='set-active')
    def set_active(self, request, pk=None):
        link = self.get_object()
        active = request.data.get('is_active')
        if not isinstance(active, bool):
            raise ValidationError({'is_active': 'true or false is required.'})
        link.is_active = active
        link.save(update_fields=['is_active'])
        return Response(self.get_serializer(link).data)


class LabourPaymentViewSet(viewsets.GenericViewSet):
    """POST one entry with many labourers; creates one LABOUR ExpenseTransaction per labour."""

    serializer_class = LabourPaymentBatchSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}

    def create(self, request):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        _require_project_access(request.user, data['project'])

        batch = uuid.uuid4()
        with transaction.atomic():
            created = [
                ExpenseTransaction.objects.create(
                    project=data['project'],
                    expense_date=data['expense_date'],
                    expense_category=ExpenseCategory.LABOUR,
                    expense_type='Labour Payment',
                    party_type=PartyType.LABOUR,
                    labour=line['labour'],
                    paid_by_owner=data['paid_by_owner'],
                    amount=line['amount'],
                    payment_mode=data['payment_mode'],
                    reference_no=data.get('reference_no') or None,
                    remarks=data.get('remarks') or None,
                    payment_batch=batch,
                    created_by=request.user,
                )
                for line in data['payments']
            ]
            # Paying an inactive labour means they are working here again.
            ProjectLabour.objects.filter(
                project=data['project'], labour__in=[t.labour_id for t in created], is_active=False
            ).update(is_active=True)
        return Response({
            'payment_batch': str(batch),
            'total': sum((t.amount for t in created), ZERO),
            'transactions': [{'id': t.id, 'labour': t.labour_id, 'amount': t.amount} for t in created],
        }, status=status.HTTP_201_CREATED)


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
