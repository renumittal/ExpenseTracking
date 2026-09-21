from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from .models import (
    Contractor,
    ContractorContract,
    ExpenseCategory,
    ExpenseTransaction,
    Labour,
    Manager,
    ManagerFund,
    ManagerLabourDistribution,
    Owner,
    PaymentMode,
    Project,
    ProjectLabour,
    ProjectManager,
    Supplier,
)
from .permissions import can_give_manager_fund, is_admin


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = [
            'id', 'name', 'code', 'plot_size', 'location', 'start_date',
            'expected_completion_date', 'status', 'remarks', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class ExpenseTransactionSerializer(serializers.ModelSerializer):
    # Bill metadata only. The storage path is never exposed; the file is fetched through
    # expense-transactions/<id>/bill/ (which checks canViewBill).
    has_bill = serializers.SerializerMethodField()

    def get_has_bill(self, obj):
        return bool(obj.bill_path)

    class Meta:
        model = ExpenseTransaction
        fields = [
            'id', 'project', 'expense_date', 'expense_category', 'expense_type',
            'party_type', 'labour', 'contractor_contract', 'supplier', 'payee_name',
            'paid_by_owner', 'amount', 'payment_mode', 'reference_no', 'description',
            'remarks', 'status', 'created_by', 'created_at', 'modified_by', 'modified_at',
            'has_bill', 'bill_filename',
        ]
        read_only_fields = ['status', 'created_by', 'created_at', 'modified_by', 'modified_at', 'bill_filename']

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

        # Contractor payments only: the contract must belong to the project being paid.
        # (Labour / Supplier / Miscellaneous never reach this check.)
        contract = attrs.get('contractor_contract')
        if attrs.get('expense_category') == ExpenseCategory.CONTRACTOR and contract is not None:
            if contract.project_id != attrs['project'].id:
                raise serializers.ValidationError({
                    'contractor_contract': 'This contract belongs to a different project.'
                })
        return attrs


class ExpenseTransactionEditSerializer(serializers.ModelSerializer):
    """
    What may be corrected on a saved expense. Project, category, person, owner and status are fixed: changing
    them would move money between ledgers, so those need a cancel + a new entry.
    """

    class Meta:
        model = ExpenseTransaction
        fields = ['expense_date', 'amount', 'payment_mode', 'reference_no', 'description', 'remarks',
                  'payee_name', 'expense_type']
        extra_kwargs = {'amount': {'min_value': Decimal('0.01')}}

    def validate(self, attrs):
        if self.instance.expense_category != ExpenseCategory.MISCELLANEOUS:
            attrs.pop('payee_name', None)
            attrs.pop('expense_type', None)
        elif 'payee_name' in attrs and not (attrs['payee_name'] or '').strip():
            raise serializers.ValidationError({'payee_name': 'Payee name is required.'})
        return attrs


class ManagerFundSerializer(serializers.ModelSerializer):
    """An Owner giving money to a Manager for a project (not an expense)."""
    distributed_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    manager_name = serializers.CharField(source='manager.name', read_only=True)
    given_by_owner_name = serializers.CharField(source='given_by_owner.name', read_only=True)

    class Meta:
        model = ManagerFund
        fields = [
            'id', 'project', 'manager', 'manager_name', 'fund_date', 'fund_amount', 'given_by_owner',
            'given_by_owner_name', 'payment_mode', 'remarks', 'created_at', 'created_by',
            'distributed_amount', 'balance',
        ]
        read_only_fields = ['created_at', 'created_by']
        extra_kwargs = {'fund_amount': {'min_value': Decimal('0.01')}}

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user is not None and not can_give_manager_fund(user, attrs['project']):
            raise PermissionDenied('Only an owner of this project can give a fund to a manager.')
        if user is not None and not is_admin(user) and attrs['given_by_owner'].user_id != user.id:
            raise serializers.ValidationError({'given_by_owner': 'You can only record funds given by yourself.'})
        try:                                    # the model holds the shared rules (also used by Django admin)
            ManagerFund(**{k: v for k, v in attrs.items()}).clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict)
        return attrs


class ManagerLabourDistributionSerializer(serializers.ModelSerializer):
    """Read-only view of a distribution. Writes go through ManagerDistributionBatchSerializer + core/ledger.py."""
    labour_name = serializers.CharField(source='labour.name', read_only=True)
    manager_name = serializers.CharField(source='manager.name', read_only=True)
    status = serializers.SerializerMethodField()
    payment_batch = serializers.SerializerMethodField()

    class Meta:
        model = ManagerLabourDistribution
        fields = [
            'id', 'manager_fund', 'project', 'manager', 'manager_name', 'date', 'labour', 'labour_name', 'amount',
            'remarks', 'created_at', 'created_by', 'expense_transaction', 'status', 'payment_batch',
        ]
        read_only_fields = fields

    def get_status(self, obj):
        return 'ACTIVE' if obj.is_active else 'CANCELLED'

    def get_payment_batch(self, obj):
        exp = obj.expense_transaction
        return str(exp.payment_batch) if exp and exp.payment_batch else None


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ['id', 'name', 'supplier_type', 'mobile', 'address', 'remarks']


class NewSupplierSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    mobile = serializers.CharField(max_length=20)
    supplier_type = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    remarks = serializers.CharField(required=False, allow_blank=True, default='')
    # Answers to the "is this the same supplier?" question (see SupplierViewSet.add).
    use_supplier = serializers.IntegerField(required=False, allow_null=True, default=None)
    confirm_new = serializers.BooleanField(required=False, default=False)


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


class ManagerDistributionBatchSerializer(serializers.Serializer):
    """One manager distribution entry: a date and one amount per labourer. The fund is chosen by the server."""
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    manager = serializers.PrimaryKeyRelatedField(queryset=Manager.objects.all())
    date = serializers.DateField()
    remarks = serializers.CharField(required=False, allow_blank=True, default='')
    include_inactive = serializers.BooleanField(required=False, default=False)
    payments = LabourPaymentLineSerializer(many=True, allow_empty=False)

    def validate_payments(self, lines):
        ids = [line['labour'].id for line in lines]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('Each labour can appear only once in one entry.')
        return lines

    def validate(self, attrs):
        if not ProjectManager.objects.filter(project=attrs['project'], manager=attrs['manager']).exists():
            raise serializers.ValidationError({'manager': 'This manager is not assigned to this project.'})
        labours = [line['labour'] for line in attrs['payments']]
        links = {l.labour_id: l for l in ProjectLabour.objects.filter(project=attrs['project'], labour__in=labours)}
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


class NewContractorSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    mobile = serializers.CharField(max_length=20)
    work_type = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    remarks = serializers.CharField(required=False, allow_blank=True, default='')
    # Answers to the "is this the same contractor?" question (see ContractorViewSet.create).
    use_contractor = serializers.IntegerField(required=False, allow_null=True, default=None)
    confirm_new = serializers.BooleanField(required=False, default=False)


class ContractorContractSerializer(serializers.ModelSerializer):
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True, source='balance')
    overpayment_warning = serializers.SerializerMethodField()
    contractor_name = serializers.CharField(source='contractor.name', read_only=True)
    contract_amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    work_description = serializers.CharField(max_length=255)

    class Meta:
        model = ContractorContract
        fields = [
            'id', 'project', 'contractor', 'contractor_name', 'contract_date', 'contract_amount',
            'work_description', 'remarks', 'paid_amount', 'balance_amount', 'overpayment_warning',
        ]

    def get_overpayment_warning(self, obj):
        if obj.paid_amount > obj.contract_amount:
            return (
                f'paid_amount ({obj.paid_amount}) exceeds contract_amount ({obj.contract_amount}) '
                f'by {obj.paid_amount - obj.contract_amount}.'
            )
        return None
