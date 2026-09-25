import re
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.db.models import F, Max, Q, Sum
from rest_framework import mixins, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Contractor,
    ContractorContract,
    ExpenseCategory,
    ExpenseTransaction,
    Labour,
    Manager,
    ManagerFund,
    ManagerLabourDistribution,
    Owner,
    PartyType,
    Project,
    ProjectLabour,
    Role,
    Supplier,
    TransactionStatus,
    ZERO,
    annotate_last_paid,
)
from . import bills, ledger
from .access import services
from .permissions import (
    AdminOnly,
    CAN_ADD_EXPENSE,
    CAN_DELETE_EXPENSE,
    CAN_EDIT_EXPENSE,
    CAN_RECORD_LABOUR_PAYMENT,
    CAN_UPLOAD_BILL,
    CAN_VIEW_BILL,
    CAN_VIEW_EXPENSES,
    RoleAllowed,
    can_cancel_distribution,
    can_distribute_manager_fund,
    can_view_manager_fund,
    effective_permissions,
    has_permission,
    is_admin,
    is_project_member,
    owns_project,
)
from .people import stored_matrix
from .serializers import (
    ContractorContractSerializer,
    ContractorSerializer,
    ExpenseTransactionEditSerializer,
    ExpenseTransactionSerializer,
    LabourPaymentBatchSerializer,
    LabourSerializer,
    ManagerDistributionBatchSerializer,
    ManagerFundSerializer,
    ManagerLabourDistributionSerializer,
    NewContractorSerializer,
    NewProjectLabourSerializer,
    NewSupplierSerializer,
    ProjectLabourSerializer,
    ProjectSerializer,
    SupplierSerializer,
)


def _unique_project_code(name):
    """A short, unique code derived from `name` (e.g. 'Green Valley Phase 2' -> 'GREENVALL'), for a
    create that left `code` blank. Falls back to 'PROJ' if the name has no letters/digits, then
    appends a counter until it is unique."""
    base = re.sub(r'[^A-Z0-9]', '', name.upper())[:9] or 'PROJ'
    code, n = base, 1
    while Project.objects.filter(code=code).exists():
        n += 1
        code = f'{base}{n}'
    return code


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginView(ObtainAuthToken):
    """POST {username, password} -> {token, role, username}."""

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        data = request.data.copy()
        # Create User treats usernames/emails case-insensitively; log in the same way (phones auto-capitalise).
        who = (data.get('username') or '').strip()
        if who:
            User = get_user_model()
            matches = list(User.objects.filter(Q(username__iexact=who) | Q(email__iexact=who))[:2])
            exact = [u for u in matches if u.username == who]
            if exact:
                matches = exact
            if len(matches) == 1:
                who = matches[0].username
            data['username'] = who
        serializer = self.serializer_class(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        token, _ = Token.objects.get_or_create(user=user)
        return Response({
            'token': token.key,
            'username': user.username,
            'role': services.display_role(user),
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
        manager = getattr(user, 'manager_profile', None)
        permissions, project_permissions = effective_permissions(user)
        # Full per-project truth (every permission code, not just the 5 legacy SERVER_PERMS), from
        # the RBAC v2 engine -- this is what lets the web app hide a button because of a per-project
        # override (e.g. Manoj's EXPENSE.EDIT blocked on one project only), not just his role.
        matrix = services.effective_matrix(user)
        permissions_by_project = {
            ('GLOBAL' if key == 'GLOBAL' else str(key)): {code: v['allowed'] for code, v in perms.items()}
            for key, perms in matrix.items()
        }
        return Response({
            'user_id': user.id,
            'username': user.username,
            'role': services.display_role(user),
            'owner_id': owner.id if owner else None,
            'manager_id': manager.id if manager else None,
            'name': owner.name if owner else manager.name if manager else user.get_username(),
            'is_super_admin': is_admin(user),
            # What the server will allow (the API enforces the same). The web app uses it to show/hide things.
            'permissions': permissions,
            'project_permissions': project_permissions,
            'permissions_by_project': permissions_by_project,
            # The saved Role & Permissions matrix ({permission: {role: bool}}); the same on every device.
            'permission_matrix': stored_matrix(),
        })


class ProjectPeopleView(APIView):
    """
    GET /projects/<id>/people/ -- the owners, managers and labour actually assigned to this project.
    Read-only, built from UserAccess (OWNER/MANAGER grants) and ProjectLabour. Only someone who
    belongs to the project (or an admin) can ask; anyone else gets 404, so nothing about other projects leaks.
    Mobile numbers are deliberately not included.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        if project is None or not is_project_member(request.user, project):
            raise NotFound('Project not found.')
        labour = ProjectLabour.objects.filter(project=project).select_related('labour').order_by('labour__name')
        return Response({
            'project': {'id': project.id, 'name': project.name, 'code': project.code, 'status': project.status},
            'owners': list(Owner.objects.filter(user__in=services.users_with_role(project, 'OWNER'))
                           .order_by('name').values('id', 'name')),
            'managers': list(Manager.objects.filter(user__in=services.users_with_role(project, 'MANAGER'))
                              .order_by('name').values('id', 'name')),
            'labour': [{'id': l.labour_id, 'name': l.labour.name, 'type': l.labour.type, 'is_active': l.is_active}
                       for l in labour],
        })


# ---------------------------------------------------------------------------
# Owner-facing endpoints
# ---------------------------------------------------------------------------

class ProjectViewSet(mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.ReadOnlyModelViewSet):
    """
    Read: admin sees every project; an owner only the projects they hold an OWNER-role grant on.
    Create/update (including archiving, a status change): super admin only -- project administration
    lives entirely in Settings -> Projects now (see people.py's project-members endpoints, gated the
    same way). A project's code never changes once created; if none is given on create, one is
    generated (see perform_create).
    """

    serializer_class = ProjectSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update'):
            return [AdminOnly()]
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user
        if is_admin(user):
            return Project.objects.all()
        return services.projects_with_role(user, 'OWNER')

    def perform_create(self, serializer):
        code = (serializer.validated_data.get('code') or '').strip().upper()
        serializer.save(code=code or _unique_project_code(serializer.validated_data.get('name', '')))

    def perform_update(self, serializer):
        serializer.validated_data.pop('code', None)
        serializer.save()

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
                'work_description': contract.work_description,
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

    No hard delete: create, list, retrieve, PATCH (canEditExpense: date, amount, mode, reference, notes only)
    and the `cancel` action (canDeleteExpense: status=CANCELLED with a required reason, never removes the row).
    """

    serializer_class = ExpenseTransactionSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER, Role.MANAGER}
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def _guard_change(self, instance, permission, allow_distribution=False):
        # Project-aware: respects a per-project ALLOW/DENY override (e.g. Manoj is OWNER on Site B
        # but has EXPENSE.EDIT blocked there specifically), not just "does this role ever get this".
        if not services.has_perm(self.request.user, permission, instance.project):
            raise PermissionDenied('You do not have permission to do this.')
        if instance.status == TransactionStatus.CANCELLED:
            raise ValidationError('This expense is already cancelled.')
        if not allow_distribution and hasattr(instance, 'manager_labour_distribution'):
            raise ValidationError('This expense comes from a manager fund distribution. Cancel it from Manager Fund.')

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        self._guard_change(instance, CAN_EDIT_EXPENSE)
        serializer = ExpenseTransactionEditSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(modified_by=request.user)
        return Response(self.get_serializer(instance).data)

    def get_queryset(self):
        user = self.request.user
        if is_admin(user):
            qs = ExpenseTransaction.objects.all()
        else:
            # Project-aware: only projects this user's role (with any per-project override applied)
            # actually grants canViewExpenses on -- not just "any project they're linked to".
            viewable = [
                p.id for p in services.accessible_projects(user)
                if services.has_perm(user, CAN_VIEW_EXPENSES, p)
            ]
            qs = ExpenseTransaction.objects.filter(project_id__in=viewable)

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
        if not services.has_perm(user, CAN_ADD_EXPENSE, project):
            raise PermissionDenied('You are not authorized to add expenses on this project.')
        serializer.save(created_by=user)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Reversal, never a hard delete: sets status=CANCELLED with a required reason."""
        instance = self.get_object()
        reason = request.data.get('remarks') or request.data.get('reason')
        if not reason:
            raise ValidationError({'remarks': 'A reason is required to cancel a transaction.'})
        self._guard_change(instance, CAN_DELETE_EXPENSE, allow_distribution=True)   # cancelling it directly was always allowed
        instance.cancel(cancelled_by=request.user, reason=reason)
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=['get', 'post'], url_path='bill', parser_classes=[MultiPartParser])
    def bill(self, request, pk=None):
        """
        Supplier bill, one per transaction (no replace / delete).

        POST multipart {file}: needs canUploadBill. SUPPLIER + ACTIVE transactions only.
        GET: needs canViewBill. Returns a short-lived signed URL, never the file or its path.

        The permission is checked against the server-side RolePermission table, then the
        transaction is looked up through the normal project-scoped queryset (404 otherwise).
        """
        needed = CAN_UPLOAD_BILL if request.method == 'POST' else CAN_VIEW_BILL
        instance = self.get_object()
        if not services.has_perm(request.user, needed, instance.project):
            raise PermissionDenied('You do not have permission to ' + ('upload' if request.method == 'POST' else 'view') + ' bills.')
        if request.method == 'POST':
            return self._upload_bill(request, instance)
        return self._view_bill(instance)

    def _upload_bill(self, request, instance):
        if instance.expense_category != ExpenseCategory.SUPPLIER:
            raise ValidationError('Bills can only be attached to supplier expenses.')
        if instance.status != TransactionStatus.ACTIVE:
            raise ValidationError('Bills cannot be attached to a cancelled expense.')
        if instance.bill_path:
            return Response({'detail': 'This expense already has a bill.'}, status=status.HTTP_409_CONFLICT)

        uploaded = request.FILES.get('file')
        try:
            content_type, filename = bills.validate_bill_file(uploaded)
        except bills.BillValidationError as exc:
            raise ValidationError({'file': str(exc)})

        path = bills.new_object_path(instance.project_id, instance.pk, content_type)
        try:
            bills.upload_object(path, uploaded.read(), content_type)
        except bills.StorageNotConfigured:
            return Response({'detail': 'Bill storage is not configured.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except bills.StorageError:
            return Response({'detail': 'Could not store the bill. Please try again.'}, status=status.HTTP_502_BAD_GATEWAY)

        # Claim the single bill slot atomically: two simultaneous uploads cannot both win.
        # update() (not save()) so the expense's own fields and modified_at are untouched.
        try:
            claimed = ExpenseTransaction.objects.filter(pk=instance.pk, bill_path='').update(
                bill_path=path, bill_filename=filename, bill_content_type=content_type,
                bill_size=uploaded.size, bill_uploaded_by=request.user, bill_uploaded_at=timezone.now(),
            )
        except Exception:
            bills.delete_object(path)
            raise
        if not claimed:
            bills.delete_object(path)
            return Response({'detail': 'This expense already has a bill.'}, status=status.HTTP_409_CONFLICT)
        instance.refresh_from_db()
        return Response(self.get_serializer(instance).data, status=status.HTTP_201_CREATED)

    def _view_bill(self, instance):
        if not instance.bill_path:
            return Response({'detail': 'This expense has no bill.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            url = bills.signed_url(instance.bill_path)
        except bills.StorageNotConfigured:
            return Response({'detail': 'Bill storage is not configured.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except bills.StorageError:
            return Response({'detail': 'Could not open the bill. Please try again.'}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(
            {
                'url': url,
                'expires_in': settings.BILL_URL_EXPIRY_SECONDS,
                'filename': instance.bill_filename,
                'content_type': instance.bill_content_type,
            },
            headers={'Cache-Control': 'no-store'},
        )


# ---------------------------------------------------------------------------
# Manager-facing endpoints
# ---------------------------------------------------------------------------

def _scope(qs, user):
    """Admin: everything. Rows on any project this user has an OWNER-role grant on, plus their own
    rows anywhere as a manager (a person can hold both, on different projects)."""
    if is_admin(user):
        return qs
    owned = services.projects_with_role(user, 'OWNER')
    return qs.filter(Q(project__in=owned) | Q(manager__user=user))


def _filter_by_params(qs, params):
    if params.get('project'):
        qs = qs.filter(project_id=params['project'])
    if params.get('manager'):
        qs = qs.filter(manager_id=params['manager'])
    return qs


class ManagerFundViewSet(viewsets.ModelViewSet):
    """
    Fund ledger: money an Owner gives a Manager for a project (this is NOT an expense).

    Admin: all funds. Owner: funds on their projects. Manager: only their own funds (read-only).
    Only an owner of the project (or admin) can record a fund; funds are never edited or deleted here.
    """

    serializer_class = ManagerFundSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER, Role.MANAGER}
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        if not can_view_manager_fund(self.request.user):
            raise PermissionDenied('You do not have permission to view manager funds.')
        qs = ManagerFund.objects.select_related('manager', 'given_by_owner', 'project')
        return _filter_by_params(_scope(qs, self.request.user), self.request.query_params)

    def create(self, request, *args, **kwargs):
        project_id = request.data.get('project')
        if not (is_admin(request.user)
                or services.users_with_role(project_id, 'OWNER').filter(pk=request.user.id).exists()):
            raise PermissionDenied('Only an owner can give a fund to a manager.')
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Per manager and project: total received, total distributed, available balance (from the database)."""
        pairs = self.get_queryset().values_list('project_id', 'manager_id').distinct()
        projects = {p.id: p for p in Project.objects.filter(id__in={p for p, _ in pairs})}
        managers = {m.id: m for m in Manager.objects.filter(id__in={m for _, m in pairs})}
        rows = [ledger.position(projects[p], managers[m]) for p, m in sorted(pairs)]
        return Response({'summary': rows})

    @action(detail=False, methods=['get'])
    def statement(self, request):
        """Fund history, distribution history and a date-wise ledger with running balance, per manager/project."""
        pairs = self.get_queryset().values_list('project_id', 'manager_id').distinct()
        projects = {p.id: p for p in Project.objects.filter(id__in={p for p, _ in pairs})}
        managers = {m.id: m for m in Manager.objects.filter(id__in={m for _, m in pairs})}
        return Response({'statements': [ledger.statement(projects[p], managers[m]) for p, m in sorted(pairs)]})


class ManagerLabourDistributionViewSet(viewsets.ModelViewSet):
    """
    A manager hands out money from their fund to labour. Everything goes through core/ledger.py: the balance
    is checked under a lock, money is taken oldest fund first, and each distribution creates exactly one
    LABOUR ExpenseTransaction. Rows are never edited or deleted; an owner cancels one instead.

    Admin: all. Owner: distributions on their projects (view + cancel). Manager: only their own.
    """

    serializer_class = ManagerLabourDistributionSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER, Role.MANAGER}
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        if not can_view_manager_fund(self.request.user):
            raise PermissionDenied('You do not have permission to view manager funds.')
        qs = ManagerLabourDistribution.objects.select_related(
            'labour', 'manager', 'expense_transaction').order_by('-date', '-id')
        return _filter_by_params(_scope(qs, self.request.user), self.request.query_params)

    def _save_batch(self, request, data):
        """Authorize, then save the whole batch (or nothing). Returns (payment_batch, rows)."""
        if not can_distribute_manager_fund(request.user, data['project'], data['manager']):
            raise PermissionDenied('Only the manager assigned to this project can distribute this fund.')
        return ledger.distribute(
            project=data['project'], manager=data['manager'], date=data['date'], remarks=data['remarks'],
            payments=[(line['labour'], line['amount']) for line in data['payments']], actor=request.user,
        )

    def create(self, request, *args, **kwargs):
        """Single distribution (kept for compatibility): one labour. The fund is picked by the server."""
        body = request.data
        ser = ManagerDistributionBatchSerializer(data={
            'project': body.get('project'), 'manager': body.get('manager'), 'date': body.get('date'),
            'remarks': body.get('remarks', ''), 'payments': [{'labour': body.get('labour'), 'amount': body.get('amount')}],
        })
        ser.is_valid(raise_exception=True)
        batch, rows = self._save_batch(request, ser.validated_data)
        data = self.get_serializer(rows[0]).data
        data['allocations'] = [r.id for r in rows]        # more than one row when it was split across funds
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def batch(self, request):
        """POST {project, manager, date, remarks?, payments: [{labour, amount}, ...]} -- all saved or none."""
        ser = ManagerDistributionBatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        batch, rows = self._save_batch(request, data)
        return Response({
            'payment_batch': str(batch),
            'total': sum((r.amount for r in rows), ZERO),
            'distributions': ManagerLabourDistributionSerializer(rows, many=True).data,
            'position': ledger.position(data['project'], data['manager']),
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Owner / admin only. Cancels this one distribution: the amount returns to the manager's balance."""
        distribution = self.get_object()
        if not can_cancel_distribution(request.user, distribution.project):
            raise PermissionDenied('Only an owner of this project can cancel a distribution.')
        reason = request.data.get('remarks') or request.data.get('reason')
        if not reason:
            raise ValidationError({'remarks': 'A reason is required to cancel a distribution.'})
        distribution = ledger.cancel_distribution(distribution, cancelled_by=request.user, reason=reason)
        distribution.refresh_from_db()
        return Response({
            'distribution': self.get_serializer(distribution).data,
            'position': ledger.position(distribution.project, distribution.manager),
        })


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

    @action(detail=False, methods=['post'], url_path='add')
    def add(self, request):
        """
        Add a supplier without creating duplicates (same rules as contractors/labour):
        same mobile reuses that supplier; same name with no mobile on file reuses it and
        fills the mobile; same name with a different mobile answers 409 with candidates and
        needs {use_supplier: <id>} or {confirm_new: true}. A supplier has no contract:
        every purchase is its own ExpenseTransaction.
        """
        ser = NewSupplierSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        name = ' '.join(data['name'].split())
        mobile = _digits(data['mobile'])[-10:]
        if not name:
            raise ValidationError({'name': 'This field may not be blank.'})
        if len(mobile) < 10:
            raise ValidationError({'mobile': 'Enter a valid mobile number.'})

        with transaction.atomic():
            everyone = list(Supplier.objects.order_by('id'))
            same_name = [x for x in everyone if _norm(x.name) == _norm(name)]
            same_mobile = [x for x in everyone if _digits(x.mobile)[-10:] == mobile]
            supplier = None

            if data['use_supplier']:
                supplier = next((x for x in same_name + same_mobile if x.id == data['use_supplier']), None)
                if supplier is None:
                    raise ValidationError({'use_supplier': 'That is not a matching supplier.'})
            elif same_mobile:
                supplier = same_mobile[0]
            else:
                supplier = next((x for x in same_name if not x.mobile.strip()), None)
                if supplier is None and same_name and not data['confirm_new']:
                    return Response({
                        'code': 'possible_duplicate',
                        'detail': 'A supplier with this name already exists. Is it the same supplier?',
                        'candidates': [
                            {
                                'supplier': x.id,
                                'name': x.name,
                                'supplier_type': x.supplier_type,
                                'mobile_masked': f'******{_digits(x.mobile)[-4:]}' if _digits(x.mobile) else '',
                            }
                            for x in same_name
                        ],
                    }, status=status.HTTP_409_CONFLICT)

            reused = supplier is not None
            if supplier is None:
                supplier = Supplier.objects.create(
                    name=name, mobile=mobile, supplier_type=data['supplier_type'].strip(),
                    remarks=data['remarks'].strip())
            elif not supplier.mobile.strip():
                supplier.mobile = mobile
                supplier.save(update_fields=['mobile'])
        body = SupplierSerializer(supplier).data
        body['reused'] = reused
        return Response(body, status=status.HTTP_200_OK if reused else status.HTTP_201_CREATED)


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
    """Same rule as expense entry: admin, or a real OWNER-role assignment on the project."""
    if not is_admin(user) and not services.users_with_role(project, 'OWNER').filter(pk=user.id).exists():
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
            qs = qs.filter(project__in=services.projects_with_role(user, 'OWNER'))
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
    # OWNER only: the serializer requires `paid_by_owner` to be the acting user's own Owner record
    # (self-attribution, see LabourPaymentBatchSerializer.validate), which a MANAGER can never satisfy
    # -- a manager's equivalent flow is ManagerLabourDistributionViewSet ("distribute from my fund"),
    # gated by canDistributeManagerFund (see can_distribute_manager_fund, now also project-aware).
    allowed_roles = {Role.OWNER}

    def create(self, request):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        # Project-aware: replaces the old ownership-only check with the real permission code,
        # respecting a per-project override.
        if not services.has_perm(request.user, CAN_RECORD_LABOUR_PAYMENT, data['project']):
            raise PermissionDenied('You are not authorized to record labour payments on this project.')

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
            qs = ContractorContract.objects.all()
        else:
            qs = ContractorContract.objects.filter(project__in=services.projects_with_role(user, 'OWNER'))
        if self.request.query_params.get('project'):
            qs = qs.filter(project_id=self.request.query_params['project'])
        return qs.select_related('contractor').order_by('id')

    def perform_create(self, serializer):
        user = self.request.user
        project = serializer.validated_data['project']
        if not is_admin(user):
            owned = services.users_with_role(project, 'OWNER').filter(pk=user.id).exists()
            if not owned:
                raise PermissionDenied('You are not authorized on this project.')
        serializer.save()


def _contractor_candidate(contractor):
    tail = _digits(contractor.mobile)[-4:]
    return {
        'contractor': contractor.id,
        'name': contractor.name,
        'work_type': contractor.work_type,
        'mobile_masked': f'******{tail}' if tail else '',
    }


class ContractorViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    Contractor master.

    GET  list of contractors.
    POST {name, mobile, work_type?, remarks?}  add a contractor without creating duplicates
         (same rules as project-labour): same mobile reuses that contractor; same name with
         no mobile on file reuses it and fills the mobile; same name with a different mobile
         answers 409 with candidates and needs {use_contractor: <id>} or {confirm_new: true}.
         A contractor belongs to no project; the project-specific deal is a ContractorContract.
    """

    serializer_class = ContractorSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}
    queryset = Contractor.objects.order_by('name', 'id')
    pagination_class = None

    def create(self, request):
        ser = NewContractorSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        name = ' '.join(data['name'].split())
        mobile = _digits(data['mobile'])[-10:]
        if not name:
            raise ValidationError({'name': 'This field may not be blank.'})
        if len(mobile) < 10:
            raise ValidationError({'mobile': 'Enter a valid mobile number.'})

        with transaction.atomic():
            everyone = list(Contractor.objects.order_by('id'))
            same_name = [c for c in everyone if _norm(c.name) == _norm(name)]
            same_mobile = [c for c in everyone if _digits(c.mobile)[-10:] == mobile]
            contractor = None

            if data['use_contractor']:
                contractor = next((c for c in same_name + same_mobile if c.id == data['use_contractor']), None)
                if contractor is None:
                    raise ValidationError({'use_contractor': 'That is not a matching contractor.'})
            elif same_mobile:
                contractor = same_mobile[0]
            else:
                contractor = next((c for c in same_name if not c.mobile.strip()), None)
                if contractor is None and same_name and not data['confirm_new']:
                    return Response({
                        'code': 'possible_duplicate',
                        'detail': 'A contractor with this name already exists. Is it the same contractor?',
                        'candidates': [_contractor_candidate(c) for c in same_name],
                    }, status=status.HTTP_409_CONFLICT)

            reused = contractor is not None
            if contractor is None:
                contractor = Contractor.objects.create(
                    name=name, mobile=mobile, work_type=data['work_type'].strip(), remarks=data['remarks'].strip())
            elif not contractor.mobile.strip():
                contractor.mobile = mobile
                contractor.save(update_fields=['mobile'])
        body = ContractorSerializer(contractor).data
        body['reused'] = reused
        return Response(body, status=status.HTTP_200_OK if reused else status.HTTP_201_CREATED)
