from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import reports, views

router = DefaultRouter()
router.register('projects', views.ProjectViewSet, basename='project')
router.register('expense-transactions', views.ExpenseTransactionViewSet, basename='expensetransaction')
router.register('manager-funds', views.ManagerFundViewSet, basename='managerfund')
router.register('manager-labour-distributions', views.ManagerLabourDistributionViewSet, basename='managerlabourdistribution')
router.register('suppliers', views.SupplierViewSet, basename='supplier')
router.register('labour', views.LabourViewSet, basename='labour')
router.register('project-labour', views.ProjectLabourViewSet, basename='projectlabour')
router.register('labour-payments', views.LabourPaymentViewSet, basename='labourpayment')
router.register('contractor-contracts', views.ContractorContractViewSet, basename='contractorcontract')

urlpatterns = [
    path('auth/login/', views.LoginView.as_view(), name='login'),
    path('auth/logout/', views.LogoutView.as_view(), name='logout'),
    path('me/', views.MeView.as_view(), name='me'),
    path('manager-summary/', views.ManagerSummaryView.as_view(), name='manager-summary'),

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
