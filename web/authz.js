/* UI authorization (Phase 1). The single place that knows about roles.
   The rest of the app only asks:  can('canAddExpense')  /  Authz.navFor(can).

   This hides things in the UI only. It does NOT protect the API: Phase 2 enforces the same
   permission names on the server (map each `canXxx` below to a backend permission).

   Roles:  SUPER_ADMIN -> every project (server role ADMIN)
           OWNER / MANAGER / VIEWER -> only the projects they are assigned to */
(function () {
  'use strict';

  const R = { SUPER_ADMIN: 'super_admin', OWNER: 'owner', MANAGER: 'manager', VIEWER: 'viewer' };
  const ROLE_LABEL = { super_admin: 'SUPER ADMIN', owner: 'OWNER', manager: 'MANAGER', viewer: 'VIEWER' };
  const ROLE_LIST = [R.SUPER_ADMIN, R.OWNER, R.MANAGER, R.VIEWER];
  const RANK = { super_admin: 4, owner: 3, manager: 2, viewer: 1 };

  // ---------- THE permission definition (single source of truth) ----------
  // One entry per permission: which roles have it BY DEFAULT, and which roles are locked (`fixed`).
  // A locked role cannot be edited on the Role & Permissions screen (it protects against lock-out).
  // The screen, can(), the menu and every button all read the same current matrix built from this list.
  const ALL = ROLE_LIST;
  const OPS = [R.SUPER_ADMIN, R.OWNER, R.MANAGER];   // day-to-day entry
  const OWN = [R.SUPER_ADMIN, R.OWNER];              // management of a project
  const ADM = [R.SUPER_ADMIN];                       // application level
  const NOT_MANAGER = [R.SUPER_ADMIN, R.OWNER, R.VIEWER];
  const SA_ON = { super_admin: true };
  const GROUPS = [
    { id: 'view',     label: 'View / Dekhna' },
    { id: 'ops',      label: 'Daily Operations / Data Entry' },
    { id: 'project',  label: 'Project Management' },
    { id: 'app',      label: 'Application Management' },
    { id: 'personal', label: 'Personal' },
  ];
  const DEFINITIONS = [
    { key: 'canViewProjects',      group: 'view', label: 'View Projects',    roles: NOT_MANAGER, fixed: SA_ON },
    { key: 'canViewExpenses',      group: 'view', label: 'View Expenses',    roles: NOT_MANAGER },
    { key: 'canViewLabour',        group: 'view', label: 'View Labour',      roles: ALL },
    { key: 'canViewReports',       group: 'view', label: 'View Reports',     roles: ALL },
    { key: 'canViewSuppliers',     group: 'view', label: 'View Suppliers',   roles: OWN },
    { key: 'canViewContractors',   group: 'view', label: 'View Contractors', roles: OWN },
    { key: 'canViewManagerFund',   group: 'view', label: 'View Manager Fund', roles: OPS },

    { key: 'canAddExpense',          group: 'ops', label: 'Add Expense',            roles: OPS },
    { key: 'canEditExpense',         group: 'ops', label: 'Edit Expense',           roles: OPS },
    { key: 'canDeleteExpense',       group: 'ops', label: 'Delete Expense',         roles: OWN },
    { key: 'canManageLabour',        group: 'ops', label: 'Manage Labour',          roles: OPS },
    { key: 'canRecordLabourPayment', group: 'ops', label: 'Labour Payment',         roles: OPS },
    { key: 'canManageSuppliers',     group: 'ops', label: 'Manage Suppliers',       roles: OWN },
    { key: 'canManageContractors',   group: 'ops', label: 'Manage Contractors',     roles: OWN },
    { key: 'canGiveManagerFund',     group: 'ops', label: 'Give Manager Fund',      roles: OWN },
    { key: 'canDistributeManagerFund', group: 'ops', label: 'Distribute Manager Fund', roles: [R.SUPER_ADMIN, R.MANAGER] },

    { key: 'canManageProjectMembers',  group: 'project', label: 'Manage Project Members', roles: OWN },
    { key: 'canManageProjectSettings', group: 'project', label: 'Manage Project Settings', roles: OWN },
    { key: 'canResetUserPassword',     group: 'project', label: 'Reset User Password',    roles: OWN },

    { key: 'canCreateProject',              group: 'app', label: 'Create Project',        roles: ADM, fixed: SA_ON },
    { key: 'canManageUsers',                group: 'app', label: 'Manage Users',          roles: ADM, fixed: SA_ON },
    { key: 'canManageApplicationSettings',  group: 'app', label: 'Application Settings',  roles: ADM, fixed: SA_ON },
    // Only SUPER_ADMIN can ever open the Role & Permissions screen; nobody else can be given it.
    { key: 'canManagePermissions',          group: 'app', label: 'Role & Permissions',    roles: ADM,
      fixed: { super_admin: true, owner: false, manager: false, viewer: false } },

    // Everyone logged in can change their own password (even with no project), so it is not editable.
    { key: 'canChangeOwnPassword', group: 'personal', label: 'Change Own Password', roles: ALL, anyUser: true,
      fixed: { super_admin: true, owner: true, manager: true, viewer: true } },
  ];
  const DEF = Object.fromEntries(DEFINITIONS.map(d => [d.key, d]));
  const isFixed = (key, role) => !!(DEF[key] && DEF[key].fixed && role in DEF[key].fixed);

  // ---------- the matrix: { permission: { role: true|false } } ----------
  const clone = m => JSON.parse(JSON.stringify(m));
  function normalize(src) {                      // unknown / missing / non-boolean -> default; locked -> forced
    const out = {};
    DEFINITIONS.forEach(d => {
      out[d.key] = {};
      ROLE_LIST.forEach(r => {
        const saved = src && src[d.key] && src[d.key][r];
        out[d.key][r] = isFixed(d.key, r) ? d.fixed[r] : typeof saved === 'boolean' ? saved : d.roles.includes(r);
      });
    });
    return out;
  }
  const DEFAULT = normalize(null);
  // Phase 1 storage: this browser only (localStorage). Phase 2 replaces load/save with the server.
  const STORE_KEY = 'authz.permissionMatrix.v1';
  function readSaved() { try { return JSON.parse(localStorage.getItem(STORE_KEY)); } catch (e) { return null; } }
  let current = normalize(readSaved());
  window.addEventListener('storage', e => { if (e.key === STORE_KEY) current = normalize(readSaved()); });

  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const getMatrix = () => clone(current);
  const defaultMatrix = () => clone(DEFAULT);
  const isDefault = m => same(normalize(m || current), DEFAULT);
  // Applies immediately (can() reads `current`). Returns false if the browser refused to store it.
  function saveMatrix(m) {
    current = normalize(m);
    try {
      if (same(current, DEFAULT)) localStorage.removeItem(STORE_KEY); else localStorage.setItem(STORE_KEY, JSON.stringify(current));
      return true;
    } catch (e) { return false; }
  }
  const resetMatrix = () => saveMatrix(DEFAULT);

  // ---------- which screen needs which permission (array = any of; no entry = any logged-in user) ----------
  const SETTINGS_ANY = ['canManageProjectSettings', 'canManageApplicationSettings', 'canManagePermissions'];
  const REPORT_PERMISSION = { LABOUR: 'canViewLabour', SUPPLIER: 'canViewSuppliers', CONTRACTOR: 'canViewContractors', MISCELLANEOUS: 'canViewExpenses' };
  const ROUTE_PERMISSION = {
    add: canEnterAny, done: canEnterAny,
    list: 'canViewExpenses',
    reports: 'canViewReports',
    project: 'canViewProjects', newproject: 'canCreateProject',
    users: 'canManageUsers', members: 'canManageProjectMembers', settings: SETTINGS_ANY,
    permissions: 'canManagePermissions', resetpw: 'canResetUserPassword', profile: 'canChangeOwnPassword',
    fund: 'canViewManagerFund', givefund: 'canGiveManagerFund', distribute: 'canDistributeManagerFund',
  };
  const reportPermission = key => REPORT_PERMISSION[key] || 'canViewReports';
  const CATEGORIES = ['LABOUR', 'CONTRACTOR', 'SUPPLIER', 'MISCELLANEOUS'];
  // Expense categories: a category is visible only if its view permission is on,
  // and you can enter one only if you may also add it. Used by Add Expense, the Expenses list and Reports.
  const canViewCategory = (can, key) => can(reportPermission(key));
  // The Add screen (expense / labour-payment entry) is available only if some category can be entered.
  function canEnterAny(can) { return CATEGORIES.some(k => canAddCategory(can, k)); }
  const canAddCategory = (can, key) => canViewCategory(can, key) && can(key === 'LABOUR' ? 'canRecordLabourPayment' : 'canAddExpense');
  // Menu. `primary` items sit in the bottom bar; the rest go under "Menu".
  const NAV = [
    { id: 'home',        hash: '#/home',          icon: '🏠', hi: 'होम',       en: 'Dashboard',   perm: null, primary: true },
    { id: 'add',         hash: '#/add',           icon: '➕', hi: 'खर्च डालें', en: 'Add Expense', perm: canEnterAny, primary: true },
    { id: 'list',        hash: '#/list',          icon: '📋', hi: 'खर्च देखें', en: 'Expenses',    perm: 'canViewExpenses', primary: true },
    { id: 'reports',     hash: '#/reports',       icon: '📊', hi: 'हिसाब',     en: 'Reports',     perm: 'canViewReports', primary: true },
    { id: 'project',     hash: '#/project',       icon: '🏗️', hi: 'प्रोजेक्ट',  en: 'Projects',    perm: 'canViewProjects' },
    { id: 'labour',      hash: '#/report/LABOUR', icon: '👷', hi: 'मज़दूर',    en: 'Labour',      perm: 'canViewLabour' },
    { id: 'suppliers',   hash: '#/report/SUPPLIER', icon: '🚚', hi: 'सप्लायर', en: 'Suppliers',   perm: 'canViewSuppliers' },
    { id: 'contractors', hash: '#/report/CONTRACTOR', icon: '🧱', hi: 'ठेकेदार', en: 'Contractors', perm: 'canViewContractors' },
    { id: 'fund',        hash: '#/fund',          icon: '💰', hi: 'फंड',       en: 'Manager Fund', perm: 'canViewManagerFund' },
    { id: 'users',       hash: '#/users',         icon: '👥', hi: 'यूज़र',      en: 'Users',       perm: 'canManageUsers' },
    { id: 'members',     hash: '#/members',       icon: '🤝', hi: 'सदस्य',      en: 'Members',     perm: 'canManageProjectMembers' },
    { id: 'settings',    hash: '#/settings',      icon: '⚙️', hi: 'सेटिंग',     en: 'Settings',    perm: SETTINGS_ANY },
  ];

  // ---------- test users (UI only; no real login behind them) ----------
  // `project` is a position in the real project list (0 = first). SUPER_ADMIN needs no assignment.
  const DEMO_USERS = [
    { id: 'superadmin', email: 'superadmin@test.com', name: 'Renu Mittal',  role: R.SUPER_ADMIN, assign: [] },
    { id: 'owner',      email: 'owner@test.com',      name: 'Amit Sharma',  role: R.OWNER,   assign: [{ project: 0, role: R.OWNER }] },
    { id: 'manager',    email: 'manager@test.com',    name: 'Raj Kumar',    role: R.MANAGER, assign: [{ project: 0, role: R.MANAGER }] },
    { id: 'viewer',     email: 'viewer@test.com',     name: 'Neha Verma',   role: R.VIEWER,  assign: [{ project: 0, role: R.VIEWER }] },
    { id: 'mixed',      email: 'mixed@test.com',      name: 'Sunil Gupta',  role: null,      assign: [{ project: 0, role: R.MANAGER }, { project: 1, role: R.VIEWER }] },
    { id: 'none',       email: 'noproject@test.com',  name: 'Pooja Singh',  role: null,      assign: [] },
  ];
  const demoUser = id => DEMO_USERS.find(u => u.id === id) || null;

  // What the real API calls a user (core_profile.role). Phase 2 replaces this with per-project roles.
  const SERVER_ROLE = { ADMIN: R.SUPER_ADMIN, OWNER: R.OWNER, MANAGER: R.MANAGER };

  const highestRole = list => list.reduce((best, a) => (!best || RANK[a.role] > RANK[best] ? a.role : best), null);

  // [{ projectId, role }] for a person; SUPER_ADMIN gets every project.
  function assignments(role, assign, projects) {
    const byId = projects.slice().sort((a, b) => a.id - b.id);
    if (role === R.SUPER_ADMIN) return byId.map(p => ({ projectId: p.id, role: R.SUPER_ADMIN }));
    return assign.flatMap(a => byId.slice(a.project, a.project + 1).map(p => ({ projectId: p.id, role: a.role })));
  }

  /* currentUser = { id, name, email, role, assignedProjects: [{ projectId, role }], allProjects, demo } */
  function buildUser({ me, projects, demo }) {
    if (demo) {
      const list = assignments(demo.role, demo.assign, projects);
      return { id: demo.id, name: demo.name, email: demo.email, role: demo.role || highestRole(list),
        assignedProjects: list, allProjects: demo.role === R.SUPER_ADMIN, demo: true };
    }
    const role = SERVER_ROLE[me.role] || null;      // no role -> no access (fail closed)
    // The API already returns only this person's projects (all of them for ADMIN).
    const list = role ? projects.map(p => ({ projectId: p.id, role })) : [];
    return { id: me.owner_id || me.username, name: me.name, email: me.username, role,
      assignedProjects: list, allProjects: role === R.SUPER_ADMIN, demo: false };
  }

  // Everyone the Users / Members screens can list (test users only, until Phase 2 adds a users API).
  const directory = projects => DEMO_USERS.map(u => {
    const list = assignments(u.role, u.assign, projects);
    return { id: u.id, name: u.name, email: u.email, role: u.role || highestRole(list), assignedProjects: list, allProjects: u.role === R.SUPER_ADMIN };
  });

  // A user's role in one project (null when not assigned).
  function roleIn(user, projectId) {
    const a = user && user.assignedProjects.find(x => x.projectId === projectId);
    return a ? a.role : null;
  }

  // Unknown permission or no role -> false (fail closed). Reads the current matrix.
  // Role permission only says WHAT a role may do; WHICH projects is decided by assignedProjects.
  function can(user, projectId, perm) {
    const def = DEF[perm];
    if (!user || !def) return false;
    if (def.anyUser) return true;
    if (user.role === R.SUPER_ADMIN) return current[perm][R.SUPER_ADMIN];   // not tied to a project
    const role = roleIn(user, projectId);
    return !!role && current[perm][role];
  }

  const allows = (perm, can) => (typeof perm === 'function' ? perm(can) : [].concat(perm).some(can));   // one permission, any-of list, or a rule
  const navFor = can => NAV.filter(n => !n.perm || allows(n.perm, can));
  const routePermission = (page, arg) => (page === 'report' ? reportPermission(arg) : ROUTE_PERMISSION[page] || null);

  window.Authz = { ROLES: R, ROLE_LIST, ROLE_LABEL, GROUPS, DEFINITIONS, DEMO_USERS, demoUser, buildUser, directory, roleIn, can, navFor,
    routePermission, allows, reportPermission, canViewCategory, canAddCategory, getMatrix, defaultMatrix, saveMatrix, resetMatrix, isFixed, isDefault };
})();
