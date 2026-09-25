/* Simple expense diary. Plain JS, talks to the existing /api/ endpoints.
   Technical errors go to the console only; users only see simple messages. */
(function () {
  'use strict';

  // Shown in the user menu, and bumped whenever the app ships a user-visible change --
  // bump web/sw.js's CACHE version in the same commit so the install and the label agree.
  const APP_VERSION = 'v12';

  // ---------- words the user sees ----------
  // L(hi, en) shows only ONE language at a time, picked by the current language switcher --
  // never both together (see web/i18n.js for how the switcher's choice is stored).
  const L = (hi, en) => (I18n.getLang() === 'hi' ? hi : en);
  const CATS = [
    { key: 'LABOUR',        icon: '👷', hi: 'मज़दूर',   en: 'Labour',     type: 'Labour Payment',     party: 'LABOUR' },
    { key: 'CONTRACTOR',    icon: '🏗️', hi: 'ठेकेदार',  en: 'Contractor', type: 'Contractor Payment', party: 'CONTRACTOR' },
    { key: 'SUPPLIER',      icon: '🚚', hi: 'सप्लायर',  en: 'Supplier',   type: 'Supplier Payment',   party: 'SUPPLIER' },
    { key: 'MISCELLANEOUS', icon: '📦', hi: 'दूसरा खर्च', en: 'Other',      type: 'Other',              party: 'NONE' },
  ];
  const CAT = Object.fromEntries(CATS.map(c => [c.key, c]));
  // Add Expense tab query param (#/add?tab=labour|other|supplier|contractor) <-> expense category.
  // "other" (not "miscellaneous") is the tab name because that's what the category is called
  // everywhere else user-facing (see CATS' MISCELLANEOUS label above).
  const TAB_TO_CAT = { labour: 'LABOUR', other: 'MISCELLANEOUS', supplier: 'SUPPLIER', contractor: 'CONTRACTOR' };
  const CAT_TO_TAB = { LABOUR: 'labour', MISCELLANEOUS: 'other', SUPPLIER: 'supplier', CONTRACTOR: 'contractor' };
  const catLabel = c => L(c.hi, c.en);
  const MODES = [
    { key: 'CASH', hi: 'नकद', en: 'Cash' },
    { key: 'UPI', hi: 'UPI', en: 'UPI' },
    { key: 'BANK_TRANSFER', hi: 'बैंक', en: 'Bank' },
    { key: 'CHEQUE', hi: 'चेक', en: 'Cheque' },
  ];
  const modeLabel = m => L(m.hi, m.en);
  const SUPPLIER_TYPES = ['Electrical Material', 'Plumbing Material', 'Building Material', 'Saria / Steel',
    'Chokhat / Door', 'Wood / Timber', 'Paint / Hardware', 'Other'];
  const STATUS_KEY = { PLANNED: 'statusPlanned', ONGOING: 'statusOngoing', COMPLETED: 'statusCompleted', ARCHIVED: 'statusArchived' };
  const statusLabel = s => I18n.t(STATUS_KEY[s] || s);
  const MONTHS_HI = ['जनवरी', 'फ़रवरी', 'मार्च', 'अप्रैल', 'मई', 'जून', 'जुलाई', 'अगस्त', 'सितंबर', 'अक्तूबर', 'नवंबर', 'दिसंबर'];
  const MONTHS_EN_FULL = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const MSG = {
    get problem() { return L('कुछ समस्या हुई। कृपया दोबारा प्रयास करें.', 'Something went wrong. Please try again.'); },
    get network() { return L('इंटरनेट नहीं चल रहा है। कृपया इंटरनेट देखकर दोबारा प्रयास करें.', 'No internet connection. Please check and try again.'); },
    get login() { return L('नाम या पासवर्ड गलत है। कृपया दोबारा भरें.', 'Incorrect username or password. Please try again.'); },
    get saved() { return L('खर्च सफलतापूर्वक सेव हो गया.', 'Expense saved successfully.'); },
    // 403 messages: kept per-screen so a denial on one screen (e.g. Add Expense) is never shown
    // while the user is looking at an unrelated screen (e.g. Dashboard) -- see friendly() below.
    get forbidden() { return L('आपको इसकी अनुमति नहीं है.', 'You do not have permission for this.'); },
    get noPermissionAdd() { return L('इस प्रोजेक्ट पर खर्च डालने की अनुमति नहीं है.', 'You do not have permission to add expenses on this project.'); },
    get noPermissionView() { return L('इस जानकारी को देखने की अनुमति नहीं है.', 'You do not have permission to view this.'); },
  };

  // ---------- small helpers ----------
  // API address comes from config.js; empty means "same server" (local development).
  let API = (window.APP_CONFIG && window.APP_CONFIG.apiBase) || '../api/';
  if (!API.endsWith('/')) API += '/';
  if (!(window.APP_CONFIG && window.APP_CONFIG.apiBase) && /github\.io$/.test(location.hostname)) {
    console.error('web/config.js: apiBase is empty. Set it to the hosted Django API address.');
  }
  const $view = document.getElementById('view');
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = n => '₹ ' + Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: ignore */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };
  // The business runs on IST wall-clock dates regardless of the device's own timezone (week/month
  // boundaries, "today" defaults). IST has no DST, so a fixed +5:30 offset from the UTC instant is
  // exact; reading it back with getUTC*() (not local getters) avoids the browser re-applying its
  // own timezone on top.
  const isoUTC = d => `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
  const istNow = () => new Date(Date.now() + 330 * 60000);
  const today = () => isoUTC(istNow());
  const yesterday = () => { const d = istNow(); d.setUTCDate(d.getUTCDate() - 1); return isoUTC(d); };
  const EN_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const shortDate = s => { const [y, m, d] = String(s).split('-').map(Number); return y ? `${String(d).padStart(2, '0')}-${EN_MONTHS[m - 1]}-${y}` : ''; };
  const niceDate = s => { const [y, m, d] = String(s).split('-').map(Number); return y ? `${d} ${L(MONTHS_HI[m - 1], MONTHS_EN_FULL[m - 1])} ${y}` : ''; };

  const state = { token: store.get('token'), me: null, user: null, realProjects: [], projects: [], project: null, names: null, demoId: store.get('demoUser'), fundForbidden: false };
  // Set once a new service worker has installed alongside a still-running old one (see the
  // registration code near the bottom): shows the reload banner until the user taps it.
  let updateAvailable = false;
  // Closes the top-right user menu, if open; set by renderUserbar() on each render and invoked by
  // the one shared document/hashchange listener below (registered once, not per-render).
  let userMenuCloser = null;
  const DEMO = !!(window.APP_CONFIG && window.APP_CONFIG.demoRoles);
  // The one permission check used everywhere: can('canAddSupplierExpense'). Role names live only in authz.js.
  // The server decides these (it returns them in /me/); the local matrix can only hide more, never grant more.
  const SERVER_PERMS = ['canViewManagerFund', 'canGiveManagerFund', 'canDistributeManagerFund', 'canUploadBill', 'canViewBill', 'canViewProjectFunds'];
  const serverAllows = perm => {
    const me = state.me;
    if (!me || !me.permissions) return true;                                  // an older server: nothing to check
    if (state.project && me.project_permissions) return (me.project_permissions[String(state.project.id)] || []).includes(perm);
    return !!me.permissions[perm];
  };
  const can = perm => Authz.can(state.user, state.project && state.project.id, perm) && (!SERVER_PERMS.includes(perm) || serverAllows(perm));
  // Permissions only the server decides (not in the local matrix, so localStorage can never grant them).
  // A server that does not send permissions -> hidden.
  const serverOnly = perm => !!(state.me && state.me.permissions) && serverAllows(perm);
  const canAny = perms => Authz.allows(perms, can);               // a screen may need one permission, any of several, or a rule
  const canAdd = () => canAny(Authz.routePermission('add'));     // Add Expense screen (expense or labour payment)
  const noProject = () => can('canCreateProject')
    ? `<div class="empty"><div class="ico">🏠</div><h2>${L('अभी कोई प्रोजेक्ट नहीं है', 'No projects exist yet')}</h2></div><a class="btn green" href="#/settings/new">➕ ${L('नया प्रोजेक्ट', 'New Project')}</a>`
    : `<div class="empty"><div class="ico">🏠</div><h2>${L('आपको अभी कोई प्रोजेक्ट नहीं दिया गया है', 'No projects have been assigned to you yet')}</h2><p>${L('कृपया एडमिन से संपर्क करें.', 'Please contact the administrator.')}</p></div>`;

  class AppError extends Error {
    constructor(kind, status, data) { super(kind); this.kind = kind; this.status = status; this.data = data; }
  }

  async function api(path, { method = 'GET', body } = {}) {
    const headers = { Accept: 'application/json' };
    if (state.token) headers.Authorization = 'Token ' + state.token;
    if (body) headers['Content-Type'] = 'application/json';
    let res;
    try {
      res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    } catch (e) {
      console.error('network error', path, e);
      throw new AppError('network');
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body is fine */ }
    if (res.status === 401 && path !== 'auth/login/') {
      logoutLocal();
      location.hash = '#/login';
      throw new AppError('auth', 401);
    }
    if (!res.ok) {
      console.error('server error', method, path, res.status, data);
      throw new AppError('server', res.status, data);
    }
    return data;
  }

  // Plain validation messages the server sends for a rejected request (never technical detail).
  function serverMsg(e) {
    if (!e || e.status !== 400 || !e.data) return '';
    const out = [];
    (function walk(v) {
      if (typeof v === 'string') { if (!out.includes(v)) out.push(v); }
      else if (Array.isArray(v)) v.forEach(walk);
      else if (v && typeof v === 'object') Object.values(v).forEach(walk);
    })(e.data);
    return out.slice(0, 3).join(' ');
  }
  // `forbiddenMsg` is the 403 text for this call site specifically (e.g. MSG.noPermissionView on a
  // view-only screen, MSG.noPermissionAdd on the Add Expense submit) -- defaults to a generic denial
  // so a screen that has no reason to mention "add expense" never shows that wording.
  const friendly = (e, forbiddenMsg) => (e && e.kind === 'network' ? MSG.network : e && e.status === 403 ? (forbiddenMsg || MSG.forbidden) : MSG.problem);
  const errBox = text => `<div class="msg error" role="alert">${esc(text)}</div>`;
  const loading = () => { $view.innerHTML = `<div class="spinner">⏳ ${L('रुकिए...', 'Loading...')}</div>`; };

  function logoutLocal() {
    store.del('token'); store.del('projectId'); store.del('demoUser');
    Object.assign(state, { token: null, me: null, user: null, realProjects: [], projects: [], project: null, names: null, demoId: null, fundForbidden: false });
  }

  // ---------- data loading ----------
  async function loadBasics() {
    if (state.me) return;
    const me = await api('me/');
    const projects = me.owner_id || me.is_super_admin ? await api('projects/') : me.role === 'MANAGER' ? await managerProjects(me) : [];
    state.me = me;
    Authz.setMatrix(me.permission_matrix);
    Authz.setEffectiveMatrix(me.permissions_by_project);
    state.realProjects = projects.results || projects;
    applyUser();
  }

  // A manager cannot list projects (owner-only API): /me/ says which projects they are on, and the people
  // endpoint (open to project members) gives each one's name. Older servers: use the projects their fund shows.
  async function managerProjects(me) {
    if (me.project_permissions) {
      return Promise.all(Object.keys(me.project_permissions).map(async id => {
        const p = (await api(`projects/${id}/people/`)).project;
        return { id: p.id, name: p.name, code: p.code, status: p.status };
      }));
    }
    try {
      const seen = new Map();
      ((await api('manager-funds/summary/')).summary || []).forEach(r =>
        seen.set(r.project_id, { id: r.project_id, name: r.project_code, code: r.project_code }));
      return [...seen.values()];
    } catch (e) {
      if (e.status !== 403) throw e;
      state.fundForbidden = true;
      return [];
    }
  }

  // Build the current user and keep only the projects assigned to them.
  function applyUser() {
    const demo = DEMO ? Authz.demoUser(state.demoId) : null;
    state.user = Authz.buildUser({ me: state.me, projects: state.realProjects, demo });
    const mine = new Set(state.user.assignedProjects.map(a => a.projectId));
    // An archived project is closed: no menu should offer to add/view its data day-to-day.
    // (Super admin still manages it -- editing status, viewing its members -- from Settings,
    // which fetches its own project list and never goes through state.projects.)
    state.projects = state.realProjects.filter(p => mine.has(p.id) && p.status !== 'ARCHIVED');
    const saved = Number(store.get('projectId'));
    state.project = state.projects.find(p => p.id === saved) || state.projects[0] || null;
    state.names = null;
  }

  // ---------- profile bar: name/role + a top-right user menu (language, password, logout) ----------
  function renderUserbar(show) {
    const bar = document.getElementById('userbar');
    bar.hidden = !(show && state.user);
    if (bar.hidden) { userMenuCloser = null; return; }
    const role = (state.project && Authz.roleIn(state.user, state.project.id)) || state.user.role;
    const lang = I18n.getLang();
    bar.innerHTML = `
      <div class="who"><b>${esc(state.user.name)}</b><span class="role">${role ? esc(Authz.ROLE_LABEL[role]) : 'NO ROLE'}</span></div>
      ${DEMO ? `<label class="demo">${esc(I18n.t('testAs'))}
        <select id="demoSel" aria-label="Test user"><option value="">${esc(I18n.t('realLogin'))}</option>${Authz.DEMO_USERS.map(u => `<option value="${u.id}" ${u.id === state.demoId ? 'selected' : ''}>${esc(u.email)}</option>`).join('')}</select></label>` : ''}
      <div class="user-menu">
        <button type="button" class="user-chip" id="userMenuBtn" aria-haspopup="true" aria-expanded="false" aria-label="${esc(I18n.t('account'))}: ${esc(state.user.name)}">
          <span class="avatar">${esc(initials(state.user.name))}</span>
        </button>
        <div class="user-menu-panel" id="userMenuPanel" hidden role="menu">
          <div class="user-menu-section">
            <span class="user-menu-label">${esc(I18n.t('language'))}</span>
            ${I18n.LANGS.map(l => `<button type="button" class="user-menu-lang" role="menuitemradio" aria-checked="${l.code === lang}" data-lang="${l.code}">${l.code === lang ? '✓ ' : ''}${esc(l.label)}</button>`).join('')}
          </div>
          <div class="user-menu-divider"></div>
          ${can('canChangeOwnPassword') ? `<a class="user-menu-item" role="menuitem" href="#/profile">🔑 ${esc(I18n.t('changePassword'))}</a>` : ''}
          ${can('canManagePermissions') ? `<a class="user-menu-item" role="menuitem" href="#/settings">⚙️ ${esc(I18n.t('settings'))}</a>` : ''}
          <button type="button" class="user-menu-item" role="menuitem" id="logoutBtn">🚪 ${esc(I18n.t('logout'))}</button>
          <button type="button" class="user-menu-item" role="menuitem" id="hardRefreshBtn">🔄 ${L('पूरा रीफ्रेश करें', 'Hard refresh')}</button>
          <div class="user-menu-divider"></div>
          <div class="user-menu-version">Expense Tracker ${APP_VERSION}</div>
        </div>
      </div>`;

    const btn = document.getElementById('userMenuBtn'), panel = document.getElementById('userMenuPanel');
    const closeMenu = () => { panel.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
    btn.onclick = () => {
      const opening = panel.hidden;
      panel.hidden = !opening;
      btn.setAttribute('aria-expanded', String(opening));
    };
    // One shared listener handles every render (see closeUserMenuOnOutsideClick below) --
    // renderUserbar runs on every screen change, so adding a fresh document/window listener here
    // each time would stack forever instead of replacing the previous one.
    userMenuCloser = closeMenu;
    panel.querySelectorAll('.user-menu-lang').forEach(b => b.onclick = () => {
      I18n.setLang(b.dataset.lang);
      closeMenu();
      route();   // re-render the current screen (and chrome) in the new language
    });
    document.getElementById('logoutBtn').onclick = doLogout;
    document.getElementById('hardRefreshBtn').onclick = hardRefresh;

    const sel = document.getElementById('demoSel');
    if (sel) sel.onchange = () => {
      state.demoId = sel.value || null;
      if (state.demoId) store.set('demoUser', state.demoId); else store.del('demoUser');
      applyUser();
      if (location.hash === '#/home' || !location.hash) route(); else location.hash = '#/home';
    };
  }

  // Names for turning ids into words (labour / suppliers / contractors of this project).
  // A manager has no Owner record of their own -- every expense still needs a real owner attached
  // (`paid_by_owner`), so a manager also needs the project's actual owners to choose from.
  async function loadNames(force) {
    if (state.names && !force && state.names.projectId === state.project.id) return state.names;
    const [labour, suppliers, contracts, people] = await Promise.all([
      api('labour/'), api('suppliers/'), api(`contractor-contracts/?project=${state.project.id}`),
      state.me.owner_id ? null : api(`projects/${state.project.id}/people/`),
    ]);
    state.names = { projectId: state.project.id, labour, suppliers, contracts, owners: people ? people.owners : null };
    return state.names;
  }

  // ---------- topbar breadcrumb ----------
  // One label per `tab` id (the same id chrome()'s callers already pass for nav-highlighting).
  // Several distinct screens intentionally share one tab id (e.g. Settings and every screen under
  // it all pass 'settings', so the bottom/Settings chrome highlights consistently) -- the breadcrumb
  // collapses those into one segment rather than guessing a false intermediate level for them.
  const TAB_LABEL = {
    home: () => L('होम', 'Dashboard'),
    add: () => L('खर्च डालें', 'Add Expense'),
    list: () => L('खर्च की लिस्ट', 'Expense List'),
    reports: () => L('कुल खर्च', 'Total Expense'),
    labour: () => L('मज़दूर', 'Labour'),
    suppliers: () => L('सप्लायर', 'Suppliers'),
    contractors: () => L('ठेकेदार', 'Contractors'),
    project: () => L('प्रोजेक्ट', 'Projects'),
    fund: () => L('मैनेजर फंड', 'Manager Fund'),
    managers: () => L('मैनेजर', 'Managers'),
    profile: () => L('पासवर्ड बदलें', 'Change Password'),
    users: () => L('यूज़र', 'Users'),
    settings: () => L('सेटिंग्स', 'Settings'),
  };
  // hash (as passed for `back`) -> the tab id that owns that hash, so the breadcrumb can show a
  // middle segment (e.g. Home > Total Expense > Labour) when `back` points at a different screen
  // than a bare Home.
  const HASH_TAB = { '#/home': 'home', '#/reports': 'reports', '#/fund': 'fund', '#/settings': 'settings', '#/users': 'users', '#/managers': 'managers' };

  // ---------- chrome (back button, breadcrumb, tabs) ----------
  function chrome(tab, back) {
    const loggedIn = !!state.token && tab !== 'login';
    $view.onclick = null;   // a screen may attach a delegated click handler
    renderUserbar(loggedIn);
    const warn = document.getElementById('permWarning');
    const showWarning = loggedIn && state.user && !state.user.demo && Authz.matrixState() === 'error';
    warn.hidden = !showWarning;
    if (showWarning) warn.textContent = L(
      'अनुमतियाँ लोड नहीं हो पाईं, कुछ बटन छिपे हो सकते हैं। कृपया पेज रीलोड करें.',
      'Permissions could not be loaded -- some buttons may be hidden. Please reload the page.');
    const updateBanner = document.getElementById('updateBanner');
    updateBanner.hidden = !updateAvailable;
    if (updateAvailable) updateBanner.textContent = L(
      '🔄 नया वर्शन उपलब्ध है. रीलोड करने के लिए यहाँ टैप करें.',
      '🔄 A new version is available. Tap here to reload.');
    const nav = document.getElementById('tabs');
    const items = loggedIn && state.user ? Authz.navFor(can) : [];
    const more = items.filter(n => !n.primary);
    // Add Expense opens the first category tab this user is actually permitted to enter, so they
    // never land on step 1's picker with nothing pre-selected when only one category applies to them.
    const navHash = n => {
      if (n.id !== 'add') return n.hash;
      const first = allowedCats()[0];
      return first ? `#/add?tab=${CAT_TO_TAB[first.key]}` : n.hash;
    };
    nav.innerHTML = items.filter(n => n.primary).map(n => `<a href="${navHash(n)}" data-tab="${n.id}"><span>${n.icon}</span>${I18n.primary(n)}</a>`).join('')
      + (more.length ? `<button type="button" id="menuBtn" data-tab="menu" aria-expanded="false"><span>☰</span>${I18n.t('menu')}</button>` : '');
    const sheet = document.getElementById('menuSheet');
    sheet.hidden = true;
    sheet.innerHTML = more.map(n => `<a href="${navHash(n)}" data-tab="${n.id}"><span>${n.icon}</span>${I18n.primary(n)}</a>`).join('');
    const menuBtn = document.getElementById('menuBtn');
    if (menuBtn) {
      menuBtn.onclick = () => { sheet.hidden = !sheet.hidden; menuBtn.setAttribute('aria-expanded', String(!sheet.hidden)); };
      menuBtn.classList.toggle('on', more.some(n => n.id === tab));
    }
    sheet.querySelectorAll('a').forEach(a => a.classList.toggle('on', a.dataset.tab === tab));
    document.getElementById('tabs').hidden = !(loggedIn && tab !== 'blocked' && items.length);
    document.body.classList.toggle('no-tabs', document.getElementById('tabs').hidden);
    const top = document.getElementById('topbar');
    top.hidden = !back;
    const crumb = document.getElementById('breadcrumb');
    if (back) {
      // Back always goes up the hierarchy (this screen's real parent), never "wherever the user
      // happened to click from" -- so it stays predictable no matter how a screen was reached.
      const backBtn = document.getElementById('backBtn');
      backBtn.setAttribute('href', back);
      backBtn.innerHTML = `← ${L('वापस', 'BACK')}`;

      const backTab = HASH_TAB[back];
      const trail = [{ hash: '#/home', label: TAB_LABEL.home() }];
      if (backTab && backTab !== 'home' && backTab !== tab) trail.push({ hash: back, label: TAB_LABEL[backTab]() });
      const current = TAB_LABEL[tab] ? TAB_LABEL[tab]() : '';
      crumb.hidden = !current;
      if (current) {
        crumb.innerHTML = trail.map(c => `<a href="${c.hash}">${esc(c.label)}</a><span class="crumb-sep">›</span>`).join('')
          + `<span class="crumb-current" aria-current="page">${esc(current)}</span>`;
      }
    } else {
      crumb.hidden = true;
    }
    document.querySelectorAll('.tabs a').forEach(a => a.classList.toggle('on', a.dataset.tab === tab));
    window.scrollTo(0, 0);
  }

  // Labour payments and other expenses are separate permissions.
  const allowedCats = () => CATS.filter(c => Authz.canAddCategory(can, c.key));
  const viewableCats = () => CATS.filter(c => Authz.canViewCategory(can, c.key));

  // ---------- screens ----------
  function screenNoAccess() {
    chrome('noaccess', '#/home');
    $view.innerHTML = `<div class="empty"><div class="ico">🔒</div><h2>${L('इस पेज की अनुमति नहीं है', 'You do not have access to this page.')}</h2></div>
      <a class="btn line" href="#/home">🏠 ${L('होम पर जाएँ', 'Home')}</a>`;
  }

  function screenLogin() {
    chrome('login');
    $view.innerHTML = `
      <h1>🙏 ${L('नमस्ते', 'Welcome')}</h1>
      <p class="muted">${L('अपना नाम और पासवर्ड भरिए.', 'Enter your username and password.')}</p>
      <div id="err"></div>
      <form id="f" novalidate>
        <div class="step"><label for="u">${L('आपका नाम', 'User name')}</label>
          <input id="u" type="text" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" enterkeyhint="next"></div>
        <div class="step"><label for="p" class="q">${L('पासवर्ड', 'Password')}</label>
          <input id="p" type="password" autocomplete="current-password" enterkeyhint="go">
          <button type="button" class="btn line" id="show" style="min-height:52px;font-size:1rem">👁 ${L('पासवर्ड दिखाएँ', 'Show password')}</button></div>
        <button class="btn green big" type="submit">✅ ${L('अंदर जाएँ', 'LOGIN')}</button>
      </form>`;
    const p = document.getElementById('p');
    document.getElementById('show').onclick = e => {
      p.type = p.type === 'password' ? 'text' : 'password';
      e.currentTarget.textContent = p.type === 'password' ? `👁 ${L('पासवर्ड दिखाएँ', 'Show password')}` : `🙈 ${L('पासवर्ड छिपाएँ', 'Hide password')}`;
    };
    document.getElementById('f').onsubmit = async ev => {
      ev.preventDefault();
      const err = document.getElementById('err');
      const username = document.getElementById('u').value.trim();
      if (!username) { err.innerHTML = errBox(L('कृपया अपना नाम भरें.', 'Please enter your username.')); return; }
      if (!p.value) { err.innerHTML = errBox(L('कृपया पासवर्ड भरें.', 'Please enter your password.')); return; }
      const btn = ev.submitter || ev.target.querySelector('button[type=submit]');
      btn.disabled = true;
      try {
        const r = await api('auth/login/', { method: 'POST', body: { username, password: p.value } });
        state.token = r.token; store.set('token', r.token);
        location.hash = '#/home';
      } catch (e) {
        btn.disabled = false;
        err.innerHTML = errBox(e.status === 400 ? MSG.login : friendly(e));
      }
    };
  }

  function screenBlocked() {
    chrome('blocked');
    $view.innerHTML = `
      <div class="empty"><div class="ico">🙏</div>
        <h1>${L('यह लॉगिन खर्च डालने के लिए नहीं है', 'This login is not for adding expenses')}</h1>
        <p>${L('कृपया मालिक वाले नाम और पासवर्ड से अंदर जाएँ.', 'Please log in with an owner username and password.')}</p></div>
      <button class="btn line" id="out">🚪 ${L('बाहर निकलें', 'LOGOUT')}</button>`;
    document.getElementById('out').onclick = doLogout;
  }

  async function doLogout() {
    if (permDirty() && !confirm('You have unsaved permission changes. Log out without saving?')) return;
    permDraft = null;
    try { await api('auth/logout/', { method: 'POST' }); } catch (e) { /* still log out on this phone */ }
    logoutLocal();
    location.hash = '#/login';
  }

  // Stays logged in (unlike doLogout): drops the installed service worker and its cached app
  // shell so the next load is guaranteed fresh, for when a stale PWA install won't update itself.
  async function hardRefresh() {
    try {
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map(r => r.unregister()));
      }
      if ('caches' in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map(k => caches.delete(k)));
      }
    } catch (e) { console.error('hard refresh', e); }
    location.reload();
  }

  // Manager Dashboard: the default landing screen for a manager with a project selected (see
  // screenHome below). Owner/admin/viewer keep the plain quick-links home screen.
  const DASH_CATS = ['LABOUR', 'MISCELLANEOUS', 'SUPPLIER', 'CONTRACTOR'];
  let mdRange = 'month', mdFrom = '', mdTo = '', mdPage = 1, mdResults = [], mdHasNext = false;
  let mdInTotal = 0, mdOutTotal = 0, mdCount = 0;

  function mdWeekRange() {
    const t = istNow(); const day = t.getUTCDay(); const diff = day === 0 ? 6 : day - 1;
    const mon = new Date(t); mon.setUTCDate(t.getUTCDate() - diff);
    return { from: isoUTC(mon), to: today() };
  }
  function mdMonthRange() {
    const t = istNow();
    return { from: `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, '0')}-01`, to: today() };
  }
  function mdComputeDates() {
    if (mdRange === 'week') return mdWeekRange();
    if (mdRange === 'custom') { const m = mdMonthRange(); return { from: mdFrom || m.from, to: mdTo || m.to }; }
    return mdMonthRange();
  }
  // One transactions-widget row (Manager Dashboard, Owner Dashboard, Manager detail). A manager
  // expense row (Owner Dashboard/Manager detail scope) carries `by_manager` -- shown in the meta line.
  const txnRowHtml = row => `
          <div class="card item"><div class="row">
            <span class="who">${row.type === 'IN' ? '📥' : '📤'} ${esc(row.description || row.category)}</span>
            <span class="amount ${row.type === 'IN' ? 'in' : 'out'}">${row.type === 'IN' ? '+' : '−'} ${money(row.amount)}</span>
          </div><div class="meta">${shortDate(row.date)} · ${esc(modeName(row.payment_mode))}${row.by_manager ? ` · ${L('द्वारा', 'by')} ${esc(row.by_manager)}` : ''}</div></div>`;

  // Bottom sheet: "Total balance with you (N projects)" + one row per project, tap to switch.
  // Reuses the same .modal/.modal-box pop-up pattern as confirmBox() elsewhere in this file.
  function projectPickerSheet(summary) {
    const box = document.createElement('div');
    box.className = 'modal';
    box.innerHTML = `<div class="modal-box" role="dialog" aria-modal="true" aria-label="${L('प्रोजेक्ट चुनिए', 'Choose a project')}">
      <h2 style="margin-top:0">${L('आपके पास कुल बैलेंस', 'Total balance with you')} (${summary.projects.length} ${L('प्रोजेक्ट', 'projects')})</h2>
      <div class="card tot-box ${Number(summary.total_balance) < 0 ? 'bad' : 'ok'}"><div class="big-total">${money(summary.total_balance)}</div></div>` +
      summary.projects.map(p => `
        <button type="button" class="card pick ${p.id === state.project.id ? 'on' : ''}" data-id="${p.id}">
          <div class="row"><b>${esc(p.name)}</b>${p.id === state.project.id ? '<span class="pill">✔</span>' : ''}</div>
          <div class="meta">${L('मिला', 'Received')} ${money(p.received)} · ${L('बाँटा', 'Distributed')} ${money(p.distributed)}</div>
          <div class="row"><span class="muted">${L('बचा हुआ', 'Balance')}</span><span class="amount">${money(p.balance)}</span></div>
        </button>`).join('') +
      `<button class="btn line" type="button" id="pp-close">${L('बंद करें', 'Close')}</button></div>`;
    document.body.appendChild(box);
    const close = () => { box.remove(); document.removeEventListener('keydown', onKey); window.removeEventListener('hashchange', close); };
    const onKey = e => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    window.addEventListener('hashchange', close);
    box.querySelector('#pp-close').onclick = close;
    box.querySelectorAll('button.pick').forEach(b => b.onclick = () => {
      const p = state.projects.find(x => x.id === Number(b.dataset.id));
      if (p) { state.project = p; store.set('projectId', p.id); state.names = null; }
      close();
      route();
    });
  }

  async function screenManagerDashboard() {
    chrome('home');
    const project = state.project;
    loading();
    let summary;
    try { summary = await api(`projects/${project.id}/manager-summary/`); }
    catch (e) { $view.innerHTML = errBox(friendly(e, MSG.noPermissionView)); return; }

    const allowedCreate = (summary.allowed_categories && summary.allowed_categories.create) || [];
    const allowedView = (summary.allowed_categories && summary.allowed_categories.view) || [];
    const cards = DASH_CATS.map(k => CAT[k]).filter(c => allowedView.includes(c.key.toLowerCase()));
    const cardHref = c => allowedCreate.includes(c.key.toLowerCase())
      ? `#/add?tab=${CAT_TO_TAB[c.key]}` : `#/list?cat=${c.key}`;
    const many = (summary.projects || []).length > 1;

    async function loadTxns(reset) {
      if (reset) { mdPage = 1; mdResults = []; }
      const { from, to } = mdComputeDates();
      const r = await api(`projects/${project.id}/transactions/?from=${from}&to=${to}&page=${mdPage}`);
      mdInTotal = r.in_total; mdOutTotal = r.out_total; mdCount = r.count;
      mdResults = reset ? r.results : mdResults.concat(r.results);
      mdHasNext = !!r.next;
    }

    try { await loadTxns(true); } catch (e) { mdResults = []; mdHasNext = false; mdInTotal = mdOutTotal = mdCount = 0; }

    function render() {
      const { from, to } = mdComputeDates();
      const bal = Number(summary.balance);
      $view.innerHTML = `
        <h1 class="md-title">🏗️ ${esc(project.name)}${many ? `<button type="button" class="pill md-badge" id="md-projects">${state.projects.findIndex(p => p.id === project.id) + 1} ${L('में से', 'of')} ${summary.projects.length}</button>` : ''}</h1>
        <p class="muted">👤 ${L('मैनेजर', 'Manager')}: <b>${esc(state.user.name)}</b></p>
        <div class="tot-grid md-fund-cards">
          <div class="card tot-box"><div class="muted">${I18n.t('fundReceivedTitle')}</div><div class="big-total">${money(summary.fund_received)}</div></div>
          <div class="card tot-box"><div class="muted">${I18n.t('totalDistributedTitle')}</div><div class="big-total">${money(summary.total_distributed)}</div></div>
          <div class="card tot-box ${bal > 0 ? 'ok' : ''} ${bal < 0 ? 'bad' : ''}"><div class="muted">${I18n.t('availableBalanceTitle')}</div><div class="big-total">${money(summary.balance)}</div></div>
        </div>
        ${cards.length ? `<h2>${L('श्रेणी अनुसार', 'By Category')}</h2><div class="md-cat-grid">` + cards.map(c => `
          <a class="card item" href="${cardHref(c)}">
            <div class="row"><span class="who">${c.icon} ${catLabel(c)}</span><span class="amount">${money((summary.category_totals || {})[c.key.toLowerCase()])}</span></div>
          </a>`).join('') + `</div>` : ''}
        <h2>🧾 ${L('लेन-देन', 'Transactions')}</h2>
        <div class="choices small" id="md-chips">
          <button type="button" class="choice" data-r="week" aria-pressed="${mdRange === 'week'}">${I18n.t('thisWeek')}</button>
          <button type="button" class="choice" data-r="month" aria-pressed="${mdRange === 'month'}">${I18n.t('thisMonth')}</button>
          <button type="button" class="choice" data-r="custom" aria-pressed="${mdRange === 'custom'}">${I18n.t('customRange')}</button>
        </div>
        ${mdRange === 'custom' ? `<div class="step" id="md-custom">
          <label for="md-from">${I18n.t('fromDate')}</label><input id="md-from" type="date" value="${mdFrom || from}">
          <label for="md-to">${I18n.t('toDate')}</label><input id="md-to" type="date" value="${mdTo || to}">
        </div>` : ''}
        <div class="fund-table" role="table">
          <div class="fund-row head" role="row"><span>${I18n.t('inTotal')}</span><span>${I18n.t('outTotal')}</span><span>${I18n.t('txnCount')}</span></div>
          <div class="fund-row"><span>${money(mdInTotal)}</span><span>${money(mdOutTotal)}</span><span>${mdCount}</span></div>
        </div>
        ${mdResults.length ? mdResults.map(txnRowHtml).join('') : `<div class="empty">${I18n.t('noTransactionsYet')}</div>`}
        ${mdHasNext ? `<button type="button" class="btn line" id="md-more">${I18n.t('loadMore')}</button>` : ''}`;

      const projBtn = document.getElementById('md-projects');
      if (projBtn) projBtn.onclick = () => projectPickerSheet(summary);
      document.getElementById('md-chips').querySelectorAll('button').forEach(b => b.onclick = async () => {
        mdRange = b.dataset.r;
        if (mdRange !== 'custom') { loading(); await loadTxns(true); render(); } else render();
      });
      const fromEl = document.getElementById('md-from'), toEl = document.getElementById('md-to');
      if (fromEl && toEl) {
        const applyCustom = async () => { mdFrom = fromEl.value; mdTo = toEl.value; loading(); await loadTxns(true); render(); };
        fromEl.onchange = applyCustom; toEl.onchange = applyCustom;
      }
      const more = document.getElementById('md-more');
      if (more) more.onclick = async () => { mdPage += 1; more.disabled = true; more.textContent = '…'; await loadTxns(false); render(); };
    }
    render();
  }

  // Owner Dashboard: the default landing screen for an owner (or admin) with canViewProjectFunds and
  // a project selected. Every figure comes straight from projects/<id>/owner-summary/ (core/ledger.py);
  // nothing here computes money.
  async function screenOwnerDashboard() {
    chrome('home');
    const project = state.project;
    loading();
    let summary;
    try { summary = await api(`projects/${project.id}/owner-summary/`); }
    catch (e) { $view.innerHTML = errBox(friendly(e, MSG.noPermissionView)); return; }

    async function loadTxns(reset) {
      if (reset) { mdPage = 1; mdResults = []; }
      const { from, to } = mdComputeDates();
      const r = await api(`projects/${project.id}/transactions/?from=${from}&to=${to}&page=${mdPage}`);
      mdInTotal = r.in_total; mdOutTotal = r.out_total; mdCount = r.count;
      mdResults = reset ? r.results : mdResults.concat(r.results);
      mdHasNext = !!r.next;
    }
    try { await loadTxns(true); } catch (e) { mdResults = []; mdHasNext = false; mdInTotal = mdOutTotal = mdCount = 0; }

    function render() {
      const { from, to } = mdComputeDates();
      const withMgr = Number(summary.with_managers);
      const catRows = CATS.map(c => ({ c, total: summary.category_totals[c.key.toLowerCase()] })).filter(x => x.total !== undefined);
      const maxVal = Math.max(1, ...catRows.map(x => Number(x.total)), Math.abs(withMgr));
      const bar = (val, cls) => `<div class="bar-track"><div class="bar-fill ${cls || ''}" style="width:${Math.min(100, Math.abs(Number(val)) / maxVal * 100)}%"></div></div>`;
      $view.innerHTML = `
        <h1>🏗️ ${esc(project.name)}</h1>
        <p class="muted">👤 ${L('मालिक', 'Owner')}: <b>${esc(state.user.name)}</b></p>
        <div class="tot-grid md-fund-cards">
          <div class="card tot-box"><div class="muted">${L('मैनेजरों को दिया', 'Given to Managers')}</div><div class="big-total">${money(summary.given)}</div></div>
          <div class="card tot-box"><div class="muted">${L('मैनेजरों ने खर्च किया', 'Spent by Managers')}</div><div class="big-total">${money(summary.spent_by_managers)}</div></div>
          <div class="card tot-box ${withMgr > 0 ? 'ok' : ''} ${withMgr < 0 ? 'bad' : ''}"><div class="muted">${L('मैनेजरों के पास बचा', 'With Managers')}</div><div class="big-total">${money(summary.with_managers)}</div></div>
        </div>
        <h2>${L('श्रेणी अनुसार खर्च', 'Spend by category')}</h2>
        <div class="cat-bars">${catRows.map(x => `
          <div class="card item cat-bar"><div class="row"><span class="who">${x.c.icon} ${catLabel(x.c)}</span><span class="amount">${money(x.total)}</span></div>${bar(x.total)}</div>`).join('')}
          <div class="cat-bar-divider"></div>
          <a class="card item cat-bar" href="#/managers"><div class="row"><span class="who">💰 ${L('मैनेजरों के पास उपलब्ध फंड', 'Fund available with Managers')}</span><span class="amount">${money(summary.with_managers)} <span class="chev">›</span></span></div>${bar(summary.with_managers, 'ok')}</a>
        </div>
        <div class="card item cat-total"><div class="row"><b>${L('कुल प्रोजेक्ट खर्च', 'Total project spend')}</b><b class="amount">${money(summary.total_project_spend)}</b></div></div>
        <h2>🧾 ${L('लेन-देन', 'Transactions')}</h2>
        <div class="choices small" id="md-chips">
          <button type="button" class="choice" data-r="week" aria-pressed="${mdRange === 'week'}">${I18n.t('thisWeek')}</button>
          <button type="button" class="choice" data-r="month" aria-pressed="${mdRange === 'month'}">${I18n.t('thisMonth')}</button>
          <button type="button" class="choice" data-r="custom" aria-pressed="${mdRange === 'custom'}">${I18n.t('customRange')}</button>
        </div>
        ${mdRange === 'custom' ? `<div class="step" id="md-custom">
          <label for="md-from">${I18n.t('fromDate')}</label><input id="md-from" type="date" value="${mdFrom || from}">
          <label for="md-to">${I18n.t('toDate')}</label><input id="md-to" type="date" value="${mdTo || to}">
        </div>` : ''}
        <div class="fund-table" role="table">
          <div class="fund-row head" role="row"><span>${I18n.t('inTotal')}</span><span>${I18n.t('outTotal')}</span><span>${I18n.t('txnCount')}</span></div>
          <div class="fund-row"><span>${money(mdInTotal)}</span><span>${money(mdOutTotal)}</span><span>${mdCount}</span></div>
        </div>
        ${mdResults.length ? mdResults.map(txnRowHtml).join('') : `<div class="empty">${I18n.t('noTransactionsYet')}</div>`}
        ${mdHasNext ? `<button type="button" class="btn line" id="md-more">${I18n.t('loadMore')}</button>` : ''}`;

      document.getElementById('md-chips').querySelectorAll('button').forEach(b => b.onclick = async () => {
        mdRange = b.dataset.r;
        if (mdRange !== 'custom') { loading(); await loadTxns(true); render(); } else render();
      });
      const fromEl = document.getElementById('md-from'), toEl = document.getElementById('md-to');
      if (fromEl && toEl) {
        const applyCustom = async () => { mdFrom = fromEl.value; mdTo = toEl.value; loading(); await loadTxns(true); render(); };
        fromEl.onchange = applyCustom; toEl.onchange = applyCustom;
      }
      const more = document.getElementById('md-more');
      if (more) more.onclick = async () => { mdPage += 1; more.disabled = true; more.textContent = '…'; await loadTxns(false); render(); };
    }
    render();
  }

  // Managers screen (#/managers): every manager on this project, given/spent/balance, tap through to
  // their read-only detail. Same 3 cards as the Owner Dashboard.
  async function screenManagers() {
    chrome('managers', '#/home');
    if (!state.project) { $view.innerHTML = noProject(); return; }
    loading();
    let summary;
    try { summary = await api(`projects/${state.project.id}/owner-summary/`); }
    catch (e) { $view.innerHTML = errBox(friendly(e, MSG.noPermissionView)); return; }
    const withMgr = Number(summary.with_managers);
    const initials2 = n => (n || '').trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase();
    $view.innerHTML = `
      <h1>🧑‍💼 ${L('मैनेजर', 'Managers')}</h1>
      <p class="muted">${L('प्रोजेक्ट', 'Project')}: <b>${esc(state.project.name)}</b></p>
      <div class="tot-grid md-fund-cards">
        <div class="card tot-box"><div class="muted">${L('मैनेजरों को दिया', 'Given to Managers')}</div><div class="big-total">${money(summary.given)}</div></div>
        <div class="card tot-box"><div class="muted">${L('मैनेजरों ने खर्च किया', 'Spent by Managers')}</div><div class="big-total">${money(summary.spent_by_managers)}</div></div>
        <div class="card tot-box ${withMgr > 0 ? 'ok' : ''} ${withMgr < 0 ? 'bad' : ''}"><div class="muted">${L('मैनेजरों के पास बचा', 'With Managers')}</div><div class="big-total">${money(summary.with_managers)}</div></div>
      </div>` +
      (summary.managers.length ? summary.managers.map(m => `
        <a class="card item mgr-row" href="#/managers/${m.id}">
          <span class="mgr-avatar">${esc(initials2(m.name))}</span>
          <span class="mgr-mid"><b>${esc(m.name)}</b><span class="meta">${L('दिया', 'Given')} ${money(m.given)} · ${L('खर्च', 'Spent')} ${money(m.spent)}</span></span>
          <span class="mgr-bal"><span class="meta">${L('बचा हुआ', 'Balance')}</span><b class="${Number(m.balance) < 0 ? 'amount-bad' : 'amount-ok'}">${money(m.balance)}</b></span>
          <span class="chev">›</span>
        </a>`).join('') : `<div class="empty">${L('इस प्रोजेक्ट में अभी कोई मैनेजर नहीं है.', 'No manager on this project yet.')}</div>`) +
      (can('canGiveManagerFund') ? `<a class="btn green" href="#/givefund">➕ ${L('मैनेजर को फंड दें', 'Give Fund to Manager')}</a>` : '');
  }

  // Manager detail (#/managers/<id>): owner's read-only view of one manager's dashboard, built from
  // the same manager-summary/transactions endpoints the Manager Dashboard itself uses (?manager_id=).
  async function screenManagerDetail(managerId) {
    chrome('managers', '#/managers');
    if (!state.project) { $view.innerHTML = noProject(); return; }
    loading();
    const project = state.project;
    let summary, funds, ownerSummary;
    try {
      [summary, funds, ownerSummary] = await Promise.all([
        api(`projects/${project.id}/manager-summary/?manager_id=${managerId}`),
        api(`manager-funds/?project=${project.id}&manager=${managerId}`),
        api(`projects/${project.id}/owner-summary/`),
      ]);
    } catch (e) { $view.innerHTML = errBox(friendly(e, MSG.noPermissionView)); return; }

    const allowedView = (summary.allowed_categories && summary.allowed_categories.view) || [];
    const cards = DASH_CATS.map(k => CAT[k]).filter(c => allowedView.includes(c.key.toLowerCase()));
    const fundsTotal = funds.reduce((s, f) => s + Number(f.fund_amount), 0);
    const name = (ownerSummary.managers.find(m => m.id === managerId) || {}).name || (funds[0] || {}).manager_name || L('मैनेजर', 'Manager');

    async function loadTxns(reset) {
      if (reset) { mdPage = 1; mdResults = []; }
      const { from, to } = mdComputeDates();
      const r = await api(`projects/${project.id}/transactions/?manager_id=${managerId}&from=${from}&to=${to}&page=${mdPage}`);
      mdInTotal = r.in_total; mdOutTotal = r.out_total; mdCount = r.count;
      mdResults = reset ? r.results : mdResults.concat(r.results);
      mdHasNext = !!r.next;
    }
    try { await loadTxns(true); } catch (e) { mdResults = []; mdHasNext = false; mdInTotal = mdOutTotal = mdCount = 0; }

    function render() {
      const { from, to } = mdComputeDates();
      const bal = Number(summary.balance);
      $view.innerHTML = `
        <h1>👤 ${esc(name || L('मैनेजर', 'Manager'))}</h1>
        <p class="muted">${L('मैनेजर', 'Manager')} · <b>${esc(project.name)}</b> <span class="pill">${L('सिर्फ़ देखने के लिए', 'View only')}</span></p>
        <div class="tot-grid md-fund-cards">
          <div class="card tot-box"><div class="muted">${I18n.t('fundReceivedTitle')}</div><div class="big-total">${money(summary.fund_received)}</div></div>
          <div class="card tot-box"><div class="muted">${I18n.t('totalDistributedTitle')}</div><div class="big-total">${money(summary.total_distributed)}</div></div>
          <div class="card tot-box ${bal > 0 ? 'ok' : ''} ${bal < 0 ? 'bad' : ''}"><div class="muted">${I18n.t('availableBalanceTitle')}</div><div class="big-total">${money(summary.balance)}</div></div>
        </div>
        ${cards.length ? `<h2>${L('श्रेणी अनुसार', 'By Category')}</h2><div class="md-cat-grid">` + cards.map(c => `
          <div class="card item"><div class="row"><span class="who">${c.icon} ${catLabel(c)}</span><span class="amount">${money((summary.category_totals || {})[c.key.toLowerCase()])}</span></div></div>`).join('') + `</div>` : ''}
        <h2>📥 ${L('फंड मिला', 'Fund Given')} <span class="muted">(${money(fundsTotal)})</span></h2>
        ${funds.length ? funds.map(f => `
          <div class="card item"><div class="row"><span class="who">${shortDate(f.fund_date)} · ${esc(modeName(f.payment_mode))}</span><span class="amount">${money(f.fund_amount)}</span></div></div>`).join('')
          : `<div class="empty">${L('अभी कोई फंड नहीं मिला.', 'No fund given yet.')}</div>`}
        <h2>🧾 ${L('लेन-देन', 'Transactions')} <span class="pill">${L('सिर्फ़ देखने के लिए', 'View only')}</span></h2>
        <div class="choices small" id="md-chips">
          <button type="button" class="choice" data-r="week" aria-pressed="${mdRange === 'week'}">${I18n.t('thisWeek')}</button>
          <button type="button" class="choice" data-r="month" aria-pressed="${mdRange === 'month'}">${I18n.t('thisMonth')}</button>
          <button type="button" class="choice" data-r="custom" aria-pressed="${mdRange === 'custom'}">${I18n.t('customRange')}</button>
        </div>
        ${mdRange === 'custom' ? `<div class="step" id="md-custom">
          <label for="md-from">${I18n.t('fromDate')}</label><input id="md-from" type="date" value="${mdFrom || from}">
          <label for="md-to">${I18n.t('toDate')}</label><input id="md-to" type="date" value="${mdTo || to}">
        </div>` : ''}
        <div class="fund-table" role="table">
          <div class="fund-row head" role="row"><span>${I18n.t('inTotal')}</span><span>${I18n.t('outTotal')}</span><span>${I18n.t('txnCount')}</span></div>
          <div class="fund-row"><span>${money(mdInTotal)}</span><span>${money(mdOutTotal)}</span><span>${mdCount}</span></div>
        </div>
        ${mdResults.length ? mdResults.map(txnRowHtml).join('') : `<div class="empty">${I18n.t('noTransactionsYet')}</div>`}
        ${mdHasNext ? `<button type="button" class="btn line" id="md-more">${I18n.t('loadMore')}</button>` : ''}
        ${can('canGiveManagerFund') ? `<a class="btn green" id="mgr-givefund" href="#/givefund">➕ ${L('फंड दें', 'Give Fund to')} ${esc(name)}</a>` : ''}`;

      const giveBtn = document.getElementById('mgr-givefund');
      if (giveBtn) giveBtn.onclick = () => { fundManager = managerId; };
      document.getElementById('md-chips').querySelectorAll('button').forEach(b => b.onclick = async () => {
        mdRange = b.dataset.r;
        if (mdRange !== 'custom') { loading(); await loadTxns(true); render(); } else render();
      });
      const fromEl = document.getElementById('md-from'), toEl = document.getElementById('md-to');
      if (fromEl && toEl) {
        const applyCustom = async () => { mdFrom = fromEl.value; mdTo = toEl.value; loading(); await loadTxns(true); render(); };
        fromEl.onchange = applyCustom; toEl.onchange = applyCustom;
      }
      const more = document.getElementById('md-more');
      if (more) more.onclick = async () => { mdPage += 1; more.disabled = true; more.textContent = '…'; await loadTxns(false); render(); };
    }
    render();
  }

  // Super Admin Dashboard: the default landing screen for a super admin with canViewAllProjects.
  // Project-agnostic (no project switcher) -- every figure comes from GET admin-summary/.
  const ATTN_LABEL = t => t === 'no_manager' ? L('असाइन करें', 'Assign') : L('समीक्षा करें', 'Review');
  const ATTN_ICON = t => t === 'no_manager' ? '⚠️' : '🔒';
  async function screenSuperAdminDashboard() {
    chrome('home');
    loading();
    let summary;
    try { summary = await api('admin-summary/'); }
    catch (e) { $view.innerHTML = errBox(friendly(e, MSG.noPermissionView)); return; }

    $view.innerHTML = `
      <h1>🏢 ${L('सभी प्रोजेक्ट', 'All Projects')}</h1>
      <div class="tot-grid md-fund-cards">
        <div class="card tot-box"><div class="muted">${L('सक्रिय प्रोजेक्ट', 'Active Projects')}</div><div class="big-total">${summary.active_projects}</div></div>
        <div class="card tot-box"><div class="muted">${L('कुल खर्च', 'Total Spent')}</div><div class="big-total">${money(summary.total_spent)}</div></div>
        <div class="card tot-box"><div class="muted">${L('यूज़र', 'Users')}</div><div class="big-total">${summary.users}</div></div>
      </div>
      ${summary.attention.length ? `<h2>⚠️ ${L('ध्यान चाहिए', 'Needs attention')}</h2>` + summary.attention.map(a => `
        <div class="card item mgr-row">
          <span class="mgr-avatar">${ATTN_ICON(a.type)}</span>
          <span class="mgr-mid"><b>${esc(a.label)}</b></span>
          <button type="button" class="btn line attn-action" data-type="${a.type}" data-id="${a.target_id}" style="min-height:44px;width:auto;margin:0;padding:6px 16px;font-size:.95rem">${ATTN_LABEL(a.type)}</button>
        </div>`).join('') : ''}
      <h2>🏗️ ${L('प्रोजेक्ट', 'Projects')}</h2>
      ${summary.projects.length ? summary.projects.map(p => `
        <button type="button" class="card pick item mgr-row proj-row" data-p="${p.id}">
          <span class="mgr-mid"><b>${esc(p.name)}</b> <span class="pill">${esc(statusLabel(p.status))}</span><span class="meta">${esc(p.managers.join(', ') || L('कोई मैनेजर नहीं', 'No manager'))}</span></span>
          <span class="mgr-bal"><b>${money(p.spent)}</b></span>
          <span class="chev">›</span>
        </button>`).join('') : `<div class="empty">${L('अभी कोई प्रोजेक्ट नहीं है.', 'No projects yet.')}</div>`}
      <h2>⚡ ${L('त्वरित कार्रवाई', 'Quick actions')}</h2>
      <div class="qa-grid">
        ${can('canCreateProject') ? `<a class="btn line qa" href="#/settings/new">➕ ${L('नया प्रोजेक्ट', 'New Project')}</a>` : ''}
        ${can('canManagePermissions') ? `<a class="btn line qa" href="#/access">🔐 ${L('यूज़र और एक्सेस', 'Users & Access')}</a>` : ''}
        ${can('canManagePermissions') ? `<a class="btn line qa" href="#/settings">⚙️ ${L('प्रोजेक्ट सेटिंग्स', 'Project Settings')}</a>` : ''}
        ${can('canViewReports') ? `<a class="btn line qa" href="#/reports">📊 ${L('रिपोर्ट', 'Reports')}</a>` : ''}
      </div>`;

    $view.querySelectorAll('.attn-action').forEach(b => b.onclick = () => {
      if (b.dataset.type === 'no_manager') { location.hash = `#/settings/${b.dataset.id}`; }
      else { ac.selectedUserId = Number(b.dataset.id); location.hash = '#/access/users'; }
    });
    // Tap a project row: same "select a project" mechanism as the existing switcher (screenProject),
    // then open that project's dashboard using the Owner widgets directly (a super admin has no
    // project of their own to land on via screenHome's normal branching).
    $view.querySelectorAll('.proj-row').forEach(b => b.onclick = async () => {
      const p = state.realProjects.find(x => x.id === Number(b.dataset.p));
      if (!p) return;
      state.project = p; store.set('projectId', p.id); state.names = null;
      await screenOwnerDashboard();
    });
  }

  async function screenHome() {
    chrome('home');
    const proj = state.project;
    // Manager Dashboard is the default landing screen once a manager has a project selected; a
    // super admin (project-agnostic: no project switcher) gets the Super Admin Dashboard; an owner
    // with the fund-visibility permission gets the Owner Dashboard; everyone else (a plain owner
    // without that permission, or a viewer) keeps the quick-links screen.
    if (proj && state.me.manager_id) { await screenManagerDashboard(); return; }
    if (state.user.role === 'super_admin' && can('canViewAllProjects')) { await screenSuperAdminDashboard(); return; }
    if (proj && state.me.owner_id && can('canViewProjectFunds')) { await screenOwnerDashboard(); return; }
    $view.innerHTML = `
      <h1>${L('नमस्ते', 'Hello')}, ${esc(state.user.name)} 🙏</h1>
      ${proj ? `<p class="muted">${L('प्रोजेक्ट', 'Project')}: <b>${esc(proj.name)}</b></p>` : noProject()}
      <div class="home-grid">
        ${canAdd() ? `<a class="btn green big" href="#/add"><span class="ico">➕</span><span>${L('खर्च डालें', 'Add Expense')}</span></a>` : ''}
        ${can('canViewExpenses') ? `<a class="btn big" href="#/list"><span class="ico">📋</span><span>${L('खर्च की लिस्ट', 'Expense List')}</span></a>` : ''}
        ${can('canViewProjects') ? `<a class="btn big" href="#/project"><span class="ico">🏠</span><span>${L('मेरा प्रोजेक्ट', 'My Project')}</span></a>` : ''}
        ${can('canViewManagerFund') ? `<a class="btn big" href="#/fund"><span class="ico">💰</span><span>${L('मैनेजर फंड', 'Manager Fund')}</span></a>` : ''}
        ${can('canViewReports') ? `<a class="btn big" href="#/reports"><span class="ico">📊</span><span>${L('कुल खर्च', 'Total Expense')}</span></a>` : ''}
      </div>`;
  }

  let projectFlash = '';
  async function screenProject() {
    chrome('project', '#/home');
    loading();
    try {
      // A manager's own card must show the same scoped figure as the admin Fund screen
      // (what they distributed to labour), not the whole project's merged expense total --
      // otherwise the two screens disagree and look like a data bug.
      const isManager = !!state.me.manager_id && can('canViewManagerFund');
      const [totals, fundByProject] = await Promise.all([
        isManager ? Promise.resolve(null) : Promise.all(state.projects.map(p => api(`projects/${p.id}/summary/`).catch(() => null))),
        isManager ? api('manager-funds/statement/').then(r => {
          const m = {};
          (r.statements || []).forEach(x => { m[x.position.project_id] = x.position; });
          return m;
        }).catch(() => ({})) : Promise.resolve(null),
      ]);
      if (!state.projects.length) { $view.innerHTML = noProject(); return; }
      const many = state.projects.length > 1;
      const shownFlash = projectFlash; projectFlash = '';
      $view.innerHTML = `<h1>🏗️ ${L(many ? 'मेरे प्रोजेक्ट' : 'मेरा प्रोजेक्ट', 'Projects')}</h1>` +
        (shownFlash ? `<div class="msg ok" role="status">${esc(shownFlash)}</div>` : '') +
        (can('canCreateProject') ? `<a class="btn green" href="#/settings/new">➕ ${L('नया प्रोजेक्ट', 'New Project')}</a>` : '') +
        (many ? `<p class="muted">${L('जिस प्रोजेक्ट में काम करना है उसे छूइए.', 'Tap the project you want to work on.')}</p>` : '') +
        (state.projects.map((p, i) => {
          const pos = fundByProject && fundByProject[p.id];
          const totalLine = pos
            ? `<div style="margin-top:8px">${L('मज़दूरों को बाँटा', 'Distributed to Labour')} <span class="amount">${money(pos.total_distributed)}</span></div>`
            : (totals && totals[i] ? `<div style="margin-top:8px">${L('कुल खर्च', 'Total Expense')} <span class="amount">${money(totals[i].total_expense)}</span></div>` : '');
          return `
          <${many ? 'button type="button"' : 'div'} class="card pick ${p.id === state.project.id ? 'on' : ''}" data-id="${p.id}">
            <div class="row"><b style="font-size:1.3rem">${esc(p.name)}</b>${many && p.id === state.project.id ? `<span class="pill">✔ ${L('चुना है', 'Selected')}</span>` : ''}</div>
            <div><span class="pill">${esc(Authz.ROLE_LABEL[Authz.roleIn(state.user, p.id)] || '')}</span></div>
            ${p.location ? `<div class="muted">📍 ${esc(p.location)}</div>` : ''}
            ${p.plot_size ? `<div class="muted">${L('प्लॉट', 'Plot')}: ${esc(p.plot_size)}</div>` : ''}
            <div class="muted">${esc(statusLabel(p.status))}${p.start_date ? ` · ${L('शुरू', 'Started')}: ` + niceDate(p.start_date) : ''}</div>
            ${totalLine}
          </${many ? 'button' : 'div'}>`;
        }).join('') || noProject());
      $view.querySelectorAll('button.pick').forEach(b => b.onclick = () => {
        state.project = state.projects.find(p => p.id === Number(b.dataset.id));
        store.set('projectId', state.project.id);
        state.names = null;
        location.hash = '#/home';
      });
    } catch (e) { $view.innerHTML = errBox(friendly(e, MSG.noPermissionView)); }
  }

  // ----- Add expense -----
  // `params` (URLSearchParams from the route) may carry ?tab=labour|other|supplier|contractor --
  // the Manager Dashboard's category cards and the bottom nav's Add Expense item link here to
  // pre-select a category. A missing or unpermitted tab just leaves the normal category picker
  // (step 1) untouched, so nothing breaks for a plain #/add visit.
  async function screenAdd(params) {
    chrome('add', '#/home');
    if (!state.project) { $view.innerHTML = noProject(); return; }
    if (!allowedCats().length && !can('canGiveManagerFund')) { $view.innerHTML = `<div class="empty"><div class="ico">🔒</div><h2>${L('अभी कोई खर्च श्रेणी उपलब्ध नहीं है', 'No expense category is available to you.')}</h2></div>`; return; }
    loading();
    let names;
    try { names = await loadNames(); } catch (e) { $view.innerHTML = errBox(friendly(e, MSG.noPermissionAdd)); return; }

    // A manager has no Owner record of their own; every expense still needs one attached
    // (`paid_by_owner`), so a manager picks which of the project's real owners it's on behalf of.
    // With exactly one (the usual case) it's picked silently; with none, there's nobody to attribute
    // to and the form can't be used at all.
    const ownerChoices = state.me.owner_id ? [] : (names.owners || []);
    if (!state.me.owner_id && !ownerChoices.length) {
      $view.innerHTML = `<div class="empty"><div class="ico">🔒</div><h2>${L('इस प्रोजेक्ट पर कोई मालिक नहीं जुड़ा है', 'No owner is assigned to this project')}</h2><p>${L('खर्च दर्ज नहीं किया जा सकता। कृपया एडमिन से संपर्क करें.', 'An expense cannot be attributed. Please contact the administrator.')}</p></div>`;
      return;
    }

    const f = { cat: '', amount: '', name: '', contractId: '', supplierId: '', what: '', date: today(), mode: 'CASH', note: '',
      ownerId: state.me.owner_id || (ownerChoices.length === 1 ? ownerChoices[0].id : '') };
    let saving = false;
    $view.innerHTML = `
      <div class="add-expense">
      <h1>➕ ${L('खर्च डालें', 'Add Expense')}</h1>
      <p class="muted">${L('प्रोजेक्ट', 'Project')}: <b>${esc(state.project.name)}</b></p>
      ${state.projects.length > 1 && can('canViewProjects') ? `<a class="btn line" href="#/project" style="min-height:56px;font-size:1.05rem">🔁 ${L('प्रोजेक्ट बदलें', 'Change Project')}</a>` : ''}
      <div id="top"></div>
      ${ownerChoices.length > 1 ? `<div class="step" id="s-owner"><label for="owner">${L('किस मालिक की तरफ से?', 'On behalf of')}</label>
        <select id="owner"><option value="">${L('— मालिक चुनिए —', '— Choose an owner —')}</option>${ownerChoices.map(o => `<option value="${o.id}">${esc(o.name)}</option>`).join('')}</select>
        <div class="field-error" id="e-owner"></div></div>` : ''}
      <div class="step" id="s-cat"><div class="q"><span class="num">1</span>${L('किस चीज़ का खर्च है?', 'What is this expense for?')}</div>
        <div class="choices">${allowedCats().map(c => `<button type="button" class="choice" data-cat="${c.key}" aria-pressed="false"><span class="ico">${c.icon}</span>${catLabel(c)}</button>`).join('')}${can('canGiveManagerFund') ? `<a class="choice" href="#/givefund"><span class="ico">💰</span>${L('फंड दें', 'Give Fund')}</a>` : ''}</div>
        <div class="field-error" id="e-cat"></div></div>
      <div class="step" id="s-amt"><label for="amt"><span class="num">2</span>${L('कितना पैसा?', 'Amount')}</label>
        <div class="rupee"><span>₹</span><input id="amt" type="text" inputmode="decimal" pattern="[0-9.]*" autocomplete="off" enterkeyhint="next" placeholder="0"></div>
        <div class="words" id="words"></div><div class="field-error" id="e-amt"></div></div>
      <div class="step" id="s-who"></div>
      <div class="step" id="s-date"><label for="date"><span class="num">4</span>${L('कब दिया?', 'Date')}</label>
        <input id="date" type="date" value="${f.date}">
        <div class="quick"><button type="button" class="choice" data-d="0">${L('आज', 'Today')}</button><button type="button" class="choice" data-d="1">${L('कल', 'Yesterday')}</button></div>
        <div class="field-error" id="e-date"></div></div>
      <div class="step"><div class="q"><span class="num">5</span>${L('कैसे दिया?', 'Paid by')}</div>
        <div class="choices small">${MODES.map(m => `<button type="button" class="choice" data-mode="${m.key}" aria-pressed="${m.key === 'CASH'}">${modeLabel(m)}</button>`).join('')}</div></div>
      <div class="step"><label for="note"><span class="num">6</span>${L('कोई जानकारी?', 'Note (optional)')}</label>
        <textarea id="note" placeholder="${L('जैसे: सीमेंट के 10 बैग', 'e.g. 10 bags of cement')}"></textarea></div>
      <div id="bottom"></div>
      <button class="btn green big" id="save" type="button">💾 ${L('खर्च सेव करें', 'SAVE EXPENSE')}</button>
      </div>`;

    const $ = id => document.getElementById(id);
    const setErr = (id, text) => { $(id).textContent = text || ''; $(id).parentElement.classList.toggle('bad', !!text); };

    // ----- Labour: pick several labourers of this project, one amount each -----
    const lab = { showInactive: false, lists: {}, on: new Set(), amt: {}, q: '' };
    let labGen = 0;   // bumped whenever the labour panel is (re)built or left, so stale async work stops
    const labAlive = gen => gen === labGen && f.cat === 'LABOUR' && !!document.getElementById('llist');
    const labKinds = () => (lab.showInactive ? ['active', 'inactive'] : ['active']);
    async function labLoad(kind) {
      if (!lab.lists[kind]) lab.lists[kind] = await api(`project-labour/?project=${state.project.id}&status=${kind}`);
      return lab.lists[kind];
    }
    // Money as whole paise (integers) so totals never pick up floating-point errors.
    const paise = t => {
      const m = /^(\d*)(?:\.(\d{0,2}))?$/.exec(String(t || '').trim());
      return m ? Number(m[1] || 0) * 100 + Number((m[2] || '').padEnd(2, '0') || 0) : 0;
    };
    const paiseText = c => `${Math.floor(c / 100)}.${String(c % 100).padStart(2, '0')}`;
    const labRows = () => {
      const rows = lab.lists.active.slice();
      (lab.lists.inactive || []).forEach(r => { if (lab.showInactive || lab.on.has(r.labour)) rows.push(r); });
      return rows;
    };
    const labTotal = () => [...lab.on].reduce((sum, id) => sum + paise(lab.amt[id]), 0);
    function labUpdate() {
      if (!$('ltotal')) return;
      const hiddenSel = document.querySelectorAll('#llist .lab-row.on[hidden]').length;
      $('ltotal').textContent = money(labTotal() / 100);
      $('lcount').textContent = lab.on.size ? `(${lab.on.size} ${L('चुने', 'selected')}${hiddenSel ? ` · ${hiddenSel} ${L('खोज में छिपे', 'hidden by search')}` : ''})` : '';
    }
    // Search only hides rows; it never clears a selection or an amount.
    function labFilter() {
      const q = lab.q.trim().toLowerCase();
      $('llist').querySelectorAll('.lab-row').forEach(r => { r.hidden = !!q && !r.dataset.name.includes(q); });
      labUpdate();
    }
    function labDrawRows() {
      const rows = labRows();
      if (!$('llist')) return;
      $('llist').innerHTML = rows.length ? rows.map(r => `
        <div class="lab-row ${lab.on.has(r.labour) ? 'on' : ''}" data-id="${r.labour}" data-name="${esc((r.name + ' ' + r.mobile).toLowerCase())}">
          <label class="lab-pick"><input type="checkbox" class="lab-chk" ${lab.on.has(r.labour) ? 'checked' : ''}>
            <span class="lab-name">${esc(r.name)}${r.type ? ` <small>${esc(r.type)}</small>` : ''}
              ${r.is_active ? '' : `<span class="pill warn">${L('काम बंद', 'Inactive')}</span>`}
              ${r.last_paid ? `<span class="lab-last">Last paid: ${shortDate(r.last_paid)}</span>` : ''}</span></label>
          <span class="lab-amt"><span>₹</span><input class="lab-a" type="text" inputmode="decimal" autocomplete="off" enterkeyhint="next" placeholder="0" aria-label="${esc(r.name)} ${L('राशि', 'amount')}" value="${esc(lab.amt[r.labour] || '')}"></span>
          ${r.is_active && can('canManageLabour') ? `<button type="button" class="lab-stop">${L('काम बंद करें', 'Mark inactive')}</button>` : ''}
        </div>`).join('')
        : `<div class="msg info">${L('इस प्रोजेक्ट में अभी कोई चालू मज़दूर नहीं है। "नया मज़दूर" जोड़िए या पुराने मज़दूर दिखाइए.', 'No active labour on this project yet. Add a new labour, or show inactive ones.')}</div>`;
      labFilter();
    }
    function labModal(gen) {
      const box = document.createElement('div');
      box.className = 'modal';
      box.innerHTML = `<form class="modal-box" role="dialog" aria-modal="true" aria-label="${L('नया मज़दूर', 'Add Labour')}" novalidate>
        <h2 style="margin-top:0">➕ ${L('नया मज़दूर', 'Add Labour')}</h2>
        <div id="m-err"></div>
        <div id="m-fields">
          <label for="m-name">${L('नाम / मिस्त्री', 'Name')} *</label>
          <input id="m-name" type="text" autocomplete="off" autocapitalize="words" enterkeyhint="next">
          <label for="m-mob">${L('मोबाइल नंबर', 'Mobile')} *</label>
          <input id="m-mob" type="text" inputmode="tel" autocomplete="off" maxlength="15" enterkeyhint="next">
          <label for="m-type">${L('काम का प्रकार', 'Type (optional)')}</label>
          <input id="m-type" type="text" autocomplete="off" placeholder="${L('जैसे: मिस्त्री, हेल्पर', 'e.g. Mason, Helper')}" enterkeyhint="next">
          <label for="m-rem">${L('जानकारी', 'Remarks (optional)')}</label>
          <input id="m-rem" type="text" autocomplete="off" enterkeyhint="done">
          <button class="btn green" type="submit" id="m-save">💾 ${L('सेव करें', 'SAVE')}</button>
        </div>
        <div id="m-dup" hidden></div>
        <button class="btn line" type="button" id="m-cancel">${L('रद्द करें', 'Cancel')}</button>
      </form>`;
      document.body.appendChild(box);
      const m = id => box.querySelector('#' + id);
      const close = () => {
        box.remove();
        document.removeEventListener('keydown', onKey);
        window.removeEventListener('hashchange', close);
      };
      const onKey = e => { if (e.key === 'Escape') close(); };
      document.addEventListener('keydown', onKey);
      window.addEventListener('hashchange', close);      // Back / navigation must not leave the overlay behind
      m('m-cancel').onclick = close;
      m('m-name').focus();

      // The labour now exists on the server; show it in the list and select it.
      async function added(r) {
        close();
        if (!labAlive(gen)) return;
        try {
          lab.on.add(r.labour);
          lab.q = ''; if ($('lq')) $('lq').value = '';
          if (lab.lists.active) {
            ['active', 'inactive'].forEach(k => { if (lab.lists[k]) lab.lists[k] = lab.lists[k].filter(x => x.labour !== r.labour); });
            lab.lists.active.unshift(r);
            labDrawRows();
          } else {
            lab.lists = {};            // the first load had failed: reload it, the new labour is included
            await drawLabour();
          }
          if ($('lnote')) $('lnote').innerHTML = r.reused ? `<div class="msg info">${L('यह मज़दूर पहले से था — इस प्रोजेक्ट में चालू कर दिया.', 'This labour already existed — marked active on this project.')}</div>` : '';
          const row = document.querySelector(`#llist .lab-row[data-id="${r.labour}"]`);
          if (row) { row.scrollIntoView({ block: 'center' }); row.querySelector('.lab-a').focus(); }
        } catch (err) { console.error('after add labour', err); }
      }

      // Same name, different mobile: ask "is this the same person?" (only when there is such a match).
      function askSame(cands, send) {
        m('m-fields').hidden = true;
        const dup = m('m-dup');
        dup.hidden = false;
        dup.innerHTML = `<h2 style="margin-top:0">${L('क्या यह वही मज़दूर है?', 'Is this the same person?')}</h2>` +
          cands.map(c => `<div class="card"><b>${esc(c.name)}</b>
            <div class="muted">Mobile: ${esc(c.mobile_masked || '—')}</div>
            <div class="muted">${c.last_paid ? 'Last paid: ' + shortDate(c.last_paid) : 'Never paid'}</div>
            <button class="btn green" type="button" data-use="${c.labour}" style="margin-bottom:0">✔ ${L('यही है', 'Use this Labour')}</button></div>`).join('') +
          `<button class="btn line" type="button" id="m-new">➕ ${L('अलग व्यक्ति है', 'Different Person')}</button>`;
        dup.querySelectorAll('[data-use]').forEach(b => b.onclick = () => send({ use_labour: Number(b.dataset.use) }));
        m('m-new').onclick = () => send({ confirm_new: true });
      }

      box.querySelector('form').onsubmit = async ev => {
        ev.preventDefault();
        const name = m('m-name').value.trim(), mobile = m('m-mob').value.trim();
        if (!name) { m('m-err').innerHTML = errBox(L('कृपया नाम भरें.', 'Please enter a name.')); m('m-name').focus(); return; }
        if (mobile.replace(/\D/g, '').length < 10) { m('m-err').innerHTML = errBox(L('कृपया सही मोबाइल नंबर भरें.', 'Please enter a valid mobile number.')); m('m-mob').focus(); return; }
        const send = async extra => {
          const btns = box.querySelectorAll('button');
          btns.forEach(b => { if (b.id !== 'm-cancel') b.disabled = true; });
          m('m-err').innerHTML = '';
          let r;
          try {
            r = await api('project-labour/', { method: 'POST', body: {
              project: state.project.id, name, mobile, type: m('m-type').value.trim(), remarks: m('m-rem').value.trim(), ...extra } });
          } catch (e) {
            btns.forEach(b => { b.disabled = false; });
            if (e.status === 409 && e.data && e.data.code === 'possible_duplicate') { askSame(e.data.candidates || [], send); return; }
            m('m-fields').hidden = false; m('m-dup').hidden = true;
            m('m-err').innerHTML = errBox(serverMsg(e) || friendly(e));
            return;
          }
          await added(r);
        };
        send({});
      };
    }
    // ----- Contractor: pick a contract of this project, or add a contractor + contract -----
    function contractorModal() {
      const box = document.createElement('div');
      box.className = 'modal';
      box.innerHTML = `<form class="modal-box" role="dialog" aria-modal="true" aria-label="${L('नया ठेकेदार', 'Add Contractor')}" novalidate>
        <h2 style="margin-top:0" id="m-title">➕ ${L('नया ठेकेदार', 'Add Contractor')}</h2>
        <div id="m-err"></div>
        <div id="m-fields">
          <label for="m-name">${L('नाम', 'Name')} *</label>
          <input id="m-name" type="text" autocomplete="off" autocapitalize="words" enterkeyhint="next">
          <label for="m-mob">${L('मोबाइल नंबर', 'Mobile')} *</label>
          <input id="m-mob" type="text" inputmode="tel" autocomplete="off" maxlength="15" enterkeyhint="next">
          <label for="m-type">${L('काम का प्रकार', 'Work Type (optional)')}</label>
          <input id="m-type" type="text" autocomplete="off" placeholder="${L('जैसे: RCC, बिजली, प्लंबिंग', 'e.g. RCC, Electrical, Plumbing')}" enterkeyhint="next">
          <label for="m-rem">${L('जानकारी', 'Remarks (optional)')}</label>
          <input id="m-rem" type="text" autocomplete="off" enterkeyhint="done">
          <button class="btn green" type="submit" id="m-save">➡ ${L('आगे', 'NEXT')}</button>
        </div>
        <div id="m-dup" hidden></div>
        <div id="m-contract" hidden>
          <p class="muted">${L('ठेकेदार', 'Contractor')}: <b id="m-cname"></b></p>
          <label for="c-work">${L('काम का विवरण', 'Work Description')} *</label>
          <input id="c-work" type="text" autocomplete="off" autocapitalize="sentences" placeholder="${L('जैसे: RCC + Structure', 'e.g. RCC + Structure')}" enterkeyhint="next">
          <label for="c-amt">${L('कुल ठेका राशि', 'Contract Amount')} *</label>
          <div class="rupee"><span>₹</span><input id="c-amt" type="text" inputmode="decimal" autocomplete="off" enterkeyhint="next" placeholder="0"></div>
          <label for="c-date">${L('ठेके की तारीख', 'Contract Date')} *</label>
          <input id="c-date" type="date" value="${today()}">
          <label for="c-rem">${L('जानकारी', 'Remarks (optional)')}</label>
          <input id="c-rem" type="text" autocomplete="off" enterkeyhint="done">
          <button class="btn green" type="submit" id="c-save">💾 ${L('ठेका सेव करें', 'SAVE CONTRACT')}</button>
        </div>
        <button class="btn line" type="button" id="m-cancel">${L('रद्द करें', 'Cancel')}</button>
      </form>`;
      document.body.appendChild(box);
      const m = id => box.querySelector('#' + id);
      const close = () => {
        box.remove();
        document.removeEventListener('keydown', onKey);
        window.removeEventListener('hashchange', close);
      };
      const onKey = e => { if (e.key === 'Escape') close(); };
      document.addEventListener('keydown', onKey);
      window.addEventListener('hashchange', close);
      m('m-cancel').onclick = close;
      m('m-name').focus();
      let contractor = null;   // set once the contractor exists on the server

      // Step 2: the contractor is known; now the project-specific contract.
      function contractStep(c) {
        contractor = c;
        m('m-fields').hidden = true; m('m-dup').hidden = true; m('m-contract').hidden = false;
        m('m-title').innerHTML = `➕ ${L('नया ठेका', 'Add Contract')}`;
        m('m-cname').textContent = c.name;
        m('m-err').innerHTML = c.reused ? `<div class="msg info">${L('यह ठेकेदार पहले से था — इसी को चुना है.', 'This contractor already existed — selected them.')}</div>` : '';
        m('c-work').focus();
      }

      // Same name, different mobile: ask "is this the same contractor?".
      function askSame(cands, send) {
        m('m-fields').hidden = true;
        const dup = m('m-dup');
        dup.hidden = false;
        dup.innerHTML = `<h2 style="margin-top:0">${L('क्या यह वही Contractor है?', 'Is this the same contractor?')}</h2>` +
          cands.map(c => `<div class="card"><b>${esc(c.name)}</b>
            <div class="muted">Mobile: ${esc(c.mobile_masked || '—')}</div>
            ${c.work_type ? `<div class="muted">${esc(c.work_type)}</div>` : ''}
            <button class="btn green" type="button" data-use="${c.contractor}" style="margin-bottom:0">✔ ${L('यही है', 'Use Existing Contractor')}</button></div>`).join('') +
          `<button class="btn line" type="button" id="m-new">➕ ${L('अलग ठेकेदार है', 'Different Contractor')}</button>`;
        dup.querySelectorAll('[data-use]').forEach(b => b.onclick = () => send({ use_contractor: Number(b.dataset.use) }));
        m('m-new').onclick = () => send({ confirm_new: true });
      }

      async function saveContractor(extra) {
        const name = m('m-name').value.trim(), mobile = m('m-mob').value.trim();
        const btns = box.querySelectorAll('button');
        btns.forEach(b => { if (b.id !== 'm-cancel') b.disabled = true; });
        m('m-err').innerHTML = '';
        try {
          const r = await api('contractors/', { method: 'POST', body: {
            name, mobile, work_type: m('m-type').value.trim(), remarks: m('m-rem').value.trim(), ...extra } });
          btns.forEach(b => { b.disabled = false; });
          contractStep(r);
        } catch (e) {
          btns.forEach(b => { b.disabled = false; });
          if (e.status === 409 && e.data && e.data.code === 'possible_duplicate') { askSame(e.data.candidates || [], saveContractor); return; }
          m('m-fields').hidden = false; m('m-dup').hidden = true;
          m('m-err').innerHTML = errBox(serverMsg(e) || friendly(e));
        }
      }

      async function saveContract() {
        const work = m('c-work').value.trim(), cents = paise(m('c-amt').value);
        const bad = (id, text) => { m('m-err').innerHTML = errBox(text); m(id).focus(); };
        if (!work) return bad('c-work', L('कृपया काम का विवरण भरें.', 'Please enter the work description.'));
        if (!(cents > 0)) return bad('c-amt', L('कृपया ठेका राशि भरें (0 से ज़्यादा).', 'Please enter a contract amount greater than zero.'));
        if (cents >= 1e12) return bad('c-amt', L('राशि बहुत बड़ी है। कृपया जाँच लें.', 'That amount is too large. Please check it.'));
        if (!m('c-date').value) return bad('c-date', L('कृपया तारीख चुनिए.', 'Please choose a date.'));
        const btns = box.querySelectorAll('button');
        btns.forEach(b => { if (b.id !== 'm-cancel') b.disabled = true; });
        m('m-err').innerHTML = '';
        let made;
        try {
          made = await api('contractor-contracts/', { method: 'POST', body: {
            project: state.project.id, contractor: contractor.id, work_description: work,
            contract_amount: paiseText(cents), contract_date: m('c-date').value, remarks: m('c-rem').value.trim() } });
        } catch (e) {
          btns.forEach(b => { b.disabled = false; });
          m('m-err').innerHTML = errBox(serverMsg(e) || friendly(e));
          return;
        }
        close();
        try {
          state.names = null;
          names = await loadNames(true);
          if (f.cat !== 'CONTRACTOR') return;
          drawWho();
          selectContract(String(made.id));
          const card = document.querySelector(`[data-contract="${made.id}"]`);
          if (card) card.scrollIntoView({ block: 'center' });
        } catch (err) { console.error('after add contract', err); }
      }

      box.querySelector('form').onsubmit = ev => {
        ev.preventDefault();
        if (contractor) return saveContract();
        const name = m('m-name').value.trim(), mobile = m('m-mob').value.trim();
        if (!name) { m('m-err').innerHTML = errBox(L('कृपया नाम भरें.', 'Please enter a name.')); m('m-name').focus(); return; }
        if (mobile.replace(/\D/g, '').length < 10) { m('m-err').innerHTML = errBox(L('कृपया सही मोबाइल नंबर भरें.', 'Please enter a valid mobile number.')); m('m-mob').focus(); return; }
        saveContractor({});
      };
    }
    function selectContract(id) {
      f.contractId = id;
      document.querySelectorAll('[data-contract]').forEach(b => b.classList.toggle('on', b.dataset.contract === id));
      if ($('e-who')) setErr('e-who', '');
    }
    const contractCard = c => {
      const bal = Number(c.balance_amount);
      return `<button type="button" class="card pick" data-contract="${c.id}">
        <b style="font-size:1.3rem">${esc(c.contractor_name)}</b>
        ${c.work_description ? `<div class="muted">${esc(c.work_description)}</div>` : ''}
        <div class="row"><span class="muted">${L('पूरा काम', 'Contract')}</span><span>${money(c.contract_amount)}</span></div>
        <div class="row"><span class="muted">${L('अब तक दिया', 'Paid')}</span><span>${money(c.paid_amount)}</span></div>
        <div class="row"><b>${bal < 0 ? L('ज़्यादा दिया', 'Overpaid') : L('देना बाकी', 'Balance due')}</b><span class="pill ${bal < 0 ? 'warn' : ''}" style="font-size:1.1rem">${money(Math.abs(bal))}</span></div>
      </button>`;
    };

    // ----- Supplier: pick a supplier (or add one), then say what was bought -----
    function supplierModal() {
      const box = document.createElement('div');
      box.className = 'modal';
      box.innerHTML = `<form class="modal-box" role="dialog" aria-modal="true" aria-label="${L('नया Supplier', 'Add Supplier')}" novalidate>
        <h2 style="margin-top:0">➕ ${L('नया Supplier', 'Add Supplier')}</h2>
        <div id="m-err"></div>
        <div id="m-fields">
          <label for="m-name">${L('नाम', 'Supplier Name')} *</label>
          <input id="m-name" type="text" autocomplete="off" autocapitalize="words" enterkeyhint="next">
          <label for="m-mob">${L('मोबाइल नंबर', 'Mobile')} *</label>
          <input id="m-mob" type="text" inputmode="tel" autocomplete="off" maxlength="15" enterkeyhint="next">
          <label for="m-type">${L('सामान का प्रकार', 'Supplier Type (optional)')}</label>
          <select id="m-type"><option value="">${L('— चुनिए —', '— Choose —')}</option>${SUPPLIER_TYPES.map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join('')}</select>
          <label for="m-rem">${L('जानकारी', 'Remarks (optional)')}</label>
          <input id="m-rem" type="text" autocomplete="off" enterkeyhint="done">
          <button class="btn green" type="submit" id="m-save">💾 ${L('सेव करें', 'SAVE')}</button>
        </div>
        <div id="m-dup" hidden></div>
        <button class="btn line" type="button" id="m-cancel">${L('रद्द करें', 'Cancel')}</button>
      </form>`;
      document.body.appendChild(box);
      const m = id => box.querySelector('#' + id);
      const close = () => {
        box.remove();
        document.removeEventListener('keydown', onKey);
        window.removeEventListener('hashchange', close);
      };
      const onKey = e => { if (e.key === 'Escape') close(); };
      document.addEventListener('keydown', onKey);
      window.addEventListener('hashchange', close);
      m('m-cancel').onclick = close;
      m('m-name').focus();

      // Same name, different mobile: ask "is this the same supplier?".
      function askSame(cands, send) {
        m('m-fields').hidden = true;
        const dup = m('m-dup');
        dup.hidden = false;
        dup.innerHTML = `<h2 style="margin-top:0">${L('क्या यह वही Supplier है?', 'Is this the same supplier?')}</h2>` +
          cands.map(c => `<div class="card"><b>${esc(c.name)}</b>
            <div class="muted">Mobile: ${esc(c.mobile_masked || '—')}</div>
            ${c.supplier_type ? `<div class="muted">${esc(c.supplier_type)}</div>` : ''}
            <button class="btn green" type="button" data-use="${c.supplier}" style="margin-bottom:0">✔ ${L('यही है', 'Use Existing')}</button></div>`).join('') +
          `<button class="btn line" type="button" id="m-new">➕ ${L('अलग Supplier है', 'Different Supplier')}</button>`;
        dup.querySelectorAll('[data-use]').forEach(b => b.onclick = () => send({ use_supplier: Number(b.dataset.use) }));
        m('m-new').onclick = () => send({ confirm_new: true });
      }

      async function send(extra) {
        const btns = box.querySelectorAll('button');
        btns.forEach(b => { if (b.id !== 'm-cancel') b.disabled = true; });
        m('m-err').innerHTML = '';
        let r;
        try {
          r = await api('suppliers/add/', { method: 'POST', body: {
            name: m('m-name').value.trim(), mobile: m('m-mob').value.trim(), supplier_type: m('m-type').value,
            remarks: m('m-rem').value.trim(), ...extra } });
        } catch (e) {
          btns.forEach(b => { b.disabled = false; });
          if (e.status === 409 && e.data && e.data.code === 'possible_duplicate') { askSame(e.data.candidates || [], send); return; }
          m('m-fields').hidden = false; m('m-dup').hidden = true;
          m('m-err').innerHTML = errBox(serverMsg(e) || friendly(e));
          return;
        }
        close();
        try {
          state.names = null;
          names = await loadNames(true);
          if (f.cat !== 'SUPPLIER') return;
          const keep = f.what;
          drawWho();
          if ($('material')) { $('material').value = keep; f.what = keep; }
          selectSupplier(String(r.id));
          if ($('material') && !keep) $('material').focus();
        } catch (err) { console.error('after add supplier', err); }
      }

      box.querySelector('form').onsubmit = ev => {
        ev.preventDefault();
        if (!m('m-name').value.trim()) { m('m-err').innerHTML = errBox(L('कृपया नाम भरें.', 'Please enter a name.')); m('m-name').focus(); return; }
        if (m('m-mob').value.replace(/\D/g, '').length < 10) { m('m-err').innerHTML = errBox(L('कृपया सही मोबाइल नंबर भरें.', 'Please enter a valid mobile number.')); m('m-mob').focus(); return; }
        send({});
      };
    }
    function selectSupplier(id) {
      f.supplierId = id;
      if ($('supplier')) $('supplier').value = id;
      if ($('e-who')) setErr('e-who', '');
    }

    async function drawLabour() {
      const gen = ++labGen;
      $('s-who').innerHTML = `
        <div class="q"><span class="num">3</span>${L('किस मज़दूर को दिया?', 'Labour Payments')} <small id="lcount"></small></div>
        <div class="lab-bar">
          <input id="lq" type="text" autocomplete="off" enterkeyhint="search" placeholder="🔍 ${L('मज़दूर खोजिए', 'Search Labour')}" value="${esc(lab.q)}">
          ${can('canManageLabour') ? `<button type="button" class="btn line" id="ladd">➕ ${L('नया', 'Add Labour')}</button>` : ''}
        </div>
        <label class="lab-inact"><input type="checkbox" id="linact" ${lab.showInactive ? 'checked' : ''}> ${L('पुराने / काम बंद मज़दूर भी दिखाएँ', 'Show Inactive')}</label>
        <div id="lnote"></div>
        <div id="llist"><div class="spinner">⏳ ${L('रुकिए...', 'Loading...')}</div></div>
        <div class="card row lab-total"><span>${L('कुल मज़दूरी', 'Total Labour Payment')}</span><span class="amount" id="ltotal">${money(0)}</span></div>
        <div><div class="field-error" id="e-lab"></div></div>`;
      $('lq').oninput = e => { lab.q = e.target.value; labFilter(); };
      if ($('ladd')) $('ladd').onclick = () => labModal(gen);
      $('linact').onchange = async e => {
        lab.showInactive = e.target.checked;
        try { await labLoad('inactive'); if (labAlive(gen)) labDrawRows(); }
        catch (err) { if (labAlive(gen)) $('llist').innerHTML = errBox(friendly(err)); }
      };
      const list = $('llist');
      list.onchange = e => {
        if (!e.target.classList.contains('lab-chk')) return;
        const row = e.target.closest('.lab-row'), id = Number(row.dataset.id);
        if (e.target.checked) { lab.on.add(id); row.classList.add('on'); row.querySelector('.lab-a').focus(); }
        else { lab.on.delete(id); row.classList.remove('on'); }
        row.classList.remove('bad-row');
        labUpdate();
      };
      list.oninput = e => {
        if (!e.target.classList.contains('lab-a')) return;
        const row = e.target.closest('.lab-row'), id = Number(row.dataset.id);
        const [whole, ...rest] = e.target.value.replace(/[^0-9.]/g, '').split('.');
        e.target.value = whole.slice(0, 9) + (rest.length ? '.' + rest.join('').slice(0, 2) : '');
        lab.amt[id] = e.target.value;
        if (e.target.value) { lab.on.add(id); row.classList.add('on'); row.querySelector('.lab-chk').checked = true; }
        row.classList.remove('bad-row');
        setErr('e-lab', '');
        labUpdate();
      };
      list.onkeydown = e => {
        if (e.key !== 'Enter' || !e.target.classList.contains('lab-a')) return;
        e.preventDefault();
        let row = e.target.closest('.lab-row').nextElementSibling;
        while (row && row.hidden) row = row.nextElementSibling;
        if (row) row.querySelector('.lab-a').focus(); else e.target.blur();
      };
      list.onclick = async e => {
        const btn = e.target.closest('.lab-stop');
        if (!btn) return;
        const row = btn.closest('.lab-row'), id = Number(row.dataset.id);
        const link = (lab.lists.active || []).find(x => x.labour === id);
        if (!link || !confirm(L(`${link.name} को इस प्रोजेक्ट में "काम बंद" करें?\nपुराना हिसाब बना रहेगा.`, `Mark ${link.name} inactive on this project?\nPast records will stay.`))) return;
        try {
          await api(`project-labour/${link.id}/set-active/`, { method: 'POST', body: { is_active: false } });
          lab.lists.active = lab.lists.active.filter(x => x !== link);
          if (lab.lists.inactive) lab.lists.inactive.push({ ...link, is_active: false });
          lab.on.delete(id); delete lab.amt[id];
          if (labAlive(gen)) labDrawRows();
        } catch (err) { if (labAlive(gen)) $('lnote').innerHTML = errBox(serverMsg(err) || friendly(err)); }
      };
      try {
        await labLoad('active');
        if (lab.showInactive) await labLoad('inactive');
        if (labAlive(gen)) labDrawRows();
      } catch (err) { if (labAlive(gen)) $('llist').innerHTML = errBox(friendly(err)); }
    }

    function drawWho() {
      const cat = f.cat;
      let html = '';
      $('s-amt').hidden = cat === 'LABOUR';   // labour has one amount per person instead
      if (cat !== 'LABOUR') labGen++;         // stop any labour load still in flight
      if (cat === 'LABOUR') { f.name = ''; f.contractId = ''; f.supplierId = ''; f.what = ''; drawLabour(); drawHistory(); return; }
      if (!cat) {
        html = `<div class="q"><span class="num">3</span>${L('किसको दिया?', 'Name')}</div><p class="muted">${L('पहले ऊपर बताइए कि किस चीज़ का खर्च है.', 'First choose above what this expense is for.')}</p>`;
      } else if (cat === 'CONTRACTOR') {
        html = `<div class="q"><span class="num">3</span>${L('किस ठेकेदार को दिया?', 'Contractor')}</div>
          ${can('canManageContractors') ? `<button class="btn line" type="button" id="cadd">➕ ${L('नया ठेकेदार', 'Add Contractor')}</button>` : ''}` +
          (names.contracts.length
            ? names.contracts.map(contractCard).join('')
            : `<div class="msg info">${L('इस प्रोजेक्ट में अभी कोई ठेकेदार नहीं जुड़ा है। "नया ठेकेदार" दबाइए.', 'No contractor is linked to this project yet. Tap "Add Contractor".')}</div>`) +
          '<div class="field-error" id="e-who"></div>';
      } else if (cat === 'SUPPLIER') {
        html = `<div class="q"><span class="num">3</span>${L('किस सप्लायर को दिया?', 'Supplier')}</div>
          ${can('canManageSuppliers') ? `<button class="btn line" type="button" id="sadd">➕ ${L('नया Supplier', 'Add Supplier')}</button>` : ''}` +
          (names.suppliers.length
            ? `<select id="supplier"><option value="">${L('— सप्लायर चुनिए —', '— Choose a supplier —')}</option>${names.suppliers.map(x => `<option value="${x.id}">${esc(x.name)}${x.supplier_type ? ' — ' + esc(x.supplier_type) : ''}</option>`).join('')}</select>`
            : `<div class="msg info">${L('अभी कोई सप्लायर नहीं है। "नया Supplier" दबाइए.', 'No supplier yet. Tap "Add Supplier".')}</div>`) +
          `<label for="material" style="margin-top:18px;display:block;font-weight:700">${L('क्या सामान लिया?', 'Material / Item')} *</label>
          <input id="material" type="text" autocomplete="off" autocapitalize="sentences" placeholder="${L('जैसे: सीमेंट, रेत', 'e.g. Cement, Sand')}" enterkeyhint="next">
          <div class="field-error" id="e-who"></div>`;
      } else {
        const label = cat === 'LABOUR' ? L('किस मज़दूर को दिया?', 'Name') : cat === 'SUPPLIER' ? L('किस सप्लायर को दिया?', 'Name') : L('किसको दिया?', 'Name');
        html = `<label for="name"><span class="num">3</span>${label}</label>
          <input id="name" type="text" autocomplete="off" autocapitalize="words" enterkeyhint="next" placeholder="${L('नाम लिखिए', 'Enter name')}">
          <div class="suggest" id="sug"></div><div class="hint" id="newhint"></div><div class="field-error" id="e-who"></div>` +
          (cat === 'MISCELLANEOUS' ? `<label for="what" style="margin-top:18px;display:block;font-weight:700">${L('किस काम का?', '(optional, e.g. Tea, Transport)')}</label><input id="what" type="text" autocomplete="off" autocapitalize="sentences" enterkeyhint="next">` : '');
      }
      $('s-who').innerHTML = html;
      f.name = ''; f.contractId = ''; f.supplierId = ''; f.what = '';
      if ($('sadd')) $('sadd').onclick = supplierModal;
      if ($('supplier')) $('supplier').onchange = e => { f.supplierId = e.target.value; setErr('e-who', ''); };
      if ($('material')) $('material').oninput = e => { f.what = e.target.value; setErr('e-who', ''); };
      if ($('cadd')) $('cadd').onclick = contractorModal;
      document.querySelectorAll('[data-contract]').forEach(b => b.onclick = () => selectContract(b.dataset.contract));
      if ($('what')) $('what').oninput = e => { f.what = e.target.value; };
      if ($('name')) $('name').oninput = e => { f.name = e.target.value; setErr('e-who', ''); drawSuggest(); };
      drawHistory();
    }

    // ----- History (Labour / Other only): date-grouped list of this project's entries for the
    // currently selected category, reusing the .card/.item/.row/.amount list styling used
    // elsewhere (see screenFund). Collapsed by default; loaded on demand, and reloaded fresh every
    // time this screen is (re)opened -- including right after a save, since saving routes to
    // #/done and coming back to Add Expense remounts this screen from scratch.
    let historyOpen = false;
    const historyCache = {};
    async function loadHistory() {
      const body = document.getElementById('hist-body');
      if (!body) return;
      body.innerHTML = `<div class="spinner">⏳ ${L('रुकिए...', 'Loading...')}</div>`;
      try {
        const key = `${state.project.id}:${f.cat}`;
        if (!historyCache[key]) {
          historyCache[key] = await api(`expense-transactions/?project=${state.project.id}&category=${f.cat}&status=ACTIVE`);
        }
        const rows = historyCache[key];
        const labourName = id => (names.labour.find(x => x.id === id) || {}).name || '';
        const byDate = {};
        rows.forEach(r => { (byDate[r.expense_date] = byDate[r.expense_date] || []).push(r); });
        const dates = Object.keys(byDate).sort().reverse();
        body.innerHTML = dates.length ? dates.map(d => {
          const list = byDate[d];
          const total = list.reduce((s, r) => s + Number(r.amount), 0);
          return `<div class="card item"><div class="row"><span class="who">${niceDate(d)}</span><span class="amount">${money(total)}</span></div>` +
            list.map(r => `<div class="meta">${esc(r.expense_category === 'LABOUR' ? labourName(r.labour) : (r.payee_name || r.expense_type))} · ${money(r.amount)} · ${esc(modeName(r.payment_mode))}</div>`).join('') +
            `</div>`;
        }).join('') : `<div class="empty">${I18n.t('noTransactionsYet')}</div>`;
      } catch (e) { body.innerHTML = errBox(friendly(e)); }
    }
    function drawHistory() {
      const box = $('bottom');
      if (!box) return;
      if (f.cat !== 'LABOUR' && f.cat !== 'MISCELLANEOUS') { box.innerHTML = ''; return; }
      box.innerHTML = `<button type="button" class="btn line" id="hist-toggle">🕘 ${I18n.t('history')} ${historyOpen ? '▲' : '▼'}</button><div id="hist-body"></div>`;
      $('hist-toggle').onclick = () => { historyOpen = !historyOpen; drawHistory(); if (historyOpen) loadHistory(); };
      if (historyOpen) loadHistory();
    }

    function knownNames() {
      return (f.cat === 'LABOUR' ? names.labour : f.cat === 'SUPPLIER' ? names.suppliers : []).map(x => x.name);
    }
    function drawSuggest() {
      const list = knownNames(), typed = f.name.trim().toLowerCase();
      const exact = list.some(n => n.toLowerCase() === typed);
      const matches = list.filter(n => typed && n.toLowerCase().includes(typed) && n.toLowerCase() !== typed).slice(0, 5);
      $('sug').innerHTML = matches.map(n => `<button type="button">${esc(n)}</button>`).join('');
      $('sug').querySelectorAll('button').forEach(b => b.onclick = () => { f.name = b.textContent; $('name').value = f.name; drawSuggest(); });
      $('newhint').textContent = f.cat !== 'MISCELLANEOUS' && typed && !exact ? `✚ ${L('यह नया नाम जुड़ जाएगा.', 'This new name will be added.')}` : '';
    }

    document.querySelectorAll('[data-cat]').forEach(b => b.onclick = () => {
      f.cat = b.dataset.cat;
      document.querySelectorAll('[data-cat]').forEach(x => x.setAttribute('aria-pressed', x === b));
      setErr('e-cat', '');
      drawWho();
    });
    // #/add?tab=labour|other|supplier|contractor: pre-select that category's button (falls back to
    // no pre-selection -- the normal step-1 picker -- when the tab is missing or not permitted).
    const wantCat = TAB_TO_CAT[((params && params.get('tab')) || '').toLowerCase()];
    if (wantCat && allowedCats().some(c => c.key === wantCat)) {
      const tabBtn = document.querySelector(`[data-cat="${wantCat}"]`);
      if (tabBtn) tabBtn.click();
    }
    document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => {
      f.mode = b.dataset.mode;
      document.querySelectorAll('[data-mode]').forEach(x => x.setAttribute('aria-pressed', x === b));
    });
    document.querySelectorAll('[data-d]').forEach(b => b.onclick = () => { $('date').value = b.dataset.d === '0' ? today() : yesterday(); setErr('e-date', ''); });
    if ($('owner')) $('owner').onchange = e => { f.ownerId = e.target.value; setErr('e-owner', ''); };
    $('amt').oninput = e => {
      e.target.value = e.target.value.replace(/[^0-9.]/g, '').replace(/(\..*)\./g, '$1');
      f.amount = e.target.value;
      $('words').textContent = Number(f.amount) > 0 ? money(f.amount) : '';
      setErr('e-amt', '');
    };
    drawWho();

    function firstError() {
      const amt = Number(f.amount);
      const fail = (id, text, scrollTo) => { setErr(id, text); $(scrollTo).scrollIntoView({ behavior: 'smooth', block: 'center' }); return true; };
      if (!f.ownerId) return fail('e-owner', L('कृपया मालिक चुनिए.', 'Please choose an owner.'), 's-owner');
      if (!f.cat) return fail('e-cat', L('कृपया बताइए किस चीज़ का खर्च है.', 'Please choose what this expense is for.'), 's-cat');
      if (f.cat === 'LABOUR') {
        if (!lab.on.size) return fail('e-lab', L('कृपया कम से कम एक मज़दूर चुनिए.', 'Please choose at least one labour.'), 's-who');
        const bad = [...lab.on].filter(id => !(paise(lab.amt[id]) > 0));
        $('llist').querySelectorAll('.lab-row').forEach(r => r.classList.toggle('bad-row', bad.includes(Number(r.dataset.id))));
        if (bad.length) {
          $('llist').querySelector('.bad-row').scrollIntoView({ behavior: 'smooth', block: 'center' });
          setErr('e-lab', L('चुने हुए हर मज़दूर की राशि भरिए (0 से ज़्यादा).', 'Enter an amount greater than zero for every selected labour.'));
          return true;
        }
      } else {
        if (!f.amount || !(amt > 0)) return fail('e-amt', L('कृपया राशि भरें.', 'Please enter an amount.'), 's-amt');
        if (amt >= 1e10) return fail('e-amt', L('राशि बहुत बड़ी है। कृपया जाँच लें.', 'That amount is too large. Please check it.'), 's-amt');
      }
      if (f.cat === 'CONTRACTOR' && !f.contractId) return fail('e-who', L('कृपया ठेकेदार चुनिए.', 'Please choose a contractor.'), 's-who');
      if (f.cat === 'SUPPLIER' && !f.supplierId) return fail('e-who', L('कृपया सप्लायर चुनिए.', 'Please choose a supplier.'), 's-who');
      if (f.cat === 'SUPPLIER' && !f.what.trim()) return fail('e-who', L('कृपया बताइए क्या सामान लिया.', 'Please enter what was bought.'), 's-who');
      if (f.cat === 'MISCELLANEOUS' && !f.name.trim()) return fail('e-who', L('कृपया नाम भरें.', 'Please enter a name.'), 's-who');
      if (!$('date').value) return fail('e-date', L('कृपया तारीख चुनिए.', 'Please choose a date.'), 's-date');
      return false;
    }

    $('save').onclick = async () => {
      if (saving) return;
      $('top').innerHTML = ''; $('bottom').innerHTML = '';
      if (firstError()) return;
      saving = true; $('save').disabled = true; $('save').firstChild.textContent = `⏳ ${L('सेव हो रहा है...', 'Saving...')} `;
      try {
        if (f.cat === 'LABOUR') {
          const picked = labRows().filter(r => lab.on.has(r.labour));
          const res = await api('labour-payments/', { method: 'POST', body: {
            project: state.project.id,
            expense_date: $('date').value,
            paid_by_owner: f.ownerId,
            payment_mode: f.mode,
            remarks: $('note').value.trim() || null,
            // Only true when the user picked a labour from "Show Inactive".
            include_inactive: picked.some(r => !r.is_active),
            payments: picked.map(r => ({ labour: r.labour, amount: paiseText(paise(lab.amt[r.labour])) })),
          } });
          state.names = null;
          sessionStorage.setItem('justSaved', JSON.stringify({ amount: res.total, who: picked.slice(0, 3).map(r => r.name).join(', ') + (picked.length > 3 ? ` +${picked.length - 3}` : '') }));
          location.hash = '#/done';
          return;
        }
        const cat = CAT[f.cat];
        const body = {
          project: state.project.id,
          expense_date: $('date').value,
          expense_category: f.cat,
          expense_type: f.cat === 'MISCELLANEOUS' ? (f.what.trim() || cat.type) : cat.type,
          party_type: cat.party,
          paid_by_owner: f.ownerId,
          amount: Number(f.amount).toFixed(2),
          payment_mode: f.mode,
          remarks: $('note').value.trim() || null,
        };
        if (f.cat === 'CONTRACTOR') body.contractor_contract = Number(f.contractId);
        else if (f.cat === 'MISCELLANEOUS') body.payee_name = f.name.trim();
        else {
          body.supplier = Number(f.supplierId);
          body.description = f.what.trim();   // the material / item bought
        }
        await api('expense-transactions/', { method: 'POST', body });
        state.names = null;
        sessionStorage.setItem('justSaved', JSON.stringify({ amount: body.amount, who: f.cat === 'CONTRACTOR' ? names.contracts.find(c => String(c.id) === f.contractId).contractor_name : f.cat === 'SUPPLIER' ? names.suppliers.find(x => String(x.id) === f.supplierId).name : f.name.trim() }));
        location.hash = '#/done';
      } catch (e) {
        saving = false; $('save').disabled = false; $('save').firstChild.textContent = `💾 ${L('खर्च सेव करें', 'SAVE EXPENSE')} `;
        $('bottom').innerHTML = errBox(serverMsg(e) || friendly(e, MSG.noPermissionAdd));
        $('bottom').scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    };
  }

  function screenDone() {
    chrome('add');
    let info = null;
    try { info = JSON.parse(sessionStorage.getItem('justSaved')); sessionStorage.removeItem('justSaved'); } catch (e) { /* ignore */ }
    $view.innerHTML = `
      <div class="success"><div class="tick">✅</div><h1>${MSG.saved}</h1>
        ${info ? `<div class="card"><div class="amount">${money(info.amount)}</div><div class="muted">${esc(info.who)}</div></div>` : ''}</div>
      <a class="btn green big" href="#/add">➕ ${L('एक और खर्च डालें', 'Add Another')}</a>
      ${can('canViewExpenses') ? `<a class="btn line" href="#/list">📋 ${L('खर्च की लिस्ट', 'Expense List')}</a>` : ''}
      <a class="btn line" href="#/home">🏠 ${L('होम पर जाएँ', 'Home')}</a>`;
  }

  // ----- Supplier bills: a separate action on an expense that is already saved (never part of Save Expense) -----
  const BILL_EXT = ['jpg', 'jpeg', 'png', 'pdf'], BILL_MAX = 5 * 1024 * 1024;   // the server enforces the same rules
  const billMsgs = {                                                             // shown under a card after an action
    get 403() { return L('आपको इसकी अनुमति नहीं है.', 'You do not have permission.'); },
    get 404() { return L('बिल नहीं मिला.', 'Bill not found.'); },
    get 409() { return L('इस खर्च पर बिल पहले से लगा है.', 'This expense already has a bill.'); },
    get 502() { return L('बिल सेव नहीं हो सका। कृपया दोबारा कोशिश करें.', 'Could not store the bill. Try again.'); },
    get 503() { return L('बिल स्टोरेज अभी चालू नहीं है। कृपया एडमिन से संपर्क करें.', 'Bill storage is not set up yet. Please contact the administrator.'); },
  };
  const billErr = e => (e && e.kind === 'network' ? MSG.network : e && e.status === 400 && serverMsg(e) ? serverMsg(e) : billMsgs[e && e.status] || MSG.problem);
  function billFileProblem(file) {
    if (!BILL_EXT.includes((file.name.split('.').pop() || '').toLowerCase())) return L('सिर्फ़ JPG, PNG या PDF फ़ाइल चुनिए.', 'Only JPG, PNG or PDF files are allowed.');
    if (!file.size) return L('फ़ाइल खाली है.', 'The file is empty.');
    if (file.size > BILL_MAX) return L('फ़ाइल 5 MB से बड़ी है.', 'The file is larger than 5 MB.');
    return '';
  }
  // The shared api() sends JSON; a file needs multipart, so this is its own small call (same auth and error handling).
  async function uploadBill(id, file) {
    const fd = new FormData();
    fd.append('file', file, file.name);
    let res;
    try {
      res = await fetch(API + `expense-transactions/${id}/bill/`, { method: 'POST', headers: { Accept: 'application/json', Authorization: 'Token ' + state.token }, body: fd });
    } catch (e) { console.error('network error', e); throw new AppError('network'); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body is fine */ }
    if (res.status === 401) { logoutLocal(); location.hash = '#/login'; throw new AppError('auth', 401); }
    if (!res.ok) { console.error('server error', 'bill upload', res.status, data); throw new AppError('server', res.status, data); }
    return data;
  }
  // The signed link is short-lived and never stored. The tab is opened inside the tap so the browser allows it.
  async function openBill(id, box) {
    const tab = window.open('', '_blank');
    if (tab) { try { tab.opener = null; tab.document.write(`<p style="font:20px sans-serif;padding:24px">⏳ ${L('बिल खुल रहा है...', 'Opening bill...')}</p>`); } catch (e) { /* ignore */ } }
    try {
      const r = await api(`expense-transactions/${id}/bill/`);
      if (tab) tab.location.href = r.url;
      else box.innerHTML = `<a class="btn line" href="${esc(r.url)}" target="_blank" rel="noopener">📄 ${L('बिल खोलें', 'Open bill')}</a>`;
    } catch (e) {
      if (tab) tab.close();
      box.innerHTML = errBox(billErr(e));
    }
  }

  // ----- Expenses list -----
  let listState = { cat: '', shown: 20, range: 'month', from: '', to: '' };

  // The register is paginated (max 500 per page): follow every page so the history is never cut short.
  // An optional person filter (labour / supplier / contractor / contractor_contract) narrows it on the server.
  async function allRegisterRows(params) {
    const q = new URLSearchParams({ project: state.project.id, page_size: 500 });
    ['labour', 'supplier', 'contractor', 'contractor_contract'].forEach(k => { if (params.get(k)) q.set(k, params.get(k)); });
    let rows = [];
    for (let page = 1; ; page++) {
      q.set('page', page);
      const res = await api(`reports/payment-register/?${q}`);
      if (!res || !res.results) return res || rows;
      rows = rows.concat(res.results);
      if (!res.next) return rows;
    }
  }

  // Edit form for one saved expense. Only date, amount, mode, reference and notes can change; the server checks
  // the permission and refuses cancelled or manager-fund expenses.
  function editExpense(row, card, redraw) {
    const isMisc = row.expense_category === 'MISCELLANEOUS', isSupplier = row.expense_category === 'SUPPLIER';
    card.querySelector('.row-actions').hidden = true;
    const box = card.querySelector('.act-msg');
    box.innerHTML = `<form class="edit-form" novalidate>
      <label>${L('तारीख', 'Date')}</label><input name="expense_date" type="date" value="${esc(row.expense_date)}">
      <label>${L('रकम', 'Amount')}</label><input name="amount" type="number" inputmode="decimal" step="0.01" min="0.01" value="${esc(row.amount)}">
      <label>${L('कैसे दिया', 'Payment mode')}</label>
      <select name="payment_mode">${MODES.map(m => `<option value="${m.key}" ${m.key === row.payment_mode ? 'selected' : ''}>${modeLabel(m)}</option>`).join('')}</select>
      ${isMisc ? `<label>${L('किसे दिया', 'Paid to')}</label><input name="payee_name" type="text" value="${esc(row.payee_name || '')}">` : ''}
      ${isSupplier ? `<label>${L('सामान', 'Material')}</label><input name="description" type="text" value="${esc(row.description || '')}">` : ''}
      <label>${L('रेफ़रेंस नंबर', 'Reference no')}</label><input name="reference_no" type="text" value="${esc(row.reference_no || '')}">
      <label>${L('नोट', 'Remarks')}</label><input name="remarks" type="text" value="${esc(row.remarks || '')}">
      <div class="edit-msg"></div>
      <button class="btn green" type="submit">💾 ${L('सेव करें', 'SAVE')}</button>
      <button class="btn line" type="button" data-cancel>${L('रद्द करें', 'Cancel')}</button></form>`;
    const f = box.querySelector('form'), msg = f.querySelector('.edit-msg');
    f.querySelector('[data-cancel]').onclick = () => { box.innerHTML = ''; card.querySelector('.row-actions').hidden = false; };
    f.onsubmit = async ev => {
      ev.preventDefault();
      const v = n => f.elements[n] && f.elements[n].value.trim();
      if (!(Number(v('amount')) > 0)) { msg.innerHTML = errBox(L('सही रकम भरें.', 'Please enter a valid amount.')); return; }
      if (!v('expense_date')) { msg.innerHTML = errBox(L('तारीख चुनें.', 'Please choose a date.')); return; }
      const body = { expense_date: v('expense_date'), amount: v('amount'), payment_mode: v('payment_mode'),
        reference_no: v('reference_no') || null, remarks: v('remarks') || null };
      if (isMisc) body.payee_name = v('payee_name');
      if (isSupplier) body.description = v('description') || null;
      const save = f.querySelector('button[type=submit]'); save.disabled = true;
      try {
        Object.assign(row, await api(`expense-transactions/${row.id}/`, { method: 'PATCH', body }));
        redraw();
      } catch (e) { save.disabled = false; msg.innerHTML = errBox(serverMsg(e) || friendly(e)); }
    };
  }

  async function screenList(params) {
    chrome('list', '#/home');
    if (!state.project) { $view.innerHTML = noProject(); return; }
    const personKeyInit = ['labour', 'supplier', 'contractor', 'contractor_contract'].find(k => params.get(k));
    // A single person's history link ("View Expenses" from a Labour/Supplier/Contractor report) means
    // their full account, not this month only -- so it starts unfiltered by date; the ordinary Expense
    // List (no person filter) starts on "This month" per the date chips below.
    listState = { cat: params.get('cat') || '', shown: 20, range: personKeyInit ? 'all' : 'month', from: '', to: '' };
    loading();
    let rows, names;
    try {
      [names, rows] = await Promise.all([
        loadNames(true),
        allRegisterRows(params),
      ]);
    } catch (e) { $view.innerHTML = errBox(friendly(e, MSG.noPermissionView)); return; }
    const seen = new Set(viewableCats().map(c => c.key));
    rows = rows.filter(r => seen.has(r.expense_category));
    if (!seen.has(listState.cat)) listState.cat = '';

    const personKey = ['labour', 'supplier', 'contractor', 'contractor_contract'].find(k => params.get(k));
    const personNote = personKey ? `<div class="msg info">${L('एक व्यक्ति का पूरा हिसाब', 'Showing one person only')} · <a href="#/list">${L('सब देखें', 'Show all')}</a></div>` : '';

    const nameOf = r => {
      if (r.labour) return (names.labour.find(x => x.id === r.labour) || {}).name;
      if (r.supplier) return (names.suppliers.find(x => x.id === r.supplier) || {}).name;
      if (r.contractor_contract) return (names.contracts.find(x => x.id === r.contractor_contract) || {}).contractor_name;
      return r.payee_name;
    };

    // Upload / View Bill: supplier expenses only, and only what the SERVER allows (from /me/).
    const billNote = {};                              // id -> message shown under that card
    const billActions = r => {
      if (r.expense_category !== 'SUPPLIER') return '';
      let btn = '';
      if (r.has_bill) {
        if (serverOnly('canViewBill')) btn = `<button type="button" class="btn line bill-view" data-id="${r.id}">👁 View Bill <span class="sub">${esc(r.bill_filename || '')}</span></button>`;
      } else if (r.status === 'ACTIVE' && serverOnly('canUploadBill')) {
        btn = `<button type="button" class="btn line bill-up" data-id="${r.id}">📎 Upload Bill <span class="sub">JPG · PNG · PDF</span></button>
          <input type="file" class="bill-file" data-id="${r.id}" accept="image/jpeg,image/png,application/pdf,.jpg,.jpeg,.png,.pdf" hidden>`;
      }
      return btn || billNote[r.id] ? `<div class="row-actions bill-actions">${btn}</div><div class="bill-msg" data-id="${r.id}">${billNote[r.id] || ''}</div>` : '';
    };

    function draw() {
      const range = listState.range === 'week' ? mdWeekRange() : mdMonthRange();
      const from = listState.range === 'custom' ? (listState.from || range.from) : range.from;
      const to = listState.range === 'custom' ? (listState.to || range.to) : range.to;
      const shown = rows.filter(r => (!listState.cat || r.expense_category === listState.cat)
        && (listState.range === 'all' || (r.expense_date >= from && r.expense_date <= to)));
      const total = shown.reduce((s, r) => s + Number(r.amount), 0);
      const chip = (key, label) => `<button type="button" class="choice" data-c="${key}" aria-pressed="${listState.cat === key}">${label}</button>`;
      const rangeChip = (key, label) => `<button type="button" class="choice" data-r="${key}" aria-pressed="${listState.range === key}">${label}</button>`;

      const visible = shown.slice(0, listState.shown);
      const groups = {};
      visible.forEach(r => { (groups[r.expense_date] = groups[r.expense_date] || []).push(r); });
      const dates = Object.keys(groups).sort().reverse();
      const rowCard = r => `
          <div class="card item" data-id="${r.id}">
            <div class="row"><span class="who">${esc(nameOf(r) || '—')}</span><span class="amount">${money(r.amount)}</span></div>
            <div class="meta">${CAT[r.expense_category].icon} ${catLabel(CAT[r.expense_category])}${r.expense_category === 'MISCELLANEOUS' && r.expense_type !== 'Other' ? ' · ' + esc(r.expense_type) : ''}</div>
            ${r.expense_category === 'SUPPLIER' && r.description ? `<div class="note">🧱 ${esc(r.description)}</div>` : ''}
            ${r.remarks ? `<div class="note">📝 ${esc(r.remarks)}</div>` : ''}
            ${billActions(r)}
            ${can('canEditExpense') || can('canDeleteExpense') ? `<div class="row-actions">${can('canEditExpense') ? `<button type="button" class="btn line act act-edit">✏️ ${L('बदलें', 'Edit')}</button>` : ''}${can('canDeleteExpense') ? `<button type="button" class="btn line danger act act-del">🗑 ${L('हटाएँ', 'Delete')}</button>` : ''}</div>` : ''}
            <div class="act-msg"></div>
          </div>`;
      const dayGroup = d => {
        const list = groups[d];
        const dayTotal = list.reduce((s, r) => s + Number(r.amount), 0);
        return `<div class="list-day"><div class="row list-day-head"><span>${niceDate(d)}</span><span class="amount">${money(dayTotal)}</span></div>${list.map(rowCard).join('')}</div>`;
      };
      $view.innerHTML = `
        <h1>📋 ${L('खर्च की लिस्ट', 'Expense List')}</h1>
        <p class="muted">${L('हर खर्च अलग-अलग यहाँ दिखता है', 'Every expense, one by one')}</p>
        ${personNote}
        <div class="chips">${chip('', L('सब', 'All'))}${viewableCats().map(c => chip(c.key, `${c.icon} ${catLabel(c)}`)).join('')}</div>
        ${personKeyInit ? '' : `<div class="chips" id="list-range-chips">${rangeChip('week', I18n.t('thisWeek'))}${rangeChip('month', I18n.t('thisMonth'))}${rangeChip('custom', I18n.t('customRange'))}</div>
        ${listState.range === 'custom' ? `<div class="step" id="list-custom">
          <label for="list-from">${I18n.t('fromDate')}</label><input id="list-from" type="date" value="${listState.from || range.from}">
          <label for="list-to">${I18n.t('toDate')}</label><input id="list-to" type="date" value="${listState.to || range.to}">
        </div>` : ''}`}
        <div class="card"><div class="row"><span>${L('कुल खर्च', 'Total')}</span><span class="amount">${money(total)}</span></div></div>` +
        (dates.length ? dates.map(dayGroup).join('') : `<div class="empty"><div class="ico">📭</div><p>${L('अभी कोई खर्च नहीं है.', 'No expenses yet.')}</p>${canAdd() ? `<a class="btn green" href="#/add">➕ ${L('खर्च डालें', 'Add Expense')}</a>` : ''}</div>`) +
        (shown.length > listState.shown ? `<button type="button" class="btn line" id="more">⬇ ${L('और दिखाएँ', 'Show more')}</button>` : '');
      const rangeChips = document.getElementById('list-range-chips');
      if (rangeChips) rangeChips.querySelectorAll('[data-r]').forEach(b => b.onclick = () => { listState.range = b.dataset.r; listState.shown = 20; draw(); });
      const listFrom = document.getElementById('list-from'), listTo = document.getElementById('list-to');
      if (listFrom && listTo) {
        const applyCustom = () => { listState.from = listFrom.value; listState.to = listTo.value; listState.shown = 20; draw(); };
        listFrom.onchange = applyCustom; listTo.onchange = applyCustom;
      }
      const rowOf = b => rows.find(x => x.id === Number(b.closest('.card').dataset.id));
      $view.querySelectorAll('.act-del').forEach(b => b.onclick = () => {
        const row = rowOf(b), box = b.closest('.card').querySelector('.act-msg');
        if (!row) return;
        confirmBox(L('यह खर्च हटाएँ?', 'Delete this expense?'), L('हटाएँ', 'Delete'), async () => {
          try {
            await api(`expense-transactions/${row.id}/cancel/`, { method: 'POST', body: { remarks: `Deleted by ${state.user.email || state.user.name}` } });
            rows = rows.filter(x => x.id !== row.id);
            draw();
          } catch (e) { box.innerHTML = errBox(serverMsg(e) || friendly(e)); }
        });
      });
      $view.querySelectorAll('.act-edit').forEach(b => b.onclick = () => {
        const row = rowOf(b), card = b.closest('.card');
        if (row) editExpense(row, card, draw);
      });
      const boxOf = id => $view.querySelector(`.bill-msg[data-id="${id}"]`);
      $view.querySelectorAll('.bill-up').forEach(b => b.onclick = () => b.nextElementSibling.click());     // opens the file picker / camera
      $view.querySelectorAll('.bill-file').forEach(input => input.onchange = async () => {
        const id = Number(input.dataset.id), file = input.files[0], row = rows.find(x => x.id === id), box = boxOf(id);
        input.value = '';
        if (!file || !row) return;
        const bad = billFileProblem(file);
        if (bad) { box.innerHTML = errBox(bad); return; }
        const btn = input.previousElementSibling;
        btn.disabled = true; box.innerHTML = `<div class="msg info">⏳ ${L('बिल अपलोड हो रहा है...', 'Uploading...')}</div>`;
        try {
          const saved = await uploadBill(id, file);
          row.has_bill = true; row.bill_filename = saved.bill_filename;
          billNote[id] = `<div class="msg ok" role="status">✅ ${L('बिल लग गया.', 'Bill uploaded.')}</div>`;
        } catch (e) {
          if (e.status === 409) { row.has_bill = true; billNote[id] = errBox(billErr(e)); }     // someone attached one meanwhile
          else { btn.disabled = false; box.innerHTML = errBox(billErr(e)); return; }
        }
        draw();
      });
      $view.querySelectorAll('.bill-view').forEach(b => b.onclick = () => openBill(Number(b.dataset.id), boxOf(b.dataset.id)));
      $view.querySelectorAll('[data-c]').forEach(b => b.onclick = () => { listState.cat = b.dataset.c; listState.shown = 20; draw(); });
      const more = document.getElementById('more');
      if (more) more.onclick = () => { listState.shown += 20; draw(); };
    }
    draw();
  }

  // ----- Reports -----
  async function screenReports() {
    chrome('reports', '#/home');
    if (!state.project) { $view.innerHTML = noProject(); return; }
    loading();
    let d;
    try { d = await api(`projects/${state.project.id}/dashboard/`); } catch (e) { $view.innerHTML = errBox(friendly(e, MSG.noPermissionView)); return; }
    const cats = viewableCats();
    const all = cats.length === CATS.length;
    // Everything on this screen is limited to the categories you may view.
    const total = all ? Number(d.total_expense) : cats.reduce((sum, c) => sum + Number(d.category_breakup[c.key] || 0), 0);
    const pct = v => (total > 0 ? Math.round((Number(v) / total) * 100) : 0);
    // This total is the whole project (every category, every manager/owner). A manager's own
    // fund-distribution figure (as shown on the Manager Fund screen) is a smaller subset of it,
    // not a separate number -- call that out explicitly so the two screens don't look like they disagree.
    const myFund = state.me.manager_id && (d.manager_fund_summary || []).find(f => f.manager_id === state.me.manager_id);

    $view.innerHTML = `
      <h1>📊 ${L('कुल खर्च', 'Total Expense')}</h1>
      <p class="muted">${L('जोड़ और हिसाब -- किस पर कितना खर्च हुआ', 'Totals and breakdown -- how much went where')}</p>
      <p class="muted">${L('प्रोजेक्ट', 'Project')}: <b>${esc(state.project.name)}</b></p>
      <div class="card"><div class="muted">${L('पूरे प्रोजेक्ट का कुल खर्च (सभी श्रेणी, सभी लोग)', 'Whole project total (all categories, everyone)')}</div><div class="big-total">${money(total)}</div></div>
      ${myFund ? `<div class="card"><div class="muted">${L('आपने मज़दूरों को बाँटा (Manager Fund से)', 'You distributed to labour (from your Manager Fund)')}</div><div class="big-total">${money(myFund.distributed_amount)}</div><div class="muted">${L('यह ऊपर के कुल खर्च का एक हिस्सा है, अलग नहीं', 'This is a part of the total above, not a separate figure')}</div></div>` : ''}
      <h2>${L('किस पर कितना खर्च हुआ', 'Spend by category')}</h2>
      ${cats.map(c => { const link = can(Authz.reportPermission(c.key)); return `
        <${link ? `a href="#/report/${c.key}"` : 'div'} class="card">
          <div class="row"><b>${c.icon} ${catLabel(c)}</b><span class="amount">${money(d.category_breakup[c.key])}</span></div>
          <div class="bar"><i style="width:${pct(d.category_breakup[c.key])}%"></i></div>
          <div class="muted" style="margin-top:6px">${pct(d.category_breakup[c.key])}%${link ? ' · ' + L('देखने के लिए छूइए ›', 'Tap to view ›') : ''}</div>
        </${link ? 'a' : 'div'}>`; }).join('')}
      ${all && d.owner_contribution.length ? `<h2>${L('मालिक का हिस्सा', 'Owner Share')}</h2>` + d.owner_contribution.map(o => `
        <div class="card"><div class="row"><b>${esc(o.owner_name)}</b><span class="amount">${money(o.total)}</span></div>
        <div class="muted">${L(`कुल खर्च का ${pct(o.total)}% दिया`, `Paid ${pct(o.total)}% of total expense`)}</div></div>`).join('') : ''}
      ${can('canViewContractors') && d.contractor_positions.length ? `<h2>${L('ठेकेदार का हिसाब', 'Contractor Statement')}</h2>` + d.contractor_positions.map(c => {
        const bal = Number(c.balance);
        return `<div class="card"><b>${esc(c.contractor_name)}</b>
          ${c.work_description ? `<div class="muted">${esc(c.work_description)}</div>` : ''}
          <div class="row"><span class="muted">${L('पूरा काम', 'Contract')}</span><span>${money(c.contract_amount)}</span></div>
          <div class="row"><span class="muted">${L('अब तक दिया', 'Paid')}</span><span>${money(c.paid_amount)}</span></div>
          <div class="row"><b>${bal < 0 ? L('ज़्यादा दिया', 'Overpaid') : L('देना बाकी', 'Balance due')}</b><span class="pill ${bal < 0 ? 'warn' : ''}" style="font-size:1.1rem">${money(Math.abs(bal))}</span></div></div>`;
      }).join('') : ''}`;
  }

  async function screenReportOne(key) {
    const c = CAT[key];
    if (!c || !state.project) { location.hash = '#/reports'; return; }
    chrome({ LABOUR: 'labour', SUPPLIER: 'suppliers', CONTRACTOR: 'contractors' }[key] || 'reports', '#/reports');
    loading();
    const q = `?project=${state.project.id}`;
    // "View Expenses" for one person: opens the full server-side history filtered to them.
    const viewLink = filter => (can('canViewExpenses') ? `<a class="btn line" href="#/list?${filter}" style="margin-top:8px">📋 ${L('खर्च देखें', 'View Expenses')}</a>` : '');
    try {
      let rows, total;
      if (key === 'LABOUR') { const r = await api('reports/labour/' + q); rows = r.labour.map(x => [x.labour_name, x.total_paid, `labour=${x.labour_id}`]); total = r.grand_total; }
      else if (key === 'SUPPLIER') { const r = await api('reports/supplier/' + q); rows = r.suppliers.map(x => [x.supplier_name, x.total_paid, `supplier=${x.supplier_id}`]); total = r.grand_total; }
      else if (key === 'CONTRACTOR') {
        // One card per contract, so a contractor with several contracts shows each one.
        const r = await api('reports/contractor/' + q);
        total = r.contracts.reduce((s, x) => s + Number(x.paid_amount), 0);
        rows = r.contracts.map(x => {
          const bal = Number(x.balance);
          return `<b>${esc(x.contractor_name)}</b>
            ${x.work_description ? `<div class="muted">${esc(x.work_description)}</div>` : ''}
            <div class="row"><span class="muted">${L('पूरा काम', 'Contract')}</span><span>${money(x.contract_amount)}</span></div>
            <div class="row"><span class="muted">${L('अब तक दिया', 'Paid')}</span><span>${money(x.paid_amount)}</span></div>
            <div class="row"><b>${bal < 0 ? L('ज़्यादा दिया', 'Overpaid') : L('देना बाकी', 'Balance due')}</b><span class="pill ${bal < 0 ? 'warn' : ''}" style="font-size:1.1rem">${money(Math.abs(bal))}</span></div>
            ${viewLink(`contractor_contract=${x.contract_id}`)}`;
        });
      }
      else { const r = await api('reports/misc/' + q); rows = r.expense_types.map(x => [x.expense_type, x.total]); total = r.grand_total; }
      $view.innerHTML = `
        <h1>${c.icon} ${catLabel(c)}</h1>
        <div class="card"><div class="muted">${L('कुल खर्च', 'Total')}</div><div class="big-total">${money(total)}</div></div>` +
        (rows.length ? rows.map(row => `<div class="card">${key === 'CONTRACTOR' ? row : `<div class="row"><b>${esc(row[0])}</b><span class="amount">${money(row[1])}</span></div>${row[2] ? viewLink(row[2]) : ''}`}</div>`).join('')
          : `<div class="empty"><div class="ico">📭</div>${L('अभी कोई खर्च नहीं है.', 'No expenses yet.')}</div>`) +
        (can('canViewExpenses') ? `<a class="btn line" href="#/list?cat=${key}">📋 ${L('सारे खर्च देखें', 'View all')}</a>` : '');
    } catch (e) { $view.innerHTML = errBox(friendly(e, MSG.noPermissionView)); }
  }

  // ---------- management screens (users, members, passwords): all stored on the server ----------
  const roleName = r => Authz.ROLE_LABEL[r] || '—';
  const WEB_ROLE = { ADMIN: Authz.ROLES.SUPER_ADMIN, OWNER: Authz.ROLES.OWNER, MANAGER: Authz.ROLES.MANAGER };
  const projName = id => (state.realProjects.find(p => p.id === id) || {}).name || '';
  // The server says who may be reset (can_reset); it checks again when the password is sent.
  const asPerson = u => ({ id: u.id, name: u.name, email: u.username, role: WEB_ROLE[u.role] || null, canReset: !!u.can_reset,
    allProjects: u.role === 'ADMIN', projects: u.projects || [] });
  // Everyone the reset screen may need: all users, or (without Manage Users) the members of the current project.
  async function loadPeople() {
    if (can('canManageUsers')) return (await api('users/')).map(asPerson);
    if (!state.project) return [];
    const d = await api(`projects/${state.project.id}/members/`);
    return d.members.map(asPerson);
  }
  const passwordFields = (withOld) => `
    ${withOld ? `<label for="pw0">${L('पुराना पासवर्ड', 'Current password')}</label><input id="pw0" type="password" autocomplete="current-password">` : ''}
    <label for="pw1">${L('नया पासवर्ड', 'New password (min 8)')}</label><input id="pw1" type="password" autocomplete="new-password">
    <label for="pw2">${L('नया पासवर्ड दोबारा', 'Confirm')}</label><input id="pw2" type="password" autocomplete="new-password">`;
  // Checks the typed passwords, then sends them (over HTTPS) to `send`; the server stores only the hash.
  function passwordForm(formId, withOld, send) {
    const f = document.getElementById(formId), $ = id => document.getElementById(id);
    f.onsubmit = async ev => {
      ev.preventDefault();
      const out = $('pwmsg');
      if (withOld && !$('pw0').value) { out.innerHTML = errBox(L('कृपया पुराना पासवर्ड भरें.', 'Please enter your current password.')); return; }
      if ($('pw1').value.length < 8) { out.innerHTML = errBox(L('नया पासवर्ड कम से कम 8 अक्षर का हो.', 'The new password must be at least 8 characters.')); return; }
      if ($('pw1').value !== $('pw2').value) { out.innerHTML = errBox(L('दोनों पासवर्ड एक जैसे नहीं हैं.', 'The two passwords do not match.')); return; }
      const btn = f.querySelector('button[type=submit]'); btn.disabled = true;
      try {
        await send(withOld ? $('pw0').value : null, $('pw1').value);
        f.reset();
        out.innerHTML = `<div class="msg ok" role="status">✅ ${L('पासवर्ड बदल गया.', 'Password changed.')}</div>`;
      } catch (e) { out.innerHTML = errBox(serverMsg(e) || friendly(e)); }
      btn.disabled = false;
    };
  }

  function screenProfile() {
    chrome('profile', '#/home');
    const role = (state.project && Authz.roleIn(state.user, state.project.id)) || state.user.role;
    $view.innerHTML = `<h1>🔑 ${L('पासवर्ड बदलें', 'Change Password')}</h1>
      <div class="card"><b>${esc(state.user.name)}</b><div class="muted">${esc(roleName(role))}${state.user.email ? ' · ' + esc(state.user.email) : ''}</div></div>
      <div id="pwmsg"></div>
      <form id="pwf" novalidate>${passwordFields(true)}<button class="btn green" type="submit">💾 ${L('पासवर्ड बदलें', 'CHANGE PASSWORD')}</button></form>`;
    passwordForm('pwf', true, async (old, pw) => {
      const res = await api('auth/change-password/', { method: 'POST', body: { old_password: old, new_password: pw } });
      state.token = res.token; store.set('token', res.token);    // the server signed out every other session
    });
  }

  async function screenResetPassword(id) {
    // Only reachable from the Users screen or a project's Members section in Settings, both
    // super-admin only, so canManageUsers is always true here.
    chrome('users', '#/users');
    loading();
    let target;
    try { target = (await loadPeople()).find(u => String(u.id) === String(id)); } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
    if (!target || !target.canReset) { location.hash = '#/home'; return; }
    $view.innerHTML = `<h1>🔑 ${L('पासवर्ड रीसेट', 'Reset Password')}</h1>
      <div class="card"><b>${esc(target.name)}</b><div class="muted">${esc(roleName(target.role))}${target.email ? ' · ' + esc(target.email) : ''}</div></div>
      <div id="pwmsg"></div>
      <form id="pwf" novalidate>${passwordFields(false)}<button class="btn green" type="submit">💾 ${L('नया पासवर्ड सेट करें', 'RESET PASSWORD')}</button></form>`;
    passwordForm('pwf', false, (_, pw) => api(`users/${target.id}/reset-password/`, { method: 'POST', body: { new_password: pw } }));
  }

  let userNote = '';
  async function screenUsers() {
    chrome('users', '#/home');
    loading();
    let list;
    try { list = (await api('users/')).map(asPerson); } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
    $view.innerHTML = `<h1>👥 ${L('यूज़र', 'Users')}</h1>` +
      (state.me.is_super_admin ? `<h2>➕ ${L('नया यूज़र', 'Create User')}</h2>
        <div class="card"><div id="cumsg">${userNote}</div>
          <form id="cuf" novalidate>
            <label for="cu-name">${L('नाम', 'Name')}</label><input id="cu-name" type="text" autocomplete="off">
            <label for="cu-user">${L('यूज़र नाम / ईमेल', 'Username or email')}</label><input id="cu-user" type="text" autocomplete="off" autocapitalize="none">
            <label for="cu-mobile">${L('मोबाइल', 'Mobile')}</label><input id="cu-mobile" type="tel" inputmode="tel" autocomplete="off">
            <label for="cu-role">${L('भूमिका', 'Role')}</label>
            <select id="cu-role"><option value="OWNER">OWNER</option><option value="MANAGER" selected>MANAGER</option></select>
            <label for="cu-pw1">${L('पासवर्ड', 'Password (min 8)')}</label><input id="cu-pw1" type="password" autocomplete="new-password">
            <label for="cu-pw2">${L('पासवर्ड दोबारा', 'Confirm password')}</label><input id="cu-pw2" type="password" autocomplete="new-password">
            <div class="muted">${L("प्रोजेक्ट बाद में हर प्रोजेक्ट के सदस्य पेज से जोड़ें.", "Add projects later from each project's Members screen.")}</div>
            <button class="btn green" type="submit">➕ ${L('यूज़र बनाएँ', 'CREATE USER')}</button></form></div>` : '') +
      list.map(u => `<div class="card"><div class="row"><b>${esc(u.name)}</b><span class="pill">${esc(roleName(u.role))}</span></div>
        <div class="muted">${esc(u.email)}</div>
        <div class="muted">${u.allProjects ? L('सारे प्रोजेक्ट', 'All projects') : u.projects.length ? u.projects.map(a => esc(projName(a.project_id)) + ' (' + roleName(WEB_ROLE[a.role]) + ')').join(', ') : L('कोई प्रोजेक्ट नहीं', 'No projects')}</div>
        ${u.canReset ? `<a class="btn line" href="#/resetpw/${esc(u.id)}" style="min-height:52px;font-size:1rem">🔑 ${L('पासवर्ड रीसेट', 'Reset Password')}</a>` : ''}</div>`).join('');
    const f = document.getElementById('cuf');
    if (f) f.onsubmit = async ev => {
      ev.preventDefault();
      const v = id => document.getElementById(id).value.trim(), out = document.getElementById('cumsg');
      if (!v('cu-name') || !v('cu-user')) { out.innerHTML = errBox(L('नाम और यूज़र नाम भरें.', 'Enter a name and username.')); return; }
      if (document.getElementById('cu-pw1').value.length < 8) { out.innerHTML = errBox(L('पासवर्ड कम से कम 8 अक्षर का हो.', 'The password must be at least 8 characters.')); return; }
      if (document.getElementById('cu-pw1').value !== document.getElementById('cu-pw2').value) { out.innerHTML = errBox(L('दोनों पासवर्ड एक जैसे नहीं हैं.', 'The two passwords do not match.')); return; }
      f.querySelector('button[type=submit]').disabled = true;
      try {
        await api('users/', { method: 'POST', body: { name: v('cu-name'), username: v('cu-user'), mobile: v('cu-mobile'), role: v('cu-role'),
          password: document.getElementById('cu-pw1').value, confirm_password: document.getElementById('cu-pw2').value } });
        userNote = `<div class="msg ok" role="status">✅ ${L('यूज़र बन गया.', 'User created.')}</div>`;
        screenUsers();
      } catch (e) {
        f.querySelector('button[type=submit]').disabled = false;
        out.innerHTML = errBox(serverMsg(e) || friendly(e));
      }
    };
    userNote = '';
  }

  // ================= Settings (super admin only): Projects | Users & access =================
  // Reached only via the top-right user menu (renderUserbar), never the bottom nav -- see NAV in
  // authz.js. "Users & access" is the existing screens at #/access/* (unchanged); this tab bar just
  // links out to them so both live under one Settings shell.
  const PROJECT_STATUSES = ['PLANNED', 'ONGOING', 'COMPLETED', 'ARCHIVED'];
  const settingsTabs = active => `<div class="ac-tabs">
    <a href="#/settings" class="${active === 'projects' ? 'on' : ''}">🏗️ ${esc(I18n.t('projectsTab'))}</a>
    <a href="#/access" class="${active === 'access' ? 'on' : ''}">🔐 ${esc(I18n.t('usersAccessTab'))}</a>
  </div>`;

  function screenSettings(arg) {
    if (arg === 'new') return screenProjectForm(null);
    if (arg && /^\d+$/.test(arg)) return screenProjectForm(Number(arg));
    return screenProjectsList();
  }

  async function screenProjectsList() {
    chrome('settings', '#/home');
    loading();
    let projects;
    try { projects = await api('projects/'); projects = projects.results || projects; }
    catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
    $view.innerHTML = `<h1>⚙️ ${esc(I18n.t('settings'))}</h1>` + settingsTabs('projects') +
      `<a class="btn green" href="#/settings/new">➕ ${esc(I18n.t('newProject'))}</a>` +
      (projects.length ? `<div style="overflow-x:auto"><table class="proj-table"><thead><tr>
          <th>${esc(I18n.t('projectCode'))}</th><th>${esc(I18n.t('projectName'))}</th><th>${esc(I18n.t('location'))}</th>
          <th>${esc(I18n.t('status'))}</th><th>${esc(I18n.t('membersCount'))}</th></tr></thead><tbody>
        ${projects.map(p => `<tr data-id="${p.id}" tabindex="0">
            <td data-label="${esc(I18n.t('projectCode'))}">${esc(p.code)}</td>
            <td data-label="${esc(I18n.t('projectName'))}"><b>${esc(p.name)}</b></td>
            <td data-label="${esc(I18n.t('location'))}">${esc(p.location || '—')}</td>
            <td data-label="${esc(I18n.t('status'))}"><span class="status-pill ${esc(p.status)}">${esc(I18n.t(STATUS_KEY[p.status] || p.status))}</span></td>
            <td data-label="${esc(I18n.t('membersCount'))}">${Number(p.member_count || 0)}</td>
          </tr>`).join('')}</tbody></table></div>`
        : `<div class="empty"><div class="ico">🏗️</div><p>${esc(I18n.t('noProjectsYet'))}</p></div>`);
    $view.querySelectorAll('tr[data-id]').forEach(tr => {
      const go = () => { location.hash = `#/settings/${tr.dataset.id}`; };
      tr.onclick = go;
      tr.onkeydown = e => { if (e.key === 'Enter') go(); };
    });
  }

  async function screenProjectForm(id) {
    chrome('settings', '#/settings');
    const isNew = id == null;
    let p = null, membersData = null, candidates = [];
    if (!isNew) {
      loading();
      try {
        p = await api(`projects/${id}/`);
        [membersData, candidates] = await Promise.all([api(`projects/${id}/members/`), api(`users/?project=${id}`)]);
      } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
    }
    let note = '';

    const membersSection = () => {
      const members = membersData.members.map(m => ({ ...asPerson(m), role: WEB_ROLE[m.role] }));
      return `<h2>${esc(I18n.t('projectMembers'))}</h2><div id="pmmsg">${note}</div>` +
        membersData.admins.map(u => `<div class="card"><div class="row"><b>${esc(u.name)}</b><span class="pill">${esc(roleName(Authz.ROLES.SUPER_ADMIN))}</span></div></div>`).join('') +
        (members.map(u => `<div class="card"><div class="row"><b>${esc(u.name)}</b><span class="pill">${esc(roleName(u.role))}</span></div>
            <div class="muted">${esc(u.email)}</div>
            <label for="mr-${u.id}">${esc(I18n.t('role'))}</label>
            <select id="mr-${u.id}" class="m-role" data-id="${u.id}" data-name="${esc(u.name)}">
              <option value="OWNER" ${u.role === Authz.ROLES.OWNER ? 'selected' : ''}>OWNER</option>
              <option value="MANAGER" ${u.role === Authz.ROLES.MANAGER ? 'selected' : ''}>MANAGER</option>
            </select>
            <button type="button" class="btn line m-remove" data-id="${u.id}" data-name="${esc(u.name)}" style="min-height:52px;font-size:1rem">➖ ${esc(I18n.t('remove'))}</button>
            ${u.canReset ? `<a class="btn line" href="#/resetpw/${esc(u.id)}" style="min-height:52px;font-size:1rem">🔑 ${esc(I18n.t('changePassword'))}</a>` : ''}</div>`
          ).join('') || `<div class="empty">${esc(I18n.t('noMembersYet'))}</div>`) +
        `<h3>${esc(I18n.t('addMember'))}</h3>
        <form id="addm" novalidate><label for="m-user">${esc(I18n.t('selectUser'))}</label>
          <select id="m-user"><option value="">—</option>${candidates.map(u => `<option value="${u.id}">${esc(u.name)} · ${esc(u.username)} · ${esc(roleName(WEB_ROLE[u.role]))}</option>`).join('')}</select>
          <label for="m-role">${esc(I18n.t('role'))}</label>
          <select id="m-role" disabled><option value="OWNER">OWNER</option><option value="MANAGER">MANAGER</option></select>
          <button class="btn green" type="submit">➕ ${esc(I18n.t('addMember'))}</button></form>`;
    };

    const draw = () => {
      $view.innerHTML = `<h1>⚙️ ${esc(I18n.t('settings'))}</h1>` + settingsTabs('projects') +
        `<h2>${isNew ? esc(I18n.t('newProject')) : esc(p.name)}</h2>
        <div id="pfmsg"></div>
        <form id="pf" novalidate>
          <label for="pf-name">${esc(I18n.t('projectName'))} *</label><input id="pf-name" type="text" value="${esc(isNew ? '' : p.name)}">
          ${isNew
            ? `<label for="pf-code">${esc(I18n.t('projectCode'))}</label><input id="pf-code" type="text" autocapitalize="characters"><div class="muted">${esc(I18n.t('codeAutoHint'))}</div>`
            : `<label>${esc(I18n.t('projectCode'))}</label><div class="muted">${esc(p.code)}</div>`}
          <label for="pf-loc">${esc(I18n.t('location'))}</label><input id="pf-loc" type="text" value="${esc(isNew ? '' : (p.location || ''))}">
          <label for="pf-plot">${esc(I18n.t('plotSize'))}</label><input id="pf-plot" type="text" value="${esc(isNew ? '' : (p.plot_size || ''))}">
          ${isNew ? '' : `
          <label for="pf-status">${esc(I18n.t('status'))}</label>
          <select id="pf-status">${PROJECT_STATUSES.map(k => `<option value="${k}" ${k === p.status ? 'selected' : ''}>${esc(I18n.t(STATUS_KEY[k]))}</option>`).join('')}</select>
          <label for="pf-start">${esc(I18n.t('startDate'))}</label><input id="pf-start" type="date" value="${esc(p.start_date || '')}">
          <label for="pf-end">${esc(I18n.t('expectedCompletion'))}</label><input id="pf-end" type="date" value="${esc(p.expected_completion_date || '')}">
          <label for="pf-rem">${esc(I18n.t('remarks'))}</label><input id="pf-rem" type="text" value="${esc(p.remarks || '')}">`}
          <button class="btn green" type="submit">💾 ${esc(I18n.t(isNew ? 'createProject' : 'save'))}</button>
        </form>` +
        (isNew ? '' : membersSection());
      bindForm();
      if (!isNew) bindMembers();
    };

    function bindForm() {
      const f = document.getElementById('pf');
      let saving = false;
      f.onsubmit = async ev => {
        ev.preventDefault();
        if (saving) return;
        const v = fid => document.getElementById(fid).value.trim();
        const out = document.getElementById('pfmsg');
        if (!v('pf-name')) { out.innerHTML = errBox(I18n.t('nameRequired')); return; }
        saving = true;
        const btn = f.querySelector('button[type=submit]'); btn.disabled = true;
        try {
          if (isNew) {
            const body = { name: v('pf-name'), location: v('pf-loc'), plot_size: v('pf-plot') };
            const code = v('pf-code'); if (code) body.code = code.toUpperCase();
            const created = await api('projects/', { method: 'POST', body });
            state.me = null;                       // reload the project list from the server
            await loadBasics();
            location.hash = `#/settings/${created.id}`;
            return;
          }
          p = await api(`projects/${id}/`, { method: 'PATCH', body: {
            name: v('pf-name'), location: v('pf-loc'), plot_size: v('pf-plot'), status: document.getElementById('pf-status').value,
            start_date: v('pf-start') || null, expected_completion_date: v('pf-end') || null, remarks: v('pf-rem') } });
          note = `<div class="msg ok" role="status">✅ ${esc(I18n.t('saved'))}</div>`;
          draw();
        } catch (e) {
          out.innerHTML = errBox(serverMsg(e) || friendly(e));
        } finally { saving = false; }
      };
    }

    function bindMembers() {
      const url = `projects/${id}/members/`;
      const run = async (path, method, body, ok) => {
        try { await api(path, { method, body }); note = `<div class="msg ok" role="status">✅ ${esc(ok)}</div>`; }
        catch (e) { note = errBox(serverMsg(e) || friendly(e)); }
        try { [membersData, candidates] = await Promise.all([api(url), api(`users/?project=${id}`)]); } catch (e) { /* keep what is shown */ }
        draw();
      };
      $view.querySelectorAll('.m-remove').forEach(b => b.onclick = () => {
        confirmBox(I18n.t('confirmRemoveMember'), I18n.t('remove'), () => run(`${url}${b.dataset.id}/`, 'DELETE', undefined, I18n.t('memberRemoved')));
      });
      $view.querySelectorAll('.m-role').forEach(sel => sel.onchange = () => {
        if (confirm(`${sel.dataset.name} → ${sel.value}?`)) run(`${url}${sel.dataset.id}/`, 'PATCH', { role: sel.value }, I18n.t('roleChanged'));
        else draw();
      });
      const sel = document.getElementById('m-user');
      sel.onchange = () => { const u = candidates.find(x => x.id === Number(sel.value)); if (u && u.role) document.getElementById('m-role').value = u.role; };
      document.getElementById('addm').onsubmit = ev => {
        ev.preventDefault();
        const picked = candidates.find(x => x.id === Number(sel.value));
        if (!picked) { document.getElementById('pmmsg').innerHTML = errBox(I18n.t('selectUser')); return; }
        run(url, 'POST', { username: picked.username, role: picked.role || document.getElementById('m-role').value }, I18n.t('memberAdded'));
      };
    }

    draw();
  }

  // ----- Role & Permissions: edits the same matrix that can() reads (authz.js); saved on the server. -----
  let permDraft = null, permFlash = '', permRole = 'owner';       // permDraft = edits not saved yet
  const permDirty = () => !!permDraft && JSON.stringify(permDraft) !== JSON.stringify(Authz.getMatrix());
  window.addEventListener('beforeunload', e => { if (permDirty()) { e.preventDefault(); e.returnValue = ''; } });

  async function screenPermissions() {
    chrome('settings', '#/settings');
    if (!permDraft) {
      loading();
      try { const me = await api('me/'); state.me.permission_matrix = me.permission_matrix; Authz.setMatrix(me.permission_matrix); }   // latest from the server
      catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
      permDraft = Authz.getMatrix();
    }
    const flash = permFlash; permFlash = '';
    const roles = Authz.ROLE_LIST, defs = Authz.DEFINITIONS;
    const cell = (d, r) => Authz.isFixed(d.key, r)
      ? `<span class="lock">🔒 ${permDraft[d.key][r] ? 'Required' : 'OFF'}</span>`
      : `<button type="button" class="sw" role="switch" data-perm="${d.key}" data-role="${r}" aria-label="${esc(d.label)} — ${roleName(r)}"><i></i><b></b></button>`;
    const groupRows = (fn) => Authz.GROUPS.map(g => fn(g, defs.filter(d => d.group === g.id))).join('');
    $view.innerHTML = `<h1>🔐 Role & Permissions</h1>` + settingsTabs('access') + `
      <div class="msg info">Saved on the server: every phone and computer uses these permissions. Project access (which projects a person can open) is separate and is not changed here.</div>
      <div id="pmsg">${flash === 'saved' ? '<div class="msg ok" role="status">Permissions updated successfully.</div>'
        : flash === 'reset' ? '<div class="msg ok" role="status">Permissions were reset to the default configuration.</div>'
        : flash.startsWith('err:') ? errBox(flash.slice(4)) : ''}</div>
      <div class="perm-bar"><span id="pstate" class="pstate"></span>
        <button type="button" class="pbtn primary" id="psave">Save Changes</button>
        <button type="button" class="pbtn" id="pcancel">Cancel</button>
        <button type="button" class="pbtn danger" id="preset">Reset to Default</button></div>
      <div class="perm-desktop"><table class="perm"><thead><tr><th scope="col">Permission</th>${roles.map(r => `<th scope="col">${roleName(r)}</th>`).join('')}</tr></thead><tbody>
        ${groupRows((g, list) => `<tr class="grp"><th colspan="${roles.length + 1}">${esc(g.label)}</th></tr>` +
          list.map(d => `<tr><th scope="row">${esc(d.label)}</th>${roles.map(r => `<td>${cell(d, r)}</td>`).join('')}</tr>`).join(''))}
      </tbody></table></div>
      <div class="perm-mobile"><label for="prole" class="q">Role</label>
        <select id="prole">${roles.map(r => `<option value="${r}" ${r === permRole ? 'selected' : ''}>${roleName(r)}</option>`).join('')}</select>
        <div id="plist"></div></div>`;

    const drawMobile = () => {
      $('plist').innerHTML = groupRows((g, list) => `<h2>${esc(g.label)}</h2>` +
        list.map(d => `<div class="prow"><span>${esc(d.label)}</span>${cell(d, permRole)}</div>`).join(''));
      sync();
    };
    const $ = id => document.getElementById(id);
    function sync() {
      $view.querySelectorAll('.sw').forEach(b => {
        const on = permDraft[b.dataset.perm][b.dataset.role];
        b.setAttribute('aria-checked', String(on));
        b.querySelector('b').textContent = on ? 'ON' : 'OFF';
      });
      const dirty = permDirty();
      $('pstate').textContent = dirty ? '● Unsaved changes' : Authz.isDefault() ? 'Default permissions' : 'Custom permissions in use';
      $('pstate').classList.toggle('dirty', dirty);
      $('psave').disabled = !dirty; $('pcancel').disabled = !dirty;
    }
    $view.onclick = e => {
      const b = e.target.closest('.sw');
      if (!b) return;
      permDraft[b.dataset.perm][b.dataset.role] = !permDraft[b.dataset.perm][b.dataset.role];
      $('pmsg').innerHTML = '';
      sync();
    };
    $('prole').onchange = e => { permRole = e.target.value; drawMobile(); };
    const commit = async (send, ok) => {
      try { const saved = await send(); state.me.permission_matrix = saved; Authz.setMatrix(saved); permFlash = ok; permDraft = null; }
      catch (e) { permFlash = 'err:' + (e.status === 403 ? 'Only the super admin can change permissions.' : serverMsg(e) || friendly(e)); }
      screenPermissions();
    };
    $('psave').onclick = () => { $('psave').disabled = true; commit(() => api('permission-matrix/', { method: 'PUT', body: { matrix: permDraft } }), 'saved'); };
    $('pcancel').onclick = () => { permDraft = Authz.getMatrix(); $('pmsg').innerHTML = ''; screenPermissions(); };
    $('preset').onclick = () => confirmBox('Reset all role permissions to the default configuration?', 'Reset', () => {
      commit(() => api('permission-matrix/', { method: 'DELETE' }), 'reset');
    });
    drawMobile();
  }

  // ================= Settings -> Access Control (super admin only) =================
  // Three screens: landing (3 cards), User Access (person-centric), Check Access. The existing
  // Role & Permissions screen above (#/permissions) is the third card, unchanged -- it already talks
  // to the server's role-template table, which the RBAC v2 engine reads directly, so nothing there
  // needed to change.
  const ac = { catalog: null, users: null, search: '', selectedUserId: null, detail: null, checkUserId: null, checkData: null, compareAll: false };

  async function loadAcCatalog() { if (!ac.catalog) ac.catalog = await api('access/catalog/'); return ac.catalog; }
  function toast(text) {
    const t = document.createElement('div');
    t.className = 'msg ok';
    t.setAttribute('role', 'status');
    t.style.cssText = 'position:fixed;left:16px;right:16px;bottom:calc(var(--tabs-h,0px) + 16px);z-index:40;max-width:600px;margin:0 auto;';
    t.textContent = text;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 4000);
  }
  function drawer(html, onClose) {
    const box = document.createElement('div');
    box.className = 'modal';
    box.innerHTML = `<div class="modal-box" role="dialog" aria-modal="true">${html}</div>`;
    document.body.appendChild(box);
    const close = () => { box.remove(); document.removeEventListener('keydown', onKey); window.removeEventListener('hashchange', close); if (onClose) onClose(); };
    const onKey = e => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    window.addEventListener('hashchange', close);
    box.addEventListener('click', e => { if (e.target === box) close(); });
    return { box, close };
  }
  const roleLabel = name => (name || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  const initials = name => (String(name || '').trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('') || '?').toUpperCase();

  async function screenAccess(arg, params) {
    if (!can('canManagePermissions')) return screenNoAccess();
    if (arg === 'users') return screenAccessUsers(params);
    if (arg === 'check') return screenAccessCheck(params);
    return screenAccessLanding();
  }

  async function screenAccessLanding() {
    chrome('settings', '#/settings');
    loading();
    let users;
    try { users = await api('access/users/'); } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
    const superCount = users.filter(u => u.is_superadmin).length;
    $view.innerHTML = `<h1>🔐 Access Control</h1>` + settingsTabs('access') + `
      <div class="msg info">Manage what each role can do, who can open which project, and check exactly what someone can do.</div>
      <div class="ac-cards">
        <a class="ac-card" href="#/permissions"><span class="ico">🧩</span><div><h3>Roles & Permissions</h3><p>What each role can do by default.</p></div></a>
        <a class="ac-card" href="#/access/users"><span class="ico">👥</span><div><h3>User Access <span class="count">${users.length}</span></h3><p>Who can open which project, with what role.</p></div></a>
        <a class="ac-card" href="#/access/check"><span class="ico">🔍</span><div><h3>Check Access</h3><p>See exactly what a person can do${superCount ? ` — ${superCount} super admin${superCount > 1 ? 's' : ''}` : ''}.</p></div></a>
      </div>`;
  }

  // ---------------- User Access ----------------

  async function screenAccessUsers() {
    chrome('settings', '#/settings');
    loading();
    let catalog;
    try { catalog = await loadAcCatalog(); ac.users = await api('access/users/'); } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
    if (ac.selectedUserId && !ac.users.some(u => u.id === ac.selectedUserId)) ac.selectedUserId = null;
    drawAccessUsers(catalog);
  }

  function drawAccessUsers(catalog) {
    const q = ac.search.toLowerCase();
    const list = ac.users.filter(u => !q || u.name.toLowerCase().includes(q) || u.username.toLowerCase().includes(q));
    $view.innerHTML = `<h1>👥 User Access</h1>` + settingsTabs('access') + `
      <input class="ac-search" id="acq" placeholder="Search people..." value="${esc(ac.search)}" aria-label="Search people">
      <div class="ac-people" id="acPeople">${list.length ? list.map(u => `
        <button type="button" class="ac-person ${u.id === ac.selectedUserId ? 'on' : ''}" data-id="${u.id}">
          <span class="ac-avatar">${esc(initials(u.name))}</span>
          <span class="name">${esc(u.name)}</span>
          <span class="chip">${u.is_superadmin ? 'Super Admin' : u.has_global ? 'All projects' : `${u.project_count} project${u.project_count === 1 ? '' : 's'}`}</span>
        </button>`).join('') : '<div class="empty"><div class="ico">🔍</div><p>No one matches your search.</p></div>'}
      </div>
      <div id="acDetail"></div>`;
    document.getElementById('acq').oninput = e => { ac.search = e.target.value; drawAccessUsers(catalog); };
    document.querySelectorAll('#acPeople .ac-person').forEach(b => b.onclick = () => { ac.selectedUserId = Number(b.dataset.id); ac.detail = null; drawAccessUsers(catalog); loadAccessDetail(catalog); });
    if (ac.selectedUserId) { if (ac.detail) renderAccessDetail(catalog); else loadAccessDetail(catalog); }
  }

  async function loadAccessDetail(catalog) {
    document.getElementById('acDetail').innerHTML = `<div class="spinner">⏳ ${L('रुकिए...', 'Loading...')}</div>`;
    try { ac.detail = await api(`access/users/${ac.selectedUserId}/access/`); } catch (e) { document.getElementById('acDetail').innerHTML = errBox(friendly(e)); return; }
    renderAccessDetail(catalog);
  }

  function renderAccessDetail(catalog) {
    const d = ac.detail, el = document.getElementById('acDetail');
    if (!d) return;
    const projectOptions = state.realProjects;
    el.innerHTML = `<div class="card">
        <h2 style="margin-top:0">${esc(d.user.name)} <small class="muted">${esc(d.user.username)}</small></h2>
        ${d.is_superadmin ? '<div class="msg info">This person is a Super Admin: full access everywhere.</div>' : ''}
        ${d.access.length ? d.access.map(a => `
          <div class="ac-project-row" data-access="${a.id}">
            <span class="pname">${a.scope_type === 'GLOBAL' ? '🌐 All projects' : esc(a.project_name)}</span>
            <select data-role-for="${a.id}" aria-label="Role">
              ${catalog.roles.filter(r => !r.is_superadmin || a.role === 'SUPER_ADMIN').map(r => `<option value="${r.name}" ${r.name === a.role ? 'selected' : ''}>${esc(roleLabel(r.name))}</option>`).join('')}
            </select>
            <button type="button" class="ac-badge ${a.override_count ? 'changes' : 'role'}" data-special="${a.id}">⚙️ Special changes${a.override_count ? ` (${a.override_count})` : ''}</button>
            <button type="button" class="btn line" style="width:auto;min-height:40px;padding:4px 12px" data-remove="${a.id}">Remove</button>
          </div>`).join('') : '<p class="muted">No project access yet.</p>'}
        <button type="button" class="btn green" id="acAddAccess" style="margin-top:14px">➕ Add project access</button>
      </div>
      <div class="row-actions">
        <button type="button" class="btn line" id="acCopyFrom">📋 Copy access from another person</button>
        <button type="button" class="btn line" id="acSameRole">🔁 Give same role on all projects</button>
        <button type="button" class="btn danger" id="acRemoveAll">🗑 Remove from all projects</button>
      </div>`;

    el.querySelectorAll('[data-role-for]').forEach(sel => sel.onchange = async () => {
      const id = Number(sel.dataset.roleFor);
      try {
        await api(`access/users/${ac.selectedUserId}/access/${id}/`, { method: 'PATCH', body: { role: sel.value } });
        toast('Role updated.');
        await loadAccessDetail(catalog);
      } catch (e) { alert(serverMsg(e) || friendly(e)); await loadAccessDetail(catalog); }
    });
    el.querySelectorAll('[data-remove]').forEach(b => b.onclick = () => confirmBox('Remove this project access?', 'Remove', async () => {
      try { await api(`access/users/${ac.selectedUserId}/access/${b.dataset.remove}/`, { method: 'DELETE' }); toast('Removed.'); await screenAccessUsers(); }
      catch (e) { alert(serverMsg(e) || friendly(e)); }
    }));
    el.querySelectorAll('[data-special]').forEach(b => b.onclick = () => openOverridesDrawer(catalog, Number(b.dataset.special)));
    document.getElementById('acAddAccess').onclick = () => openAddAccessDrawer(catalog, projectOptions);
    document.getElementById('acCopyFrom').onclick = () => openCopyFromDrawer(catalog);
    document.getElementById('acSameRole').onclick = () => openSameRoleDrawer(catalog);
    document.getElementById('acRemoveAll').onclick = () => confirmBox('Remove this person from every project? This cannot be undone.', 'Remove all', async () => {
      try { await api(`access/users/${ac.selectedUserId}/access/all/`, { method: 'DELETE' }); toast('All access removed.'); await screenAccessUsers(); }
      catch (e) { alert(serverMsg(e) || friendly(e)); }
    });
  }

  function openAddAccessDrawer(catalog, projects) {
    const roles = catalog.roles.filter(r => !r.is_superadmin);
    const { box, close } = drawer(`
      <h2 style="margin-top:0">Add project access</h2>
      <div id="aaMsg"></div>
      <label class="q">1. Pick project(s)</label>
      <div style="max-height:180px;overflow-y:auto;border:2px solid var(--line);border-radius:10px;padding:8px">
        ${projects.map(p => `<label style="display:flex;align-items:center;gap:8px;min-height:40px"><input type="checkbox" value="${p.id}" class="aa-proj"> ${esc(p.name)}</label>`).join('') || '<p class="muted">No projects yet.</p>'}
      </div>
      <label class="q" style="margin-top:14px">2. Pick role</label>
      <div>${roles.map((r, i) => `<label style="display:flex;gap:8px;align-items:flex-start;margin:8px 0"><input type="radio" name="aa-role" value="${r.name}" ${i === 0 ? 'checked' : ''}> <span><b>${esc(roleLabel(r.name))}</b><br><small class="muted">${esc(r.description)}</small></span></label>`).join('')}</div>
      <button type="button" class="btn line" style="margin-top:10px" id="aaCancel">Cancel</button>
      <button type="button" class="btn green" id="aaSave">Save</button>`);
    box.querySelector('#aaCancel').onclick = close;
    box.querySelector('#aaSave').onclick = async () => {
      const project_ids = [...box.querySelectorAll('.aa-proj:checked')].map(c => Number(c.value));
      const role = box.querySelector('input[name=aa-role]:checked').value;
      if (!project_ids.length) { box.querySelector('#aaMsg').innerHTML = errBox('Pick at least one project.'); return; }
      try {
        await api(`access/users/${ac.selectedUserId}/access/`, { method: 'POST', body: { scope_type: 'PROJECT', project_ids, role } });
        close(); toast('Project access added.'); await screenAccessUsers();
      } catch (e) { box.querySelector('#aaMsg').innerHTML = errBox(serverMsg(e) || friendly(e)); }
    };
  }

  function openCopyFromDrawer(catalog) {
    const others = ac.users.filter(u => u.id !== ac.selectedUserId);
    const { box, close } = drawer(`
      <h2 style="margin-top:0">Copy access from another person</h2>
      <div id="cfMsg"></div>
      <label for="cfSel">Copy from</label>
      <select id="cfSel">${others.map(u => `<option value="${u.id}">${esc(u.name)}</option>`).join('')}</select>
      <button type="button" class="btn line" style="margin-top:10px" id="cfCancel">Cancel</button>
      <button type="button" class="btn green" id="cfSave">Copy</button>`);
    box.querySelector('#cfCancel').onclick = close;
    box.querySelector('#cfSave').onclick = async () => {
      try {
        await api(`access/users/${ac.selectedUserId}/copy-from/`, { method: 'POST', body: { from_user_id: Number(box.querySelector('#cfSel').value) } });
        close(); toast('Access copied.'); await screenAccessUsers();
      } catch (e) { box.querySelector('#cfMsg').innerHTML = errBox(serverMsg(e) || friendly(e)); }
    };
  }

  function openSameRoleDrawer(catalog) {
    const roles = catalog.roles.filter(r => !r.is_superadmin);
    const { box, close } = drawer(`
      <h2 style="margin-top:0">Give same role on all projects</h2>
      <div id="srMsg"></div>
      <select id="srSel">${roles.map(r => `<option value="${r.name}">${esc(roleLabel(r.name))}</option>`).join('')}</select>
      <button type="button" class="btn line" style="margin-top:10px" id="srCancel">Cancel</button>
      <button type="button" class="btn green" id="srSave">Apply</button>`);
    box.querySelector('#srCancel').onclick = close;
    box.querySelector('#srSave').onclick = async () => {
      try {
        await api(`access/users/${ac.selectedUserId}/same-role-all/`, { method: 'POST', body: { role: box.querySelector('#srSel').value } });
        close(); toast('Updated.'); await screenAccessUsers();
      } catch (e) { box.querySelector('#srMsg').innerHTML = errBox(serverMsg(e) || friendly(e)); }
    };
  }

  async function openOverridesDrawer(catalog, accessId) {
    let data;
    try { data = await api(`access/users/${ac.selectedUserId}/access/${accessId}/overrides/`); }
    catch (e) { alert(friendly(e)); return; }
    const groups = catalog.groups.filter(g => data.rows.some(r => r.group === g.id));
    const changed = () => data.rows.filter(r => r.state !== 'same').length;
    const stateOf = code => data.rows.find(r => r.code === code).state;
    const setState = (code, s) => { data.rows.find(r => r.code === code).state = s; };
    const { box, close } = drawer(`
      <h2 style="margin-top:0">Special changes for this project</h2>
      <p class="muted" id="ovCount">${changed()} special change${changed() === 1 ? '' : 's'}${changed() ? ' — <a href="#" id="ovReset">Reset all</a>' : ''}</p>
      <div id="ovMsg"></div>
      <div id="ovRows">${groups.map(g => `<h3 style="color:var(--blue);margin:14px 0 4px">${esc(g.label)}</h3>` +
        data.rows.filter(r => r.group === g.id).map(r => `
          <div class="ac-project-row" title="${esc(r.description)}">
            <span class="pname">${esc(r.label)} <small class="muted">(role gives: ${r.role_gives ? '✓' : '✗'})</small></span>
            <span class="tristate" data-code="${r.code}">
              <button type="button" data-v="same" class="${r.state === 'same' ? 'on same' : ''}">Same as role</button>
              <button type="button" data-v="allow" class="${r.state === 'allow' ? 'on allow' : ''}">➕ Extra</button>
              <button type="button" data-v="deny" class="${r.state === 'deny' ? 'on deny' : ''}">⛔ Block</button>
            </span>
          </div>`).join('')).join('')}</div>
      <button type="button" class="btn line" style="margin-top:10px" id="ovCancel">Cancel</button>
      <button type="button" class="btn green" id="ovSave">Save</button>`);
    const refreshCount = () => { box.querySelector('#ovCount').innerHTML = `${changed()} special change${changed() === 1 ? '' : 's'}${changed() ? ' — <a href="#" id="ovReset">Reset all</a>' : ''}`; bindReset(); };
    const bindReset = () => { const a = box.querySelector('#ovReset'); if (a) a.onclick = ev => { ev.preventDefault(); data.rows.forEach(r => r.state = 'same'); redraw(); }; };
    const redraw = () => {
      box.querySelectorAll('.tristate').forEach(t => {
        const s = stateOf(t.dataset.code);
        t.querySelectorAll('button').forEach(b => b.className = b.dataset.v === s ? `on ${s}` : '');
      });
      refreshCount();
    };
    box.querySelectorAll('.tristate button').forEach(b => b.onclick = () => { setState(b.closest('.tristate').dataset.code, b.dataset.v); redraw(); });
    bindReset();
    box.querySelector('#ovCancel').onclick = close;
    box.querySelector('#ovSave').onclick = async () => {
      const changes = {}; data.rows.filter(r => r.state !== 'same' || true).forEach(r => changes[r.code] = r.state);
      try {
        await api(`access/users/${ac.selectedUserId}/access/${accessId}/overrides/`, { method: 'PUT', body: { changes } });
        close(); toast('Special changes saved.'); await screenAccessUsers();
      } catch (e) { box.querySelector('#ovMsg').innerHTML = errBox(serverMsg(e) || friendly(e)); }
    };
  }

  // ---------------- Check Access ----------------

  async function screenAccessCheck() {
    chrome('settings', '#/settings');
    loading();
    let catalog, users;
    try { catalog = await loadAcCatalog(); users = ac.users || (ac.users = await api('access/users/')); }
    catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
    drawAccessCheck(catalog, users);
  }

  function drawAccessCheck(catalog, users) {
    $view.innerHTML = `<h1>🔍 Check Access</h1>` + settingsTabs('access') + `
      <label for="chkUser" class="q">Pick a person</label>
      <select id="chkUser"><option value="">Choose...</option>${users.map(u => `<option value="${u.id}" ${u.id === ac.checkUserId ? 'selected' : ''}>${esc(u.name)}</option>`).join('')}</select>
      <div id="chkBody"></div>
      <h2 style="margin-top:28px">Who can...?</h2>
      <label for="whoPerm" class="q">Permission</label>
      <select id="whoPerm">${catalog.permissions.map(p => `<option value="${p.code}">${esc(p.label)}</option>`).join('')}</select>
      <label for="whoProj" class="q">Project</label>
      <select id="whoProj"><option value="GLOBAL">Every project</option>${state.realProjects.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select>
      <button type="button" class="btn line" id="whoGo" style="margin-top:8px">Search</button>
      <div id="whoResult"></div>`;
    document.getElementById('chkUser').onchange = async e => {
      ac.checkUserId = e.target.value ? Number(e.target.value) : null;
      ac.checkData = null;
      if (!ac.checkUserId) { document.getElementById('chkBody').innerHTML = ''; return; }
      document.getElementById('chkBody').innerHTML = `<div class="spinner">⏳ ${L('रुकिए...', 'Loading...')}</div>`;
      try { ac.checkData = await api(`access/check/${ac.checkUserId}/`); } catch (e2) { document.getElementById('chkBody').innerHTML = errBox(friendly(e2)); return; }
      renderCheckBody(catalog);
    };
    document.getElementById('whoGo').onclick = async () => {
      const code = document.getElementById('whoPerm').value, project = document.getElementById('whoProj').value;
      const out = document.getElementById('whoResult');
      out.innerHTML = `<div class="spinner">⏳ ${L('रुकिए...', 'Loading...')}</div>`;
      try {
        const rows = await api(`access/who-can/?code=${encodeURIComponent(code)}&project=${encodeURIComponent(project)}`);
        out.innerHTML = rows.length ? `<div class="ac-people">${rows.map(r => `<div class="ac-person"><span class="ac-avatar">${esc(initials(r.name))}</span><span class="name">${esc(r.name)}</span><span class="chip">${esc(r.reason)}</span></div>`).join('')}</div>`
          : '<div class="empty"><div class="ico">🔍</div><p>No one can do this yet.</p></div>';
      } catch (e2) { out.innerHTML = errBox(friendly(e2)); }
    };
    if (ac.checkUserId && ac.checkData) renderCheckBody(catalog);
  }

  function renderCheckBody(catalog) {
    const el = document.getElementById('chkBody');
    const scopes = ac.checkData.scopes;
    if (!scopes.length) { el.innerHTML = '<div class="empty"><div class="ico">🚫</div><p>This person has no access anywhere.</p></div>'; return; }
    const activeKey = el.dataset.active || scopes[0].key;
    const codeLabel = code => (catalog.permissions.find(p => p.code === code) || { label: code }).label;
    const reasonText = (code, source) => source === 'deny' ? 'Blocked for this project' : source === 'allow' ? 'Extra access given' : source === 'role' ? 'From their role' : 'Not allowed';
    const renderTabs = () => `<div class="ac-tabs">${scopes.map(s => `<button type="button" data-scope="${s.key}" class="${s.key == activeKey && !ac.compareAll ? 'on' : ''}">${esc(s.label)}</button>`).join('')}
        <button type="button" data-compare="1" class="${ac.compareAll ? 'on' : ''}">📊 Compare all projects</button></div>`;
    if (ac.compareAll) {
      el.innerHTML = renderTabs() + `<div class="ac-grid-wrap"><table class="ac-grid"><thead><tr><th>Permission</th>${scopes.map(s => `<th>${esc(s.label)}</th>`).join('')}</tr></thead><tbody>
        ${catalog.permissions.map(p => `<tr><td>${esc(p.label)}</td>${scopes.map(s => {
          const v = s.permissions[p.code]; return `<td>${v && v.allowed ? '✅' : v && v.source === 'deny' ? '⛔' : '—'}</td>`;
        }).join('')}</tr>`).join('')}</tbody></table></div>`;
    } else {
      const scope = scopes.find(s => s.key == activeKey) || scopes[0];
      el.innerHTML = renderTabs() + catalog.groups.map(g => `<h3 style="color:var(--blue);margin:14px 0 4px">${esc(g.label)}</h3>` +
        catalog.permissions.filter(p => p.group === g.id).map(p => {
          const v = scope.permissions[p.code] || { allowed: false, source: null };
          const status = v.source === 'deny' ? { c: 'deny', t: '⛔ Blocked' } : v.allowed ? { c: 'allow', t: '✓ Allowed' } : { c: 'off', t: '✗ Not allowed' };
          return `<div class="ac-check-row" title="${esc(p.description)}"><span class="status ${status.c}">${status.t}</span><span>${esc(p.label)}</span><span class="why">${esc(reasonText(p.code, v.source))}</span></div>`;
        }).join('')).join('');
    }
    el.dataset.active = activeKey;
    el.querySelectorAll('[data-scope]').forEach(b => b.onclick = () => { ac.compareAll = false; el.dataset.active = b.dataset.scope; renderCheckBody(catalog); });
    const cmp = el.querySelector('[data-compare]'); if (cmp) cmp.onclick = () => { ac.compareAll = !ac.compareAll; renderCheckBody(catalog); };
  }

  // Small Cancel / confirm dialog (same look as the other pop-ups).
  function confirmBox(text, okLabel, onOk) {
    const box = document.createElement('div');
    box.className = 'modal';
    box.innerHTML = `<div class="modal-box" role="dialog" aria-modal="true" aria-label="${esc(text)}">
      <h2 style="margin-top:0">${esc(text)}</h2>
      <button class="btn line" type="button" id="cb-cancel">${L('रद्द करें', 'Cancel')}</button>
      <button class="btn green" type="button" id="cb-ok">${esc(okLabel)}</button></div>`;
    document.body.appendChild(box);
    const close = () => { box.remove(); document.removeEventListener('keydown', onKey); window.removeEventListener('hashchange', close); };
    const onKey = e => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    window.addEventListener('hashchange', close);
    box.querySelector('#cb-cancel').onclick = close;
    box.querySelector('#cb-ok').onclick = () => { close(); onOk(); };
    box.querySelector('#cb-cancel').focus();
  }

  // Project creation now lives entirely in Settings -> Projects (screenProjectForm above, super admin
  // only); see #/settings/new.

  // ----- Manager Fund: Owner --fund--> Manager --distribution--> Labour. All numbers come from the server. -----
  let fundFlash = '', fundManager = null, fundAll = false;      // fundManager = the manager shown; fundAll = the all-projects overview
  // Money as whole paise (integers), so an amount never picks up floating-point errors.
  const toPaise = t => {
    const m = /^(\d*)(?:\.(\d{0,2}))?$/.exec(String(t || '').trim());
    return m ? Number(m[1] || 0) * 100 + Number((m[2] || '').padEnd(2, '0') || 0) : 0;
  };
  const fromPaise = c => `${Math.floor(c / 100)}.${String(c % 100).padStart(2, '0')}`;
  const modeName = k => modeLabel(MODES.find(m => m.key === k) || { hi: k, en: k });
  const fundNoAccess = () => `<div class="empty"><div class="ico">🔒</div><h2>${L('इस फंड को देखने की अनुमति नहीं है', 'You do not have permission to view Manager Fund.')}</h2></div>`;
  const fundErr = e => (e && e.status === 403 ? L('इस Manager Fund कार्रवाई की अनुमति नहीं है.', 'You do not have permission for this Manager Fund action.') : (serverMsg(e) || friendly(e)));

  async function screenFund() {
    chrome('fund', '#/home');
    if (state.fundForbidden) { $view.innerHTML = fundNoAccess(); return; }
    if (!state.project) { $view.innerHTML = noProject(); return; }
    loading();
    let every;
    try { every = (await api('manager-funds/statement/')).statements; }      // the server returns only what this user may see
    catch (e) { $view.innerHTML = e.status === 403 ? fundNoAccess() : errBox(fundErr(e)); return; }
    const stmts = every.filter(x => x.position.project_id === state.project.id);
    const flash = fundFlash; fundFlash = '';
    const canAll = state.projects.length > 1 && !state.me.manager_id;          // owners / admin: an all-projects overview
    if (!canAll) fundAll = false;
    const projectPicker = state.projects.length > 1
      ? `<select id="fproj" aria-label="Project">${canAll ? `<option value="all" ${fundAll ? 'selected' : ''}>${L('सभी प्रोजेक्ट', 'All projects')}</option>` : ''}${state.projects.map(p => `<option value="${p.id}" ${!fundAll && p.id === state.project.id ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>` : '';
    // Manager | Fund Received | Distributed to Labour | Available Balance, one row per manager (+ a project total).
    const fundTable = list => {
      const sum = k => list.reduce((t, x) => t + Number(x.position[k]), 0);
      const cells = (name, r, d, b) => `<span class="c-name">${name}</span><span data-label="${L('फंड मिला', 'Fund Received')}">${money(r)}</span><span data-label="${L('मज़दूरों को बाँटा', 'Distributed to Labour')}">${money(d)}</span><span data-label="${L('बचा हुआ', 'Available Balance')}"><b>${money(b)}</b></span>`;
      return `<div class="fund-table" role="table"><div class="fund-row head" role="row"><span>${L('मैनेजर', 'Manager')}</span><span>${L('फंड मिला', 'Fund Received')}</span><span>${L('मज़दूरों को बाँटा', 'Distributed to Labour')}</span><span>${L('बचा हुआ', 'Available Balance')}</span></div>
        ${list.map(x => `<button type="button" class="fund-row pick ${!fundAll && x.position.project_id === state.project.id && x.position.manager_id === fundManager ? 'on' : ''}" data-p="${x.position.project_id}" data-m="${x.position.manager_id}">${cells(esc(x.position.manager_name), x.position.total_received, x.position.total_distributed, x.position.available_balance)}</button>`).join('')}
        ${list.length > 1 ? `<div class="fund-row total">${cells(L('कुल', 'Project total'), sum('total_received'), sum('total_distributed'), sum('available_balance'))}</div>` : ''}</div>`;
    };
    const bindPick = () => $view.querySelectorAll('.fund-row.pick').forEach(b => b.onclick = () => {
      state.project = state.projects.find(p => p.id === Number(b.dataset.p)); store.set('projectId', state.project.id); state.names = null;
      fundAll = false; fundManager = Number(b.dataset.m); screenFund(); window.scrollTo(0, 0);
    });
    const actions = `${can('canGiveManagerFund') ? `<a class="btn green" href="#/givefund">➕ ${L('फंड दें', 'Give Fund')}</a>` : ''}
      ${can('canDistributeManagerFund') && stmts.length ? `<a class="btn" href="#/distribute">📤 ${L('मज़दूरों को दें', 'Distribute to Labour')}</a>` : ''}`;
    const head = `<h1>💰 ${L('मैनेजर फंड', 'Manager Fund')}</h1>
      <p class="muted">${L('प्रोजेक्ट', 'Project')}: <b>${fundAll ? L('सभी प्रोजेक्ट', 'All projects') : esc(state.project.name)}</b></p>${projectPicker}
      ${flash ? `<div class="msg ok" role="status">${esc(flash)}</div>` : ''}`;
    const bindProject = () => {
      const sel = document.getElementById('fproj');
      if (sel) sel.onchange = () => {
        fundAll = sel.value === 'all';
        if (!fundAll) { state.project = state.projects.find(p => p.id === Number(sel.value)); store.set('projectId', state.project.id); state.names = null; }
        fundManager = null; screenFund();
      };
    };
    if (fundAll) {                                    // every project the user may see, manager-wise
      const parts = state.projects.map(p => ({ p, list: every.filter(x => x.position.project_id === p.id) })).filter(x => x.list.length);
      $view.innerHTML = `${head}` + (parts.map(x => `<h2>🏗️ ${esc(x.p.name)}</h2>${fundTable(x.list)}`).join('')
        || `<div class="empty"><div class="ico">📭</div><p>${L('अभी किसी प्रोजेक्ट में कोई फंड नहीं दिया गया है.', 'No fund has been given on any project yet.')}</p></div>`);
      bindProject(); bindPick();
      return;
    }
    if (!stmts.length) {
      $view.innerHTML = `${head}<div class="empty"><div class="ico">📭</div><p>${L('इस प्रोजेक्ट में अभी किसी मैनेजर को फंड नहीं मिला है.', 'No fund has been given to a manager on this project yet.')}</p></div>${actions}`;
      bindProject();
      return;
    }
    let cur = stmts.find(x => x.position.manager_id === fundManager) || stmts[0];
    fundManager = cur.position.manager_id;

    const owners = new Map(cur.funds.map(f => [f.id, f.given_by_owner_name]));
    const pill = st => `<span class="pill ${st === 'ACTIVE' ? '' : 'warn'}">${st === 'ACTIVE' ? L('चालू', 'Active') : L('रद्द — गिना नहीं गया', 'Cancelled — not counted')}</span>`;
    const draw = () => {
      const p = cur.position, bal = Number(p.available_balance);
      $view.innerHTML = `${head}
        ${!state.me.manager_id ? `<h2>${L('मैनेजर', 'Manager-wise Summary')}</h2>${fundTable(stmts)}` : ''}
        <p class="muted">${L('मैनेजर', 'Manager')}: <b>${esc(p.manager_name)}</b></p>
        <div class="tot-grid">
          <div class="card tot-box"><div class="muted">${L('कुल फंड मिला', 'Total Fund Received')}</div><div class="big-total">${money(p.total_received)}</div><div class="muted">${L('मालिक → मैनेजर', 'Owner → Manager')}</div></div>
          <div class="card tot-box"><div class="muted">${L('कुल बाँटा गया', 'Total Distributed')}</div><div class="big-total">${money(p.total_distributed)}</div><div class="muted">${L('मैनेजर → मज़दूर', 'Manager → Labour')}</div></div>
          <div class="card tot-box ${bal > 0 ? 'ok' : ''}"><div class="muted">${L('बचा हुआ फंड', 'Available Balance')}</div><div class="big-total">${money(p.available_balance)}</div><div class="muted">${L('= मिला − बाँटा', 'Received − active distribution')}</div></div>
        </div>
        ${p.total_received > 0 && bal === 0 ? `<div class="msg info">${L('पूरा फंड बाँटा जा चुका है — अब कोई बचत नहीं.', 'No balance left.')}</div>` : ''}
        ${actions}
        <h2>📥 ${L('फंड मिला', 'Fund Received History')}</h2>` +
        (cur.funds.map(f => `<div class="card item"><div class="row"><span class="who">${money(f.amount)}</span><span class="meta">${shortDate(f.date)}</span></div>
          <div class="meta">${L('मालिक', 'Owner')}: <b>${esc(f.given_by_owner_name)}</b> · ${esc(modeName(f.payment_mode))}</div>
          ${f.remarks ? `<div class="note">📝 ${esc(f.remarks)}</div>` : ''}</div>`).join('') || `<div class="empty">${L('अभी कोई फंड नहीं मिला.', 'No fund received yet.')}</div>`) +
        `<h2>📤 ${L('मज़दूरों को दिया', 'Labour Distribution History')}</h2>` +
        (cur.distributions.slice().reverse().map(d => `<div class="card item ${d.status === 'ACTIVE' ? '' : 'cancelled'}"><div class="row"><span class="who">${esc(d.labour_name)}</span><span class="amount">${money(d.amount)}</span></div>
          <div class="meta">${shortDate(d.date)} · ${L('फंड', 'Fund')} #${d.manager_fund_id} (${esc(owners.get(d.manager_fund_id) || '—')}) ${pill(d.status)}</div>
          ${d.remarks ? `<div class="note">📝 ${esc(d.remarks)}</div>` : ''}</div>`).join('') || `<div class="empty">${L('अभी किसी मज़दूर को नहीं दिया.', 'No distribution to labour yet.')}</div>`) +
        `<h2>🧾 ${L('हिसाब-किताब', 'Running Statement')}</h2>
        <p class="muted">${L('📥 फंड = मैनेजर को मिला पैसा · 📤 = मैनेजर ने मज़दूर को दिया · रद्द किया हुआ जोड़ा नहीं जाता.', '📥 Fund = money received by manager · 📤 = manager paid to labour · cancelled entries are not counted.')}</p>` +
        (cur.ledger.map(e => `<div class="card item ${e.status === 'ACTIVE' ? '' : 'cancelled'}"><div class="row"><span class="who">${e.kind === 'FUND' ? '📥' : '📤'} ${esc(e.label)}</span>
          <span class="amount ${e.kind === 'FUND' ? 'in' : 'out'}">${e.kind === 'FUND' ? '+' : '−'} ${money(e.amount)}</span></div>
          <div class="row meta"><span>${shortDate(e.date)} ${e.status === 'ACTIVE' ? '' : pill(e.status)}</span><span>${L('बचा', 'Balance')}: <b>${money(e.running_balance)}</b></span></div></div>`).join('') || `<div class="empty">${L('अभी कुछ नहीं.', 'Nothing yet.')}</div>`);
      bindProject(); bindPick();
    };
    draw();
  }

  // Give Fund (owner / admin). The people come from the project's own assignments; the server re-checks everything.
  async function screenGiveFund() {
    chrome('fund', '#/fund');
    if (!state.project) { $view.innerHTML = noProject(); return; }
    loading();
    let people;
    try { people = await api(`projects/${state.project.id}/people/`); }
    catch (e) { $view.innerHTML = errBox(fundErr(e)); return; }
    const onBehalf = state.user.allProjects;          // an admin may record a fund on behalf of a project owner
    const options = list => list.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('');
    const title = `<h1>➕ ${L('फंड दें', 'Give Fund')}</h1>
      <p class="muted">${L('प्रोजेक्ट', 'Project')}: <b>${esc(state.project.name)}</b> · ${L('मालिक → मैनेजर. यह खर्च नहीं है.', 'Owner → Manager. This is not an expense.')}</p>`;
    if (!people.managers.length) {
      $view.innerHTML = `${title}<div class="msg info">${L('इस प्रोजेक्ट में अभी कोई मैनेजर नहीं जुड़ा है.', 'No manager is assigned to this project yet.')}</div>
        <a class="btn line" href="#/fund">← ${L('वापस', 'Back')}</a>`;
      return;
    }
    $view.innerHTML = `${title}<div id="gmsg"></div>
      <form id="gf" novalidate>
        <label for="g-mgr">${L('मैनेजर', 'Manager')} *</label>
        <select id="g-mgr"><option value="">${L('— चुनिए —', '— Choose —')}</option>${people.managers.map(x => `<option value="${x.id}" ${x.id === fundManager ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select>
        ${onBehalf ? `<label for="g-own">${L('किस मालिक ने दिया', 'Given by owner')} *</label>
          <select id="g-own"><option value="">${L('— चुनिए —', '— Choose —')}</option>${options(people.owners)}</select>`
          : `<p class="muted">${L('दिया', 'Given by')}: <b>${esc(state.me.name)}</b></p>`}
        <label for="g-amt">${L('राशि', 'Amount')} *</label>
        <div class="rupee"><span>₹</span><input id="g-amt" type="text" inputmode="decimal" autocomplete="off" placeholder="0"></div>
        <label for="g-date">${L('तारीख', 'Date')} *</label><input id="g-date" type="date" value="${today()}">
        <label for="g-mode">${L('कैसे दिया', 'Payment mode')}</label>
        <select id="g-mode">${MODES.map(m => `<option value="${m.key}">${modeLabel(m)}</option>`).join('')}</select>
        <label for="g-note">${L('जानकारी', 'Remarks (optional)')}</label><input id="g-note" type="text" autocomplete="off">
        <button class="btn green" type="submit" id="g-save">💾 ${L('फंड सेव करें', 'SAVE FUND')}</button>
        <a class="btn line" href="#/fund">${L('रद्द करें', 'Cancel')}</a>
      </form>`;
    const $ = id => document.getElementById(id);
    $('g-amt').oninput = e => { e.target.value = e.target.value.replace(/[^0-9.]/g, '').replace(/(\..*)\./g, '$1'); };
    $('gf').onsubmit = async ev => {
      ev.preventDefault();
      const bad = t => { $('gmsg').innerHTML = errBox(t); window.scrollTo(0, 0); };
      const manager = Number($('g-mgr').value), owner = onBehalf ? Number($('g-own').value) : state.me.owner_id;
      const cents = toPaise($('g-amt').value);
      if (!manager) return bad(L('कृपया मैनेजर चुनिए.', 'Please choose a manager.'));
      if (!owner) return bad(L('कृपया मालिक चुनिए.', 'Please choose the owner.'));
      if (!(cents > 0)) return bad(L('कृपया राशि भरें (0 से ज़्यादा).', 'Please enter an amount greater than zero.'));
      if (cents >= 1e12) return bad(L('राशि बहुत बड़ी है। कृपया जाँच लें.', 'That amount is too large. Please check it.'));
      if (!$('g-date').value) return bad(L('कृपया तारीख चुनिए.', 'Please choose a date.'));
      $('g-save').disabled = true; $('gmsg').innerHTML = '';
      try {
        await api('manager-funds/', { method: 'POST', body: {
          project: state.project.id, manager, given_by_owner: owner, fund_amount: fromPaise(cents),
          fund_date: $('g-date').value, payment_mode: $('g-mode').value, remarks: $('g-note').value.trim() } });
      } catch (e) { $('g-save').disabled = false; bad(fundErr(e)); return; }
      fundFlash = L('फंड सेव हो गया.', 'Fund saved.'); fundManager = manager;
      location.hash = '#/fund';
    };
  }

  // Distribute to Labour: several labourers, one amount each, saved as ONE batch (all or nothing) by the server.
  // The screen shows the balance and stops an over-spend early; the server still makes the final decision.
  async function screenDistribute() {
    chrome('fund', '#/fund');
    if (!state.project) { $view.innerHTML = noProject(); return; }
    loading();
    let people;
    try { people = await api(`projects/${state.project.id}/people/`); }
    catch (e) { $view.innerHTML = errBox(fundErr(e)); return; }
    const own = state.me.manager_id;                          // a manager distributes only their own fund
    const managers = own ? people.managers.filter(m => m.id === own) : people.managers;
    const back = `<a class="btn line" href="#/fund">← ${L('फंड देखें', 'Back to Manager Fund')}</a>`;
    const title = `<h1>📤 ${L('मज़दूरों को दें', 'Distribute to Labour')}</h1>
      <p class="muted">${L('प्रोजेक्ट', 'Project')}: <b>${esc(state.project.name)}</b></p>`;
    if (!managers.length) { $view.innerHTML = `${title}<div class="msg info">${L('कोई मैनेजर नहीं मिला.', 'No manager found for this project.')}</div>${back}`; return; }
    let manager = managers.find(m => m.id === fundManager) || managers[0];
    const picked = new Set(), amt = {};
    let q = '', showInactive = false, available = 0, saving = false;

    $view.innerHTML = `${title}
      ${state.projects.length > 1 ? `<select id="dproj" aria-label="Project">${state.projects.map(p => `<option value="${p.id}" ${p.id === state.project.id ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>` : ''}
      <div id="dmsg"></div>
      <form id="df" novalidate>
        ${managers.length > 1 ? `<label for="d-mgr">${L('मैनेजर', 'Manager')}</label><select id="d-mgr">${managers.map(m => `<option value="${m.id}" ${m.id === manager.id ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}</select>`
          : `<p class="muted">${L('मैनेजर', 'Manager')}: <b>${esc(manager.name)}</b></p>`}
        <div class="card tot-box"><div class="muted">${L('उपलब्ध फंड', 'Available Balance')}</div><div class="big-total" id="d-avail">…</div><div class="muted" id="d-note"></div></div>
        <label for="d-date">${L('तारीख', 'Date')} *</label><input id="d-date" type="date" value="${today()}">
        <div class="q">${L('किन मज़दूरों को दिया?', 'Labour &amp; amounts')} <small id="d-count"></small></div>
        <input id="d-q" type="text" autocomplete="off" placeholder="🔍 ${L('मज़दूर खोजिए', 'Search Labour')}">
        <label class="lab-inact"><input type="checkbox" id="d-inact"> ${L('पुराने / काम बंद मज़दूर भी दिखाएँ', 'Show Inactive')}</label>
        <div id="dlist"></div>
        <div class="card lab-total"><div class="row"><span>${L('इस बार का कुल', 'Total This Distribution')}</span><span class="amount" id="d-total">₹ 0</span></div>
          <div class="row"><span>${L('उपलब्ध फंड', 'Available Balance')}</span><span class="amount" id="d-avail2">₹ 0</span></div>
          <div class="row"><span>${L('बाँटने के बाद बचेगा', 'Balance After Distribution')}</span><span class="amount" id="d-after">₹ 0</span></div></div>
        <div class="field-error" id="d-warn"></div>
        <label for="d-note-in">${L('जानकारी', 'Remarks (optional)')}</label><input id="d-note-in" type="text" autocomplete="off">
        <button class="btn green" type="submit" id="d-save">💾 ${L('बाँट दें', 'SAVE DISTRIBUTION')}</button>
        <a class="btn line" href="#/fund">${L('रद्द करें', 'Cancel')}</a>
      </form>`;
    const $ = id => document.getElementById(id);
    const sum = () => [...picked].reduce((t, id) => t + toPaise(amt[id]), 0);
    const problem = () => {
      if (!picked.size) return L('कम से कम एक मज़दूर चुनिए.', 'Please choose at least one labour.');
      if ([...picked].some(id => !(toPaise(amt[id]) > 0))) return L('चुने हुए हर मज़दूर की राशि भरिए (0 से ज़्यादा).', 'Enter an amount greater than zero for every selected labour.');
      if (sum() > available) return L('कुल राशि उपलब्ध फंड से ज़्यादा है — घटाइए.', 'Total is more than the available balance.');
      return '';
    };
    function update() {
      const total = sum(), after = available - total;
      $('d-avail').textContent = money(available / 100); $('d-avail2').textContent = money(available / 100);
      $('d-total').textContent = money(total / 100); $('d-after').textContent = money(after / 100);
      $('d-after').classList.toggle('out', after < 0);
      $('d-count').textContent = picked.size ? `(${picked.size} ${L('चुने', 'selected')})` : '';
      $('d-warn').textContent = total > available ? problem() : '';
      $('d-save').disabled = saving || !!problem();
    }
    function drawRows() {
      const rows = people.labour.filter(l => (l.is_active || showInactive || picked.has(l.id)) && (!q || l.name.toLowerCase().includes(q)));
      $('dlist').innerHTML = rows.length ? rows.map(l => `
        <div class="lab-row ${picked.has(l.id) ? 'on' : ''}" data-id="${l.id}">
          <label class="lab-pick"><input type="checkbox" class="lab-chk" ${picked.has(l.id) ? 'checked' : ''}>
            <span class="lab-name">${esc(l.name)}${l.type ? ` <small>${esc(l.type)}</small>` : ''} ${l.is_active ? '' : `<span class="pill warn">${L('काम बंद', 'Inactive')}</span>`}</span></label>
          <span class="lab-amt"><span>₹</span><input class="lab-a" type="text" inputmode="decimal" autocomplete="off" placeholder="0" aria-label="${esc(l.name)} ${L('राशि', 'amount')}" value="${esc(amt[l.id] || '')}"></span>
        </div>`).join('') : `<div class="msg info">${L('इस प्रोजेक्ट में कोई मज़दूर नहीं मिला.', 'No labour found.')}</div>`;
    }
    async function loadBalance() {
      try {
        const r = await api(`manager-funds/summary/?project=${state.project.id}&manager=${manager.id}`);
        const row = (r.summary || [])[0];
        available = row ? toPaise(row.available_balance) : 0;
        $('d-note').textContent = row ? '' : L('इस मैनेजर को अभी कोई फंड नहीं मिला.', 'No fund given yet.');
      } catch (e) { available = 0; $('dmsg').innerHTML = errBox(fundErr(e)); }
      update();
    }
    $('dlist').onchange = e => {
      if (!e.target.classList.contains('lab-chk')) return;
      const row = e.target.closest('.lab-row'), id = Number(row.dataset.id);
      if (e.target.checked) { picked.add(id); row.classList.add('on'); row.querySelector('.lab-a').focus(); } else { picked.delete(id); row.classList.remove('on'); }
      $('dmsg').innerHTML = ''; update();
    };
    $('dlist').oninput = e => {
      if (!e.target.classList.contains('lab-a')) return;
      const row = e.target.closest('.lab-row'), id = Number(row.dataset.id);
      const [whole, ...rest] = e.target.value.replace(/[^0-9.]/g, '').split('.');
      e.target.value = whole.slice(0, 9) + (rest.length ? '.' + rest.join('').slice(0, 2) : '');
      amt[id] = e.target.value;
      if (e.target.value) { picked.add(id); row.classList.add('on'); row.querySelector('.lab-chk').checked = true; }
      $('dmsg').innerHTML = ''; update();
    };
    $('d-q').oninput = e => { q = e.target.value.trim().toLowerCase(); drawRows(); };
    $('d-inact').onchange = e => { showInactive = e.target.checked; drawRows(); };
    if ($('dproj')) $('dproj').onchange = () => { state.project = state.projects.find(p => p.id === Number($('dproj').value)); store.set('projectId', state.project.id); state.names = null; fundManager = null; screenDistribute(); };
    if ($('d-mgr')) $('d-mgr').onchange = e => { manager = managers.find(m => m.id === Number(e.target.value)); loadBalance(); };
    $('df').onsubmit = async ev => {
      ev.preventDefault();
      if (saving) return;
      const bad = t => { $('dmsg').innerHTML = errBox(t); window.scrollTo(0, 0); };
      if (problem()) return bad(problem());
      if (!$('d-date').value) return bad(L('कृपया तारीख चुनिए.', 'Please choose a date.'));
      const lines = people.labour.filter(l => picked.has(l.id));
      saving = true; update(); $('dmsg').innerHTML = '';
      try {
        const r = await api('manager-labour-distributions/batch/', { method: 'POST', body: {
          project: state.project.id, manager: manager.id, date: $('d-date').value, remarks: $('d-note-in').value.trim(),
          include_inactive: lines.some(l => !l.is_active),
          payments: lines.map(l => ({ labour: l.id, amount: fromPaise(toPaise(amt[l.id])) })) } });
        fundFlash = L(`बँट गया: ${money(r.total)} — ${lines.length} मज़दूर, एक बैच में.`, `Distribution saved: ${money(r.total)} — ${lines.length} labour, one batch.`);
        fundManager = manager.id;
        location.hash = '#/fund';
      } catch (e) {
        saving = false;
        bad(fundErr(e));
        await loadBalance();                         // the balance may have changed: show the truth
      }
    };
    drawRows(); update(); loadBalance();
  }

  // ---------- router ----------
  async function route() {
    const [path, qs] = (location.hash.replace(/^#/, '') || '/home').split('?');
    const params = new URLSearchParams(qs || '');
    if (!state.token) { if (path !== '/login') { location.hash = '#/login'; return; } return screenLogin(); }
    if (path === '/login') { location.hash = '#/home'; return; }
    try { await loadBasics(); } catch (e) { if (e.kind !== 'auth') { chrome('blocked'); $view.innerHTML = errBox(friendly(e)); } return; }
    if (!state.me.owner_id && state.me.role !== 'MANAGER') return screenBlocked();

    const [, page, arg] = path.split('/');
    if (page !== 'permissions' && permDraft) {          // never drop unsaved permission edits silently
      if (permDirty() && !confirm('You have unsaved permission changes. Leave without saving?')) { location.hash = '#/permissions'; return; }
      permDraft = null;
    }
    const need = Authz.routePermission(page, arg);
    if (need && !canAny(need)) return screenNoAccess();   // typed-in / bookmarked links
    switch (page) {
      case 'add': return screenAdd(params);
      case 'done': return screenDone();
      case 'list': return screenList(params);
      case 'reports': return screenReports();
      case 'report': return screenReportOne(arg);
      case 'project': return screenProject();
      case 'profile': return screenProfile();
      case 'users': return screenUsers();
      case 'settings': return screenSettings(arg);
      case 'permissions': return screenPermissions();
      case 'access': return screenAccess(arg, params);
      case 'fund': return screenFund();
      case 'givefund': return screenGiveFund();
      case 'managers': return arg ? screenManagerDetail(Number(arg)) : screenManagers();
      case 'distribute': return screenDistribute();
      case 'resetpw': return screenResetPassword(arg);
      default: return screenHome();
    }
  }

  // On phones the on-screen keyboard shrinks the page: hide the bottom bar while typing.
  const typing = el => el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
  document.addEventListener('focusin', e => { if (typing(e.target)) document.body.classList.add('typing'); });
  document.addEventListener('focusout', () => setTimeout(() => { if (!typing(document.activeElement)) document.body.classList.remove('typing'); }, 50));

  document.getElementById('updateBanner').addEventListener('click', () => location.reload());

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('sw.js').then(reg => {
        // A worker installing while one is already active/controlling this page means a newer
        // version was just deployed -- tell the user instead of silently swapping code under them.
        const announce = worker => worker.addEventListener('statechange', () => {
          if (worker.state === 'installed' && navigator.serviceWorker.controller) {
            updateAvailable = true;
            const b = document.getElementById('updateBanner');
            b.hidden = false;
            b.textContent = I18n.getLang() === 'hi'
              ? '🔄 नया वर्शन उपलब्ध है. रीलोड करने के लिए यहाँ टैप करें.'
              : '🔄 A new version is available. Tap here to reload.';
          }
        });
        if (reg.installing) announce(reg.installing);
        reg.addEventListener('updatefound', () => reg.installing && announce(reg.installing));
      }).catch(e => console.error('service worker', e));
    });
  }

  document.addEventListener('click', e => {
    const sheet = document.getElementById('menuSheet');
    if (!sheet.hidden && !e.target.closest('#menuSheet, #menuBtn')) sheet.hidden = true;
    if (userMenuCloser && !e.target.closest('.user-menu')) userMenuCloser();
  });
  window.addEventListener('hashchange', () => { if (userMenuCloser) userMenuCloser(); });
  window.addEventListener('hashchange', route);
  route();
})();
