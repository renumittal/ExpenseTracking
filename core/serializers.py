from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import (
    Contractor,
    ContractorContract,
    ExpenseTransaction,
    Labour,
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

    def validate(self, attrs):
        """
        Re-run the model's own consistency checks (expense_category vs. party_type,
        and the required labour/contractor_contract/supplier FK for that party_type)
        at the API layer -- ModelSerializer does not call model.clean() on its own.
        """
        instance = ExpenseTransaction(**attrs)
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, 'message_dict', exc.messages))
        return attrs


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

    def validate(self, attrs):
        manager_fund = attrs.get('manager_fund') or getattr(self.instance, 'manager_fund', None)
        project = attrs.get('project') or getattr(self.instance, 'project', None)
        manager = attrs.get('manager') or getattr(self.instance, 'manager', None)
        amount = attrs.get('amount', getattr(self.instance, 'amount', None))

        if manager_fund and project and manager_fund.project_id != project.id:
            raise serializers.ValidationError({'project': 'project must match the manager_fund\'s project.'})
        if manager_fund and manager and manager_fund.manager_id != manager.id:
            raise serializers.ValidationError({'manager': 'manager must match the manager_fund\'s manager.'})

        # Hard block: this is an internal cash advance, not a client-facing
        # contract, so (unlike ContractorContract) we never allow overdrawing it.
        if manager_fund and amount is not None and amount > manager_fund.balance:
            raise serializers.ValidationError(
                f'Distribution amount {amount} exceeds available fund balance {manager_fund.balance}.'
            )
        return attrs


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ['id', 'name', 'supplier_type', 'mobile', 'address', 'remarks']


class LabourSerializer(serializers.ModelSerializer):
    class Meta:
        model = Labour
        fields = ['id', 'name', 'type', 'mobile']


class ContractorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contractor
        fields = ['id', 'name', 'work_type', 'mobile', 'remarks']


class ContractorContractSerializer(serializers.ModelSerializer):
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True, source='balance')
    overpayment_warning = serializers.SerializerMethodField()
    contractor_name = serializers.CharField(source='contractor.name', read_only=True)

    class Meta:
        model = ContractorContract
        fields = [
            'id', 'project', 'contractor', 'contractor_name', 'contract_date', 'contract_amount',
            'remarks', 'paid_amount', 'balance_amount', 'overpayment_warning',
        ]

    def get_overpayment_warning(self, obj):
        if obj.paid_amount > obj.contract_amount:
            return (
                f'paid_amount ({obj.paid_amount}) exceeds contract_amount ({obj.contract_amount}) '
                f'by {obj.paid_amount - obj.contract_amount}.'
            )
        return None
