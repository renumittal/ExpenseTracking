from django.contrib import admin

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
    ProjectOwner,
    Supplier,
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


@admin.register(Contractor)
class ContractorAdmin(admin.ModelAdmin):
    list_display = ('name', 'work_type', 'mobile')
    list_filter = ('work_type',)
    search_fields = ('name', 'mobile')


@admin.register(ContractorContract)
class ContractorContractAdmin(admin.ModelAdmin):
    list_display = ('contractor', 'project', 'contract_date', 'contract_amount', 'paid_amount', 'balance')
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
