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

  const state = { token: store.get('token'), me: null, projects: [], project: null, names: null };

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
    store.del('token'); store.del('projectId');
    Object.assign(state, { token: null, me: null, projects: [], project: null, names: null });
  }

  // ---------- data loading ----------
  async function loadBasics() {
    if (state.me) return;
    const me = await api('me/');
    const projects = me.owner_id ? await api('projects/') : [];
    state.me = me;
    state.projects = projects.results || projects;
    const saved = Number(store.get('projectId'));
    state.project = state.projects.find(p => p.id === saved) || state.projects[0] || null;
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
    document.getElementById('tabs').hidden = !(loggedIn && tab !== 'home' && tab !== 'blocked');
    document.body.classList.toggle('no-tabs', document.getElementById('tabs').hidden);
    const top = document.getElementById('topbar');
    top.hidden = !back;
    if (back) document.getElementById('backBtn').setAttribute('href', back);
    document.querySelectorAll('.tabs a').forEach(a => a.classList.toggle('on', a.dataset.tab === tab));
    window.scrollTo(0, 0);
  }

  // ---------- screens ----------
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
    try { await api('auth/logout/', { method: 'POST' }); } catch (e) { /* still log out on this phone */ }
    logoutLocal();
    location.hash = '#/login';
  }

  function screenHome() {
    chrome('home');
    const proj = state.project;
    $view.innerHTML = `
      <h1>नमस्ते, ${esc(state.me.name)} 🙏</h1>
      <p class="muted">${proj ? 'प्रोजेक्ट: <b>' + esc(proj.name) + '</b>' : 'अभी कोई प्रोजेक्ट नहीं जुड़ा है.'}</p>
      <div class="home-grid">
        <a class="btn green big" href="#/add"><span class="ico">➕</span><span>खर्च डालें<span class="sub">Add Expense</span></span></a>
        <a class="btn big" href="#/list"><span class="ico">📋</span><span>खर्च देखें<span class="sub">View Expenses</span></span></a>
        <a class="btn big" href="#/project"><span class="ico">🏠</span><span>मेरा प्रोजेक्ट<span class="sub">My Project</span></span></a>
        <a class="btn big" href="#/reports"><span class="ico">📊</span><span>हिसाब देखें<span class="sub">Total Expense</span></span></a>
      </div>
      <button class="btn line" id="out" style="margin-top:28px">🚪 बाहर निकलें <span class="sub">LOGOUT</span></button>`;
    document.getElementById('out').onclick = doLogout;
  }

  async function screenProject() {
    chrome('home', '#/home');
    loading();
    try {
      const totals = await Promise.all(state.projects.map(p => api(`projects/${p.id}/summary/`).catch(() => null)));
      const many = state.projects.length > 1;
      $view.innerHTML = `<h1>🏠 मेरा प्रोजेक्ट <small>My Project</small></h1>` +
        (many ? '<p class="muted">जिस प्रोजेक्ट में काम करना है उसे छूइए.</p>' : '') +
        (state.projects.map((p, i) => `
          <${many ? 'button type="button"' : 'div'} class="card pick ${p.id === state.project.id ? 'on' : ''}" data-id="${p.id}">
            <div class="row"><b style="font-size:1.3rem">${esc(p.name)}</b>${many && p.id === state.project.id ? '<span class="pill">✔ चुना है</span>' : ''}</div>
            ${p.location ? `<div class="muted">📍 ${esc(p.location)}</div>` : ''}
            ${p.plot_size ? `<div class="muted">प्लॉट: ${esc(p.plot_size)}</div>` : ''}
            <div class="muted">${esc(STATUS_HI[p.status] || '')}${p.start_date ? ' · शुरू: ' + niceDate(p.start_date) : ''}</div>
            ${totals[i] ? `<div style="margin-top:8px">कुल खर्च <span class="amount">${money(totals[i].total_expense)}</span></div>` : ''}
          </${many ? 'button' : 'div'}>`).join('') || '<div class="empty">अभी कोई प्रोजेक्ट नहीं जुड़ा है.</div>');
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
    if (!state.project) { $view.innerHTML = '<div class="empty"><div class="ico">🏠</div>अभी कोई प्रोजेक्ट नहीं जुड़ा है.</div>'; return; }
    loading();
    let names;
    try { names = await loadNames(); } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }

    const f = { cat: '', amount: '', name: '', contractId: '', what: '', date: today(), mode: 'CASH', note: '' };
    let saving = false;
    $view.innerHTML = `
      <h1>➕ खर्च डालें <small>Add Expense</small></h1>
      <p class="muted">प्रोजेक्ट: <b>${esc(state.project.name)}</b></p>
      ${state.projects.length > 1 ? '<a class="btn line" href="#/project" style="min-height:56px;font-size:1.05rem">🔁 प्रोजेक्ट बदलें <span class="sub">Change Project</span></a>' : ''}
      <div id="top"></div>
      <div class="step" id="s-cat"><div class="q"><span class="num">1</span>किस चीज़ का खर्च है?</div>
        <div class="choices">${CATS.map(c => `<button type="button" class="choice" data-cat="${c.key}" aria-pressed="false"><span class="ico">${c.icon}</span>${c.hi}<br><small>${c.en}</small></button>`).join('')}</div>
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
          ${r.is_active ? '<button type="button" class="lab-stop">काम बंद करें <small>Mark inactive</small></button>' : ''}
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

    async function drawLabour() {
      const gen = ++labGen;
      $('s-who').innerHTML = `
        <div class="q"><span class="num">3</span>किस मज़दूर को दिया? <small>Labour Payments</small> <small id="lcount"></small></div>
        <div class="lab-bar">
          <input id="lq" type="text" autocomplete="off" enterkeyhint="search" placeholder="🔍 मज़दूर खोजिए (Search Labour)" value="${esc(lab.q)}">
          <button type="button" class="btn line" id="ladd">➕ नया <span class="sub">Add Labour</span></button>
        </div>
        <label class="lab-inact"><input type="checkbox" id="linact" ${lab.showInactive ? 'checked' : ''}> पुराने / काम बंद मज़दूर भी दिखाएँ <small>Show Inactive</small></label>
        <div id="lnote"></div>
        <div id="llist"><div class="spinner">⏳ रुकिए...</div></div>
        <div class="card row lab-total"><span>कुल मज़दूरी <small>Total Labour Payment</small></span><span class="amount" id="ltotal">${money(0)}</span></div>
        <div><div class="field-error" id="e-lab"></div></div>`;
      $('lq').oninput = e => { lab.q = e.target.value; labFilter(); };
      $('ladd').onclick = () => labModal(gen);
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
      if (cat === 'LABOUR') { f.name = ''; f.contractId = ''; f.what = ''; drawLabour(); return; }
      if (!cat) {
        html = '<div class="q"><span class="num">3</span>किसको दिया? <small>Name</small></div><p class="muted">पहले ऊपर बताइए कि किस चीज़ का खर्च है.</p>';
      } else if (cat === 'CONTRACTOR') {
        html = `<div class="q"><span class="num">3</span>किस ठेकेदार को दिया? <small>Contractor</small></div>
          <button class="btn line" type="button" id="cadd">➕ नया ठेकेदार <span class="sub">Add Contractor</span></button>` +
          (names.contracts.length
            ? names.contracts.map(contractCard).join('')
            : '<div class="msg info">इस प्रोजेक्ट में अभी कोई ठेकेदार नहीं जुड़ा है। "नया ठेकेदार" दबाइए.</div>') +
          '<div class="field-error" id="e-who"></div>';
      } else {
        const label = cat === 'LABOUR' ? 'किस मज़दूर को दिया?' : cat === 'SUPPLIER' ? 'किस सप्लायर को दिया?' : 'किसको दिया?';
        html = `<label for="name"><span class="num">3</span>${label} <small>Name</small></label>
          <input id="name" type="text" autocomplete="off" autocapitalize="words" enterkeyhint="next" placeholder="नाम लिखिए">
          <div class="suggest" id="sug"></div><div class="hint" id="newhint"></div><div class="field-error" id="e-who"></div>` +
          (cat === 'MISCELLANEOUS' ? `<label for="what" style="margin-top:18px;display:block;font-weight:700">किस काम का? <small>(optional, जैसे: चाय, ट्रांसपोर्ट)</small></label><input id="what" type="text" autocomplete="off" autocapitalize="sentences" enterkeyhint="next">` : '');
      }
      $('s-who').innerHTML = html;
      f.name = ''; f.contractId = ''; f.what = '';
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
      if (f.cat !== 'CONTRACTOR' && f.cat !== 'LABOUR' && !f.name.trim()) return fail('e-who', 'कृपया नाम भरें.', 's-who');
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
          const typed = f.name.trim();
          let match = names.suppliers.find(x => x.name.toLowerCase() === typed.toLowerCase());
          if (!match) match = await api('suppliers/', { method: 'POST', body: { name: typed } });
          body.supplier = match.id;
        }
        await api('expense-transactions/', { method: 'POST', body });
        state.names = null;
        sessionStorage.setItem('justSaved', JSON.stringify({ amount: body.amount, who: f.cat === 'CONTRACTOR' ? names.contracts.find(c => String(c.id) === f.contractId).contractor_name : f.name.trim() }));
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
      <a class="btn line" href="#/list">📋 खर्च देखें <span class="sub">View Expenses</span></a>
      <a class="btn line" href="#/home">🏠 होम पर जाएँ <span class="sub">Home</span></a>`;
  }

  // ----- Expenses list -----
  let listState = { cat: '', shown: 20 };

  async function screenList(params) {
    chrome('list', '#/home');
    if (!state.project) { $view.innerHTML = '<div class="empty"><div class="ico">🏠</div>अभी कोई प्रोजेक्ट नहीं जुड़ा है.</div>'; return; }
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
        <div class="chips">${chip('', 'सब')}${CATS.map(c => chip(c.key, `${c.icon} ${c.hi}`)).join('')}</div>
        <div class="card"><div class="row"><span>कुल खर्च <small>Total</small></span><span class="amount">${money(total)}</span></div></div>` +
        (shown.length ? shown.slice(0, listState.shown).map(r => `
          <div class="card item">
            <div class="row"><span class="who">${esc(nameOf(r) || '—')}</span><span class="amount">${money(r.amount)}</span></div>
            <div class="meta">${CAT[r.expense_category].icon} ${CAT[r.expense_category].hi}${r.expense_category === 'MISCELLANEOUS' && r.expense_type !== 'Other' ? ' · ' + esc(r.expense_type) : ''} · ${niceDate(r.expense_date)}</div>
            ${r.remarks ? `<div class="note">📝 ${esc(r.remarks)}</div>` : ''}
          </div>`).join('') : `<div class="empty"><div class="ico">📭</div><p>अभी कोई खर्च नहीं है.</p><a class="btn green" href="#/add">➕ खर्च डालें <span class="sub">Add Expense</span></a></div>`) +
        (shown.length > listState.shown ? '<button type="button" class="btn line" id="more">⬇ और दिखाएँ <span class="sub">Show more</span></button>' : '');
      $view.querySelectorAll('[data-c]').forEach(b => b.onclick = () => { listState.cat = b.dataset.c; listState.shown = 20; draw(); });
      const more = document.getElementById('more');
      if (more) more.onclick = () => { listState.shown += 20; draw(); };
    }
    draw();
  }

  // ----- Reports -----
  async function screenReports() {
    chrome('reports', '#/home');
    if (!state.project) { $view.innerHTML = '<div class="empty"><div class="ico">🏠</div>अभी कोई प्रोजेक्ट नहीं जुड़ा है.</div>'; return; }
    loading();
    let d;
    try { d = await api(`projects/${state.project.id}/dashboard/`); } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
    const total = Number(d.total_expense);
    const pct = v => (total > 0 ? Math.round((Number(v) / total) * 100) : 0);

    $view.innerHTML = `
      <h1>📊 हिसाब देखें <small>Total Expense</small></h1>
      <p class="muted">प्रोजेक्ट: <b>${esc(state.project.name)}</b></p>
      <div class="card"><div class="muted">कुल खर्च <small>Total Expense</small></div><div class="big-total">${money(total)}</div></div>
      <h2>किस पर कितना खर्च हुआ</h2>
      ${CATS.map(c => `
        <a class="card" href="#/report/${c.key}">
          <div class="row"><b>${c.icon} ${c.hi} <small>${c.en}</small></b><span class="amount">${money(d.category_breakup[c.key])}</span></div>
          <div class="bar"><i style="width:${pct(d.category_breakup[c.key])}%"></i></div>
          <div class="muted" style="margin-top:6px">${pct(d.category_breakup[c.key])}% · देखने के लिए छूइए ›</div>
        </a>`).join('')}
      ${d.owner_contribution.length ? `<h2>मालिक का हिस्सा <small>Owner Share</small></h2>` + d.owner_contribution.map(o => `
        <div class="card"><div class="row"><b>${esc(o.owner_name)}</b><span class="amount">${money(o.total)}</span></div>
        <div class="muted">कुल खर्च का ${pct(o.total)}% दिया</div></div>`).join('') : ''}
      ${d.contractor_positions.length ? `<h2>ठेकेदार का हिसाब</h2>` + d.contractor_positions.map(c => {
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
    chrome('reports', '#/reports');
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
        `<a class="btn line" href="#/list?cat=${key}">📋 सारे खर्च देखें <span class="sub">View all</span></a>`;
    } catch (e) { $view.innerHTML = errBox(friendly(e)); }
  }

  // ---------- router ----------
  async function route() {
    const [path, qs] = (location.hash.replace(/^#/, '') || '/home').split('?');
    const params = new URLSearchParams(qs || '');
    if (!state.token) { if (path !== '/login') { location.hash = '#/login'; return; } return screenLogin(); }
    if (path === '/login') { location.hash = '#/home'; return; }
    try { await loadBasics(); } catch (e) { if (e.kind !== 'auth') { chrome('blocked'); $view.innerHTML = errBox(friendly(e)); } return; }
    if (!state.me.owner_id) return screenBlocked();

    const [, page, arg] = path.split('/');
    switch (page) {
      case 'add': return screenAdd();
      case 'done': return screenDone();
      case 'list': return screenList(params);
      case 'reports': return screenReports();
      case 'report': return screenReportOne(arg);
      case 'project': return screenProject();
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

  window.addEventListener('hashchange', route);
  route();
})();
