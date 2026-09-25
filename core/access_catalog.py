"""
The RBAC v2 permission catalogue: single source of truth for every valid permission code, its
resource/action, its plain-language label, and the dependency rules between codes.

Codes are kept byte-for-byte identical to the existing ones in `web/authz.js` DEFINITIONS and
`core/permissions.py` PERMISSION_DEFAULTS (e.g. `canEditExpense`), per the instruction to keep
permission codes stable -- this file only adds structure (resource/action/description) on top of
strings that already exist, it does not rename them.

Resource/Action are metadata used for: grouping in the UI, and the dependency rules below. They are
seeded into the `Resource`/`Action`/`ResourcePermission` tables by the 0007 data migration and by
`seed_rbac` (both import this module so there is exactly one place that can go stale).
"""

# id, label (kept close to web/authz.js GROUPS)
GROUPS = [
    ('view', 'View / Dekhna'),
    ('ops', 'Daily Operations / Data Entry'),
    ('project', 'Project Management'),
    ('app', 'Application Management'),
    ('personal', 'Personal'),
]

# code -> (resource, action, group, label, description)
# `description` is the plain-language ⓘ tooltip text required by the UI spec.
PERMISSIONS = {
    'canViewProjects': ('PROJECT', 'VIEW', 'view', 'View Projects',
                         'Can see the list of projects and open one.'),
    'canViewExpenses': ('EXPENSE', 'VIEW', 'view', 'View Expenses',
                         'Can see the list of recorded expenses.'),
    'canViewLabour': ('LABOUR', 'VIEW', 'view', 'View Labour',
                       'Can see which labour are working on a project.'),
    'canViewReports': ('REPORTS', 'VIEW', 'view', 'View Reports',
                        'Can open the expense and payment reports.'),
    'canViewSuppliers': ('SUPPLIER', 'VIEW', 'view', 'View Suppliers',
                          'Can see the supplier list and their balances.'),
    'canViewContractors': ('CONTRACTOR', 'VIEW', 'view', 'View Contractors',
                            'Can see the contractor list and their contracts.'),
    'canViewManagerFund': ('MANAGER_FUND', 'VIEW', 'view', 'View Manager Fund',
                            'Can see how much fund a manager has been given and spent.'),
    'canViewProjectFunds': ('MANAGER_FUND', 'VIEW_ALL', 'view', "View All Managers' Funds",
                             "Can see every manager's fund position on a project, not just their own."),
    'canViewBill': ('BILL', 'VIEW', 'view', 'View Bill',
                     'Can open an uploaded supplier bill.'),

    'canAddSupplierExpense': ('EXPENSE', 'CREATE', 'ops', 'Add Supplier Payment',
                               'Can record a new payment to a supplier.'),
    'canAddContractorExpense': ('EXPENSE', 'CREATE', 'ops', 'Add Contractor Payment',
                                 'Can record a new payment to a contractor.'),
    'canAddMiscExpense': ('EXPENSE', 'CREATE', 'ops', 'Add Misc Expense',
                           'Can record a new miscellaneous expense.'),
    'canEditExpense': ('EXPENSE', 'EDIT', 'ops', 'Edit Expense',
                        'Can change amount/date of an expense after saving.'),
    'canDeleteExpense': ('EXPENSE', 'DELETE', 'ops', 'Delete Expense',
                          'Can cancel an expense that was recorded by mistake.'),
    'canManageLabour': ('LABOUR', 'MANAGE', 'ops', 'Manage Labour',
                         'Can add labour to a project or mark them inactive.'),
    'canRecordLabourPayment': ('LABOUR', 'PAY', 'ops', 'Labour Payment',
                                'Can record a cash/bank payment made to labour.'),
    'canManageSuppliers': ('SUPPLIER', 'MANAGE', 'ops', 'Manage Suppliers',
                            'Can add or edit supplier details.'),
    'canManageContractors': ('CONTRACTOR', 'MANAGE', 'ops', 'Manage Contractors',
                              'Can add or edit contractor details and contracts.'),
    'canGiveManagerFund': ('MANAGER_FUND', 'GIVE', 'ops', 'Give Manager Fund',
                            'Can hand a cash/bank fund to a manager to spend.'),
    'canDistributeManagerFund': ('MANAGER_FUND', 'DISTRIBUTE', 'ops', 'Distribute Manager Fund',
                                  'Can pay labour out of a fund already given to a manager.'),
    'canUploadBill': ('BILL', 'UPLOAD', 'ops', 'Upload Bill',
                       'Can attach a supplier bill to an expense.'),

    'canManageProjectMembers': ('PROJECT_MEMBERS', 'MANAGE', 'project', 'Manage Project Members',
                                 'Can add or remove owners/managers on a project.'),
    'canManageProjectSettings': ('PROJECT_SETTINGS', 'MANAGE', 'project', 'Manage Project Settings',
                                  'Can change a project’s name, dates and status.'),
    'canResetUserPassword': ('USER', 'RESET_PASSWORD', 'project', 'Reset User Password',
                              'Can set a new password for someone on their project.'),

    'canCreateProject': ('PROJECT', 'CREATE', 'app', 'Create Project',
                          'Can create a brand-new project.'),
    'canViewAllProjects': ('PROJECT', 'VIEW_ALL', 'app', 'View All Projects',
                            "Can see the Super Admin Dashboard: every project, its spend and attention items."),
    'canManageUsers': ('USER', 'MANAGE', 'app', 'Manage Users',
                        'Can create global user accounts.'),
    'canManageApplicationSettings': ('APP_SETTINGS', 'MANAGE', 'app', 'Application Settings',
                                      'Can change application-wide settings.'),
    'canManagePermissions': ('PERMISSIONS', 'MANAGE', 'app', 'Role & Permissions',
                              'Can open the Access Control screens and change what roles can do.'),

    'canChangeOwnPassword': ('ACCOUNT', 'CHANGE_PASSWORD', 'personal', 'Change Own Password',
                              'Can change their own login password.'),
}

# Codes that are always granted to everyone logged in, never editable, never checked against a
# scope/project (mirrors web/authz.js `anyUser`).
ANY_USER_CODES = {'canChangeOwnPassword'}

# Codes locked ON for SUPER_ADMIN and not editable for any role via the UI (mirrors `fixed` in authz.js).
SUPER_ADMIN_ONLY_CODES = {
    'canCreateProject', 'canManageUsers', 'canManageApplicationSettings', 'canManagePermissions',
    'canViewAllProjects',
}

# Always required, for every role, on every scope: the baseline "can this role open the project at all".
BASELINE_CODE = 'canViewProjects'


def _view_code_for_resource(resource):
    for code, (res, action, *_rest) in PERMISSIONS.items():
        if res == resource and action == 'VIEW':
            return code
    return None


def build_dependencies():
    """
    code -> set of codes it requires, derived from the rules:
      - CREATE/EDIT/DELETE/APPROVE/MANAGE/PAY/GIVE/DISTRIBUTE/UPLOAD/RESET_PASSWORD on X => VIEW X.
      - Every non-PROJECT permission => PROJECT.VIEW (`canViewProjects`).
      - Cross-deps: canAddSupplierExpense => SUPPLIER.VIEW; canAddContractorExpense => CONTRACTOR.VIEW;
        canEditExpense => SUPPLIER.VIEW, CONTRACTOR.VIEW (editing any category's expense may show either).
    `canChangeOwnPassword` (ANY_USER_CODES) and the baseline itself are excluded.
    """
    action_needs_view = {
        'CREATE', 'EDIT', 'DELETE', 'APPROVE', 'MANAGE', 'PAY', 'GIVE', 'DISTRIBUTE', 'UPLOAD',
        'RESET_PASSWORD',
    }
    deps = {}
    for code, (resource, action, *_rest) in PERMISSIONS.items():
        if code in ANY_USER_CODES:
            continue
        needs = set()
        if action in action_needs_view:
            view_code = _view_code_for_resource(resource)
            if view_code and view_code != code:
                needs.add(view_code)
        if resource != 'PROJECT' and code != BASELINE_CODE:
            needs.add(BASELINE_CODE)
        deps[code] = needs

    # Cross-resource rules.
    deps.setdefault('canAddSupplierExpense', set()).update({'canViewSuppliers'})
    deps.setdefault('canAddContractorExpense', set()).update({'canViewContractors'})
    deps.setdefault('canEditExpense', set()).update({'canViewSuppliers', 'canViewContractors'})
    deps.setdefault('canViewProjectFunds', set()).update({'canViewManagerFund'})
    return deps


DEPENDENCIES = build_dependencies()


def all_dependencies_of(code, seen=None):
    """Transitive closure of what `code` requires (including indirect requirements)."""
    seen = seen if seen is not None else set()
    for dep in DEPENDENCIES.get(code, ()):
        if dep not in seen:
            seen.add(dep)
            all_dependencies_of(dep, seen)
    return seen


def dependents_of(code):
    """Codes that require `code` directly (used by the UI to auto-disable dependents)."""
    return {c for c, deps in DEPENDENCIES.items() if code in deps}


# "Reset to Default" target for the Roles & Permissions screen: the same defaults the 0007 data
# migration seeded with (kept here too, since a migration's own copy is frozen at the version it ran
# against -- this one is read live by core/access/api.py).
RESET_DEFAULTS = {
    'canUploadBill': {'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    'canViewBill': {'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    'canViewManagerFund': {'OWNER': True, 'MANAGER': True, 'VIEWER': False},
    'canViewProjectFunds': {'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    'canGiveManagerFund': {'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    'canDistributeManagerFund': {'OWNER': False, 'MANAGER': True, 'VIEWER': False},
    'canEditExpense': {'OWNER': True, 'MANAGER': True, 'VIEWER': False},
    'canDeleteExpense': {'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    'canManageProjectMembers': {'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    'canManageProjectSettings': {'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    'canResetUserPassword': {'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    'canViewProjects': {'OWNER': True, 'MANAGER': True, 'VIEWER': True},
    'canViewExpenses': {'OWNER': True, 'MANAGER': True, 'VIEWER': True},
    'canViewLabour': {'OWNER': True, 'MANAGER': True, 'VIEWER': True},
    'canViewReports': {'OWNER': True, 'MANAGER': True, 'VIEWER': True},
    'canViewSuppliers': {'OWNER': True, 'MANAGER': True, 'VIEWER': False},
    'canViewContractors': {'OWNER': True, 'MANAGER': True, 'VIEWER': False},
    'canAddSupplierExpense': {'OWNER': True, 'MANAGER': True, 'VIEWER': False},
    'canAddContractorExpense': {'OWNER': True, 'MANAGER': True, 'VIEWER': False},
    'canAddMiscExpense': {'OWNER': True, 'MANAGER': True, 'VIEWER': False},
    'canManageLabour': {'OWNER': True, 'MANAGER': True, 'VIEWER': False},
    'canRecordLabourPayment': {'OWNER': True, 'MANAGER': True, 'VIEWER': False},
    'canManageSuppliers': {'OWNER': True, 'MANAGER': False, 'VIEWER': False},
    'canManageContractors': {'OWNER': True, 'MANAGER': False, 'VIEWER': False},
}
