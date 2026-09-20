from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import (
    Contractor,
    ContractorContract,
    ExpenseTransaction,
    Labour,
    ManagerFund,
    ManagerLabourDistribution,
    Owner,
    PaymentMode,
    Project,
    ProjectLabour,
    Supplier,
)
from .permissions import is_admin


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


class ProjectLabourSerializer(serializers.ModelSerializer):
    """A labour as seen on one project: master fields + that project's active flag."""
    labour = serializers.IntegerField(source='labour_id', read_only=True)
    name = serializers.CharField(source='labour.name', read_only=True)
    type = serializers.CharField(source='labour.type', read_only=True)
    mobile = serializers.CharField(source='labour.mobile', read_only=True)
    last_paid = serializers.DateField(read_only=True)  # from annotate_last_paid(); informational only

    class Meta:
        model = ProjectLabour
        fields = ['id', 'project', 'labour', 'name', 'type', 'mobile', 'is_active', 'last_paid']
        read_only_fields = fields


class NewProjectLabourSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    name = serializers.CharField(max_length=255)
    mobile = serializers.CharField(max_length=20)
    type = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    remarks = serializers.CharField(required=False, allow_blank=True, default='')
    # Answers to the "is this the same person?" question (see ProjectLabourViewSet.create).
    use_labour = serializers.IntegerField(required=False, allow_null=True, default=None)
    confirm_new = serializers.BooleanField(required=False, default=False)


class LabourPaymentLineSerializer(serializers.Serializer):
    labour = serializers.PrimaryKeyRelatedField(queryset=Labour.objects.all())
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'),
        error_messages={
            'required': 'Please enter an amount for every selected labour.',
            'null': 'Please enter an amount for every selected labour.',
            'invalid': 'Please enter a valid amount.',
            'min_value': 'Please enter an amount greater than zero.',
            'max_digits': 'The amount is too large.',
            'max_whole_digits': 'The amount is too large.',
            'max_decimal_places': 'Amounts can have at most 2 decimal places.',
        },
    )


class LabourPaymentBatchSerializer(serializers.Serializer):
    """One Labour Payment entry: shared expense fields + one amount per labour."""
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    expense_date = serializers.DateField()
    paid_by_owner = serializers.PrimaryKeyRelatedField(queryset=Owner.objects.all())
    payment_mode = serializers.ChoiceField(choices=PaymentMode.choices)
    reference_no = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)
    remarks = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    include_inactive = serializers.BooleanField(required=False, default=False)
    payments = LabourPaymentLineSerializer(many=True, allow_empty=False)

    def validate_payments(self, lines):
        ids = [line['labour'].id for line in lines]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('Each labour can appear only once in one entry.')
        return lines

    def validate(self, attrs):
        request = self.context.get('request')
        if request is not None and not is_admin(request.user) and attrs['paid_by_owner'].user_id != request.user.id:
            raise serializers.ValidationError({'paid_by_owner': 'You can only record payments as yourself.'})

        labours = [line['labour'] for line in attrs['payments']]
        links = {
            link.labour_id: link
            for link in ProjectLabour.objects.filter(project=attrs['project'], labour__in=labours)
        }
        missing = [l.name for l in labours if l.id not in links]
        if missing:
            raise serializers.ValidationError({'payments': f'Not a labour of this project: {", ".join(missing)}.'})
        if not attrs['include_inactive']:
            inactive = [l.name for l in labours if not links[l.id].is_active]
            if inactive:
                verb = 'is' if len(inactive) == 1 else 'are'
                raise serializers.ValidationError({
                    'payments': f'{", ".join(inactive)} {verb} inactive. Please select from Show Inactive.'
                })
        return attrs


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
