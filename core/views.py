from rest_framework import status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    ContractorContract,
    ExpenseTransaction,
    ManagerFund,
    ManagerLabourDistribution,
    Project,
    Role,
    Supplier,
)
from .permissions import RoleAllowed, get_role, is_admin
from .serializers import (
    ContractorContractSerializer,
    ExpenseTransactionSerializer,
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


class ExpenseTransactionViewSet(viewsets.ModelViewSet):
    """
    Admin: all transactions.
    Owner: transactions for any project they're linked to (all owners' entries
    on that project, not just their own -- owners can see each other there).
    """

    serializer_class = ExpenseTransactionSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        if is_admin(user):
            return ExpenseTransaction.objects.all()
        return ExpenseTransaction.objects.filter(project__project_owners__owner__user=user).distinct()

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


# ---------------------------------------------------------------------------
# Manager-facing endpoints
# ---------------------------------------------------------------------------

class ManagerFundViewSet(viewsets.ReadOnlyModelViewSet):
    """Admin: all funds. Manager: only funds received by themselves."""

    serializer_class = ManagerFundSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.MANAGER}

    def get_queryset(self):
        user = self.request.user
        if is_admin(user):
            return ManagerFund.objects.all()
        return ManagerFund.objects.filter(manager__user=user)


class ManagerLabourDistributionViewSet(viewsets.ReadOnlyModelViewSet):
    """Admin: all distributions. Manager: only distributions made by themselves."""

    serializer_class = ManagerLabourDistributionSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.MANAGER}

    def get_queryset(self):
        user = self.request.user
        if is_admin(user):
            return ManagerLabourDistribution.objects.all()
        return ManagerLabourDistribution.objects.filter(manager__user=user)


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

class SupplierViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SupplierSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}
    queryset = Supplier.objects.all()


class ContractorContractViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ContractorContractSerializer
    permission_classes = [RoleAllowed]
    allowed_roles = {Role.OWNER}

    def get_queryset(self):
        user = self.request.user
        if is_admin(user):
            return ContractorContract.objects.all()
        return ContractorContract.objects.filter(project__project_owners__owner__user=user).distinct()
