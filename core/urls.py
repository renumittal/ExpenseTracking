from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import people, reports, views
from .access import api as access_api

router = DefaultRouter()
router.register('projects', views.ProjectViewSet, basename='project')
router.register('expense-transactions', views.ExpenseTransactionViewSet, basename='expensetransaction')
router.register('manager-funds', views.ManagerFundViewSet, basename='managerfund')
router.register('manager-labour-distributions', views.ManagerLabourDistributionViewSet, basename='managerlabourdistribution')
router.register('suppliers', views.SupplierViewSet, basename='supplier')
router.register('labour', views.LabourViewSet, basename='labour')
router.register('project-labour', views.ProjectLabourViewSet, basename='projectlabour')
router.register('labour-payments', views.LabourPaymentViewSet, basename='labourpayment')
router.register('contractors', views.ContractorViewSet, basename='contractor')
router.register('contractor-contracts', views.ContractorContractViewSet, basename='contractorcontract')

urlpatterns = [
    path('auth/login/', views.LoginView.as_view(), name='login'),
    path('auth/logout/', views.LogoutView.as_view(), name='logout'),
    path('auth/change-password/', people.ChangePasswordView.as_view(), name='change-password'),
    path('me/', views.MeView.as_view(), name='me'),
    path('manager-summary/', views.ManagerSummaryView.as_view(), name='manager-summary'),
    path('users/', people.UserListView.as_view(), name='users'),
    path('users/<int:user_id>/reset-password/', people.ResetPasswordView.as_view(), name='reset-password'),
    path('permission-matrix/', people.PermissionMatrixView.as_view(), name='permission-matrix'),
    path('projects/<int:pk>/members/', people.ProjectMembersView.as_view(), name='project-members'),
    path('projects/<int:pk>/members/<int:user_id>/', people.ProjectMemberDetailView.as_view(), name='project-member'),
    path('projects/<int:pk>/people/', views.ProjectPeopleView.as_view(), name='project-people'),

    # Settings -> Access Control (super admin only). See core/access/api.py.
    path('access/catalog/', access_api.CatalogView.as_view(), name='access-catalog'),
    path('access/role-permissions/', access_api.RolePermissionMatrixView.as_view(), name='access-role-permissions'),
    path('access/users/', access_api.UserAccessListView.as_view(), name='access-users'),
    path('access/users/<int:user_id>/access/', access_api.UserAccessDetailView.as_view(), name='access-user-detail'),
    path('access/users/<int:user_id>/access/<int:access_id>/', access_api.UserAccessGrantView.as_view(), name='access-user-grant'),
    path('access/users/<int:user_id>/access/<int:access_id>/overrides/', access_api.OverridesView.as_view(), name='access-overrides'),
    path('access/users/<int:user_id>/access/all/', access_api.UserAccessRemoveAllView.as_view(), name='access-remove-all'),
    path('access/users/<int:user_id>/copy-from/', access_api.CopyAccessView.as_view(), name='access-copy-from'),
    path('access/users/<int:user_id>/same-role-all/', access_api.SameRoleAllView.as_view(), name='access-same-role-all'),
    path('access/check/<int:user_id>/', access_api.CheckAccessView.as_view(), name='access-check'),
    path('access/who-can/', access_api.WhoCanView.as_view(), name='access-who-can'),

    path('reports/category-expense/', reports.CategoryExpenseReportView.as_view(), name='report-category-expense'),
    path('reports/contractor/', reports.ContractorReportView.as_view(), name='report-contractor'),
    path('reports/supplier/', reports.SupplierReportView.as_view(), name='report-supplier'),
    path('reports/labour/', reports.LabourReportView.as_view(), name='report-labour'),
    path('reports/misc/', reports.MiscExpenseReportView.as_view(), name='report-misc'),
    path('reports/manager-fund/', reports.ManagerFundReportView.as_view(), name='report-manager-fund'),
    path('reports/date-wise/', reports.DateWiseExpenseReportView.as_view(), name='report-date-wise'),
    path('reports/payment-register/', reports.PaymentRegisterView.as_view(), name='report-payment-register'),

    path('', include(router.urls)),
]
