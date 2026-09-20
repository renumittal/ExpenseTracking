/* Simple expense diary. Plain JS, talks to the existing /api/ endpoints.
   Technical errors go to the console only; users only see simple messages. */
(function () {
  'use strict';

  // ---------- words the user sees ----------
  const CATS = [
    { key: 'LABOUR',        icon: '👷', hi: 'मज़दूर',   en: 'Labour',     type: 'Labour Payment',     party: 'LABOUR' },
    { key: 'CONTRACTOR',    icon: '🏗️', hi: 'ठेकेदार',  en: 'Contractor', type: 'Contractor Payment', party: 'CONTRACTOR' },
    { key: 'SUPPLIER',      icon: '🚚', hi: 'सप्लायर',  en: 'Supplier',   type: 'Supplier Payment',   party: 'SUPPLIER' },
    { key: 'MISCELLANEOUS', icon: '📦', hi: 'दूसरा खर्च', en: 'Other',      type: 'Other',              party: 'NONE' },
  ];
  const CAT = Object.fromEntries(CATS.map(c => [c.key, c]));
  const MODES = [
    { key: 'CASH', hi: 'नकद', en: 'Cash' },
    { key: 'UPI', hi: 'UPI', en: 'UPI' },
    { key: 'BANK_TRANSFER', hi: 'बैंक', en: 'Bank' },
    { key: 'CHEQUE', hi: 'चेक', en: 'Cheque' },
  ];
  const SUPPLIER_TYPES = ['Electrical Material', 'Plumbing Material', 'Building Material', 'Saria / Steel',
    'Chokhat / Door', 'Wood / Timber', 'Paint / Hardware', 'Other'];
  const STATUS_HI = { PLANNED: 'शुरू होना बाकी', ONGOING: 'काम चालू है', COMPLETED: 'काम पूरा हो गया' };
  const MONTHS = ['जनवरी', 'फ़रवरी', 'मार्च', 'अप्रैल', 'मई', 'जून', 'जुलाई', 'अगस्त', 'सितंबर', 'अक्तूबर', 'नवंबर', 'दिसंबर'];
  const MSG = {
    problem: 'कुछ समस्या हुई। कृपया दोबारा प्रयास करें.',
    network: 'इंटरनेट नहीं चल रहा है। कृपया इंटरनेट देखकर दोबारा प्रयास करें.',
    login: 'नाम या पासवर्ड गलत है। कृपया दोबारा भरें.',
    saved: 'खर्च सफलतापूर्वक सेव हो गया.',
    noPermission: 'इस प्रोजेक्ट पर खर्च डालने की अनुमति नहीं है.',
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
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const today = () => iso(new Date());
  const yesterday = () => { const d = new Date(); d.setDate(d.getDate() - 1); return iso(d); };
  const EN_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const shortDate = s => { const [y, m, d] = String(s).split('-').map(Number); return y ? `${String(d).padStart(2, '0')}-${EN_MONTHS[m - 1]}-${y}` : ''; };
  const niceDate = s => { const [y, m, d] = String(s).split('-').map(Number); return y ? `${d} ${MONTHS[m - 1]} ${y}` : ''; };

  const state = { token: store.get('token'), me: null, user: null, realProjects: [], projects: [], project: null, names: null, demoId: store.get('demoUser'), fundForbidden: false };
  const DEMO = !!(window.APP_CONFIG && window.APP_CONFIG.demoRoles);
  // The one permission check used everywhere: can('canAddExpense'). Role names live only in authz.js.
  // The server decides these (it returns them in /me/); the local matrix can only hide more, never grant more.
  const SERVER_PERMS = ['canViewManagerFund', 'canGiveManagerFund', 'canDistributeManagerFund', 'canUploadBill', 'canViewBill'];
  const serverAllows = perm => {
    const me = state.me;
    if (!me || !me.permissions) return true;                                  // an older server: nothing to check
    if (state.project && me.project_permissions) return (me.project_permissions[String(state.project.id)] || []).includes(perm);
    return !!me.permissions[perm];
  };
  const can = perm => Authz.can(state.user, state.project && state.project.id, perm) && (!SERVER_PERMS.includes(perm) || serverAllows(perm));
  const canAny = perms => Authz.allows(perms, can);               // a screen may need one permission, any of several, or a rule
  const canAdd = () => canAny(Authz.routePermission('add'));     // Add Expense screen (expense or labour payment)
  const noProject = () => can('canCreateProject')
    ? `<div class="empty"><div class="ico">🏠</div><h2>अभी कोई प्रोजेक्ट नहीं है</h2><p>No projects exist yet.</p></div><a class="btn green" href="#/newproject">➕ नया प्रोजेक्ट <span class="sub">New Project</span></a>`
    : '<div class="empty"><div class="ico">🏠</div><h2>आपको अभी कोई प्रोजेक्ट नहीं दिया गया है</h2><p>कृपया एडमिन से संपर्क करें.<br>No projects have been assigned to you yet.<br>Please contact the administrator.</p></div>';

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
  const friendly = e => (e && e.kind === 'network' ? MSG.network : e && e.status === 403 ? MSG.noPermission : MSG.problem);
  const errBox = text => `<div class="msg error" role="alert">${esc(text)}</div>`;
  const loading = () => { $view.innerHTML = '<div class="spinner">⏳ रुकिए...</div>'; };

  function logoutLocal() {
    store.del('token'); store.del('projectId'); store.del('demoUser');
    Object.assign(state, { token: null, me: null, user: null, realProjects: [], projects: [], project: null, names: null, demoId: null, fundForbidden: false });
  }

  // ---------- data loading ----------
  async function loadBasics() {
    if (state.me) return;
    const me = await api('me/');
    const projects = me.owner_id ? await api('projects/') : me.role === 'MANAGER' ? await managerProjects(me) : [];
    state.me = me;
    state.realProjects = projects.results || projects;
    applyUser();
  }

  // A manager cannot list projects (owner-only API): /me/ says which projects they are on, and the people
  // endpoint (open to project members) gives each one's name. Older servers: use the projects their fund shows.
  async function managerProjects(me) {
    if (me.project_permissions) {
      return Promise.all(Object.keys(me.project_permissions).map(async id => {
        const p = (await api(`projects/${id}/people/`)).project;
        return { id: p.id, name: p.name, code: p.code };
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
    state.projects = state.realProjects.filter(p => mine.has(p.id));
    const saved = Number(store.get('projectId'));
    state.project = state.projects.find(p => p.id === saved) || state.projects[0] || null;
    state.names = null;
  }

  // ---------- profile bar ----------
  function renderUserbar(show) {
    const bar = document.getElementById('userbar');
    bar.hidden = !(show && state.user);
    if (bar.hidden) return;
    const role = (state.project && Authz.roleIn(state.user, state.project.id)) || state.user.role;
    bar.innerHTML = `
      <div class="who"><b>${esc(state.user.name)}</b><span class="role">${role ? esc(Authz.ROLE_LABEL[role]) : 'NO ROLE'}</span></div>
      ${can('canChangeOwnPassword') ? '<a class="out" href="#/profile">🔑 Change Password</a>' : ''}
      <button type="button" class="out" id="logoutBtn">🚪 Logout</button>
      ${DEMO ? `<label class="demo">Test as
        <select id="demoSel" aria-label="Test user"><option value="">Real login</option>${Authz.DEMO_USERS.map(u => `<option value="${u.id}" ${u.id === state.demoId ? 'selected' : ''}>${esc(u.email)}</option>`).join('')}</select></label>` : ''}`;
    document.getElementById('logoutBtn').onclick = doLogout;
    const sel = document.getElementById('demoSel');
    if (sel) sel.onchange = () => {
      state.demoId = sel.value || null;
      if (state.demoId) store.set('demoUser', state.demoId); else store.del('demoUser');
      applyUser();
      if (location.hash === '#/home' || !location.hash) route(); else location.hash = '#/home';
    };
  }

  // Names for turning ids into words (labour / suppliers / contractors of this project).
  async function loadNames(force) {
    if (state.names && !force && state.names.projectId === state.project.id) return state.names;
    const [labour, suppliers, contracts] = await Promise.all([
      api('labour/'), api('suppliers/'), api(`contractor-contracts/?project=${state.project.id}`),
    ]);
    state.names = { projectId: state.project.id, labour, suppliers, contracts };
    return state.names;
  }

  // ---------- chrome (back button, tabs) ----------
  function chrome(tab, back) {
    const loggedIn = !!state.token && tab !== 'login';
    $view.onclick = null;   // a screen may attach a delegated click handler
    renderUserbar(loggedIn);
    const nav = document.getElementById('tabs');
    const items = loggedIn && state.user ? Authz.navFor(can) : [];
    const more = items.filter(n => !n.primary);
    nav.innerHTML = items.filter(n => n.primary).map(n => `<a href="${n.hash}" data-tab="${n.id}"><span>${n.icon}</span>${n.hi}</a>`).join('')
      + (more.length ? `<button type="button" id="menuBtn" data-tab="menu" aria-expanded="false"><span>☰</span>मेन्यू</button>` : '');
    const sheet = document.getElementById('menuSheet');
    sheet.hidden = true;
    sheet.innerHTML = more.map(n => `<a href="${n.hash}" data-tab="${n.id}"><span>${n.icon}</span>${n.hi} <small>${n.en}</small></a>`).join('');
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
    if (back) document.getElementById('backBtn').setAttribute('href', back);
    document.querySelectorAll('.tabs a').forEach(a => a.classList.toggle('on', a.dataset.tab === tab));
    window.scrollTo(0, 0);
  }

  // Labour payments and other expenses are separate permissions.
  const allowedCats = () => CATS.filter(c => Authz.canAddCategory(can, c.key));
  const viewableCats = () => CATS.filter(c => Authz.canViewCategory(can, c.key));

  // ---------- screens ----------
  function screenNoAccess() {
    chrome('noaccess', '#/home');
    $view.innerHTML = `<div class="empty"><div class="ico">🔒</div><h2>इस पेज की अनुमति नहीं है</h2><p>You do not have access to this page.</p></div>
      <a class="btn line" href="#/home">🏠 होम पर जाएँ <span class="sub">Home</span></a>`;
  }

  function screenLogin() {
    chrome('login');
    $view.innerHTML = `
      <h1>🙏 नमस्ते</h1>
      <p class="muted">अपना नाम और पासवर्ड भरिए.</p>
      <div id="err"></div>
      <form id="f" novalidate>
        <div class="step"><label for="u">आपका नाम <small>(User name)</small></label>
          <input id="u" type="text" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" enterkeyhint="next"></div>
        <div class="step"><label for="p" class="q">पासवर्ड <small>(Password)</small></label>
          <input id="p" type="password" autocomplete="current-password" enterkeyhint="go">
          <button type="button" class="btn line" id="show" style="min-height:52px;font-size:1rem">👁 पासवर्ड दिखाएँ</button></div>
        <button class="btn green big" type="submit">✅ अंदर जाएँ <span class="sub">LOGIN</span></button>
      </form>`;
    const p = document.getElementById('p');
    document.getElementById('show').onclick = e => {
      p.type = p.type === 'password' ? 'text' : 'password';
      e.currentTarget.textContent = p.type === 'password' ? '👁 पासवर्ड दिखाएँ' : '🙈 पासवर्ड छिपाएँ';
    };
    document.getElementById('f').onsubmit = async ev => {
      ev.preventDefault();
      const err = document.getElementById('err');
      const username = document.getElementById('u').value.trim();
      if (!username) { err.innerHTML = errBox('कृपया अपना नाम भरें.'); return; }
      if (!p.value) { err.innerHTML = errBox('कृपया पासवर्ड भरें.'); return; }
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
        <h1>यह लॉगिन खर्च डालने के लिए नहीं है</h1>
        <p>कृपया मालिक वाले नाम और पासवर्ड से अंदर जाएँ.</p></div>
      <button class="btn line" id="out">🚪 बाहर निकलें <span class="sub">LOGOUT</span></button>`;
    document.getElementById('out').onclick = doLogout;
  }

  async function doLogout() {
    if (permDirty() && !confirm('You have unsaved permission changes. Log out without saving?')) return;
    permDraft = null;
    try { await api('auth/logout/', { method: 'POST' }); } catch (e) { /* still log out on this phone */ }
    logoutLocal();
    location.hash = '#/login';
  }

  function screenHome() {
    chrome('home');
    const proj = state.project;
    $view.innerHTML = `
      <h1>नमस्ते, ${esc(state.user.name)} 🙏</h1>
      ${proj ? '<p class="muted">प्रोजेक्ट: <b>' + esc(proj.name) + '</b></p>' : noProject()}
      <div class="home-grid">
        ${canAdd() ? '<a class="btn green big" href="#/add"><span class="ico">➕</span><span>खर्च डालें<span class="sub">Add Expense</span></span></a>' : ''}
        ${can('canViewExpenses') ? '<a class="btn big" href="#/list"><span class="ico">📋</span><span>खर्च देखें<span class="sub">View Expenses</span></span></a>' : ''}
        ${can('canViewProjects') ? '<a class="btn big" href="#/project"><span class="ico">🏠</span><span>मेरा प्रोजेक्ट<span class="sub">My Project</span></span></a>' : ''}
        ${can('canViewManagerFund') ? '<a class="btn big" href="#/fund"><span class="ico">💰</span><span>मैनेजर फंड<span class="sub">Manager Fund</span></span></a>' : ''}
        ${can('canViewReports') ? '<a class="btn big" href="#/reports"><span class="ico">📊</span><span>हिसाब देखें<span class="sub">Total Expense</span></span></a>' : ''}
      </div>`;
  }

  async function screenProject() {
    chrome('project', '#/home');
    loading();
    try {
      const totals = await Promise.all(state.projects.map(p => api(`projects/${p.id}/summary/`).catch(() => null)));
      if (!state.projects.length) { $view.innerHTML = noProject(); return; }
      const many = state.projects.length > 1;
      $view.innerHTML = `<h1>🏗️ ${many ? 'मेरे प्रोजेक्ट' : 'मेरा प्रोजेक्ट'} <small>Projects</small></h1>` +
        (can('canCreateProject') ? '<a class="btn green" href="#/newproject">➕ नया प्रोजेक्ट <span class="sub">New Project</span></a>' : '') +
        (many ? '<p class="muted">जिस प्रोजेक्ट में काम करना है उसे छूइए.</p>' : '') +
        (state.projects.map((p, i) => `
          <${many ? 'button type="button"' : 'div'} class="card pick ${p.id === state.project.id ? 'on' : ''}" data-id="${p.id}">
            <div class="row"><b style="font-size:1.3rem">${esc(p.name)}</b>${many && p.id === state.project.id ? '<span class="pill">✔ चुना है</span>' : ''}</div>
            <div><span class="pill">${esc(Authz.ROLE_LABEL[Authz.roleIn(state.user, p.id)] || '')}</span></div>
            ${p.location ? `<div class="muted">📍 ${esc(p.location)}</div>` : ''}
            ${p.plot_size ? `<div class="muted">प्लॉट: ${esc(p.plot_size)}</div>` : ''}
            <div class="muted">${esc(STATUS_HI[p.status] || '')}${p.start_date ? ' · शुरू: ' + niceDate(p.start_date) : ''}</div>
            ${totals[i] ? `<div style="margin-top:8px">कुल खर्च <span class="amount">${money(totals[i].total_expense)}</span></div>` : ''}
          </${many ? 'button' : 'div'}>`).join('') || noProject());
      $view.querySelectorAll('button.pick').forEach(b => b.onclick = () => {
        state.project = state.projects.find(p => p.id === Number(b.dataset.id));
        store.set('projectId', state.project.id);
        state.names = null;
        location.hash = '#/home';
      });
    } catch (e) { $view.innerHTML = errBox(friendly(e)); }
  }

  // ----- Add expense -----
  async function screenAdd() {
    chrome('add', '#/home');
    if (!state.project) { $view.innerHTML = noProject(); return; }
    if (!allowedCats().length) { $view.innerHTML = '<div class="empty"><div class="ico">🔒</div><h2>अभी कोई खर्च श्रेणी उपलब्ध नहीं है</h2><p>No expense category is available to you.</p></div>'; return; }
    loading();
    let names;
    try { names = await loadNames(); } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }

    const f = { cat: '', amount: '', name: '', contractId: '', supplierId: '', what: '', date: today(), mode: 'CASH', note: '' };
    let saving = false;
    $view.innerHTML = `
      <h1>➕ खर्च डालें <small>Add Expense</small></h1>
      <p class="muted">प्रोजेक्ट: <b>${esc(state.project.name)}</b></p>
      ${state.projects.length > 1 && can('canViewProjects') ? '<a class="btn line" href="#/project" style="min-height:56px;font-size:1.05rem">🔁 प्रोजेक्ट बदलें <span class="sub">Change Project</span></a>' : ''}
      <div id="top"></div>
      <div class="step" id="s-cat"><div class="q"><span class="num">1</span>किस चीज़ का खर्च है?</div>
        <div class="choices">${allowedCats().map(c => `<button type="button" class="choice" data-cat="${c.key}" aria-pressed="false"><span class="ico">${c.icon}</span>${c.hi}<br><small>${c.en}</small></button>`).join('')}</div>
        <div class="field-error" id="e-cat"></div></div>
      <div class="step" id="s-amt"><label for="amt"><span class="num">2</span>कितना पैसा? <small>Amount</small></label>
        <div class="rupee"><span>₹</span><input id="amt" type="text" inputmode="decimal" pattern="[0-9.]*" autocomplete="off" enterkeyhint="next" placeholder="0"></div>
        <div class="words" id="words"></div><div class="field-error" id="e-amt"></div></div>
      <div class="step" id="s-who"></div>
      <div class="step" id="s-date"><label for="date"><span class="num">4</span>कब दिया? <small>Date</small></label>
        <input id="date" type="date" value="${f.date}">
        <div class="quick"><button type="button" class="choice" data-d="0">आज<br><small>Today</small></button><button type="button" class="choice" data-d="1">कल<br><small>Yesterday</small></button></div>
        <div class="field-error" id="e-date"></div></div>
      <div class="step"><div class="q"><span class="num">5</span>कैसे दिया? <small>Paid by</small></div>
        <div class="choices small">${MODES.map(m => `<button type="button" class="choice" data-mode="${m.key}" aria-pressed="${m.key === 'CASH'}">${m.hi}<br><small>${m.en}</small></button>`).join('')}</div></div>
      <div class="step"><label for="note"><span class="num">6</span>कोई जानकारी? <small>Note (optional)</small></label>
        <textarea id="note" placeholder="जैसे: सीमेंट के 10 बैग"></textarea></div>
      <div id="bottom"></div>
      <button class="btn green big" id="save" type="button">💾 खर्च सेव करें <span class="sub">SAVE EXPENSE</span></button>`;

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
      $('lcount').textContent = lab.on.size ? `(${lab.on.size} चुने${hiddenSel ? ` · ${hiddenSel} खोज में छिपे` : ''})` : '';
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
              ${r.is_active ? '' : '<span class="pill warn">काम बंद</span>'}
              ${r.last_paid ? `<span class="lab-last">Last paid: ${shortDate(r.last_paid)}</span>` : ''}</span></label>
          <span class="lab-amt"><span>₹</span><input class="lab-a" type="text" inputmode="decimal" autocomplete="off" enterkeyhint="next" placeholder="0" aria-label="${esc(r.name)} राशि" value="${esc(lab.amt[r.labour] || '')}"></span>
          ${r.is_active && can('canManageLabour') ? '<button type="button" class="lab-stop">काम बंद करें <small>Mark inactive</small></button>' : ''}
        </div>`).join('')
        : '<div class="msg info">इस प्रोजेक्ट में अभी कोई चालू मज़दूर नहीं है। "नया मज़दूर" जोड़िए या पुराने मज़दूर दिखाइए.</div>';
      labFilter();
    }
    function labModal(gen) {
      const box = document.createElement('div');
      box.className = 'modal';
      box.innerHTML = `<form class="modal-box" role="dialog" aria-modal="true" aria-label="नया मज़दूर" novalidate>
        <h2 style="margin-top:0">➕ नया मज़दूर <small>Add Labour</small></h2>
        <div id="m-err"></div>
        <div id="m-fields">
          <label for="m-name">नाम / मिस्त्री <small>Name</small> *</label>
          <input id="m-name" type="text" autocomplete="off" autocapitalize="words" enterkeyhint="next">
          <label for="m-mob">मोबाइल नंबर <small>Mobile</small> *</label>
          <input id="m-mob" type="text" inputmode="tel" autocomplete="off" maxlength="15" enterkeyhint="next">
          <label for="m-type">काम का प्रकार <small>Type (optional)</small></label>
          <input id="m-type" type="text" autocomplete="off" placeholder="जैसे: मिस्त्री, हेल्पर" enterkeyhint="next">
          <label for="m-rem">जानकारी <small>Remarks (optional)</small></label>
          <input id="m-rem" type="text" autocomplete="off" enterkeyhint="done">
          <button class="btn green" type="submit" id="m-save">💾 सेव करें <span class="sub">SAVE</span></button>
        </div>
        <div id="m-dup" hidden></div>
        <button class="btn line" type="button" id="m-cancel">रद्द करें <span class="sub">Cancel</span></button>
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
          if ($('lnote')) $('lnote').innerHTML = r.reused ? '<div class="msg info">यह मज़दूर पहले से था — इस प्रोजेक्ट में चालू कर दिया.</div>' : '';
          const row = document.querySelector(`#llist .lab-row[data-id="${r.labour}"]`);
          if (row) { row.scrollIntoView({ block: 'center' }); row.querySelector('.lab-a').focus(); }
        } catch (err) { console.error('after add labour', err); }
      }

      // Same name, different mobile: ask "is this the same person?" (only when there is such a match).
      function askSame(cands, send) {
        m('m-fields').hidden = true;
        const dup = m('m-dup');
        dup.hidden = false;
        dup.innerHTML = `<h2 style="margin-top:0">क्या यह वही मज़दूर है? <small>Is this the same person?</small></h2>` +
          cands.map(c => `<div class="card"><b>${esc(c.name)}</b>
            <div class="muted">Mobile: ${esc(c.mobile_masked || '—')}</div>
            <div class="muted">${c.last_paid ? 'Last paid: ' + shortDate(c.last_paid) : 'Never paid'}</div>
            <button class="btn green" type="button" data-use="${c.labour}" style="margin-bottom:0">✔ यही है <span class="sub">Use this Labour</span></button></div>`).join('') +
          `<button class="btn line" type="button" id="m-new">➕ अलग व्यक्ति है <span class="sub">Different Person</span></button>`;
        dup.querySelectorAll('[data-use]').forEach(b => b.onclick = () => send({ use_labour: Number(b.dataset.use) }));
        m('m-new').onclick = () => send({ confirm_new: true });
      }

      box.querySelector('form').onsubmit = async ev => {
        ev.preventDefault();
        const name = m('m-name').value.trim(), mobile = m('m-mob').value.trim();
        if (!name) { m('m-err').innerHTML = errBox('कृपया नाम भरें.'); m('m-name').focus(); return; }
        if (mobile.replace(/\D/g, '').length < 10) { m('m-err').innerHTML = errBox('कृपया सही मोबाइल नंबर भरें.'); m('m-mob').focus(); return; }
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
      box.innerHTML = `<form class="modal-box" role="dialog" aria-modal="true" aria-label="नया ठेकेदार" novalidate>
        <h2 style="margin-top:0" id="m-title">➕ नया ठेकेदार <small>Add Contractor</small></h2>
        <div id="m-err"></div>
        <div id="m-fields">
          <label for="m-name">नाम <small>Name</small> *</label>
          <input id="m-name" type="text" autocomplete="off" autocapitalize="words" enterkeyhint="next">
          <label for="m-mob">मोबाइल नंबर <small>Mobile</small> *</label>
          <input id="m-mob" type="text" inputmode="tel" autocomplete="off" maxlength="15" enterkeyhint="next">
          <label for="m-type">काम का प्रकार <small>Work Type (optional)</small></label>
          <input id="m-type" type="text" autocomplete="off" placeholder="जैसे: RCC, बिजली, प्लंबिंग" enterkeyhint="next">
          <label for="m-rem">जानकारी <small>Remarks (optional)</small></label>
          <input id="m-rem" type="text" autocomplete="off" enterkeyhint="done">
          <button class="btn green" type="submit" id="m-save">➡ आगे <span class="sub">NEXT</span></button>
        </div>
        <div id="m-dup" hidden></div>
        <div id="m-contract" hidden>
          <p class="muted">ठेकेदार: <b id="m-cname"></b></p>
          <label for="c-work">काम का विवरण <small>Work Description</small> *</label>
          <input id="c-work" type="text" autocomplete="off" autocapitalize="sentences" placeholder="जैसे: RCC + Structure" enterkeyhint="next">
          <label for="c-amt">कुल ठेका राशि <small>Contract Amount</small> *</label>
          <div class="rupee"><span>₹</span><input id="c-amt" type="text" inputmode="decimal" autocomplete="off" enterkeyhint="next" placeholder="0"></div>
          <label for="c-date">ठेके की तारीख <small>Contract Date</small> *</label>
          <input id="c-date" type="date" value="${today()}">
          <label for="c-rem">जानकारी <small>Remarks (optional)</small></label>
          <input id="c-rem" type="text" autocomplete="off" enterkeyhint="done">
          <button class="btn green" type="submit" id="c-save">💾 ठेका सेव करें <span class="sub">SAVE CONTRACT</span></button>
        </div>
        <button class="btn line" type="button" id="m-cancel">रद्द करें <span class="sub">Cancel</span></button>
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
        m('m-title').innerHTML = '➕ नया ठेका <small>Add Contract</small>';
        m('m-cname').textContent = c.name;
        m('m-err').innerHTML = c.reused ? '<div class="msg info">यह ठेकेदार पहले से था — इसी को चुना है.</div>' : '';
        m('c-work').focus();
      }

      // Same name, different mobile: ask "is this the same contractor?".
      function askSame(cands, send) {
        m('m-fields').hidden = true;
        const dup = m('m-dup');
        dup.hidden = false;
        dup.innerHTML = `<h2 style="margin-top:0">क्या यह वही Contractor है? <small>Is this the same contractor?</small></h2>` +
          cands.map(c => `<div class="card"><b>${esc(c.name)}</b>
            <div class="muted">Mobile: ${esc(c.mobile_masked || '—')}</div>
            ${c.work_type ? `<div class="muted">${esc(c.work_type)}</div>` : ''}
            <button class="btn green" type="button" data-use="${c.contractor}" style="margin-bottom:0">✔ यही है <span class="sub">Use Existing Contractor</span></button></div>`).join('') +
          `<button class="btn line" type="button" id="m-new">➕ अलग ठेकेदार है <span class="sub">Different Contractor</span></button>`;
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
        if (!work) return bad('c-work', 'कृपया काम का विवरण भरें.');
        if (!(cents > 0)) return bad('c-amt', 'कृपया ठेका राशि भरें (0 से ज़्यादा).');
        if (cents >= 1e12) return bad('c-amt', 'राशि बहुत बड़ी है। कृपया जाँच लें.');
        if (!m('c-date').value) return bad('c-date', 'कृपया तारीख चुनिए.');
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
        if (!name) { m('m-err').innerHTML = errBox('कृपया नाम भरें.'); m('m-name').focus(); return; }
        if (mobile.replace(/\D/g, '').length < 10) { m('m-err').innerHTML = errBox('कृपया सही मोबाइल नंबर भरें.'); m('m-mob').focus(); return; }
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
        <div class="row"><span class="muted">पूरा काम <small>Contract</small></span><span>${money(c.contract_amount)}</span></div>
        <div class="row"><span class="muted">अब तक दिया <small>Paid</small></span><span>${money(c.paid_amount)}</span></div>
        <div class="row"><b>${bal < 0 ? 'ज़्यादा दिया' : 'देना बाकी'} <small>Balance</small></b><span class="pill ${bal < 0 ? 'warn' : ''}" style="font-size:1.1rem">${money(Math.abs(bal))}</span></div>
      </button>`;
    };

    // ----- Supplier: pick a supplier (or add one), then say what was bought -----
    function supplierModal() {
      const box = document.createElement('div');
      box.className = 'modal';
      box.innerHTML = `<form class="modal-box" role="dialog" aria-modal="true" aria-label="नया Supplier" novalidate>
        <h2 style="margin-top:0">➕ नया Supplier <small>Add Supplier</small></h2>
        <div id="m-err"></div>
        <div id="m-fields">
          <label for="m-name">नाम <small>Supplier Name</small> *</label>
          <input id="m-name" type="text" autocomplete="off" autocapitalize="words" enterkeyhint="next">
          <label for="m-mob">मोबाइल नंबर <small>Mobile</small> *</label>
          <input id="m-mob" type="text" inputmode="tel" autocomplete="off" maxlength="15" enterkeyhint="next">
          <label for="m-type">सामान का प्रकार <small>Supplier Type (optional)</small></label>
          <select id="m-type"><option value="">— चुनिए —</option>${SUPPLIER_TYPES.map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join('')}</select>
          <label for="m-rem">जानकारी <small>Remarks (optional)</small></label>
          <input id="m-rem" type="text" autocomplete="off" enterkeyhint="done">
          <button class="btn green" type="submit" id="m-save">💾 सेव करें <span class="sub">SAVE</span></button>
        </div>
        <div id="m-dup" hidden></div>
        <button class="btn line" type="button" id="m-cancel">रद्द करें <span class="sub">Cancel</span></button>
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
        dup.innerHTML = `<h2 style="margin-top:0">क्या यह वही Supplier है? <small>Is this the same supplier?</small></h2>` +
          cands.map(c => `<div class="card"><b>${esc(c.name)}</b>
            <div class="muted">Mobile: ${esc(c.mobile_masked || '—')}</div>
            ${c.supplier_type ? `<div class="muted">${esc(c.supplier_type)}</div>` : ''}
            <button class="btn green" type="button" data-use="${c.supplier}" style="margin-bottom:0">✔ यही है <span class="sub">Use Existing</span></button></div>`).join('') +
          `<button class="btn line" type="button" id="m-new">➕ अलग Supplier है <span class="sub">Different Supplier</span></button>`;
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
        if (!m('m-name').value.trim()) { m('m-err').innerHTML = errBox('कृपया नाम भरें.'); m('m-name').focus(); return; }
        if (m('m-mob').value.replace(/\D/g, '').length < 10) { m('m-err').innerHTML = errBox('कृपया सही मोबाइल नंबर भरें.'); m('m-mob').focus(); return; }
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
        <div class="q"><span class="num">3</span>किस मज़दूर को दिया? <small>Labour Payments</small> <small id="lcount"></small></div>
        <div class="lab-bar">
          <input id="lq" type="text" autocomplete="off" enterkeyhint="search" placeholder="🔍 मज़दूर खोजिए (Search Labour)" value="${esc(lab.q)}">
          ${can('canManageLabour') ? '<button type="button" class="btn line" id="ladd">➕ नया <span class="sub">Add Labour</span></button>' : ''}
        </div>
        <label class="lab-inact"><input type="checkbox" id="linact" ${lab.showInactive ? 'checked' : ''}> पुराने / काम बंद मज़दूर भी दिखाएँ <small>Show Inactive</small></label>
        <div id="lnote"></div>
        <div id="llist"><div class="spinner">⏳ रुकिए...</div></div>
        <div class="card row lab-total"><span>कुल मज़दूरी <small>Total Labour Payment</small></span><span class="amount" id="ltotal">${money(0)}</span></div>
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
        if (!link || !confirm(`${link.name} को इस प्रोजेक्ट में "काम बंद" करें?\nपुराना हिसाब बना रहेगा.`)) return;
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
      if (cat === 'LABOUR') { f.name = ''; f.contractId = ''; f.supplierId = ''; f.what = ''; drawLabour(); return; }
      if (!cat) {
        html = '<div class="q"><span class="num">3</span>किसको दिया? <small>Name</small></div><p class="muted">पहले ऊपर बताइए कि किस चीज़ का खर्च है.</p>';
      } else if (cat === 'CONTRACTOR') {
        html = `<div class="q"><span class="num">3</span>किस ठेकेदार को दिया? <small>Contractor</small></div>
          ${can('canManageContractors') ? '<button class="btn line" type="button" id="cadd">➕ नया ठेकेदार <span class="sub">Add Contractor</span></button>' : ''}` +
          (names.contracts.length
            ? names.contracts.map(contractCard).join('')
            : '<div class="msg info">इस प्रोजेक्ट में अभी कोई ठेकेदार नहीं जुड़ा है। "नया ठेकेदार" दबाइए.</div>') +
          '<div class="field-error" id="e-who"></div>';
      } else if (cat === 'SUPPLIER') {
        html = `<div class="q"><span class="num">3</span>किस सप्लायर को दिया? <small>Supplier</small></div>
          ${can('canManageSuppliers') ? '<button class="btn line" type="button" id="sadd">➕ नया Supplier <span class="sub">Add Supplier</span></button>' : ''}` +
          (names.suppliers.length
            ? `<select id="supplier"><option value="">— सप्लायर चुनिए —</option>${names.suppliers.map(x => `<option value="${x.id}">${esc(x.name)}${x.supplier_type ? ' — ' + esc(x.supplier_type) : ''}</option>`).join('')}</select>`
            : '<div class="msg info">अभी कोई सप्लायर नहीं है। "नया Supplier" दबाइए.</div>') +
          `<label for="material" style="margin-top:18px;display:block;font-weight:700">क्या सामान लिया? <small>Material / Item</small> *</label>
          <input id="material" type="text" autocomplete="off" autocapitalize="sentences" placeholder="जैसे: सीमेंट, रेत" enterkeyhint="next">
          <div class="field-error" id="e-who"></div>`;
      } else {
        const label = cat === 'LABOUR' ? 'किस मज़दूर को दिया?' : cat === 'SUPPLIER' ? 'किस सप्लायर को दिया?' : 'किसको दिया?';
        html = `<label for="name"><span class="num">3</span>${label} <small>Name</small></label>
          <input id="name" type="text" autocomplete="off" autocapitalize="words" enterkeyhint="next" placeholder="नाम लिखिए">
          <div class="suggest" id="sug"></div><div class="hint" id="newhint"></div><div class="field-error" id="e-who"></div>` +
          (cat === 'MISCELLANEOUS' ? `<label for="what" style="margin-top:18px;display:block;font-weight:700">किस काम का? <small>(optional, जैसे: चाय, ट्रांसपोर्ट)</small></label><input id="what" type="text" autocomplete="off" autocapitalize="sentences" enterkeyhint="next">` : '');
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
      $('newhint').textContent = f.cat !== 'MISCELLANEOUS' && typed && !exact ? '✚ यह नया नाम जुड़ जाएगा.' : '';
    }

    document.querySelectorAll('[data-cat]').forEach(b => b.onclick = () => {
      f.cat = b.dataset.cat;
      document.querySelectorAll('[data-cat]').forEach(x => x.setAttribute('aria-pressed', x === b));
      setErr('e-cat', '');
      drawWho();
    });
    document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => {
      f.mode = b.dataset.mode;
      document.querySelectorAll('[data-mode]').forEach(x => x.setAttribute('aria-pressed', x === b));
    });
    document.querySelectorAll('[data-d]').forEach(b => b.onclick = () => { $('date').value = b.dataset.d === '0' ? today() : yesterday(); setErr('e-date', ''); });
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
      if (!f.cat) return fail('e-cat', 'कृपया बताइए किस चीज़ का खर्च है.', 's-cat');
      if (f.cat === 'LABOUR') {
        if (!lab.on.size) return fail('e-lab', 'कृपया कम से कम एक मज़दूर चुनिए.', 's-who');
        const bad = [...lab.on].filter(id => !(paise(lab.amt[id]) > 0));
        $('llist').querySelectorAll('.lab-row').forEach(r => r.classList.toggle('bad-row', bad.includes(Number(r.dataset.id))));
        if (bad.length) {
          $('llist').querySelector('.bad-row').scrollIntoView({ behavior: 'smooth', block: 'center' });
          setErr('e-lab', 'चुने हुए हर मज़दूर की राशि भरिए (0 से ज़्यादा).');
          return true;
        }
      } else {
        if (!f.amount || !(amt > 0)) return fail('e-amt', 'कृपया राशि भरें.', 's-amt');
        if (amt >= 1e10) return fail('e-amt', 'राशि बहुत बड़ी है। कृपया जाँच लें.', 's-amt');
      }
      if (f.cat === 'CONTRACTOR' && !f.contractId) return fail('e-who', 'कृपया ठेकेदार चुनिए.', 's-who');
      if (f.cat === 'SUPPLIER' && !f.supplierId) return fail('e-who', 'कृपया सप्लायर चुनिए.', 's-who');
      if (f.cat === 'SUPPLIER' && !f.what.trim()) return fail('e-who', 'कृपया बताइए क्या सामान लिया.', 's-who');
      if (f.cat === 'MISCELLANEOUS' && !f.name.trim()) return fail('e-who', 'कृपया नाम भरें.', 's-who');
      if (!$('date').value) return fail('e-date', 'कृपया तारीख चुनिए.', 's-date');
      return false;
    }

    $('save').onclick = async () => {
      if (saving) return;
      $('top').innerHTML = ''; $('bottom').innerHTML = '';
      if (firstError()) return;
      saving = true; $('save').disabled = true; $('save').firstChild.textContent = '⏳ सेव हो रहा है... ';
      try {
        if (f.cat === 'LABOUR') {
          const picked = labRows().filter(r => lab.on.has(r.labour));
          const res = await api('labour-payments/', { method: 'POST', body: {
            project: state.project.id,
            expense_date: $('date').value,
            paid_by_owner: state.me.owner_id,
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
          paid_by_owner: state.me.owner_id,
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
        saving = false; $('save').disabled = false; $('save').firstChild.textContent = '💾 खर्च सेव करें ';
        $('bottom').innerHTML = errBox(serverMsg(e) || friendly(e));
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
      <a class="btn green big" href="#/add">➕ एक और खर्च डालें <span class="sub">Add Another</span></a>
      ${can('canViewExpenses') ? '<a class="btn line" href="#/list">📋 खर्च देखें <span class="sub">View Expenses</span></a>' : ''}
      <a class="btn line" href="#/home">🏠 होम पर जाएँ <span class="sub">Home</span></a>`;
  }

  // ----- Expenses list -----
  let listState = { cat: '', shown: 20 };

  async function screenList(params) {
    chrome('list', '#/home');
    if (!state.project) { $view.innerHTML = noProject(); return; }
    listState = { cat: params.get('cat') || '', shown: 20 };
    loading();
    let rows, names;
    try {
      [names, rows] = await Promise.all([
        loadNames(true),
        api(`reports/payment-register/?project=${state.project.id}&page_size=500`),
      ]);
      rows = rows.results || rows;
    } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
    const seen = new Set(viewableCats().map(c => c.key));
    rows = rows.filter(r => seen.has(r.expense_category));
    if (!seen.has(listState.cat)) listState.cat = '';

    const nameOf = r => {
      if (r.labour) return (names.labour.find(x => x.id === r.labour) || {}).name;
      if (r.supplier) return (names.suppliers.find(x => x.id === r.supplier) || {}).name;
      if (r.contractor_contract) return (names.contracts.find(x => x.id === r.contractor_contract) || {}).contractor_name;
      return r.payee_name;
    };

    function draw() {
      const shown = rows.filter(r => !listState.cat || r.expense_category === listState.cat);
      const total = shown.reduce((s, r) => s + Number(r.amount), 0);
      const chip = (key, label) => `<button type="button" class="choice" data-c="${key}" aria-pressed="${listState.cat === key}">${label}</button>`;
      $view.innerHTML = `
        <h1>📋 खर्च देखें <small>View Expenses</small></h1>
        <div class="chips">${chip('', 'सब')}${viewableCats().map(c => chip(c.key, `${c.icon} ${c.hi}`)).join('')}</div>
        <div class="card"><div class="row"><span>कुल खर्च <small>Total</small></span><span class="amount">${money(total)}</span></div></div>` +
        (shown.length ? shown.slice(0, listState.shown).map(r => `
          <div class="card item">
            <div class="row"><span class="who">${esc(nameOf(r) || '—')}</span><span class="amount">${money(r.amount)}</span></div>
            <div class="meta">${CAT[r.expense_category].icon} ${CAT[r.expense_category].hi}${r.expense_category === 'MISCELLANEOUS' && r.expense_type !== 'Other' ? ' · ' + esc(r.expense_type) : ''} · ${niceDate(r.expense_date)}</div>
            ${r.expense_category === 'SUPPLIER' && r.description ? `<div class="note">🧱 ${esc(r.description)}</div>` : ''}
            ${r.remarks ? `<div class="note">📝 ${esc(r.remarks)}</div>` : ''}
            ${can('canEditExpense') || can('canDeleteExpense') ? `<div class="row-actions">${can('canEditExpense') ? '<button type="button" class="btn line act">✏️ Edit</button>' : ''}${can('canDeleteExpense') ? '<button type="button" class="btn line danger act">🗑 Delete</button>' : ''}</div>` : ''}
          </div>`).join('') : `<div class="empty"><div class="ico">📭</div><p>अभी कोई खर्च नहीं है.</p>${canAdd() ? '<a class="btn green" href="#/add">➕ खर्च डालें <span class="sub">Add Expense</span></a>' : ''}</div>`) +
        (shown.length > listState.shown ? '<button type="button" class="btn line" id="more">⬇ और दिखाएँ <span class="sub">Show more</span></button>' : '');
      $view.querySelectorAll('.act').forEach(b => b.onclick = () => {
        b.closest('.card').insertAdjacentHTML('beforeend', NOT_CONNECTED);   // no edit/delete API yet (Phase 2)
        b.closest('.row-actions').remove();
      });
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
    try { d = await api(`projects/${state.project.id}/dashboard/`); } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
    const cats = viewableCats();
    const all = cats.length === CATS.length;
    // Everything on this screen is limited to the categories you may view.
    const total = all ? Number(d.total_expense) : cats.reduce((sum, c) => sum + Number(d.category_breakup[c.key] || 0), 0);
    const pct = v => (total > 0 ? Math.round((Number(v) / total) * 100) : 0);

    $view.innerHTML = `
      <h1>📊 हिसाब देखें <small>Total Expense</small></h1>
      <p class="muted">प्रोजेक्ट: <b>${esc(state.project.name)}</b></p>
      <div class="card"><div class="muted">कुल खर्च <small>Total Expense</small></div><div class="big-total">${money(total)}</div></div>
      <h2>किस पर कितना खर्च हुआ</h2>
      ${cats.map(c => { const link = can(Authz.reportPermission(c.key)); return `
        <${link ? `a href="#/report/${c.key}"` : 'div'} class="card">
          <div class="row"><b>${c.icon} ${c.hi} <small>${c.en}</small></b><span class="amount">${money(d.category_breakup[c.key])}</span></div>
          <div class="bar"><i style="width:${pct(d.category_breakup[c.key])}%"></i></div>
          <div class="muted" style="margin-top:6px">${pct(d.category_breakup[c.key])}%${link ? ' · देखने के लिए छूइए ›' : ''}</div>
        </${link ? 'a' : 'div'}>`; }).join('')}
      ${all && d.owner_contribution.length ? `<h2>मालिक का हिस्सा <small>Owner Share</small></h2>` + d.owner_contribution.map(o => `
        <div class="card"><div class="row"><b>${esc(o.owner_name)}</b><span class="amount">${money(o.total)}</span></div>
        <div class="muted">कुल खर्च का ${pct(o.total)}% दिया</div></div>`).join('') : ''}
      ${can('canViewContractors') && d.contractor_positions.length ? `<h2>ठेकेदार का हिसाब</h2>` + d.contractor_positions.map(c => {
        const bal = Number(c.balance);
        return `<div class="card"><b>${esc(c.contractor_name)}</b>
          ${c.work_description ? `<div class="muted">${esc(c.work_description)}</div>` : ''}
          <div class="row"><span class="muted">पूरा काम</span><span>${money(c.contract_amount)}</span></div>
          <div class="row"><span class="muted">अब तक दिया</span><span>${money(c.paid_amount)}</span></div>
          <div class="row"><b>${bal < 0 ? 'ज़्यादा दिया' : 'देना बाकी'}</b><span class="pill ${bal < 0 ? 'warn' : ''}" style="font-size:1.1rem">${money(Math.abs(bal))}</span></div></div>`;
      }).join('') : ''}`;
  }

  async function screenReportOne(key) {
    const c = CAT[key];
    if (!c || !state.project) { location.hash = '#/reports'; return; }
    chrome({ LABOUR: 'labour', SUPPLIER: 'suppliers', CONTRACTOR: 'contractors' }[key] || 'reports', '#/reports');
    loading();
    const q = `?project=${state.project.id}`;
    try {
      let rows, total;
      if (key === 'LABOUR') { const r = await api('reports/labour/' + q); rows = r.labour.map(x => [x.labour_name, x.total_paid]); total = r.grand_total; }
      else if (key === 'SUPPLIER') { const r = await api('reports/supplier/' + q); rows = r.suppliers.map(x => [x.supplier_name, x.total_paid]); total = r.grand_total; }
      else if (key === 'CONTRACTOR') {
        // One card per contract, so a contractor with several contracts shows each one.
        const r = await api('reports/contractor/' + q);
        total = r.contracts.reduce((s, x) => s + Number(x.paid_amount), 0);
        rows = r.contracts.map(x => {
          const bal = Number(x.balance);
          return `<b>${esc(x.contractor_name)}</b>
            ${x.work_description ? `<div class="muted">${esc(x.work_description)}</div>` : ''}
            <div class="row"><span class="muted">पूरा काम</span><span>${money(x.contract_amount)}</span></div>
            <div class="row"><span class="muted">अब तक दिया</span><span>${money(x.paid_amount)}</span></div>
            <div class="row"><b>${bal < 0 ? 'ज़्यादा दिया' : 'देना बाकी'}</b><span class="pill ${bal < 0 ? 'warn' : ''}" style="font-size:1.1rem">${money(Math.abs(bal))}</span></div>`;
        });
      }
      else { const r = await api('reports/misc/' + q); rows = r.expense_types.map(x => [x.expense_type, x.total]); total = r.grand_total; }
      $view.innerHTML = `
        <h1>${c.icon} ${c.hi} <small>${c.en}</small></h1>
        <div class="card"><div class="muted">कुल खर्च <small>Total</small></div><div class="big-total">${money(total)}</div></div>` +
        (rows.length ? rows.map(row => `<div class="card">${key === 'CONTRACTOR' ? row : `<div class="row"><b>${esc(row[0])}</b><span class="amount">${money(row[1])}</span></div>`}</div>`).join('')
          : '<div class="empty"><div class="ico">📭</div>अभी कोई खर्च नहीं है.</div>') +
        (can('canViewExpenses') ? `<a class="btn line" href="#/list?cat=${key}">📋 सारे खर्च देखें <span class="sub">View all</span></a>` : '');
    } catch (e) { $view.innerHTML = errBox(friendly(e)); }
  }

  // ---------- management screens (UI only: the server has no API for these yet -> Phase 2) ----------
  const NOT_CONNECTED = '<div class="msg info">यह सिर्फ़ स्क्रीन है — सर्वर से जुड़ना अभी बाकी है, इसलिए कुछ सेव नहीं हुआ.<br><small>UI only (Phase 1): nothing was saved. Saving to the server comes in Phase 2.</small></div>';
  const roleName = r => Authz.ROLE_LABEL[r] || '—';
  // Who may be reset: the permission says the capability exists; this says which people.
  //  - can manage users (SUPER_ADMIN): anyone else
  //  - otherwise: only non-owner members of the project you are working in (never a super admin)
  function resetEligible(u) {
    if (!can('canResetUserPassword') || u.id === state.user.id) return false;
    if (can('canManageUsers')) return true;
    const role = state.project && Authz.roleIn(u, state.project.id);
    return !u.allProjects && !!role && role !== Authz.ROLES.OWNER;
  }
  const projName = id => (state.realProjects.find(p => p.id === id) || {}).name || '';
  // Users we can list: the test users in test mode, otherwise just you (no users API yet).
  const people = () => (DEMO ? Authz.directory(state.realProjects) : [state.user]);
  const passwordFields = (withOld) => `
    ${withOld ? '<label for="pw0">पुराना पासवर्ड <small>Current password</small></label><input id="pw0" type="password" autocomplete="current-password">' : ''}
    <label for="pw1">नया पासवर्ड <small>New password (min 8)</small></label><input id="pw1" type="password" autocomplete="new-password">
    <label for="pw2">नया पासवर्ड दोबारा <small>Confirm</small></label><input id="pw2" type="password" autocomplete="new-password">`;
  // Checks the typed passwords; never stores or sends them.
  function passwordForm(formId, withOld) {
    const f = document.getElementById(formId), $ = id => document.getElementById(id);
    f.onsubmit = ev => {
      ev.preventDefault();
      const out = $('pwmsg');
      if (withOld && !$('pw0').value) { out.innerHTML = errBox('कृपया पुराना पासवर्ड भरें.'); return; }
      if ($('pw1').value.length < 8) { out.innerHTML = errBox('नया पासवर्ड कम से कम 8 अक्षर का हो.'); return; }
      if ($('pw1').value !== $('pw2').value) { out.innerHTML = errBox('दोनों पासवर्ड एक जैसे नहीं हैं.'); return; }
      f.reset();
      out.innerHTML = NOT_CONNECTED;
    };
  }

  function screenProfile() {
    chrome('profile', '#/home');
    const role = (state.project && Authz.roleIn(state.user, state.project.id)) || state.user.role;
    $view.innerHTML = `<h1>🔑 पासवर्ड बदलें <small>Change Password</small></h1>
      <div class="card"><b>${esc(state.user.name)}</b><div class="muted">${esc(roleName(role))}${state.user.email ? ' · ' + esc(state.user.email) : ''}</div></div>
      <div id="pwmsg"></div>
      <form id="pwf" novalidate>${passwordFields(true)}<button class="btn green" type="submit">💾 पासवर्ड बदलें <span class="sub">CHANGE PASSWORD</span></button></form>`;
    passwordForm('pwf', true);
  }

  function screenResetPassword(id) {
    const target = people().find(u => u.id === id);
    if (!target || !resetEligible(target)) { location.hash = '#/home'; return; }
    chrome('users', can('canManageUsers') ? '#/users' : '#/members');
    $view.innerHTML = `<h1>🔑 पासवर्ड रीसेट <small>Reset Password</small></h1>
      <div class="card"><b>${esc(target.name)}</b><div class="muted">${esc(roleName(target.role))}${target.email ? ' · ' + esc(target.email) : ''}</div></div>
      <div id="pwmsg"></div>
      <form id="pwf" novalidate>${passwordFields(false)}<button class="btn green" type="submit">💾 नया पासवर्ड सेट करें <span class="sub">RESET PASSWORD</span></button></form>`;
    passwordForm('pwf', false);
  }

  function screenUsers() {
    chrome('users', '#/home');
    $view.innerHTML = `<h1>👥 यूज़र <small>Users</small></h1>` + NOT_CONNECTED +
      people().map(u => `<div class="card"><div class="row"><b>${esc(u.name)}</b><span class="pill">${esc(roleName(u.role))}</span></div>
        ${u.email ? `<div class="muted">${esc(u.email)}</div>` : ''}
        <div class="muted">${u.allProjects ? 'सारे प्रोजेक्ट <small>All projects</small>' : u.assignedProjects.length ? u.assignedProjects.map(a => esc(projName(a.projectId)) + ' (' + roleName(a.role) + ')').join(', ') : 'कोई प्रोजेक्ट नहीं'}</div>
        ${resetEligible(u) ? `<a class="btn line" href="#/resetpw/${esc(u.id)}" style="min-height:52px;font-size:1rem">🔑 पासवर्ड रीसेट <span class="sub">Reset Password</span></a>` : ''}</div>`).join('');
  }

  function screenMembers() {
    chrome('members', '#/home');
    if (!state.project) { $view.innerHTML = noProject(); return; }
    const pid = state.project.id;
    const members = people().filter(u => !u.allProjects && Authz.roleIn(u, pid));
    const admins = people().filter(u => u.allProjects);
    $view.innerHTML = `<h1>🤝 सदस्य <small>Project Members</small></h1><p class="muted">प्रोजेक्ट: <b>${esc(state.project.name)}</b></p>
      <div id="mmsg">${NOT_CONNECTED}</div>` +
      admins.map(u => `<div class="card"><div class="row"><b>${esc(u.name)}</b><span class="pill">${esc(roleName(u.role))}</span></div><div class="muted">सारे प्रोजेक्ट पर अधिकार <small>Access to all projects</small></div></div>`).join('') +
      (members.map(u => {
        const role = Authz.roleIn(u, pid);
        const reset = resetEligible(u);
        return `<div class="card"><div class="row"><b>${esc(u.name)}</b><span class="pill">${esc(roleName(role))}</span></div>
          ${u.email ? `<div class="muted">${esc(u.email)}</div>` : ''}
          ${can('canManageProjectMembers') ? `<button type="button" class="btn line m-remove" data-name="${esc(u.name)}" style="min-height:52px;font-size:1rem">➖ हटाएँ <span class="sub">Remove</span></button>` : ''}
          ${reset ? `<a class="btn line" href="#/resetpw/${esc(u.id)}" style="min-height:52px;font-size:1rem">🔑 पासवर्ड रीसेट <span class="sub">Reset Password</span></a>` : ''}</div>`;
      }).join('') || '<div class="empty">अभी कोई और सदस्य नहीं है.</div>') +
      `<h2>➕ सदस्य जोड़ें <small>Add Member</small></h2>
      <form id="addm" novalidate><label for="m-who">ईमेल / यूज़र नाम <small>Email or username</small></label><input id="m-who" type="text" autocomplete="off" autocapitalize="none">
        <label for="m-role">भूमिका <small>Role</small></label>
        <select id="m-role"><option value="owner">OWNER</option><option value="manager">MANAGER</option><option value="viewer" selected>VIEWER</option></select>
        <button class="btn green" type="submit">➕ जोड़ें <span class="sub">ADD MEMBER</span></button></form>`;
    $view.querySelectorAll('.m-remove').forEach(b => b.onclick = () => {
      if (confirm(`${b.dataset.name} को इस प्रोजेक्ट से हटाएँ?`)) document.getElementById('mmsg').innerHTML = NOT_CONNECTED;
    });
    document.getElementById('addm').onsubmit = ev => {
      ev.preventDefault();
      const mm = document.getElementById('mmsg');
      if (!document.getElementById('m-who').value.trim()) { mm.innerHTML = errBox('कृपया ईमेल या यूज़र नाम भरें.'); return; }
      mm.innerHTML = NOT_CONNECTED;
    };
  }

  function screenSettings() {
    chrome('settings', '#/home');
    const p = state.project;
    $view.innerHTML = `<h1>⚙️ सेटिंग <small>Settings</small></h1>` + NOT_CONNECTED +
      (p && can('canManageProjectSettings') ? `<h2>प्रोजेक्ट सेटिंग <small>Project Settings</small></h2>
        <div class="card"><b style="font-size:1.3rem">${esc(p.name)}</b>
          <div class="muted">कोड: ${esc(p.code || '—')}</div>
          ${p.location ? `<div class="muted">📍 ${esc(p.location)}</div>` : ''}
          ${p.plot_size ? `<div class="muted">प्लॉट: ${esc(p.plot_size)}</div>` : ''}
          <div class="muted">${esc(STATUS_HI[p.status] || '')}${p.start_date ? ' · शुरू: ' + niceDate(p.start_date) : ''}</div></div>
        ${can('canManageProjectMembers') ? '<a class="btn line" href="#/members">🤝 सदस्य <span class="sub">Project Members</span></a>' : ''}` : (can('canManageProjectSettings') ? noProject() : '')) +
      (can('canManagePermissions') ? `<h2>🔐 Role & Permissions</h2>
        <a class="btn line" href="#/permissions">🔐 Role & Permissions <span class="sub">Choose what each role can do</span></a>` : '') +
      (can('canManageApplicationSettings') ? `<h2>ऐप सेटिंग <small>Application Settings</small></h2>
        <div class="card muted">सिर्फ़ Super Admin को दिखता है. अभी कोई ऐप सेटिंग नहीं है.<br><small>Super Admin only. No application settings yet.</small></div>` : '');
  }

  // ----- Role & Permissions: edits the same matrix that can() reads (authz.js). Phase 1: saved in this browser. -----
  let permDraft = null, permFlash = '', permRole = 'owner';       // permDraft = edits not saved yet
  const permDirty = () => !!permDraft && JSON.stringify(permDraft) !== JSON.stringify(Authz.getMatrix());
  window.addEventListener('beforeunload', e => { if (permDirty()) { e.preventDefault(); e.returnValue = ''; } });

  function screenPermissions() {
    chrome('settings', '#/settings');
    if (!permDraft) permDraft = Authz.getMatrix();
    const flash = permFlash; permFlash = '';
    const roles = Authz.ROLE_LIST, defs = Authz.DEFINITIONS;
    const cell = (d, r) => Authz.isFixed(d.key, r)
      ? `<span class="lock">🔒 ${permDraft[d.key][r] ? 'Required' : 'OFF'}</span>`
      : `<button type="button" class="sw" role="switch" data-perm="${d.key}" data-role="${r}" aria-label="${esc(d.label)} — ${roleName(r)}"><i></i><b></b></button>`;
    const groupRows = (fn) => Authz.GROUPS.map(g => fn(g, defs.filter(d => d.group === g.id))).join('');
    $view.innerHTML = `<h1>🔐 Role & Permissions</h1>
      <div class="msg info">Saved in this browser only (Phase 1). Other phones/computers keep the default permissions until Phase 2 stores them on the server. Project access (which projects a person can open) is separate and is not changed here.</div>
      <div id="pmsg">${flash === 'saved' ? '<div class="msg ok" role="status">Permissions updated successfully.</div>'
        : flash === 'reset' ? '<div class="msg ok" role="status">Permissions were reset to the default configuration.</div>'
        : flash === 'nostore' ? errBox('Permissions were applied for now, but this browser would not store them, so they will be lost on reload.') : ''}</div>
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
    $('psave').onclick = () => { permFlash = Authz.saveMatrix(permDraft) ? 'saved' : 'nostore'; permDraft = null; screenPermissions(); };
    $('pcancel').onclick = () => { permDraft = Authz.getMatrix(); $('pmsg').innerHTML = ''; screenPermissions(); };
    $('preset').onclick = () => confirmBox('Reset all role permissions to the default configuration?', 'Reset', () => {
      Authz.resetMatrix(); permDraft = null; permFlash = 'reset'; screenPermissions();
    });
    drawMobile();
  }

  // Small Cancel / confirm dialog (same look as the other pop-ups).
  function confirmBox(text, okLabel, onOk) {
    const box = document.createElement('div');
    box.className = 'modal';
    box.innerHTML = `<div class="modal-box" role="dialog" aria-modal="true" aria-label="${esc(text)}">
      <h2 style="margin-top:0">${esc(text)}</h2>
      <button class="btn line" type="button" id="cb-cancel">Cancel</button>
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

  function screenNewProject() {
    chrome('project', '#/project');
    $view.innerHTML = `<h1>➕ नया प्रोजेक्ट <small>New Project</small></h1><div id="npmsg">${NOT_CONNECTED}</div>
      <form id="npf" novalidate>
        <label for="np-name">प्रोजेक्ट का नाम <small>Name</small> *</label><input id="np-name" type="text" autocomplete="off">
        <label for="np-code">कोड <small>Code</small> *</label><input id="np-code" type="text" autocomplete="off" autocapitalize="characters">
        <label for="np-loc">जगह <small>Location</small></label><input id="np-loc" type="text" autocomplete="off">
        <label for="np-plot">प्लॉट साइज़ <small>Plot size</small></label><input id="np-plot" type="text" autocomplete="off">
        <button class="btn green" type="submit">💾 प्रोजेक्ट बनाएँ <span class="sub">CREATE PROJECT</span></button></form>`;
    document.getElementById('npf').onsubmit = ev => {
      ev.preventDefault();
      const out = document.getElementById('npmsg');
      if (!document.getElementById('np-name').value.trim() || !document.getElementById('np-code').value.trim()) { out.innerHTML = errBox('कृपया नाम और कोड भरें.'); return; }
      out.innerHTML = NOT_CONNECTED;
    };
  }

  // ----- Manager Fund: Owner --fund--> Manager --distribution--> Labour. All numbers come from the server. -----
  let fundFlash = '', fundManager = null;                       // fundManager = the manager whose position is shown
  // Money as whole paise (integers), so an amount never picks up floating-point errors.
  const toPaise = t => {
    const m = /^(\d*)(?:\.(\d{0,2}))?$/.exec(String(t || '').trim());
    return m ? Number(m[1] || 0) * 100 + Number((m[2] || '').padEnd(2, '0') || 0) : 0;
  };
  const fromPaise = c => `${Math.floor(c / 100)}.${String(c % 100).padStart(2, '0')}`;
  const modeName = k => (MODES.find(m => m.key === k) || { en: k }).en;
  const fundNoAccess = `<div class="empty"><div class="ico">🔒</div><h2>इस फंड को देखने की अनुमति नहीं है</h2><p>You do not have permission to view Manager Fund.</p></div>`;
  const fundErr = e => (e && e.status === 403 ? 'You do not have permission for this Manager Fund action.' : (serverMsg(e) || friendly(e)));

  async function screenFund() {
    chrome('fund', '#/home');
    if (state.fundForbidden) { $view.innerHTML = fundNoAccess; return; }
    if (!state.project) { $view.innerHTML = noProject(); return; }
    loading();
    let stmts;
    try { stmts = (await api(`manager-funds/statement/?project=${state.project.id}`)).statements; }
    catch (e) { $view.innerHTML = e.status === 403 ? fundNoAccess : errBox(fundErr(e)); return; }
    const flash = fundFlash; fundFlash = '';
    const projectPicker = state.projects.length > 1
      ? `<select id="fproj" aria-label="Project">${state.projects.map(p => `<option value="${p.id}" ${p.id === state.project.id ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>` : '';
    const actions = `${can('canGiveManagerFund') ? '<a class="btn green" href="#/givefund">➕ फंड दें <span class="sub">Give Fund</span></a>' : ''}
      ${can('canDistributeManagerFund') && stmts.length ? '<a class="btn" href="#/distribute">📤 मज़दूरों को दें <span class="sub">Distribute to Labour</span></a>' : ''}`;
    const head = `<h1>💰 मैनेजर फंड <small>Manager Fund</small></h1>
      <p class="muted">प्रोजेक्ट: <b>${esc(state.project.name)}</b></p>${projectPicker}
      ${flash ? `<div class="msg ok" role="status">${esc(flash)}</div>` : ''}`;
    const bindProject = () => {
      const sel = document.getElementById('fproj');
      if (sel) sel.onchange = () => { state.project = state.projects.find(p => p.id === Number(sel.value)); store.set('projectId', state.project.id); state.names = null; fundManager = null; screenFund(); };
    };
    if (!stmts.length) {
      $view.innerHTML = `${head}<div class="empty"><div class="ico">📭</div><p>इस प्रोजेक्ट में अभी किसी मैनेजर को फंड नहीं मिला है.<br><small>No fund has been given to a manager on this project yet.</small></p></div>${actions}`;
      bindProject();
      return;
    }
    let cur = stmts.find(x => x.position.manager_id === fundManager) || stmts[0];
    fundManager = cur.position.manager_id;

    const owners = new Map(cur.funds.map(f => [f.id, f.given_by_owner_name]));
    const pill = st => `<span class="pill ${st === 'ACTIVE' ? '' : 'warn'}">${st === 'ACTIVE' ? 'Active' : 'Cancelled — not counted'}</span>`;
    const draw = () => {
      const p = cur.position, bal = Number(p.available_balance);
      $view.innerHTML = `${head}
        ${stmts.length > 1 ? `<h2>मैनेजर <small>Manager Summary</small></h2>` + stmts.map(x => `
          <button type="button" class="card pick ${x.position.manager_id === fundManager ? 'on' : ''}" data-m="${x.position.manager_id}">
            <div class="row"><b>${esc(x.position.manager_name)}</b><span class="amount">${money(x.position.available_balance)}</span></div>
            <div class="muted">मिला ${money(x.position.total_received)} · बाँटा ${money(x.position.total_distributed)} · बचा ${money(x.position.available_balance)}</div></button>`).join('') : ''}
        <p class="muted">मैनेजर: <b>${esc(p.manager_name)}</b></p>
        <div class="tot-grid">
          <div class="card tot-box"><div class="muted">कुल फंड मिला <small>Total Fund Received</small></div><div class="big-total">${money(p.total_received)}</div><div class="muted">मालिक → मैनेजर <small>Owner → Manager</small></div></div>
          <div class="card tot-box"><div class="muted">कुल बाँटा गया <small>Total Distributed</small></div><div class="big-total">${money(p.total_distributed)}</div><div class="muted">मैनेजर → मज़दूर <small>Manager → Labour</small></div></div>
          <div class="card tot-box ${bal > 0 ? 'ok' : ''}"><div class="muted">बचा हुआ फंड <small>Available Balance</small></div><div class="big-total">${money(p.available_balance)}</div><div class="muted">= मिला − बाँटा <small>Received − active distribution</small></div></div>
        </div>
        ${p.total_received > 0 && bal === 0 ? '<div class="msg info">पूरा फंड बाँटा जा चुका है — अब कोई बचत नहीं. <small>No balance left.</small></div>' : ''}
        ${actions}
        <h2>📥 फंड मिला <small>Fund Received History</small></h2>` +
        (cur.funds.map(f => `<div class="card item"><div class="row"><span class="who">${money(f.amount)}</span><span class="meta">${shortDate(f.date)}</span></div>
          <div class="meta">मालिक: <b>${esc(f.given_by_owner_name)}</b> · ${esc(modeName(f.payment_mode))}</div>
          ${f.remarks ? `<div class="note">📝 ${esc(f.remarks)}</div>` : ''}</div>`).join('') || '<div class="empty">अभी कोई फंड नहीं मिला.</div>') +
        `<h2>📤 मज़दूरों को दिया <small>Labour Distribution History</small></h2>` +
        (cur.distributions.slice().reverse().map(d => `<div class="card item ${d.status === 'ACTIVE' ? '' : 'cancelled'}"><div class="row"><span class="who">${esc(d.labour_name)}</span><span class="amount">${money(d.amount)}</span></div>
          <div class="meta">${shortDate(d.date)} · फंड #${d.manager_fund_id} (${esc(owners.get(d.manager_fund_id) || '—')}) ${pill(d.status)}</div>
          ${d.remarks ? `<div class="note">📝 ${esc(d.remarks)}</div>` : ''}</div>`).join('') || '<div class="empty">अभी किसी मज़दूर को नहीं दिया.</div>') +
        `<h2>🧾 हिसाब-किताब <small>Running Statement</small></h2>
        <p class="muted">📥 फंड = मैनेजर को मिला पैसा · 📤 = मैनेजर ने मज़दूर को दिया · रद्द किया हुआ जोड़ा नहीं जाता.</p>` +
        (cur.ledger.map(e => `<div class="card item ${e.status === 'ACTIVE' ? '' : 'cancelled'}"><div class="row"><span class="who">${e.kind === 'FUND' ? '📥' : '📤'} ${esc(e.label)}</span>
          <span class="amount ${e.kind === 'FUND' ? 'in' : 'out'}">${e.kind === 'FUND' ? '+' : '−'} ${money(e.amount)}</span></div>
          <div class="row meta"><span>${shortDate(e.date)} ${e.status === 'ACTIVE' ? '' : pill(e.status)}</span><span>बचा: <b>${money(e.running_balance)}</b></span></div></div>`).join('') || '<div class="empty">अभी कुछ नहीं.</div>');
      bindProject();
      $view.querySelectorAll('[data-m]').forEach(b => b.onclick = () => { fundManager = Number(b.dataset.m); cur = stmts.find(x => x.position.manager_id === fundManager); draw(); window.scrollTo(0, 0); });
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
    const title = `<h1>➕ फंड दें <small>Give Fund</small></h1>
      <p class="muted">प्रोजेक्ट: <b>${esc(state.project.name)}</b> · मालिक → मैनेजर. यह खर्च नहीं है.</p>`;
    if (!people.managers.length) {
      $view.innerHTML = `${title}<div class="msg info">इस प्रोजेक्ट में अभी कोई मैनेजर नहीं जुड़ा है.<br><small>No manager is assigned to this project yet.</small></div>
        <a class="btn line" href="#/fund">← वापस <span class="sub">Back</span></a>`;
      return;
    }
    $view.innerHTML = `${title}<div id="gmsg"></div>
      <form id="gf" novalidate>
        <label for="g-mgr">मैनेजर <small>Manager</small> *</label>
        <select id="g-mgr"><option value="">— चुनिए —</option>${options(people.managers)}</select>
        ${onBehalf ? `<label for="g-own">किस मालिक ने दिया <small>Given by owner</small> *</label>
          <select id="g-own"><option value="">— चुनिए —</option>${options(people.owners)}</select>`
          : `<p class="muted">दिया: <b>${esc(state.me.name)}</b></p>`}
        <label for="g-amt">राशि <small>Amount</small> *</label>
        <div class="rupee"><span>₹</span><input id="g-amt" type="text" inputmode="decimal" autocomplete="off" placeholder="0"></div>
        <label for="g-date">तारीख <small>Date</small> *</label><input id="g-date" type="date" value="${today()}">
        <label for="g-mode">कैसे दिया <small>Payment mode</small></label>
        <select id="g-mode">${MODES.map(m => `<option value="${m.key}">${m.hi} (${m.en})</option>`).join('')}</select>
        <label for="g-note">जानकारी <small>Remarks (optional)</small></label><input id="g-note" type="text" autocomplete="off">
        <button class="btn green" type="submit" id="g-save">💾 फंड सेव करें <span class="sub">SAVE FUND</span></button>
        <a class="btn line" href="#/fund">रद्द करें <span class="sub">Cancel</span></a>
      </form>`;
    const $ = id => document.getElementById(id);
    $('g-amt').oninput = e => { e.target.value = e.target.value.replace(/[^0-9.]/g, '').replace(/(\..*)\./g, '$1'); };
    $('gf').onsubmit = async ev => {
      ev.preventDefault();
      const bad = t => { $('gmsg').innerHTML = errBox(t); window.scrollTo(0, 0); };
      const manager = Number($('g-mgr').value), owner = onBehalf ? Number($('g-own').value) : state.me.owner_id;
      const cents = toPaise($('g-amt').value);
      if (!manager) return bad('कृपया मैनेजर चुनिए. (Choose a manager.)');
      if (!owner) return bad('कृपया मालिक चुनिए. (Choose the owner.)');
      if (!(cents > 0)) return bad('कृपया राशि भरें (0 से ज़्यादा). (Amount must be greater than zero.)');
      if (cents >= 1e12) return bad('राशि बहुत बड़ी है। कृपया जाँच लें.');
      if (!$('g-date').value) return bad('कृपया तारीख चुनिए.');
      $('g-save').disabled = true; $('gmsg').innerHTML = '';
      try {
        await api('manager-funds/', { method: 'POST', body: {
          project: state.project.id, manager, given_by_owner: owner, fund_amount: fromPaise(cents),
          fund_date: $('g-date').value, payment_mode: $('g-mode').value, remarks: $('g-note').value.trim() } });
      } catch (e) { $('g-save').disabled = false; bad(fundErr(e)); return; }
      fundFlash = 'फंड सेव हो गया. (Fund saved.)'; fundManager = manager;
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
    const back = '<a class="btn line" href="#/fund">← फंड देखें <span class="sub">Back to Manager Fund</span></a>';
    const title = `<h1>📤 मज़दूरों को दें <small>Distribute to Labour</small></h1>
      <p class="muted">प्रोजेक्ट: <b>${esc(state.project.name)}</b></p>`;
    if (!managers.length) { $view.innerHTML = `${title}<div class="msg info">कोई मैनेजर नहीं मिला. <small>No manager found for this project.</small></div>${back}`; return; }
    let manager = managers.find(m => m.id === fundManager) || managers[0];
    const picked = new Set(), amt = {};
    let q = '', showInactive = false, available = 0, saving = false;

    $view.innerHTML = `${title}
      ${state.projects.length > 1 ? `<select id="dproj" aria-label="Project">${state.projects.map(p => `<option value="${p.id}" ${p.id === state.project.id ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>` : ''}
      <div id="dmsg"></div>
      <form id="df" novalidate>
        ${managers.length > 1 ? `<label for="d-mgr">मैनेजर <small>Manager</small></label><select id="d-mgr">${managers.map(m => `<option value="${m.id}" ${m.id === manager.id ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}</select>`
          : `<p class="muted">मैनेजर: <b>${esc(manager.name)}</b></p>`}
        <div class="card tot-box"><div class="muted">उपलब्ध फंड <small>Available Balance</small></div><div class="big-total" id="d-avail">…</div><div class="muted" id="d-note"></div></div>
        <label for="d-date">तारीख <small>Date</small> *</label><input id="d-date" type="date" value="${today()}">
        <div class="q">किन मज़दूरों को दिया? <small>Labour &amp; amounts</small> <small id="d-count"></small></div>
        <input id="d-q" type="text" autocomplete="off" placeholder="🔍 मज़दूर खोजिए (Search Labour)">
        <label class="lab-inact"><input type="checkbox" id="d-inact"> पुराने / काम बंद मज़दूर भी दिखाएँ <small>Show Inactive</small></label>
        <div id="dlist"></div>
        <div class="card lab-total"><div class="row"><span>इस बार का कुल <small>Total This Distribution</small></span><span class="amount" id="d-total">₹ 0</span></div>
          <div class="row"><span>उपलब्ध फंड <small>Available Balance</small></span><span class="amount" id="d-avail2">₹ 0</span></div>
          <div class="row"><span>बाँटने के बाद बचेगा <small>Balance After Distribution</small></span><span class="amount" id="d-after">₹ 0</span></div></div>
        <div class="field-error" id="d-warn"></div>
        <label for="d-note-in">जानकारी <small>Remarks (optional)</small></label><input id="d-note-in" type="text" autocomplete="off">
        <button class="btn green" type="submit" id="d-save">💾 बाँट दें <span class="sub">SAVE DISTRIBUTION</span></button>
        <a class="btn line" href="#/fund">रद्द करें <span class="sub">Cancel</span></a>
      </form>`;
    const $ = id => document.getElementById(id);
    const sum = () => [...picked].reduce((t, id) => t + toPaise(amt[id]), 0);
    const problem = () => {
      if (!picked.size) return 'कम से कम एक मज़दूर चुनिए.';
      if ([...picked].some(id => !(toPaise(amt[id]) > 0))) return 'चुने हुए हर मज़दूर की राशि भरिए (0 से ज़्यादा).';
      if (sum() > available) return 'कुल राशि उपलब्ध फंड से ज़्यादा है — घटाइए. (Total is more than the available balance.)';
      return '';
    };
    function update() {
      const total = sum(), after = available - total;
      $('d-avail').textContent = money(available / 100); $('d-avail2').textContent = money(available / 100);
      $('d-total').textContent = money(total / 100); $('d-after').textContent = money(after / 100);
      $('d-after').classList.toggle('out', after < 0);
      $('d-count').textContent = picked.size ? `(${picked.size} चुने)` : '';
      $('d-warn').textContent = total > available ? problem() : '';
      $('d-save').disabled = saving || !!problem();
    }
    function drawRows() {
      const rows = people.labour.filter(l => (l.is_active || showInactive || picked.has(l.id)) && (!q || l.name.toLowerCase().includes(q)));
      $('dlist').innerHTML = rows.length ? rows.map(l => `
        <div class="lab-row ${picked.has(l.id) ? 'on' : ''}" data-id="${l.id}">
          <label class="lab-pick"><input type="checkbox" class="lab-chk" ${picked.has(l.id) ? 'checked' : ''}>
            <span class="lab-name">${esc(l.name)}${l.type ? ` <small>${esc(l.type)}</small>` : ''} ${l.is_active ? '' : '<span class="pill warn">काम बंद</span>'}</span></label>
          <span class="lab-amt"><span>₹</span><input class="lab-a" type="text" inputmode="decimal" autocomplete="off" placeholder="0" aria-label="${esc(l.name)} राशि" value="${esc(amt[l.id] || '')}"></span>
        </div>`).join('') : '<div class="msg info">इस प्रोजेक्ट में कोई मज़दूर नहीं मिला. <small>No labour found.</small></div>';
    }
    async function loadBalance() {
      try {
        const r = await api(`manager-funds/summary/?project=${state.project.id}&manager=${manager.id}`);
        const row = (r.summary || [])[0];
        available = row ? toPaise(row.available_balance) : 0;
        $('d-note').textContent = row ? '' : 'इस मैनेजर को अभी कोई फंड नहीं मिला. (No fund given yet.)';
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
      if (!$('d-date').value) return bad('कृपया तारीख चुनिए.');
      const lines = people.labour.filter(l => picked.has(l.id));
      saving = true; update(); $('dmsg').innerHTML = '';
      try {
        const r = await api('manager-labour-distributions/batch/', { method: 'POST', body: {
          project: state.project.id, manager: manager.id, date: $('d-date').value, remarks: $('d-note-in').value.trim(),
          include_inactive: lines.some(l => !l.is_active),
          payments: lines.map(l => ({ labour: l.id, amount: fromPaise(toPaise(amt[l.id])) })) } });
        fundFlash = `बँट गया: ${money(r.total)} — ${lines.length} मज़दूर, एक बैच में. (Distribution saved.)`;
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
      case 'add': return screenAdd();
      case 'done': return screenDone();
      case 'list': return screenList(params);
      case 'reports': return screenReports();
      case 'report': return screenReportOne(arg);
      case 'project': return screenProject();
      case 'profile': return screenProfile();
      case 'users': return screenUsers();
      case 'members': return screenMembers();
      case 'settings': return screenSettings();
      case 'permissions': return screenPermissions();
      case 'newproject': return screenNewProject();
      case 'fund': return screenFund();
      case 'givefund': return screenGiveFund();
      case 'distribute': return screenDistribute();
      case 'resetpw': return screenResetPassword(arg);
      default: return screenHome();
    }
  }

  // On phones the on-screen keyboard shrinks the page: hide the bottom bar while typing.
  const typing = el => el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
  document.addEventListener('focusin', e => { if (typing(e.target)) document.body.classList.add('typing'); });
  document.addEventListener('focusout', () => setTimeout(() => { if (!typing(document.activeElement)) document.body.classList.remove('typing'); }, 50));

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(e => console.error('service worker', e)));
  }

  document.addEventListener('click', e => {
    const sheet = document.getElementById('menuSheet');
    if (!sheet.hidden && !e.target.closest('#menuSheet, #menuBtn')) sheet.hidden = true;
  });
  window.addEventListener('hashchange', route);
  route();
})();
