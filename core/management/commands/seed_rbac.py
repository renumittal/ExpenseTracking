"""
Idempotent seed for RBAC v2: the same catalogue-seeding logic the 0007 migration runs, exposed as a
management command so it can be re-run after `access_catalog.py` gains a new permission code, without
writing a new migration every time. Only ever *creates* missing rows (get_or_create) -- it never
overwrites an existing RolePermission row, so it's safe to run again after a super admin has
customized the Roles & Permissions screen.

    python manage.py seed_rbac

`--demo` also creates a small set of demo projects/users/expenses covering the standard RBAC test
scenarios (Parveen/Anil single-project owners, Manoj a two-project manager with a per-project
override). Refuses to run unless DEBUG=True or --force is passed, since it creates accounts with a
well-known password:

    python manage.py seed_rbac --demo
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.access_catalog import PERMISSIONS, RESET_DEFAULTS
from core.models import (
    Action, AccessOverride, AccessRole, ExpenseCategory, ExpenseTransaction, Labour, Manager, Owner,
    OverrideEffect, PartyType, PaymentMode, Project, ProjectLabour, Resource, ResourcePermission, RolePermission,
    ScopeType, UserAccess,
)

ROLE_DESCRIPTIONS = {
    'SUPER_ADMIN': ('Full access to every project and every setting.', True),
    'OWNER': ('Runs a project day to day: expenses, funds, members.', False),
    'MANAGER': ('Records expenses and labour payments on assigned projects.', False),
    'VIEWER': ('Read-only access: can see but not change anything.', False),
}

DEMO_PASSWORD = 'Demo@123'


class Command(BaseCommand):
    help = 'Seed the RBAC v2 catalogue (Resource/Action/ResourcePermission) and the built-in roles.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--demo', action='store_true',
            help='Also create demo projects/users (Renu/Parveen/Anil/Manoj) and sample expenses.',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Allow --demo even when DEBUG is False (it creates accounts with a well-known password).',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        resources, actions, made = {}, {}, 0
        for order, (code, (resource, action, group, label, description)) in enumerate(PERMISSIONS.items()):
            if resource not in resources:
                resources[resource] = Resource.objects.get_or_create(
                    code=resource, defaults={'label': resource.title()})[0]
            if action not in actions:
                actions[action] = Action.objects.get_or_create(
                    code=action, defaults={'label': action.title()})[0]
            _, created = ResourcePermission.objects.update_or_create(
                code=code,
                defaults={
                    'resource': resources[resource], 'action': actions[action], 'group': group,
                    'label': label, 'description': description, 'sort_order': order,
                },
            )
            made += created

        roles, role_count = {}, 0
        for name, (description, is_superadmin) in ROLE_DESCRIPTIONS.items():
            role, created = AccessRole.objects.get_or_create(
                name=name, defaults={'description': description, 'is_superadmin': is_superadmin, 'is_system': True},
            )
            roles[name] = role
            role_count += created

        # Only ever create a missing default -- never touch a row that already exists, so re-running
        # this after a super admin has customized the Roles & Permissions screen doesn't reset it.
        rp_count = 0
        for role_name in ('OWNER', 'MANAGER', 'VIEWER'):
            role = roles[role_name]
            for rp in ResourcePermission.objects.all():
                default = RESET_DEFAULTS.get(rp.code, {}).get(role_name, False)
                _, created = RolePermission.objects.get_or_create(
                    role_fk=role, resource_permission=rp,
                    defaults={'allowed': default, 'role': role_name, 'permission': rp.code},
                )
                rp_count += created

        self.stdout.write(self.style.SUCCESS(
            f'Seeded {len(resources)} resources, {len(actions)} actions, '
            f'{len(PERMISSIONS)} permissions ({made} new), {role_count} new roles, {rp_count} new role permissions.'
        ))

        if options['demo']:
            if not settings.DEBUG and not options['force']:
                raise CommandError(
                    '--demo creates accounts with a well-known password; refusing outside DEBUG. '
                    'Pass --force if you really want this (e.g. a throwaway staging environment).'
                )
            self._seed_demo(roles)

    def _seed_demo(self, roles):
        User = get_user_model()
        credentials = []

        def make_user(username, first_name):
            user, _ = User.objects.get_or_create(username=username, defaults={'first_name': first_name})
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=['password'])
            credentials.append(username)
            return user

        site_a, _ = Project.objects.get_or_create(code='SITE-A', defaults={'name': 'Site A'})
        site_b, _ = Project.objects.get_or_create(code='SITE-B', defaults={'name': 'Site B'})

        renu = make_user('renu', 'Renu')
        UserAccess.objects.get_or_create(
            user=renu, project=None, defaults={'role': roles['SUPER_ADMIN'], 'scope_type': ScopeType.GLOBAL})

        parveen = make_user('parveen', 'Parveen')
        parveen_owner, _ = Owner.objects.get_or_create(user=parveen, defaults={'name': 'Parveen'})
        UserAccess.objects.get_or_create(
            user=parveen, project=site_a, defaults={'role': roles['OWNER'], 'scope_type': ScopeType.PROJECT})

        anil = make_user('anil', 'Anil')
        anil_owner, _ = Owner.objects.get_or_create(user=anil, defaults={'name': 'Anil'})
        UserAccess.objects.get_or_create(
            user=anil, project=site_b, defaults={'role': roles['OWNER'], 'scope_type': ScopeType.PROJECT})

        manoj = make_user('manoj', 'Manoj')
        Manager.objects.get_or_create(user=manoj, defaults={'name': 'Manoj'})
        UserAccess.objects.get_or_create(
            user=manoj, project=site_a, defaults={'role': roles['MANAGER'], 'scope_type': ScopeType.PROJECT})
        manoj_b, _ = UserAccess.objects.get_or_create(
            user=manoj, project=site_b, defaults={'role': roles['MANAGER'], 'scope_type': ScopeType.PROJECT})
        edit_expense = ResourcePermission.objects.get(code='canEditExpense')
        AccessOverride.objects.update_or_create(
            user_access=manoj_b, resource_permission=edit_expense, defaults={'effect': OverrideEffect.DENY},
        )

        # A few sample rows so the screens aren't empty.
        for project, owner_user, owner, label in ((site_a, parveen, parveen_owner, 'A'), (site_b, anil, anil_owner, 'B')):
            labour, _ = Labour.objects.get_or_create(name=f'Ramesh {label}')
            ProjectLabour.objects.get_or_create(project=project, labour=labour, defaults={'is_active': True})
            ExpenseTransaction.objects.get_or_create(
                project=project, expense_date='2026-01-05', expense_category=ExpenseCategory.MISCELLANEOUS,
                expense_type='Other', party_type=PartyType.NONE, payee_name='Sample Vendor',
                defaults={
                    'paid_by_owner': owner, 'amount': '5000.00', 'payment_mode': PaymentMode.CASH,
                    'created_by': owner_user, 'description': f'Demo seed expense on Site {label}',
                },
            )

        self.stdout.write(self.style.SUCCESS(
            'Demo data ready: Site A (Parveen/OWNER, Manoj/MANAGER), Site B (Anil/OWNER, Manoj/MANAGER, '
            'EXPENSE.EDIT blocked for Manoj), Renu/SUPER_ADMIN.'
        ))
        self.stdout.write(self.style.SUCCESS(f'Login with any of these usernames, password "{DEMO_PASSWORD}":'))
        for username in credentials:
            self.stdout.write(f'  {username} / {DEMO_PASSWORD}')
