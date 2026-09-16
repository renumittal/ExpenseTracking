from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register('projects', views.ProjectViewSet, basename='project')
router.register('expense-transactions', views.ExpenseTransactionViewSet, basename='expensetransaction')
router.register('manager-funds', views.ManagerFundViewSet, basename='managerfund')
router.register('manager-labour-distributions', views.ManagerLabourDistributionViewSet, basename='managerlabourdistribution')
router.register('suppliers', views.SupplierViewSet, basename='supplier')
router.register('contractor-contracts', views.ContractorContractViewSet, basename='contractorcontract')

urlpatterns = [
    path('auth/login/', views.LoginView.as_view(), name='login'),
    path('auth/logout/', views.LogoutView.as_view(), name='logout'),
    path('manager-summary/', views.ManagerSummaryView.as_view(), name='manager-summary'),
    path('', include(router.urls)),
]
