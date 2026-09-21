from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import people, reports, views

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
