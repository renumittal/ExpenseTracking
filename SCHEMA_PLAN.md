# RBAC v2 — Schema Plan

## Current state (inspected)

- `core_profile` (`Profile`): one row per user, `role` = OWNER/MANAGER/ADMIN (`Role` TextChoices). One role per
  user, applied everywhere — there is no per-project role today.
- `core_rolepermission` (`RolePermission`): `(role: str, permission: str, allowed: bool)`. `role` already
  includes `VIEWER`, which no real user can have yet (`Role.choices` used by `Profile.role` only has
  OWNER/MANAGER/ADMIN). This *is* the existing "role template" toggle grid (`people.PermissionMatrixView`),
  edited from Settings → Role & Permissions in the web app.
- `core_projectowner` / `core_projectmanager`: which projects an Owner/Manager touches. This is the existing
  "project access" table — but it carries no role of its own; the role is implied by which table the row is
  in (`ProjectOwner` → OWNER, `ProjectManager` → MANAGER). SUPER_ADMIN gets every project implicitly
  (`permissions.is_admin`), never via a row.
- Permission codes: `core/permissions.py` (`PERMISSION_DEFAULTS`) only enforces 11 codes server-side
  (`canUploadBill`, `canViewBill`, `canViewManagerFund`, `canGiveManagerFund`, `canDistributeManagerFund`,
  `canEditExpense`, `canDeleteExpense`, `canManageUsers`, `canManageProjectMembers`,
  `canManageProjectSettings`, `canResetUserPassword`). `web/authz.js` (`DEFINITIONS`) defines a **larger**
  catalogue of 24 codes (adds `canViewProjects`, `canViewExpenses`, `canViewLabour`, `canViewReports`,
  `canViewSuppliers`, `canViewContractors`, `canAddExpense`, `canManageLabour`, `canRecordLabourPayment`,
  `canManageSuppliers`, `canManageContractors`, `canCreateProject`, `canManageApplicationSettings`,
  `canManagePermissions`, `canChangeOwnPassword`) that today are UI-only (show/hide), not server-enforced.
  This is the canonical permission catalogue this pass makes real — codes are **kept exactly as-is**
  (`canEditExpense`, not `EXPENSE.EDIT`), per the instruction to keep permission codes stable.
- Frontend is a single-page vanilla-JS PWA (`web/app.js` + `web/authz.js`), not Django templates. There is no
  server-rendered admin UI to extend — `{% if_perm %}` becomes a small JS helper (`Authz.ifPerm`), and
  `@require_perm` / `RequirePermMixin` are DRF-side (decorator + `APIView` mixin), returning 403 JSON.

## Mapping table

| Required concept | Existing table/column | Decision | Reason |
|---|---|---|---|
| Resource | — | **NEW** (`core_resource`) | Grouping label only (`EXPENSE`, `PROJECT`, `SUPPLIER`, …), used for dependency rules and UI grouping. Nothing existing models this. |
| Action | — | **NEW** (`core_action`) | Same as above (`VIEW`, `CREATE`, `EDIT`, `DELETE`, `MANAGE`, `PAY`, …). |
| ResourcePermission | `core_rolepermission.permission` (free string) + `web/authz.js DEFINITIONS` | **NEW** (`core_resourcepermission`), code = existing string (e.g. `canEditExpense`) | The valid-combination catalogue didn't exist as a table — permission codes lived as a Python dict on the server and a JS array on the client, out of sync (server only had 11 of 24). A real table becomes the single source; `code` is kept identical to today's strings so no call site or stored data needs renaming. |
| Role (+ is_superadmin, is_system, description) | `Role` (`TextChoices`, not a table) + `RolePermission.role` string | **NEW** (`core_role`) | Roles were an enum, not rows — no way to attach metadata (`is_superadmin`, description) or add a role without a code change. Seeded 1:1 from the 4 existing role names (`SUPER_ADMIN`\*, `OWNER`, `MANAGER`, `VIEWER`). \*`ADMIN` is renamed `SUPER_ADMIN` only in the new `Role` table's `name`; `Profile.role` keeps storing the string `'ADMIN'` unchanged (deprecated, read nowhere new). |
| RolePermission (role template) | `core_rolepermission` | **ALTER**: add `role_id` FK → `core_role`, `resource_permission_id` FK → `core_resourcepermission` (both nullable at first, backfilled, then NOT NULL); old `role`/`permission` char columns kept but marked deprecated | Reuses the exact table the existing Settings screen already edits — no parallel table, no data loss, no rename of a column other code reads (`role`/`permission` stay, just unused after the data migration). |
| UserAccess (user, role, scope_type, project) | `core_projectowner` + `core_projectmanager` (implicitly) | **NEW** (`core_useraccess`) | The old tables conflate "which project" with "what role" (a `ProjectOwner` row *means* OWNER) and have no GLOBAL scope row for SUPER_ADMIN (implicit via `is_admin()`). A backfill migration creates one `UserAccess` row per existing `ProjectOwner`/`ProjectManager` row (scope PROJECT) plus one GLOBAL row per ADMIN/superuser. `ProjectOwner`/`ProjectManager` are kept (deprecated) — `Owner`/`Manager` master-data rows (name, mobile, salary) still hang off them and other code (`ManagerFund.clean`, ledger balance checks) still reads them this pass. |
| AccessOverride (user_access, resource_permission, effect) | — | **NEW** (`core_accessoverride`) | Nothing existing represents a per-assignment allow/deny exception. |
| PermissionAuditLog | — | **NEW** (`core_permissionauditlog`) | Nothing existing logs permission changes. |

### Deprecated after this pass (not dropped)

- `core_rolepermission.role` (char) and `.permission` (char) — superseded by `.role_id` / `.resource_permission_id`. Still populated (kept in sync by the data migration and `RolePermission.save()`... see note below) so nothing reading the old columns breaks, but no new code reads them.
- `core_profile.role` — superseded by `UserAccess` for access decisions. Still written by `people.py` (`_ensure_profile`, user creation) so existing project-membership code keeps working unchanged this pass; `core/permissions.py` no longer *reads* it for access decisions (only `services.has_perm`/`accessible_projects` decide access now).
- `core_projectowner` / `core_projectmanager` — superseded by `UserAccess` for access *decisions*; kept as the master-data link (Owner/Manager business rows, ledger checks) since ripping them out touches unrelated modules (`ledger.py`, `reports.py`) out of scope for this pass.

Nothing is dropped *in this first pass* — see "Cleanup pass" below, where `ProjectOwner`/`ProjectManager`
are in fact dropped once it's clear no production data needs the two representations kept in sync.

## Migration plan (superseded — see "Cleanup pass" below for the final, squashed version)

---

# Cleanup pass: UserAccess as the *only* source of access truth

The two earlier passes left `UserAccess` correct but only reachable from one direction (legacy
tables → `UserAccess`, kept in sync by signals), `ProjectOwner`/`ProjectManager` still existing as
parallel (deprecated) access tables, and `Profile.role` still populated for display. Since **no
production data exists yet**, this pass simplifies instead of continuing to sync two
representations: `ProjectOwner`/`ProjectManager` are **dropped** (they held no business data beyond
the access link itself — see the table below), `Profile.role` is **no longer read anywhere**
(existing rows are never deleted, but nothing writes or reads it for access any more), and the
migration history is squashed into a clean schema-then-seed pair with no legacy backfill logic.

## 1. UserAccess is the only source of truth — no sync in either direction

`core/access/sync.py` no longer contains any `UserAccess <-> legacy table` mirroring or the
reentrancy-guard machinery that came with it. There is nothing left to sync: `ProjectOwner`/
`ProjectManager` don't exist, and `Profile.role` is dead weight (kept as a column so existing rows
aren't deleted, per instruction, but unread). The one signal that remains is unrelated to user access:
`sync_role_permission_fks`, a `pre_save` on `RolePermission` that fills `role_fk`/`resource_permission`
from the deprecated `role`/`permission` strings for any writer that still only sets those (the old
Settings → Role & Permissions screen, the Django admin) — this is a role-*template* bookkeeping detail,
not an access-truth question.

### Every reader of the old tables was moved onto `services`

| Old code | New code |
|---|---|
| `ProjectOwner.objects.filter(project=p, owner__user=u).exists()` (`owns_project`) | `services.users_with_role(p, 'OWNER').filter(pk=u.id).exists()` |
| `ProjectManager.objects.filter(project=p, manager__user=u).exists()` (`is_project_member`, `manages_project`, `can_distribute_manager_fund`) | `services.users_with_role(p, 'MANAGER').filter(pk=u.id).exists()` |
| `Project.objects.filter(project_owners__owner__user=u)` (`ProjectViewSet.get_queryset`, `reports.scoped_projects`, `ProjectLabourViewSet.get_queryset`, `ContractorContractViewSet.get_queryset`, `_require_project_access`) | `services.projects_with_role(u, 'OWNER')` |
| `Owner.objects.filter(owned_projects__project=p)` / `Manager.objects.filter(managed_projects__project=p)` (`ProjectPeopleView`) | `Owner.objects.filter(user__in=services.users_with_role(p, 'OWNER'))` / same for `Manager`/`MANAGER` |
| `ManagerFund.clean()`, `ManagerLabourDistribution` serializer: `ProjectManager.objects.filter(project=p, manager=m).exists()` | `services.users_with_role(p, 'MANAGER').filter(pk=m.user_id).exists()` — still a **business-data** check ("is this Manager really assigned here"), just reading the new table |
| `get_role(user)` (`Profile.role`) sent as `/me/`'s `role` field, `people._user_role` | `services.display_role(user)` — a **display-only** label (highest-priority role across all of a user's grants: `SUPER_ADMIN` > `OWNER` > `MANAGER`), never used for an access decision |
| `RoleAllowed` (`get_role(request.user) in allowed_roles`) | `UserAccess.objects.filter(user=user, role__name__in=allowed_roles).exists()` — a coarse "holds this role *somewhere*" pre-check; the real per-project decision still happens inside the view via `services.has_perm`/`users_with_role` |
| `is_owner(user)`, `is_manager(user)` (`Profile.role` equality) | Removed outright — every call site now asks a project-scoped question (`services.users_with_role`) instead of a global role question, since a person can hold different roles on different projects |
| `people.py` project-members screen (`ProjectMembersView`/`ProjectMemberDetailView`): `Profile`+`ProjectOwner`/`ProjectManager` rows | Rewritten around `UserAccess` directly (see `people._member_rows`, `_ensure_business_record`) — changing a member's role is now a plain `UserAccess.role` update, with no more "can only change while this is their only project" restriction (that rule existed only because `Profile.role` was one value for the whole person; per-project `UserAccess` rows don't have that limitation) |

## New helpers on `access.services`

- `users_with_role(project, role_name)` — every `User` with a PROJECT-scoped grant naming
  `role_name` on `project`. No super-admin bypass: this answers "who is literally assigned here",
  a business-identity question, not "who can do X".
- `projects_with_role(user, role_name)` — the reverse: every project `user` holds a PROJECT-scoped
  `role_name` grant on.
- `display_role(user)` — a single label for the Users list / `/me/`'s `role` field. Display only.

## Table decision: `ProjectOwner` / `ProjectManager` are dropped, not repurposed

Both tables were pure `(project, owner)`/`(project, manager)` link rows with no other columns — no
business data beyond the access link itself (checked by reading `core/models.py`: neither model had
a field besides its two FKs). Per the "keep only the business data, drop the rest" rule, since there
was no business data to keep, the whole table is removed (`migrations.DeleteModel` in
`0006_rbac_schema.py`). `Owner`/`Manager` (name, mobile, salary — real business data) are untouched;
they're created directly against a `User` now (`people._ensure_business_record`,
`core/access_catalog` seed helpers) instead of through the link table.

## `Profile.role`: unused, not dropped

Per instruction, `Profile` rows are never deleted and the `role` column stays in the schema (removing
a column is a real migration this pass doesn't need to make). Nothing writes to it any more
(`people.py`'s user-creation/member-management code no longer touches `Profile` at all) and nothing
reads it for an access decision — `core/permissions.py` and `core/access/services.py` both go
straight to `UserAccess`. `ProfileAdmin` is now read-only (`has_add_permission`/`has_change_permission`
return `False`) so the Django admin can't create a new inconsistency either.

## Migration plan (squashed, schema + seed only)

1. `0006_rbac_schema.py` — schema only: `Resource`, `Action`, `ResourcePermission`, `AccessRole`,
   `UserAccess`, `AccessOverride`, `PermissionAuditLog`; nullable `role_fk`/`resource_permission` on
   `RolePermission`; **drops `ProjectOwner`/`ProjectManager`** (plain `DeleteModel`, not preceded by
   `RemoveField`/`AlterUniqueTogether` — that ordering broke reversing all the way back to `0001` by
   stripping the model's fields from migration state before `DeleteModel` tried to reconstruct them on
   `database_backwards`). Fully reversible; verified with `manage.py migrate core 0001`.
2. `0007_rbac_seed.py` — data migration, pure seeding (idempotent, `get_or_create`/`update_or_create`
   throughout, no legacy-table backfill of any kind since none exists to backfill):
   - `Resource`/`Action`/`ResourcePermission` from `core/access_catalog.py`.
   - `AccessRole` rows for SUPER_ADMIN/OWNER/MANAGER/VIEWER.
   - Default `RolePermission` rows (from `access_catalog.RESET_DEFAULTS`) for OWNER/MANAGER/VIEWER.
   - Backfills `role_fk`/`resource_permission` on the couple of rows the *pre-RBAC* migration
     `0005_supplier_bill_and_role_permission` seeded via historical models (which never triggered the
     `sync_role_permission_fks` signal, since historical migration models aren't the real classes
     signals are connected to).
3. `0008_rbac_notnull.py` — make `role_fk`/`resource_permission` NOT NULL + `unique_role_fk_resource_permission`.

`verify_rbac_migration` (and its `--database-url` option) is **removed**: it existed to prove the
backfill from `ProjectOwner`/`ProjectManager`/`Profile.role` matched the new engine, and there is no
backfill left to verify. `manage.py seed_rbac` (see below) is the only seeding step now.

## 2. Endpoint audit: is every `has_permission`/`is_admin` call project-aware?

"Project-aware" here means the call resolves a specific `Project` from the URL/object/request body and
passes it to `access.services.has_perm(user, code, project)`, so a per-project `AccessOverride` (ALLOW
or DENY) on that one project is respected — not just "does this role ever get this code, on any
project" (`has_permission`/`has_perm_any_scope`, still correct for genuinely global actions).

| Endpoint | Permission code(s) | Project-aware? | Note |
|---|---|---|---|
| `POST /api/expense-transactions/` | `canAddExpense` | **Y** (fixed) | `perform_create`: project from `validated_data['project']` |
| `PATCH /api/expense-transactions/{id}/` | `canEditExpense` | **Y** (fixed) | `_guard_change`: project from `instance.project` |
| `POST /api/expense-transactions/{id}/cancel/` | `canDeleteExpense` | **Y** (fixed) | same `_guard_change` |
| `GET /api/expense-transactions/` | `canViewExpenses` | **Y** (fixed) | `get_queryset` now filters to `accessible_projects()` ∩ `has_perm(canViewExpenses, p)` per project, not a blanket ownership filter |
| `GET/POST /api/expense-transactions/{id}/bill/` | `canUploadBill`/`canViewBill` | **Y** (fixed) | object fetched first (project-scoped 404), then permission checked against `instance.project` |
| `POST /api/labour-payments/` | `canRecordLabourPayment` | **Y** (fixed) | project from request body; replaces the old ownership-only `_require_project_access` |
| `PATCH /api/projects/{id}/` | `canManageProjectSettings` | **Y** (fixed) | project = `serializer.instance` |
| `GET/POST/PATCH/DELETE /api/projects/{id}/members/...` | `canManageProjectMembers` | **Y** (fixed) | `_members_project()` already resolves the project; now passes it to `has_perm` |
| `POST /api/manager-funds/` (give a fund) | `canGiveManagerFund` | **Y** (pre-existing) | `can_give_manager_fund(user, project)` already took a project |
| `POST .../distribute` (manager fund) | `canDistributeManagerFund` | **Y** (pre-existing) | `can_distribute_manager_fund(user, project, manager)` already took a project |
| `GET /api/manager-funds/`, `/manager-labour-distributions/` | `canViewManagerFund` | **Partial** | the role-level gate (`can_view_manager_fund`, any-scope) is a coarse pre-check; the actual rows returned are filtered by `_scope()` (real project/manager membership) — same "WHAT here, WHERE there" split as before this pass, not rewritten |
| `GET/PATCH/DELETE /api/users/{id}/reset-password/` | `canResetUserPassword` | **Partial** | `can_reset_password()` already does its own project-relevant membership check (via `ProjectManager`) below the any-scope gate; not a single project so not converted to `has_perm(..., project)` |
| `POST /api/users/` (create global account) | `canManageUsers` | **N/A** | genuinely global — no project applies |
| `GET/POST /api/suppliers/`, `/api/contractors/`, `/api/labour/` | *(none — role-gated only)* | **N/A** | these are global master-data lists (no `project` FK on `Supplier`/`Contractor`/`Labour` at all); "project-aware" isn't a meaningful concept here |
| `GET/POST /api/project-labour/`, `/api/contractor-contracts/` | *(none — role + ownership-gated only)* | **N** | real project context exists (`ProjectLabour`/`ContractorContract` have a `project` FK) but no permission code (`canManageLabour`/`canManageContractors`) is checked at all yet — pre-existing gap, out of this pass's scope (no failing test required it) |
| `GET /api/projects/` (list) | *(none — ownership-gated only)* | **N** | pre-existing: managers never call this endpoint (the web app uses `/me/` + `/projects/{id}/people/` for them instead, see `web/app.js managerProjects()`) |

## 3. The old Roles & Permissions screen (`people.PermissionMatrixView`)

Confirmed by reading it: its `PUT` only ever sets the deprecated string columns
(`RolePermission.objects.update_or_create(role=role, permission=permission, defaults={'allowed': allowed})`).
It was **not** changed — instead, `core/access/sync.py`'s `sync_role_permission_fks` (a `pre_save`
signal on `RolePermission`, added in the first pass) fills `role_fk`/`resource_permission` from those
same strings before every save, for every writer (this screen, Django admin, `core/tests.py`
fixtures, the new `/api/access/role-permissions/` screen). One signal, every writer covered, instead of
fixing each call site individually. `core/test_rbac_v2.py::OldRolesScreenTests` exercises this against
the real `/api/permission-matrix/` endpoint and asserts `services.has_perm` changes afterward.

## 4. Frontend: per-project evaluation

`/me/` now includes `permissions_by_project`: the full output of `access.services.effective_matrix()`
(every permission code, keyed by project id or `'GLOBAL'`) reduced to plain booleans. `web/authz.js`'s
`can(user, projectId, perm)` consults this per-project map first for a real (non-demo) logged-in user,
falling back to the old role-only matrix only before `/me/` has loaded, or for the client-only demo
users (who have no server-computed matrix to reflect, since they're a local simulation layered on top
of whichever real account is actually logged in). This is what makes a per-project `AccessOverride`
(e.g. Manoj's `canEditExpense` blocked on one project only) hide the Edit button on that one project
without hiding it everywhere else.

## 5. Deploy safety

`core/checks.py` (`core.E001`/`core.E002`, registered in `CoreConfig.ready()`) fails `manage.py check`
(and therefore `migrate`/`runserver`, which run checks first) if the `ResourcePermission` catalogue is
empty, or if no `RolePermission` row grants anything — both states are otherwise silently
indistinguishable from "everyone is correctly locked out" under the fail-closed engine. It swallows any
database error so it never breaks `makemigrations`/`shell`/a first-ever `migrate` before the RBAC
tables exist.

**Deploy order** (also in `README.md`):

```
1. python manage.py migrate                 # applies 0006/0007/0008 (schema + seed + NOT NULL)
2. python manage.py seed_rbac               # idempotent; catches up the catalogue if access_catalog.py grew
3. release / restart app servers
```
`manage.py check` (run automatically by `migrate`/`runserver`) will refuse to proceed past step 1 with
a clear error if the catalogue ends up empty for any reason.

## 6. `seed_rbac --demo`

`python manage.py seed_rbac --demo` (refuses unless `DEBUG=True` or `--force`, since it creates
accounts with a well-known password) creates the standard walkthrough scenario: Projects "Site A"/
"Site B"; Renu (SUPER_ADMIN, GLOBAL); Parveen (OWNER, Site A only); Anil (OWNER, Site B only); Manoj
(MANAGER on both, with `canEditExpense` DENIED on Site B specifically) — plus one sample labour and
expense per site so the screens aren't empty. Idempotent (`get_or_create` throughout). Covered by
`core/test_rbac_v2.py::SeedRbacDemoTests`, which checks the scenario against the real API (project
visibility, the edit-blocked-on-one-project behaviour, add-on-both-projects, and that only Renu can
reach `/api/access/...`).
