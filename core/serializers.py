from rest_framework import serializers

from .models import (
    Contractor,
    ContractorContract,
    ExpenseTransaction,
    ManagerFund,
    ManagerLabourDistribution,
    Project,
    Supplier,
)


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = [
            'id', 'name', 'code', 'plot_size', 'location', 'start_date',
            'expected_completion_date', 'status', 'remarks', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class ExpenseTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseTransaction
        fields = [
            'id', 'project', 'expense_date', 'expense_category', 'expense_type',
            'party_type', 'labour', 'contractor_contract', 'supplier', 'payee_name',
            'paid_by_owner', 'amount', 'payment_mode', 'reference_no', 'description',
            'remarks', 'status', 'created_by', 'created_at', 'modified_by', 'modified_at',
        ]
        read_only_fields = ['status', 'created_by', 'created_at', 'modified_by', 'modified_at']


class ManagerFundSerializer(serializers.ModelSerializer):
    distributed_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = ManagerFund
        fields = [
            'id', 'project', 'manager', 'fund_date', 'fund_amount', 'given_by_owner',
            'payment_mode', 'remarks', 'created_at', 'distributed_amount', 'balance',
        ]
        read_only_fields = ['created_at']


class ManagerLabourDistributionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagerLabourDistribution
        fields = [
            'id', 'manager_fund', 'project', 'manager', 'date', 'labour', 'amount',
            'remarks', 'created_at', 'expense_transaction',
        ]
        read_only_fields = ['created_at', 'expense_transaction']


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ['id', 'name', 'supplier_type', 'mobile', 'address', 'remarks']


class ContractorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contractor
        fields = ['id', 'name', 'work_type', 'mobile', 'remarks']


class ContractorContractSerializer(serializers.ModelSerializer):
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = ContractorContract
        fields = [
            'id', 'project', 'contractor', 'contract_date', 'contract_amount',
            'remarks', 'paid_amount', 'balance',
        ]
