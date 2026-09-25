import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import OuterRef, Subquery


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------

class Role(models.TextChoices):
    OWNER = 'OWNER', 'Owner'
    MANAGER = 'MANAGER', 'Manager'
    ADMIN = 'ADMIN', 'Admin'


class ProjectStatus(models.TextChoices):
    PLANNED = 'PLANNED', 'Planned'
    ONGOING = 'ONGOING', 'Ongoing'
    COMPLETED = 'COMPLETED', 'Completed'
    ARCHIVED = 'ARCHIVED', 'Archived'


class ExpenseCategory(models.TextChoices):
    LABOUR = 'LABOUR', 'Labour'
    CONTRACTOR = 'CONTRACTOR', 'Contractor'
    SUPPLIER = 'SUPPLIER', 'Supplier'
    MISCELLANEOUS = 'MISCELLANEOUS', 'Miscellaneous'


class PartyType(models.TextChoices):
    LABOUR = 'LABOUR', 'Labour'
    CONTRACTOR = 'CONTRACTOR', 'Contractor'
    SUPPLIER = 'SUPPLIER', 'Supplier'
    NONE = 'NONE', 'None'


class PaymentMode(models.TextChoices):
    CASH = 'CASH', 'Cash'
    BANK_TRANSFER = 'BANK_TRANSFER', 'Bank Transfer'
    UPI = 'UPI', 'UPI'
    CHEQUE = 'CHEQUE', 'Cheque'
    OTHER = 'OTHER', 'Other'


class TransactionStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    CANCELLED = 'CANCELLED', 'Cancelled'


ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# Users / roles
# ---------------------------------------------------------------------------

class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=Role.choices)

    def __str__(self):
        return f'{self.user.get_username()} ({self.role})'


class RolePermission(models.Model):
    """
    Server-side permission matrix: one row per (role, permission). This is the authority the
    API checks (see permissions.has_permission); it is the start of the central permission
    system, so new permissions are added as new `permission` values, not new tables.

    Roles here include VIEWER, which is not a Profile.role yet. A user whose role has no row
    falls back to the built-in default in permissions.PERMISSION_DEFAULTS (fail closed).

    RBAC v2: `role`/`permission` (strings) are deprecated in favour of `role_fk`/`resource_permission`
    (real FKs, see access/services.py and access_catalog.py) but are kept and kept populated so nothing
    reading the old columns breaks. New code should read/write `role_fk`/`resource_permission`.
    """

    ROLE_CHOICES = [
        ('ADMIN', 'Admin'),
        ('OWNER', 'Owner'),
        ('MANAGER', 'Manager'),
        ('VIEWER', 'Viewer'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    permission = models.CharField(max_length=50)
    allowed = models.BooleanField(default=False)

    # RBAC v2 columns (ALTER: schema added them nullable, 0007 seeded every row, 0008 made them required).
    role_fk = models.ForeignKey(
        'AccessRole', on_delete=models.CASCADE, related_name='role_permissions', db_column='role_id',
    )
    resource_permission = models.ForeignKey(
        'ResourcePermission', on_delete=models.CASCADE, related_name='role_permissions',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['role', 'permission'], name='unique_role_permission'),
            models.UniqueConstraint(fields=['role_fk', 'resource_permission'], name='unique_role_fk_resource_permission'),
        ]
        ordering = ['permission', 'role']
        indexes = [
            models.Index(fields=['role_fk']),
        ]

    def __str__(self):
        return f'{self.role}: {self.permission} = {"ON" if self.allowed else "OFF"}'


# ---------------------------------------------------------------------------
# RBAC v2 -- role templates, per-user/per-project assignment, overrides, audit
# (see SCHEMA_PLAN.md for the reuse/alter/new mapping, access/services.py for how these resolve)
# ---------------------------------------------------------------------------

class Resource(models.Model):
    """A thing permissions act on: EXPENSE, PROJECT, SUPPLIER, ... (grouping metadata only)."""
    code = models.CharField(max_length=50, unique=True)
    label = models.CharField(max_length=100)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return self.code


class Action(models.Model):
    """A verb permissions perform: VIEW, CREATE, EDIT, MANAGE, ... (grouping metadata only)."""
    code = models.CharField(max_length=50, unique=True)
    label = models.CharField(max_length=100)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return self.code


class ResourcePermission(models.Model):
    """
    A valid (resource, action) combination = one permission code. `code` is kept identical to the
    strings already used everywhere else (`canEditExpense`, ...) -- see access_catalog.py.
    """
    code = models.CharField(max_length=50, unique=True)
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name='permissions')
    action = models.ForeignKey(Action, on_delete=models.PROTECT, related_name='permissions')
    group = models.CharField(max_length=30, blank=True, help_text='UI accordion group id (view/ops/project/...).')
    label = models.CharField(max_length=150)
    description = models.CharField(max_length=255, blank=True, help_text='Plain-language tooltip text.')
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'code']

    def __str__(self):
        return self.code


class AccessRole(models.Model):
    """A role template: a named bundle of permissions (RolePermission rows) a user can be assigned."""
    name = models.CharField(max_length=30, unique=True)
    description = models.CharField(max_length=255, blank=True)
    is_superadmin = models.BooleanField(default=False, help_text='Bypasses all permission checks everywhere.')
    is_system = models.BooleanField(default=False, help_text='Seeded role; cannot be deleted from the UI.')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ScopeType(models.TextChoices):
    GLOBAL = 'GLOBAL', 'Global'
    PROJECT = 'PROJECT', 'Project'


class UserAccess(models.Model):
    """
    One row = "this user has this role, either everywhere (GLOBAL) or on this one project (PROJECT)".
    Replaces the *access decision* previously implied by ProjectOwner/ProjectManager membership
    (role was implicit there: which table you were in). A user may hold several rows (e.g. MANAGER on
    Site A, OWNER on Site B).
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='access_grants')
    role = models.ForeignKey(AccessRole, on_delete=models.PROTECT, related_name='user_access')
    scope_type = models.CharField(max_length=10, choices=ScopeType.choices)
    project = models.ForeignKey(
        'Project', on_delete=models.CASCADE, null=True, blank=True, related_name='user_access',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # One row per (user, project, scope): a user can hold only one role on a given project,
            # and only one GLOBAL row. Django's default UniqueConstraint treats NULL as distinct (so
            # `project=NULL` rows -- the GLOBAL ones -- would never collide on their own), which is
            # exactly django/django#26495 / the reason `nulls_distinct` exists -- but only from
            # Django 5.0, and this project is pinned to Django==4.2.30 (requirements.txt), so the
            # equivalent here is two partial constraints instead (no runtime version branching: a
            # pinned dependency doesn't need one, and it would make `makemigrations --check` depend
            # on which Django happens to be installed).
            models.UniqueConstraint(
                fields=['user', 'project'], condition=models.Q(project__isnull=False),
                name='unique_user_project_access',
            ),
            models.UniqueConstraint(
                fields=['user'], condition=models.Q(scope_type='GLOBAL'), name='unique_user_global_access',
            ),
            models.CheckConstraint(
                check=(
                    models.Q(scope_type='GLOBAL', project__isnull=True)
                    | models.Q(scope_type='PROJECT', project__isnull=False)
                ),
                name='useraccess_scope_matches_project',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'project']),
        ]

    def __str__(self):
        where = 'GLOBAL' if self.scope_type == ScopeType.GLOBAL else str(self.project_id)
        return f'{self.user_id}: {self.role.name} @ {where}'

    def clean(self):
        if self.scope_type == ScopeType.GLOBAL and self.project_id:
            raise ValidationError({'project': 'A global assignment cannot have a project.'})
        if self.scope_type == ScopeType.PROJECT and not self.project_id:
            raise ValidationError({'project': 'Pick a project for a project-level assignment.'})


class OverrideEffect(models.TextChoices):
    ALLOW = 'ALLOW', 'Allow'
    DENY = 'DENY', 'Deny'


class AccessOverride(models.Model):
    """
    A per-assignment exception to what the role template grants: "Manoj, on Site B, is Blocked from
    Edit Expense even though Owner normally allows it" (DENY), or "... is given Extra access to X even
    though their role normally doesn't allow it" (ALLOW). Deny always wins (see services.has_perm).
    """
    user_access = models.ForeignKey(UserAccess, on_delete=models.CASCADE, related_name='overrides')
    resource_permission = models.ForeignKey(ResourcePermission, on_delete=models.CASCADE, related_name='overrides')
    effect = models.CharField(max_length=10, choices=OverrideEffect.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user_access', 'resource_permission'], name='unique_override'),
        ]
        indexes = [
            models.Index(fields=['user_access']),
        ]

    def __str__(self):
        return f'{self.user_access}: {self.resource_permission.code} = {self.effect}'


class PermissionAuditLog(models.Model):
    """Append-only record of every change made from the Access Control screens."""
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    action = models.CharField(max_length=50, help_text='e.g. ROLE_PERMISSION_CHANGED, USER_ACCESS_GRANTED')
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.action} by {self.actor_id} @ {self.created_at:%Y-%m-%d %H:%M}'


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class Project(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)
    plot_size = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=255, blank=True)
    start_date = models.DateField(null=True, blank=True)
    expected_completion_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=ProjectStatus.choices, default=ProjectStatus.PLANNED)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.code} - {self.name}'


# ---------------------------------------------------------------------------
# Owner
# ---------------------------------------------------------------------------

class Owner(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='owner_profile')
    name = models.CharField(max_length=255)
    mobile = models.CharField(max_length=20, blank=True)
    remarks = models.TextField(blank=True)

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class Manager(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='manager_profile')
    name = models.CharField(max_length=255)
    mobile = models.CharField(max_length=20, blank=True)
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    remarks = models.TextField(blank=True)

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Labour / Contractor / Supplier
# ---------------------------------------------------------------------------

class Labour(models.Model):
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=100, blank=True)
    mobile = models.CharField(max_length=20, blank=True)
    remarks = models.TextField(blank=True)

    def __str__(self):
        return self.name


class ProjectLabour(models.Model):
    """
    Which labour (person, from the Labour master) works on which project, and
    whether they are currently active there. Status is per project: the same
    Labour can be active on one project and inactive on another. Inactive rows
    are never deleted, so a returning labour is re-activated (same Labour id).
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='project_labours')
    labour = models.ForeignKey(Labour, on_delete=models.CASCADE, related_name='project_links')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('project', 'labour')
        indexes = [
            models.Index(fields=['project', 'is_active']),
        ]

    def __str__(self):
        return f'{self.labour.name} @ {self.project.code} ({"active" if self.is_active else "inactive"})'


class Contractor(models.Model):
    name = models.CharField(max_length=255)
    work_type = models.CharField(max_length=100, blank=True)
    mobile = models.CharField(max_length=20, blank=True)
    remarks = models.TextField(blank=True)

    def __str__(self):
        return self.name


class ContractorContract(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='contractor_contracts')
    contractor = models.ForeignKey(Contractor, on_delete=models.CASCADE, related_name='contracts')
    contract_date = models.DateField()
    contract_amount = models.DecimalField(max_digits=12, decimal_places=2)
    work_description = models.CharField(
        max_length=255, blank=True, default='',
        help_text='What this contract covers. One contractor may have several contracts on a project, '
                  'so this (with the amount) tells them apart. Required by the API.',
    )
    remarks = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['contract_date']),
        ]

    def __str__(self):
        return f'{self.contractor.name} - {self.project.code}'

    @property
    def paid_amount(self):
        total = self.expense_transactions.filter(
            status=TransactionStatus.ACTIVE,
            expense_category=ExpenseCategory.CONTRACTOR,
        ).aggregate(total=models.Sum('amount'))['total']
        return total or ZERO

    @property
    def balance(self):
        return self.contract_amount - self.paid_amount


class Supplier(models.Model):
    name = models.CharField(max_length=255)
    supplier_type = models.CharField(max_length=100, blank=True)
    mobile = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    remarks = models.TextField(blank=True)

    def __str__(self):
        return self.name


class MiscExpenseType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Expense Transaction (the core table)
# ---------------------------------------------------------------------------

class ExpenseTransaction(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name='expense_transactions')
    expense_date = models.DateField()
    expense_category = models.CharField(max_length=20, choices=ExpenseCategory.choices)
    expense_type = models.CharField(
        max_length=100,
        help_text='Free text, validated against expense_category in clean().',
    )
    party_type = models.CharField(max_length=20, choices=PartyType.choices, default=PartyType.NONE)

    labour = models.ForeignKey(
        Labour, on_delete=models.PROTECT, null=True, blank=True, related_name='expense_transactions'
    )
    contractor_contract = models.ForeignKey(
        ContractorContract, on_delete=models.PROTECT, null=True, blank=True, related_name='expense_transactions'
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, null=True, blank=True, related_name='expense_transactions'
    )
    payee_name = models.CharField(max_length=255, null=True, blank=True, help_text='Used for MISCELLANEOUS expenses.')

    payment_batch = models.UUIDField(
        null=True, blank=True, editable=False, db_index=True,
        help_text='Shared by the per-labour rows saved together from one Labour Payment entry.',
    )

    paid_by_owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name='expense_transactions')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices)
    reference_no = models.CharField(max_length=100, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=TransactionStatus.choices, default=TransactionStatus.ACTIVE)

    # Supplier bill (one per transaction). The file itself lives in private Supabase Storage;
    # only its object path and metadata are stored here. Empty bill_path means "no bill".
    bill_path = models.CharField(max_length=255, blank=True, default='', editable=False)
    bill_filename = models.CharField(max_length=255, blank=True, default='', editable=False)
    bill_content_type = models.CharField(max_length=100, blank=True, default='', editable=False)
    bill_size = models.PositiveIntegerField(null=True, blank=True, editable=False)
    bill_uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, editable=False,
        related_name='bills_uploaded',
    )
    bill_uploaded_at = models.DateTimeField(null=True, blank=True, editable=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='expense_transactions_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='expense_transactions_modified',
        null=True,
        blank=True,
    )
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['expense_date']),
            models.Index(fields=['project', 'expense_date']),
            models.Index(fields=['expense_category']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.project.code} - {self.expense_category} - {self.amount}'

    def clean(self):
        errors = {}

        category_party_map = {
            ExpenseCategory.LABOUR: PartyType.LABOUR,
            ExpenseCategory.CONTRACTOR: PartyType.CONTRACTOR,
            ExpenseCategory.SUPPLIER: PartyType.SUPPLIER,
            ExpenseCategory.MISCELLANEOUS: PartyType.NONE,
        }
        expected_party_type = category_party_map.get(self.expense_category)
        if expected_party_type and self.party_type != expected_party_type:
            errors['party_type'] = (
                f'party_type must be {expected_party_type} for expense_category {self.expense_category}.'
            )

        if self.expense_category == ExpenseCategory.LABOUR and not self.labour_id:
            errors['labour'] = 'labour is required when expense_category is LABOUR.'
        if self.expense_category == ExpenseCategory.CONTRACTOR and not self.contractor_contract_id:
            errors['contractor_contract'] = 'contractor_contract is required when expense_category is CONTRACTOR.'
        if self.expense_category == ExpenseCategory.SUPPLIER and not self.supplier_id:
            errors['supplier'] = 'supplier is required when expense_category is SUPPLIER.'
        if self.expense_category == ExpenseCategory.MISCELLANEOUS and not self.payee_name:
            errors['payee_name'] = 'payee_name is required when expense_category is MISCELLANEOUS.'

        if errors:
            raise ValidationError(errors)

    def cancel(self, cancelled_by=None, reason=None):
        """Never hard-delete a transaction; mark it CANCELLED instead (reversal)."""
        self.status = TransactionStatus.CANCELLED
        update_fields = ['status', 'modified_by', 'modified_at']
        if cancelled_by is not None:
            self.modified_by = cancelled_by
        if reason:
            self.remarks = reason
            update_fields.append('remarks')
        self.save(update_fields=update_fields)


def active_distributions(queryset):
    """Distributions that still count: those whose mirrored expense has not been cancelled."""
    return queryset.exclude(expense_transaction__status=TransactionStatus.CANCELLED)


def annotate_last_paid(project_labour_qs):
    """
    Add `last_paid` (date of the latest NON-CANCELLED payment to this labour on this
    project) to a ProjectLabour queryset. Informational only: it never decides
    whether a labour is active -- that is the explicit ProjectLabour.is_active flag.
    """
    latest = (
        ExpenseTransaction.objects
        .filter(project=OuterRef('project'), labour=OuterRef('labour'))
        .exclude(status=TransactionStatus.CANCELLED)
        .order_by('-expense_date')
        .values('expense_date')[:1]
    )
    return project_labour_qs.annotate(last_paid=Subquery(latest))


# ---------------------------------------------------------------------------
# Manager funds and distribution to labour
# ---------------------------------------------------------------------------

class ManagerFund(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='manager_funds')
    manager = models.ForeignKey(Manager, on_delete=models.CASCADE, related_name='funds_received')
    fund_date = models.DateField()
    fund_amount = models.DecimalField(max_digits=12, decimal_places=2)
    given_by_owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name='manager_funds_given')
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Who recorded this fund (NULL for rows that predate this field).
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, editable=False,
        related_name='manager_funds_recorded',
    )

    class Meta:
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['fund_date']),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(fund_amount__gt=0), name='managerfund_amount_positive'),
        ]

    def __str__(self):
        return f'{self.manager.name} fund {self.fund_amount} @ {self.project.code}'

    def clean(self):
        """Rules shared by the API and Django admin. (Balance rules live in core/ledger.py.)"""
        from .access import services  # deferred: access.services imports this module at load time

        errors = {}
        if self.fund_amount is not None and self.fund_amount <= 0:
            errors['fund_amount'] = 'Fund amount must be greater than zero.'
        if self.project_id and self.manager_id and not services.users_with_role(
                self.project_id, 'MANAGER').filter(pk=self.manager.user_id).exists():
            errors['manager'] = 'This manager is not assigned to this project.'
        if self.project_id and self.given_by_owner_id and not services.users_with_role(
                self.project_id, 'OWNER').filter(pk=self.given_by_owner.user_id).exists():
            errors['given_by_owner'] = 'This owner does not belong to this project.'
        if self.pk and self.fund_amount is not None and self.fund_amount < self.distributed_amount:
            errors['fund_amount'] = 'Fund amount cannot be less than what has already been distributed from it.'
        if errors:
            raise ValidationError(errors)

    @property
    def distributed_amount(self):
        """Active distributions only: a cancelled mirrored expense gives the money back."""
        total = active_distributions(self.distributions.all()).aggregate(total=models.Sum('amount'))['total']
        return total or ZERO

    @property
    def balance(self):
        return self.fund_amount - self.distributed_amount


class ManagerLabourDistribution(models.Model):
    """
    A manager hands out cash/bank amounts from a ManagerFund to labour.

    IMPORTANT (no double counting):
    ManagerFund itself is NOT an expense — it is money handed to a manager that has
    not yet been spent. Only when the manager actually distributes it to labour here
    does real spend happen. So save() on this model also creates a matching
    ExpenseTransaction (category=LABOUR, party_type=LABOUR) so the amount rolls into
    the normal Labour Expense totals exactly once. Do NOT separately create an
    ExpenseTransaction for the ManagerFund, and do NOT sum ManagerFund.fund_amount
    into expense totals anywhere - that would double count the same money.
    """

    # PROTECT: a fund that has distributions can never be deleted (that would orphan their expenses).
    manager_fund = models.ForeignKey(ManagerFund, on_delete=models.PROTECT, related_name='distributions')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='manager_labour_distributions')
    manager = models.ForeignKey(Manager, on_delete=models.CASCADE, related_name='labour_distributions')
    date = models.DateField()
    labour = models.ForeignKey(Labour, on_delete=models.PROTECT, related_name='manager_distributions')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Who distributed it (NULL for rows that predate this field).
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, editable=False,
        related_name='manager_distributions_recorded',
    )

    # The one expense this distribution mirrors. A distribution counts against the manager's balance
    # only while this expense is ACTIVE; cancelling it gives the money back (the row itself stays).
    expense_transaction = models.OneToOneField(
        ExpenseTransaction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name='manager_labour_distribution',
    )

    class Meta:
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['date']),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gt=0), name='managerlabourdistribution_amount_positive'),
        ]

    def __str__(self):
        return f'{self.manager.name} -> {self.labour.name}: {self.amount}'

    @property
    def is_active(self):
        return self.expense_transaction_id is None or self.expense_transaction.status == TransactionStatus.ACTIVE

    def save(self, *args, **kwargs):
        # The API goes through core/ledger.py (which locks the balance and links the expense itself).
        # This mirroring only runs for a distribution saved directly without an expense.
        with transaction.atomic():
            self._save_and_mirror(*args, **kwargs)

    def _save_and_mirror(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)

        if is_new and self.expense_transaction_id is None:
            # Mirror this distribution into ExpenseTransaction so it counts once
            # towards Labour Expense totals. See class docstring: ManagerFund is
            # NOT itself an expense, only this distribution is.
            owner = self.manager_fund.given_by_owner
            expense = ExpenseTransaction.objects.create(
                project=self.project,
                expense_date=self.date,
                expense_category=ExpenseCategory.LABOUR,
                expense_type='Labour Payment (via Manager Fund)',
                party_type=PartyType.LABOUR,
                labour=self.labour,
                paid_by_owner=owner,
                amount=self.amount,
                payment_mode=self.manager_fund.payment_mode,
                description=f'Distributed by manager {self.manager.name} from fund #{self.manager_fund_id}',
                remarks=self.remarks,
                created_by=self.created_by or owner.user,
            )
            self.expense_transaction = expense
            super().save(update_fields=['expense_transaction'])
