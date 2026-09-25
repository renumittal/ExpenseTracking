"""
Splits the single `canAddExpense` permission into one code per resource: `canAddSupplierExpense`,
`canAddContractorExpense` and `canAddMiscExpense` (labour already had its own `canRecordLabourPayment`),
per the "Add Expense should be resource-wise" request -- a generic "can add any expense" toggle hid
that supplier/contractor/misc entry are different workflows a super admin may want to allow separately.

The existing `canAddExpense` ResourcePermission row is renamed in place to `canAddMiscExpense` (so its
id, and every RolePermission row / AccessOverride that already points at it, survives untouched --
whatever a super admin had configured for "Add Expense" keeps working for Misc). The two new codes are
created fresh and seeded by copying that same row's RolePermission/AccessOverride values, so nothing's
access changes the moment this migration runs -- a super admin can then turn Supplier/Contractor entry
on or off independently going forward.

On a brand-new database this is a no-op: 0007 already seeds straight from the current access_catalog.py
(it imports the module live, not a frozen snapshot), so `canAddExpense` never existed there in the
first place.
"""
from django.db import migrations

NEW_CODES = [
    ('canAddSupplierExpense', 'Add Supplier Payment', 'Can record a new payment to a supplier.'),
    ('canAddContractorExpense', 'Add Contractor Payment', 'Can record a new payment to a contractor.'),
]


def split_forward(apps, schema_editor):
    ResourcePermission = apps.get_model('core', 'ResourcePermission')
    RolePermission = apps.get_model('core', 'RolePermission')
    AccessOverride = apps.get_model('core', 'AccessOverride')

    old = ResourcePermission.objects.filter(code='canAddExpense').first()
    if old is None:
        return

    old.code = 'canAddMiscExpense'
    old.label = 'Add Misc Expense'
    old.description = 'Can record a new miscellaneous expense.'
    old.save(update_fields=['code', 'label', 'description'])

    for code, label, description in NEW_CODES:
        new_rp = ResourcePermission.objects.create(
            code=code, resource=old.resource, action=old.action, group=old.group,
            label=label, description=description, sort_order=old.sort_order,
        )
        for rp_row in RolePermission.objects.filter(resource_permission=old):
            RolePermission.objects.create(
                role_fk=rp_row.role_fk, resource_permission=new_rp,
                allowed=rp_row.allowed, role=rp_row.role, permission=code,
            )
        for ov in AccessOverride.objects.filter(resource_permission=old):
            AccessOverride.objects.create(
                user_access=ov.user_access, resource_permission=new_rp,
                effect=ov.effect, created_by_id=ov.created_by_id,
            )


def split_backward(apps, schema_editor):
    ResourcePermission = apps.get_model('core', 'ResourcePermission')
    ResourcePermission.objects.filter(code__in=[c for c, _, _ in NEW_CODES]).delete()
    ResourcePermission.objects.filter(code='canAddMiscExpense').update(
        code='canAddExpense', label='Add Expense', description='Can record a new expense.',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_alter_project_status'),
    ]

    operations = [
        migrations.RunPython(split_forward, split_backward),
    ]
