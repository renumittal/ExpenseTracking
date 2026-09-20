from datetime import timedelta

from django.contrib import admin
from django.utils import timezone

from .models import (
    Contractor,
    ContractorContract,
    ExpenseTransaction,
    Labour,
    Manager,
    ManagerFund,
    ManagerLabourDistribution,
    MiscExpenseType,
    Owner,
    Profile,
    Project,
    ProjectManager,
    ProjectLabour,
    ProjectOwner,
    Supplier,
    annotate_last_paid,
)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__email')


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'status', 'start_date', 'expected_completion_date')
    list_filter = ('status',)
    search_fields = ('code', 'name', 'location')


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    list_display = ('name', 'mobile', 'user')
    search_fields = ('name', 'mobile', 'user__username')


@admin.register(ProjectOwner)
class ProjectOwnerAdmin(admin.ModelAdmin):
    list_display = ('project', 'owner')
    list_filter = ('project',)
    search_fields = ('project__code', 'project__name', 'owner__name')


@admin.register(Manager)
class ManagerAdmin(admin.ModelAdmin):
    list_display = ('name', 'mobile', 'monthly_salary', 'user')
    search_fields = ('name', 'mobile', 'user__username')


@admin.register(ProjectManager)
class ProjectManagerAdmin(admin.ModelAdmin):
    list_display = ('project', 'manager')
    list_filter = ('project',)
    search_fields = ('project__code', 'project__name', 'manager__name')


@admin.register(Labour)
class LabourAdmin(admin.ModelAdmin):
    list_display = ('name', 'type', 'mobile')
    list_filter = ('type',)
    search_fields = ('name', 'mobile')


class LastPaidFilter(admin.SimpleListFilter):
    """Helps spot labour who may have left. Informational: it never changes status by itself."""
    title = 'last paid'
    parameter_name = 'last_paid'

    def lookups(self, request, model_admin):
        return [('30', 'Within 30 days'), ('older', 'More than 30 days ago'), ('never', 'Never paid')]

    def queryset(self, request, queryset):
        cutoff = timezone.localdate() - timedelta(days=30)
        if self.value() == '30':
            return queryset.filter(last_paid__gte=cutoff)
        if self.value() == 'older':
            return queryset.filter(last_paid__lt=cutoff)
        if self.value() == 'never':
            return queryset.filter(last_paid__isnull=True)
        return queryset


@admin.register(ProjectLabour)
class ProjectLabourAdmin(admin.ModelAdmin):
    list_display = ('project', 'labour', 'is_active', 'last_paid')
    list_filter = ('project', 'is_active', LastPaidFilter)
    search_fields = ('project__code', 'project__name', 'labour__name', 'labour__mobile')
    actions = ['mark_inactive']

    def get_queryset(self, request):
        return annotate_last_paid(super().get_queryset(request).select_related('project', 'labour'))

    @admin.display(ordering='last_paid', description='Last paid')
    def last_paid(self, obj):
        return obj.last_paid

    @admin.action(description='Mark selected as inactive')
    def mark_inactive(self, request, queryset):
        # queryset carries an annotation, so update by pk.
        count = ProjectLabour.objects.filter(pk__in=queryset.values('pk')).update(is_active=False)
        self.message_user(request, f'{count} labour marked inactive (nothing deleted).')


@admin.register(Contractor)
class ContractorAdmin(admin.ModelAdmin):
    list_display = ('name', 'work_type', 'mobile')
    list_filter = ('work_type',)
    search_fields = ('name', 'mobile')


@admin.register(ContractorContract)
class ContractorContractAdmin(admin.ModelAdmin):
    list_display = ('contractor', 'project', 'work_description', 'contract_date', 'contract_amount', 'paid_amount', 'balance')
    list_filter = ('project', 'contract_date')
    search_fields = ('contractor__name', 'project__code', 'project__name')


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'supplier_type', 'mobile')
    list_filter = ('supplier_type',)
    search_fields = ('name', 'mobile', 'address')


@admin.register(MiscExpenseType)
class MiscExpenseTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(ExpenseTransaction)
class ExpenseTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'project', 'expense_date', 'expense_category', 'expense_type',
        'amount', 'payment_mode', 'status', 'paid_by_owner',
    )
    list_filter = ('project', 'expense_category', 'status', 'expense_date', 'payment_mode')
    search_fields = (
        'project__code', 'project__name', 'expense_type', 'reference_no',
        'payee_name', 'description', 'labour__name', 'supplier__name',
        'contractor_contract__contractor__name',
    )
    readonly_fields = ('created_at', 'modified_at')


@admin.register(ManagerFund)
class ManagerFundAdmin(admin.ModelAdmin):
    list_display = (
        'project', 'manager', 'fund_date', 'fund_amount',
        'distributed_amount', 'balance', 'given_by_owner',
    )
    list_filter = ('project', 'fund_date')
    search_fields = ('project__code', 'project__name', 'manager__name')


@admin.register(ManagerLabourDistribution)
class ManagerLabourDistributionAdmin(admin.ModelAdmin):
    list_display = ('project', 'manager', 'labour', 'date', 'amount', 'manager_fund')
    list_filter = ('project', 'date')
    search_fields = ('project__code', 'project__name', 'manager__name', 'labour__name')
    readonly_fields = ('expense_transaction', 'created_at')
